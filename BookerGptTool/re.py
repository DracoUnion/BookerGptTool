# -*- coding: utf-8 -*-

"""re 子命令：逆向工程智能体（IDA / x64dbg / x32dbg + 基础工具）。

采用开放工具调用循环：模型每轮决定调用哪个工具，编排器用
call_llm_with_toolcall_retry 驱动循环直到模型调用 tool_finish。
"""

import json
import logging
import os
import re
import traceback
from os import path
from typing import Dict, List, Any, Optional, Callable

import json_repair

from .openai import call_llm_with_toolcall_retry, set_openai_props, logger as oai_logger
from .util import render_prompt, write_text, json_dump_model
from .re_models import *
from .re_pmt import RE_SYSTEM_PROMPT, RE_ROUND_PROMPT
from .re_tools import ReTools

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def parse_final(ans: str) -> ReFinalReport:
    """把最终交付文本解析为 ReFinalReport（容错提取 JSON）。"""
    text = ans.strip()
    if text.startswith('```'):
        m = re.search(r'```(?:json)?\s*([\s\S]+?)```', text)
        if m:
            text = m.group(1)
    data = None
    try:
        data = json_repair.loads(text)
    except Exception:
        data = None
    if not isinstance(data, dict):
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end > start:
                data = json_repair.loads(text[start:end + 1])
        except Exception:
            data = None
    if not isinstance(data, dict):
        return ReFinalReport(
            status='needs_approval',
            stage='UNKNOWN',
            summary=f'无法解析模型最终输出，原文如下：\n{text[:2000]}',
            blockers=['最终输出非合法 JSON'],
            next_action='人工介入',
        )
    if 'done' in data and 'status' not in data:
        data['status'] = 'done'
    try:
        return ReFinalReport(**data)
    except Exception:
        return ReFinalReport(
            status='needs_approval',
            stage=str(data.get('stage', 'UNKNOWN')),
            summary=str(data.get('summary', json_dump_model(data))),
            blockers=['最终输出字段不全，请人工检查'],
            next_action='人工介入',
        )


class ReOrchestrator:
    """逆向工程智能体编排器（开放工具调用循环）。"""

    def __init__(self, args):
        """根据命令行参数初始化编排器与工具集。"""
        self.args = args
        self.tools = ReTools(args)
        self.pj_dir = self.tools.pj_dir
        self.max_steps = getattr(args, 'max_steps', 60)
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))
        logger.info('MCP 状态：%s', json_dump_model(self.tools.tool_mcp_status()))

    def run(self) -> ReFinalReport:
        """执行 tool-call 循环直到模型调用 tool_finish 结束。"""
        state = self.tools.tool_load_state()
        if not state.goal:
            state.goal = getattr(self.args, 'goal', '') or ''
        round_prompt = render_prompt(
            RE_ROUND_PROMPT,
            state_json=json_dump_model(state),
            user_input=state.goal or '（未提供目标，请先明确逆向目标）',
            tool_results_json='无',
        )
        msgs = [
            {'role': 'system', 'content': RE_SYSTEM_PROMPT},
            {'role': 'user', 'content': round_prompt},
        ]
        msgs[1]['content'] += (
            f'\n\n（本次运行步数上限：{self.max_steps} 轮。'
            '接近上限时应优先收敛并调用 tool_finish。）'
        )

        final: ReFinalReport = call_llm_with_toolcall_retry(
            msgs, self.args.model,
            self.tools.get_tool_defs(),
            self.tools.get_tool_dict(),
            tool_finish_name='tool_finish',
            history_fname=path.join(self.pj_dir, 'history.yaml'),
            retry=self.args.retry,
            temp=self.args.temp,
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            presence_penalty=self.args.presence_penalty,
            max_tokens=self.args.max_tokens,
            extra_body=self.args.extra_body,
            parse_output=parse_final,
        )
        # 推理模型常把最终 JSON 放 reasoning_content，content 为空导致解析失败；
        # 此时用已持久化状态重建一份可读报告。
        if (getattr(final, 'status', '') == 'needs_approval'
                and getattr(final, 'stage', '') == 'UNKNOWN'):
            try:
                rebuilt = self.tools.tool_rebuild_report()
                final = rebuilt
            except Exception as ex:
                logger.warning('重建报告失败：%s', ex)

        report_path = path.join(self.pj_dir, 'final_report.md')
        write_text(report_path, json_dump_model(final))
        logger.info('流程结束：%s | 阶段：%s', final.status, final.stage)
        logger.info('最终报告已写入：%s', report_path)
        print(json_dump_model(final))
        # 关闭 IDA 数据库（保存分析结果）
        if self.tools.ida is not None and self.tools.ida.db_open:
            try:
                self.tools.ida.close_database(save=True)
            except Exception:
                pass
        # 分离/关闭 x64dbg/x32dbg 会话（调试器保持运行，仅断连接）
        for target, xc in self.tools.xauto.items():
            try:
                if xc.connected:
                    xc.detach()
            except Exception:
                pass
        return final


# ── 处理入口 ─────────────────────────────────

def check_avail(args):
    """检查 idapro/idalib 与 x64dbg/x32dbg 是否可用，打印结果并退出。"""
    import os
    from os import path
    print('═' * 60)
    print('IDA (idalib)')
    print('═' * 60)
    ida_ok = False
    try:
        import idapro
        ida_dir = idapro.get_ida_install_dir()
        idalib = getattr(idapro, 'idalib_path', '')
        print(f'  idapro 包    : OK ({idapro.__file__})')
        print(f'  IDA 目录     : {ida_dir or "（未配置 ida-install-dir）"}')
        if idalib:
            print(f'  idalib.dll   : {idalib}')
            print(f'    exists     : {path.isfile(idalib)}')
            if path.isfile(idalib):
                ida_ok = True
        try:
            ver = idapro.get_library_version()
            print(f'  版本         : {ver}')
        except Exception:
            pass
        if not ida_ok:
            print('  [✗] IDA 不可用：idalib 缺失或未配置安装目录')
    except Exception as ex:
        print(f'  [✗] idapro 导入失败：{ex}')
        print('      请安装 idapro（pip install idapro）并配置 %APPDATA%\\Hex-Rays\\IDA Pro\\ida-config.json')

    print()
    print('═' * 60)
    print('x64dbg / x32dbg (x64dbg_automate)')
    print('═' * 60)
    xauto_ok = False
    try:
        import x64dbg_automate as XA
        print(f'  x64dbg_automate 包: OK ({XA.__file__})')
        xauto_ok = True
    except Exception as ex:
        print(f'  [✗] x64dbg_automate 导入失败：{ex}')
        print('      请安装 pip install x64dbg-automate')
    for label, key in (('x64dbg', 'x64dbg_path'), ('x32dbg', 'x32dbg_path')):
        p = getattr(args, key, '') or ''
        print(f'  {label:<6}: {p or "（未指定路径）"}  exists={path.isfile(p) if p else False}')
    # 会话发现（库本身）
    try:
        import x64dbg_automate as XA
        sessions = XA.X64DbgClient.list_sessions()
        print(f'  当前活动会话: {len(sessions)} 个')
        for s in sessions:
            print(f'    pid={s.pid}  title={s.window_title}  port={s.sess_req_rep_port}')
    except Exception as ex:
        print(f'  会话发现失败: {ex}')

    print()
    ok = ida_ok or xauto_ok
    print(f'总结：IDA={ "可用" if ida_ok else "不可用" }, '
          f'x64dbg/x32dbg={ "可用" if xauto_ok else "不可用" }')
    raise SystemExit(0 if ok else 1)


def re_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    if getattr(args, 'check_avail', False):
        check_avail(args)
    if getattr(args, 'debug', False):
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    return ReOrchestrator(args).run()


def reg_subparser(subparsers):
    p = subparsers.add_parser('re', help='逆向工程智能体（IDA / x64dbg / x32dbg + 基础工具）')
    p.add_argument(
        '-g', '--goal', default='',
        help='逆向目标（如：定位并分析某函数的反编译逻辑）',
    )
    p.add_argument(
        '-p', '--project', default='re_project',
        help='项目工作目录（也作恢复断点用）',
    )
    p.add_argument(
        '-ms', '--max-steps', type=int, default=60,
        help='本轮循环步数上限（提示模型收敛，默认 60）',
    )
    p.add_argument(
        '-ca', '--check-avail', action='store_true',
        help='检查 idapro/idalib 与 x64dbg/x32dbg 是否可用，打印并退出',
    )
    p.add_argument(
        '--x64dbg-path', default=r'D:\tools\x64dbg_latest\release\x64\x64dbg.exe',
        help='x64dbg 可执行文件路径（x64dbg_automate 启动会话用）',
    )
    p.add_argument(
        '--x32dbg-path', default=r'D:\tools\x64dbg_latest\release\x32\x32dbg.exe',
        help='x32dbg 可执行文件路径（x32dbg_automate 启动会话用）',
    )
    p.add_argument(
        '-no-ida', '--no-ida', action='store_true',
        help='禁用 IDA (idalib) 连接（仅用 x64dbg/x32dbg + 基础工具）',
    )
    p.add_argument(
        '-no-xauto', '--no-xauto', action='store_true',
        help='禁用 x64dbg/x32dbg 直连（仅用 IDA + 基础工具）',
    )
    p.add_argument('-D', '--debug', action='store_true', help='调试模式')
    p.set_defaults(func=re_handle)
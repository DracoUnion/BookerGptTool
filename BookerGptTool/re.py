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
        for client in self.tools.mcp_clients.values():
            try:
                client.close()
            except Exception:
                pass
        self.tools.mcp_clients.clear()
        return final


# ── 处理入口 ─────────────────────────────────

def re_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
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
        '-mc', '--mcp-config', default='',
        help='MCP 服务器配置文件（含 mcpServers 的 JSON，缺省 ~/.claude.json）',
    )
    p.add_argument('--idacmd', default='', help='覆盖 IDA MCP 服务器启动命令')
    p.add_argument('--idaargs', default='', help='覆盖 IDA MCP 服务器启动参数（空格分隔）')
    p.add_argument('--x64dbgurl', default='', help='覆盖 x64dbg MCP HTTP 地址')
    p.add_argument('--x64dbgtoken', default='', help='覆盖 x64dbg MCP Bearer token')
    p.add_argument('--x32dbgurl', default='', help='覆盖 x32dbg MCP HTTP 地址')
    p.add_argument('--x32dbgtoken', default='', help='覆盖 x32dbg MCP Bearer token')
    p.add_argument('-D', '--debug', action='store_true', help='调试模式')
    p.set_defaults(func=re_handle)
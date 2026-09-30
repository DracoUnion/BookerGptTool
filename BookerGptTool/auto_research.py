import json
import logging
import os
from os import path
from typing import Any, Dict

import json_repair

from .openai import call_llm_with_toolcall_retry, set_openai_props
from .util import render_prompt, write_text, json_dump_model
from .auto_research_models import *
from .auto_research_pmt import (
    AUTO_RESEARCH_SYSTEM_PROMPT, AUTO_RESEARCH_ROUND_PROMPT,
)
from .auto_research_tools import AutoResearchTools

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def parse_final(ans: str) -> FinalReport:
    """把最终交付文本解析为 FinalReport（容错提取 JSON）。"""
    text = ans.strip()
    if text.startswith('```'):
        # 去掉代码块围栏
        m = re_search_fence(text)
        if m:
            text = m
    data = None
    try:
        data = json_repair.loads(text)
    except Exception:
        pass
    if not isinstance(data, dict):
        # 尝试提取第一个 { ... } JSON 片段
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end > start:
                data = json_repair.loads(text[start:end + 1])
        except Exception:
            data = None
    if not isinstance(data, dict):
        return FinalReport(
            status='needs_approval',
            stage='UNKNOWN',
            decision_summary=f'无法解析模型最终输出，原文如下：\n{text[:2000]}',
            blockers=['模型最终输出非合法 JSON'],
            next_action='人工介入',
        )
    if 'done' in data and 'status' not in data:
        data['status'] = 'done'
    try:
        return FinalReport(**data)
    except Exception:
        return FinalReport(
            status='needs_approval',
            stage=str(data.get('stage', 'UNKNOWN')),
            decision_summary=str(data.get('decision_summary', json_dump_model(data))),
            blockers=['最终输出字段不全，请人工检查'],
            next_action='人工介入',
        )


def re_search_fence(text: str) -> str:
    """去掉 ``` 围栏，返回围栏内正文。"""
    lines = text.splitlines()
    if lines and lines[0].strip().startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines)


class AutoResearchOrchestrator:
    """合规科研自动化流水线编排器（开放工具调用循环）。"""

    def __init__(self, args):
        """根据命令行参数初始化编排器与工具集。"""
        self.args = args
        self.tools = AutoResearchTools(args)
        self.pj_dir = self.tools.pj_dir
        self.max_steps = getattr(args, 'max_steps', 60)

    def run(self) -> FinalReport:
        """执行 tool-call 循环直到模型调用 tool_finish 结束。"""
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))
        init_state = PipelineState(
            project_id=self.tools.project_id, stage='PROJECT_INIT')
        round_prompt = render_prompt(
            AUTO_RESEARCH_ROUND_PROMPT,
            pipeline_state_json=json_dump_model(init_state),
            user_input=self.args.goal,
            tool_results_json='无',
        )
        msgs = [
            {'role': 'system', 'content': AUTO_RESEARCH_SYSTEM_PROMPT},
            {'role': 'user', 'content': round_prompt},
        ]
        # 把步数上限写入提示，供模型作为节奏参考
        msgs[1]['content'] += (
            f'\n\n（本次运行步数上限：{self.max_steps} 轮。'
            '接近上限时应优先收敛并调用 tool_finish。）'
        )

        final: FinalReport = call_llm_with_toolcall_retry(
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

        report_path = path.join(self.pj_dir, 'final_report.md')
        write_text(report_path, json_dump_model(final))
        logger.info('流水线结束：%s | 阶段：%s', final.status, final.stage)
        logger.info('最终报告已写入：%s', report_path)
        print(json_dump_model(final))
        return final


# ── 处理入口 ─────────────────────────────────


def auto_research_handle(args) -> FinalReport:
    """入口函数：创建编排器并运行完整流程。"""
    return AutoResearchOrchestrator(args).run()


def reg_subparser(subparsers):
    p = subparsers.add_parser(
        'auto-research', help='合规科研自动化流水线（文献→假设→实验→写作→投稿包）')
    p.add_argument(
        '-p', '--project', default='auto_research',
        help='项目工作目录（也作恢复断点用）',
    )
    p.add_argument(
        '-g', '--goal', default='',
        help='研究问题/目标（必填，否则请在流程中向用户确认）',
    )
    p.add_argument(
        '-ms', '--max-steps', type=int, default=60,
        help='本轮循环步数上限（提示模型收敛，默认 60）',
    )
    p.set_defaults(func=auto_research_handle)

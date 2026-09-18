# -*- coding: utf-8 -*-
"""
md2wiki.py —— LLM Wiki 子命令编排器。

将 ../md2wiki 项目（llm-wiki / llm-wiki-skill 等）的完整工作流封装为命令行子命令：
workspace/config、input、ingest、query、lint、graph、discover，以及 book-summary /
competitive-brief / interview-prep 特殊命令。全程由「开放工具调用循环」驱动。
"""

import logging
import os
from os import path
from typing import Dict, Any

from .openai import call_llm_with_toolcall_retry
from .md2wiki_tools import Md2WikiTools
from .md2wiki_pmt import (
    OVERALL_PMT,
    ACTION_INGEST, ACTION_QUERY, ACTION_LINT, ACTION_GRAPH,
    ACTION_DISCOVER, ACTION_BOOK_SUMMARY, ACTION_BRIEF, ACTION_INTERVIEW,
)
from .util import render_prompt

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 动作名 → 动作模板
ACTION_MAP = {
    'ingest': ACTION_INGEST,
    'query': ACTION_QUERY,
    'lint': ACTION_LINT,
    'graph': ACTION_GRAPH,
    'discover': ACTION_DISCOVER,
    'book-summary': ACTION_BOOK_SUMMARY,
    'competitive-brief': ACTION_BRIEF,
    'interview-prep': ACTION_INTERVIEW,
}


class WikiOrchestrator:
    """编排器：通过工具调用循环驱动完整的 LLM Wiki 工作流。"""

    def __init__(self, args):
        """根据命令行参数初始化编排器与工具集。"""
        self.args = args
        self.action = getattr(args, 'action', 'ingest') or 'ingest'
        if self.action not in ACTION_MAP:
            raise ValueError(f'未知动作：{self.action}，可选：{", ".join(ACTION_MAP)}')
        self.tools = Md2WikiTools(args)
        self.wiki_root = self.tools.wiki_root

    def _action_desc(self) -> str:
        tmpl = ACTION_MAP[self.action]
        action = self.action
        target = getattr(self.args, 'target', '') or self.args.fname
        question = getattr(self.args, 'question', '') or ''
        try:
            if action == 'query':
                return render_prompt(tmpl, QUESTION=question or target)
            if action in ('competitive-brief', 'interview-prep', 'ingest'):
                return render_prompt(tmpl, TARGET=target)
            return tmpl
        except Exception as e:
            logger.warning('动作模板渲染失败，回退：%s', e)
            return tmpl

    def run(self) -> Dict[str, Any]:
        """启动工具调用循环，执行所选工作流。"""
        logger.info(self.args)
        logger.info('wiki_root: %s, action: %s', self.wiki_root, self.action)
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))

        action_desc = self._action_desc()
        prompt = render_prompt(OVERALL_PMT, ACTION_DESC=action_desc)

        call_llm_with_toolcall_retry(
            prompt, self.args.model,
            self.tools.get_tool_defs(),
            self.tools.get_tool_dict(),
            tool_finish_name='tool_finish',
            retry=self.args.retry,
            temp=self.args.temp,
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            presence_penalty=self.args.presence_penalty,
            max_tokens=self.args.max_tokens,
            extra_body=self.args.extra_body,
        )

        logger.info(f'[*] 已完成，wiki 工作区：{self.wiki_root}')
        return {'wiki_root': self.wiki_root, 'action': self.action}


def md2wiki_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return WikiOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        "md2wiki",
        help="LLM Wiki：摄入/查询/检查/图谱/发现 文档知识库",
    )
    parser.add_argument(
        "fname",
        help="待摄入文件/目录，或 wiki 项目的素材根目录",
    )
    parser.add_argument(
        "-w", "--workspace", default=None,
        help="wiki 工作区根目录（默认：<fname 所在目录>_md2wiki）",
    )
    parser.add_argument(
        "-a", "--action", default='ingest',
        choices=list(ACTION_MAP.keys()),
        help="要执行的动作：ingest/query/lint/graph/discover/book-summary/competitive-brief/interview-prep",
    )
    parser.add_argument(
        "-q", "--question", default="",
        help="query 动作的问题",
    )
    parser.add_argument(
        "-t", "--topic", default='inbox',
        help="input/ingest 时的 topic slug",
    )
    parser.add_argument(
        "-T", "--target", default="",
        help="competitive-brief / interview-prep 的动作对象名",
    )
    parser.set_defaults(func=md2wiki_handle)
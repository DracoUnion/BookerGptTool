# -*- coding: utf-8 -*-
"""
paper2textbook.py —— 多篇论文到可溯源教科书的完整编排器。

流程：
1. 读取/抽取多篇论文，形成带页码与原文摘录的概念卡片；
2. 按知识主题聚类论文，并校验论文覆盖；
3. 依据论文聚类、概念卡片和领域综述生成章—知识点大纲；
4. 逐章生成概念解析、学习目标、概念地图、练习等细纲并校验覆盖；
5. 逐章生成正文，执行格式检查、跨章一致性检查和修订；
6. 执行引用审计，合并章节并导出 Markdown/LaTeX/PDF。
"""

import html
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, Future
from os import path
from typing import Dict, List, Tuple

import yaml
from pydantic import BaseModel, parse_obj_as

from .openai import logger as oai_logger
from .paper2textbook_tools import Paper2TextbookTools
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import extname
from .openai import call_llm_retry, TOOLCALL_PMT, parse_toolcall, dispatch_tools
from .paper2textbook_pmt import OVERALL_PMT

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)



class Paper2TextbookOrchestrator:
    """编排论文拆解、教学设计、章节写作和教材交付。"""

    def __init__(self, args):
        self.args = args
        self.agent = Paper2TextbookTools(args)
        self.pool = ThreadPoolExecutor(max_workers=args.threads)
        self.hdls: List[Future] = []
        self.pj_dir = (
            path.dirname(args.dir) + '_paper2textbook'
            if path.isfile(args.dir) else
            path.abspath(args.dir) + '_paper2textbook'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        self.tools = self.agent.list_tools()


    # ── 主流程 ──────────────────────────────────────────

    def run(self):
        if not path.exists(self.args.dir):
            raise ValueError('请提供论文文件、论文目录或 ARXIV ID')
        os.makedirs(self.pj_dir, exist_ok=True)
        logger.info(self.args)
        
        tool_defs = self.agent.get_tool_defs()
        tool_pmt = TOOLCALL_PMT.replace('{tool_def}', json.dumps(tool_defs, ensure_ascii=False))
        msgs: List[dict[str, Any]] = [
            {"role": "system", 'content': tool_pmt},
            {"role": "user", "content": OVERALL_PMT},
        ]

        while True:
            res = call_llm_retry(
                    msgs, self.args.model,
                    retry=self.args.retry, 
                    temp=self.args.temp, 
                    top_p=self.args.top_p,
                    frequency_penalty=self.args.frequency_penalty,
                    presence_penalty=self.args.presence_penalty,
                    max_tokens=self.args.max_tokens,
                    extra_body=self.args.extra_body,
            )
            tool_blocks, errmsg = parse_toolcall(res)
            if errmsg or not tool_blocks:
                # No tool call: the model stopped or is giving plain text. Treat
                # as a soft stop unless it already finalised.
                errmsg = errmsg or \
                    f"未找到任何工具调用，请将工具调用包含在 [tool]...[/tool] 中。如果你想结束整个流程，调用`tool_finish`。"
                msgs.append({"role": "assistant", "content": res})
                msgs.append({"role": "user", "content": errmsg})
                continue

            print(f'toolcall: {tool_blocks}')
            toolcall_res_list = []
            toolcall_errmsgs = []
            for tc in tool_blocks:
                # finalize ends the run immediately.
                if tc.tool == "tool_finish":
                    return
                result, errmsg = dispatch_tools(self.tools, tc.tool, tc.parameters)
                if errmsg:
                    toolcall_errmsgs.append(errmsg)
                    continue
                # After a blocked gated stage, if the host paused (no --yes),
                # surface the pause and halt.
                toolcall_res_list.append({
                    "id": tc.id,
                    "result": json.dumps(result, ensure_ascii=False),
                })
            
            print(f'toolcall res: {toolcall_res_list}')
            toolcall_res_str = json.dumps(toolcall_res_list, ensure_ascii=False)
            msgs.append({"role": "assistant", "content": res})
            msgs.append({
                "role": "user",
                "content": f"[tool-result]{toolcall_res_str}[/tool-result]",
            })
            if toolcall_errmsgs:
                msgs.append({
                    "role": "user",
                    "content": '\n'.join(toolcall_errmsgs),
                })



def paper2textbook(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    Paper2TextbookOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'paper2textbook',
        help='多篇论文到可溯源教科书',
    )
    parser.add_argument('dir', help='论文文件、论文目录或 ARXIV ID（暂以本地文件/目录为主）')
    parser.add_argument('-f', '--format', choices=('md', 'tex', 'pdf'), default='md', help='输出格式')
    parser.add_argument('-T', '--threads', type=int, default=4, help='并行线程数')
    parser.add_argument('-c', '--check', type=int, default=3, help='覆盖/格式检查次数')
    parser.add_argument('--glossary', action='store_true', help='生成术语对照表')
    parser.add_argument('--consistency', action='store_true', help='执行跨章一致性检查')
    parser.add_argument('-D', '--debug', action='store_true', help='调试模式')
    parser.set_defaults(func=paper2textbook)

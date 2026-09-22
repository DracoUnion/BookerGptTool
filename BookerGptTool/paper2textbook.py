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
from .openai import call_llm_with_toolcall_retry
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
        self.tools = Paper2TextbookTools(args)
        self.pj_dir = self.tools.pj_dir
        self.pool = ThreadPoolExecutor(getattr(args, 'threads', 8))
        self.hdls: List[Future] = []


    # ── 主流程 ──────────────────────────────────────────

    def run(self):
        if not path.exists(self.args.dir):
            raise ValueError('请提供论文文件、论文目录或 ARXIV ID')
        os.makedirs(self.pj_dir, exist_ok=True)
        logger.info(self.args)
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))
        
        call_llm_with_toolcall_retry(
            OVERALL_PMT, self.args.model, 
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
        )

        logger.info(f'[*] 已完成，目标文件已写入 {self.pj_dir}')



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
    parser.add_argument('-D', '--debug', action='store_true', help='调试模式')
    parser.set_defaults(func=paper2textbook)

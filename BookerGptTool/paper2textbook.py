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


    # ── 主流程 ──────────────────────────────────────────

    def run(self):
        if not path.exists(self.args.dir):
            raise ValueError('请提供论文文件、论文目录或 ARXIV ID')
        os.makedirs(self.pj_dir, exist_ok=True)
        logger.info(self.args)
        paper_fnames = self._discover_papers()
        paper_briefs = self._paper_brief(paper_fnames)
        cards = self.step_extract_concepts(paper_fnames)
        parts = self.step_cluster_papers(paper_briefs)
        outline = self.step_gen_outline(parts, cards)
        outline_chs = sum([pt.chapters for pt in outline], [])
        details = self.step_gen_details(outline_chs, cards)
        bodies = self.step_gen_bodies(outline_chs, details, cards)
        '''
        glossary = self._gen_glossary(paper_briefs) if self.args.glossary else []
        if self.args.consistency:
            comments = self._consistency_check(bodies)
            if comments:
                logger.warning('[6] 跨章一致性检查发现问题：\n%s', '\n'.join(comments))
        self.step_assemble(
            path.basename(path.abspath(self.args.dir)),
            outline_chs, bodies, paper_briefs, glossary,
        )
        '''
        logger.info('[DONE] 教材已写入 %s', self.pj_dir)


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

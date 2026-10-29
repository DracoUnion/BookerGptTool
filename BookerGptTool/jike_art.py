# -*- coding: utf-8 -*-
"""
jike_art.py —— 文章 → 即刻发布文案（LLM）

流程：
1. 读取文章 Markdown；
2. 用 LLM 生成即刻发布文案（正文 + 话题圈）；
3. 写出 .txt。

架构参照 article_img 子命令（Agent + Orchestrator + reg_subparser）。
"""

import logging
import os
from os import path

from .util import write_text
from .jike_art_agent import JikeArtAgent
from .jike_art_models import JikeCopy

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class JikeArtOrchestrator:
    """协调即刻文案生成与写出。"""

    def __init__(self, args):
        self.args = args
        self.agent = JikeArtAgent(args)
        self.article = self._read_article(args.input)

    def _read_article(self, fname: str) -> str:
        if not path.isfile(fname):
            raise ValueError(f'文章文件不存在：{fname}')
        return open(fname, encoding='utf8').read()

    def run(self):
        if not self.article.strip():
            raise ValueError('文章内容为空')

        logger.info('[1] 生成即刻文案')
        data: JikeCopy = self.agent.gen_jike(self.article)

        out_dir = self.args.output or (path.dirname(self.args.input) or '.')
        os.makedirs(out_dir, exist_ok=True)
        base = self.args.base or path.splitext(path.basename(self.args.input))[0]

        body = data.body
        circles = ' '.join(f'#{c.lstrip("#")}' for c in (data.circles or []))
        text = body + (f'\n\n{circles}' if circles else '')
        p = path.join(out_dir, f'{base}-即刻文案.txt')
        write_text(p, text)

        logger.info(f'✓ {p}')
        return {'copy': p}


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def jike_art_handle(args):
    """入口函数：创建编排器并运行。"""
    JikeArtOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'jike-art',
        help='文章 → 即刻文案 (LLM)',
    )
    parser.add_argument('input', help='文章 Markdown 文件路径')
    parser.add_argument('-o', '--output', help='输出目录')
    parser.add_argument('--base', default='', help='输出文件名基底')
    parser.set_defaults(func=jike_art_handle)
    return parser

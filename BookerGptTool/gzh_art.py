# -*- coding: utf-8 -*-
"""
gzh_art.py —— 公众号出稿：素材（JSON）→ 文章 Markdown（LLM）

流程：
1. 读取素材 JSON；
2. 用 LLM 根据素材与 01fish 写作风格指南生成文章 Markdown；
3. 写出 .md（第一行为 # 标题）。

架构参照 article_img 子命令（Agent + Orchestrator + reg_subparser）。
"""

import json
import logging
from os import path

from .util import write_text
from .gzh_art_agent import GzhArtAgent

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class GzhArtOrchestrator:
    """协调素材读取、文章生成与写出。"""

    def __init__(self, args):
        self.args = args
        self.agent = GzhArtAgent(args)
        self.materials = self._read_materials(args.input)

    def _read_materials(self, fname: str) -> dict:
        if not path.isfile(fname):
            raise ValueError(f'素材文件不存在：{fname}')
        return json.loads(open(fname, encoding='utf8').read())

    def step_article(self) -> str:
        """生成公众号文章 Markdown。"""
        logger.info('[1] 生成公众号文章')
        return self.agent.generate_article(
            self.materials, owner=getattr(self.args, 'owner', '') or '',
        )

    def run(self):
        md = self.step_article()
        out = self.args.output or (path.splitext(self.args.input)[0] + '.md') or 'article.md'
        write_text(out, md)
        title = md.split('\n', 1)[0].lstrip('#').strip() if md.strip() else '未命名'
        logger.info(f'✓ {out}\n  标题：{title}')
        return out


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def gzh_art_handle(args):
    """入口函数：创建编排器并运行。"""
    GzhArtOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'gzh-art',
        help='出稿：素材 JSON → 公众号文章 Markdown (LLM)',
    )
    parser.add_argument('input', help='素材 JSON 路径（含 topic + materials）')
    parser.add_argument('-o', '--output', help='输出 md 路径（默认 [input].md）')
    parser.add_argument('--owner', default='', help='人类作者名（用于诚实标注）')
    parser.set_defaults(func=gzh_art_handle)
    return parser
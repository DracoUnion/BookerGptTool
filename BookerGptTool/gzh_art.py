# -*- coding: utf-8 -*-
"""
gzh_art.py —— 公众号出稿：素材目录 → 文章 Markdown（LLM）

流程：
1. 读取素材 JSON；
2. 用 LLM 根据素材与 01fish 写作风格指南生成文章 Markdown；
3. 写出 .md（第一行为 # 标题）。

架构参照 article_img 子命令（Agent + Orchestrator + reg_subparser）。
"""

import glob
import logging
from datetime import datetime
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
    """协调素材目录读取、文章生成与写出。"""

    def __init__(self, args):
        self.args = args
        self.agent = GzhArtAgent(args)
        self.materials = self._read_materials(args.input)

    def _read_materials(self, dir_name: str) -> dict:
        """扫描目录中的所有 Markdown 文件，组装为素材清单。

        每个 .md 文件视为一条素材：
          - content  = 文件正文
          - type     = 文件名（不含扩展名）
          - context  = 文件名（不含扩展名）
          - time     = 文件最后修改时间
        """
        if not path.isdir(dir_name):
            raise ValueError(f'素材目录不存在：{dir_name}')
        files = sorted(glob.glob(path.join(dir_name, '*.md')))
        if not files:
            raise ValueError(f'目录 {dir_name} 中没有找到 .md 素材文件')
        materials = []
        for f in files:
            base = path.splitext(path.basename(f))[0]
            materials.append({
                'time': datetime.fromtimestamp(path.getmtime(f)).strftime('%Y-%m-%d %H:%M'),
                'content': open(f, encoding='utf8').read(),
                'type': base,
                'context': base,
            })
        return {'topic': '', 'materials': materials}

    def step_article(self) -> str:
        """生成公众号文章 Markdown。"""
        logger.info('[1] 生成公众号文章')
        return self.agent.generate_article(
            self.materials, owner=getattr(self.args, 'owner', '') or '',
        )

    def run(self):
        md = self.step_article()
        if self.args.output:
            out = self.args.output
        else:
            base = path.basename(path.normpath(self.args.input)) or 'article'
            out = path.join(self.args.input, f'{base}.md')
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
    parser.add_argument('input', help='素材目录路径（读取其中所有 .md 文件作为素材）')
    parser.add_argument('-o', '--output', help='输出 md 路径（默认 [目录名].md）')
    parser.add_argument('--owner', default='', help='人类作者名（用于诚实标注）')
    parser.set_defaults(func=gzh_art_handle)
    return parser
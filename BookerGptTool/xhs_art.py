# -*- coding: utf-8 -*-
"""
xhs_art.py —— 文章 → 小红书轮播图 HTML + 发布文案（LLM）

流程：
1. 读取文章 Markdown；
2. 用 LLM 生成 8-10 张轮播卡片；
3. 用 01fish 色板渲染卡片 HTML（浏览器打开可整体下载/截图）；
4. 用 LLM 生成发布文案（标题 + 正文 + 标签）并写出 .txt。

架构参照 article_img 子命令（Agent + Orchestrator + reg_subparser）。
"""

import logging
import os
from os import path
from typing import List

from .util import write_text
from .xhs_art_agent import XhsArtAgent
from .xhs_art_models import XhsCard, XhsCopy

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

BRAND = {
    'green': '#1A3328', 'paper': '#F2EDE3', 'red': '#C44536',
    'moss': '#7A8C80', 'dark': '#0F1F18', 'cyan': '#D4DDD7',
}


# ════════════════════════════════════════════════════════════════
# HTML 渲染
# ════════════════════════════════════════════════════════════════

def render_cards_html(cards: List[XhsCard]) -> str:
    """用 01fish 色板渲染多张轮播卡片 HTML（多页 .card，可整体下载）。"""
    pages = []
    for i, c in enumerate(cards, 1):
        title = c.title
        body = c.body
        sub = c.sub
        kind = c.type
        is_dark = kind in ('cover', 'method')
        bg = BRAND['dark'] if is_dark else BRAND['paper']
        fg = BRAND['paper'] if is_dark else BRAND['green']
        accent = BRAND['red']
        pages.append(f'''<div class="card" style="background:{bg};color:{fg};">
  <div class="brand" style="color:{fg};opacity:.5;">01fish</div>
  <div class="page-title" style="color:{accent};"><span style="color:{fg};">{title}</span></div>
  <div class="body">{body}</div>
  {f'<div class="sub">{sub}</div>' if sub else ''}
  <div class="page-num" style="color:{fg};opacity:.4;">{i}/{len(cards)}</div>
</div>''')
    card_css = """
.card{width:1080px;height:1440px;position:relative;padding:80px 72px;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;border-radius:24px;margin:20px auto;overflow:hidden}
.brand{position:absolute;top:44px;left:72px;font-size:26px;font-weight:700;letter-spacing:2px}
.page-title{font-size:64px;font-weight:800;line-height:1.25;margin-bottom:28px}
.page-title span{display:block}
.body{font-size:34px;line-height:1.7;white-space:pre-line}
.sub{margin-top:24px;font-size:28px;opacity:.7}
.page-num{position:absolute;right:72px;bottom:44px;font-size:24px}
"""
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>小红书轮播图</title>
<style>{card_css}</style></head><body>{''.join(pages)}</body></html>'''


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class XhsArtOrchestrator:
    """协调卡片生成、HTML 渲染与发布文案生成。"""

    def __init__(self, args):
        self.args = args
        self.agent = XhsArtAgent(args)
        self.article = self._read_article(args.input)

    def _read_article(self, fname: str) -> str:
        if not path.isfile(fname):
            raise ValueError(f'文章文件不存在：{fname}')
        return open(fname, encoding='utf8').read()

    def step_cards(self) -> List[XhsCard]:
        logger.info('[1] 生成轮播卡片')
        return self.agent.gen_cards(self.article)

    def step_copy(self) -> XhsCopy:
        logger.info('[3] 生成发布文案')
        return self.agent.gen_copy(self.article)

    def run(self):
        if not self.article.strip():
            raise ValueError('文章内容为空')
        cards = self.step_cards()
        copy = self.step_copy()

        out_dir = self.args.output or (path.dirname(self.args.input) or '.')
        os.makedirs(out_dir, exist_ok=True)
        base = self.args.base or path.splitext(path.basename(self.args.input))[0]

        logger.info(f'[2] 渲染 {len(cards)} 张卡片 HTML')
        html_path = path.join(out_dir, f'{base}-小红书版.html')
        write_text(html_path, render_cards_html(cards))

        tags = copy.tags or []
        copy_text = (f"标题：{copy.title}\n\n{copy.body}\n\n"
                     + ' '.join(f'#{t.lstrip("#")}' for t in tags))
        copy_path = path.join(out_dir, f'{base}-小红书文案.txt')
        write_text(copy_path, copy_text)

        logger.info(f'✓ {html_path}')
        logger.info(f'✓ {copy_path}')
        return {'html': html_path, 'copy': copy_path}


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def xhs_art_handle(args):
    """入口函数：创建编排器并运行。"""
    XhsArtOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'xhs-art',
        help='文章 → 小红书轮播图 HTML + 发布文案 (LLM)',
    )
    parser.add_argument('input', help='文章 Markdown 文件路径')
    parser.add_argument('-o', '--output', help='输出目录（默认文章同目录）')
    parser.add_argument('--base', default='', help='输出文件名基底（默认取文章文件名）')
    parser.set_defaults(func=xhs_art_handle)
    return parser
# -*- coding: utf-8 -*-
"""
article_img.py —— 文章配图生成器（01fish 风格，HTML 可下载/PNG 导出）

流程：
1. 读取文章 Markdown；
2. 用 LLM 规划配图（配图类型、视觉概念、底色）；
3. 用 LLM 生成每张配图的视觉内容 HTML；
4. 组装为可下载的 HTML（工具栏 + 每张 slide 可单独下载 / 打包 ZIP）；
5. 可选：用 Playwright 截图为 PNG。

架构参照 code2book 子命令（Agent + Orchestrator + reg_subparser）。
"""

import json
import logging
import os
import re
import sys
from os import path
from typing import List, Dict, Any

import json_repair

from .openai import logger as oai_logger
from .openai import ask_chatgpt_retry, set_openai_props
from .article_img_models import (
    ImagePlan, ImagePlanList, ImagePage,
    IMAGE_TYPE_TAGS, Colors,
)
from .article_img_pmt import PLAN_ARTICLE_PMT, BUILD_IMAGE_PMT
from .util import (
    ext_code_block, render_prompt, write_text,
)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# Agent —— 封装所有 LLM 调用
# ════════════════════════════════════════════════════════════════

class ArticleImgAgent:
    """封装配图规划与配图内容生成的 LLM 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def plan_article(self, article: str) -> ImagePlanList:
        """根据文章内容规划配图计划。"""
        ques = render_prompt(PLAN_ARTICLE_PMT, article=article)
        parse_output = lambda s: ImagePlanList(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

    def build_image(self, plan: ImagePlan, total: int) -> ImagePage:
        """根据单张配图计划生成视觉内容 HTML。"""
        ques = render_prompt(
            BUILD_IMAGE_PMT,
            index=str(plan.index), total=str(total),
            section_title=plan.section_title,
            section_summary=plan.section_summary,
            image_type=plan.image_type,
            visual_concept=plan.visual_concept,
            key_elements='、'.join(plan.key_elements),
            is_dark='是' if plan.is_dark else '否',
        )
        parse_output = lambda s: ImagePage(
            index=plan.index, total=total,
            section_title=plan.section_title,
            image_type=plan.image_type,
            is_dark=plan.is_dark,
            **json_repair.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)


# ════════════════════════════════════════════════════════════════
# HTML 渲染
# ════════════════════════════════════════════════════════════════

# 品牌角标 SVG（暗底版；浅底版通过 CSS 替换颜色）
_BRAND_SVG_DARK = '''<svg viewBox="0 0 32 32" fill="none">
  <circle cx="13" cy="16" r="10" stroke="rgba(255,255,255,0.5)" stroke-width="2.2" fill="none"/>
  <ellipse cx="13" cy="8.5" rx="7" ry="1.5" stroke="rgba(255,255,255,0.3)" stroke-width="1.2" fill="none"/>
  <ellipse cx="14" cy="17" rx="3.5" ry="2" fill="#C44536"/>
  <polygon points="10,17 7.5,14.5 7.5,19.5" fill="#C44536"/>
  <circle cx="16" cy="16.3" r="0.7" fill="#F2EDE3"/>
  <line x1="26" y1="4" x2="26" y2="16" stroke="rgba(255,255,255,0.5)" stroke-width="2" stroke-linecap="round"/>
  <path d="M26 16 Q26 22 22 22" stroke="rgba(255,255,255,0.5)" stroke-width="2" fill="none" stroke-linecap="round"/>
  <circle cx="26" cy="4" r="2" stroke="rgba(255,255,255,0.5)" stroke-width="1.5" fill="none"/>
</svg>'''

_BRAND_SVG_LIGHT = _BRAND_SVG_DARK.replace(
    'rgba(255,255,255,0.5)', 'rgba(26,51,40,0.5)'
).replace(
    'rgba(255,255,255,0.3)', 'rgba(26,51,40,0.3)'
).replace(
    '#F2EDE3', '#1A3328'
)


def _slug(text: str, limit: int = 24) -> str:
    text = re.sub(r'[^\w一-鿿\-]', '', text)
    text = re.sub(r'[\s_]+', '-', text).strip('-')
    return text[:limit] or 'img'


def render_slide(page: ImagePage) -> str:
    """渲染单张配图 slide 的 HTML（含 label / slide / 内容）。"""
    brand = _BRAND_SVG_LIGHT if page.is_dark else _BRAND_SVG_DARK
    # 品牌色区分：暗底 → 白色文字；浅底 → 墨绿文字
    fg_class = 'on-dark' if page.is_dark else 'on-light'
    page_cls = 'dark' if page.is_dark else 'light'
    label = f'配图 {page.index} · 放在「{page.section_title}」之后'
    return f'''
  <div class="slide-label">{label}</div>
  <div class="slide {page_cls}" id="slide-{page.index}">
    <div class="bg {"bg-dark" if page.is_dark else "bg-light"}"></div>
    <div class="brand {page_cls}">{brand}</div>
    <div class="page-num {page_cls}">{page.index}/{page.total}</div>
    <div class="content">
      <div class="section-tag {fg_class}">{page.section_tag}</div>
      <div class="title {fg_class}">{page.title}</div>
      <div class="body {fg_class}">{page.content_html}</div>
    </div>
  </div>'''


def render_html(pages: List[ImagePage], article_title: str) -> str:
    """组装完整配图 HTML（工具栏 + 所有 slides + 下载脚本）。"""
    slides = '\n'.join(render_slide(p) for p in pages)
    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{article_title} - 配图</title>
<style>
  body {{ background:#111; font-family: -apple-system,"PingFang SC","Microsoft YaHei",sans-serif; padding: 60px 20px 80px; display:flex; flex-direction:column; align-items:center; }}
  .toolbar {{ position:fixed; top:0; left:0; right:0; z-index:100; background:rgba(17,17,17,0.9); display:flex; gap:10px; padding:10px 16px; align-items:center; flex-wrap:wrap; }}
  .toolbar button {{ background:#1A3328; color:#F2EDE3; border:1px solid rgba(196,69,54,0.5); padding:6px 14px; border-radius:6px; cursor:pointer; font-size:13px; }}
  .toolbar button:hover {{ background:#C44536; }}
  .toolbar input {{ background:#222; color:#F2EDE3; border:1px solid #333; padding:5px 8px; border-radius:5px; font-size:13px; }}
  .slide-label {{ color:rgba(255,255,255,0.35); font-size:13px; margin:34px 0 10px; width:800px; text-align:center; }}
  .slide {{ width:800px; height:460px; position:relative; overflow:hidden; border-radius:8px; box-shadow:0 8px 30px rgba(0,0,0,0.5); }}
  .slide.dark .bg-dark {{ position:absolute; inset:0; background:#1A3328; }}
  .slide.light .bg-light {{ position:absolute; inset:0; background:#F2EDE3; }}
  .slide .content {{ position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; z-index:2; padding:34px 60px; }}
  .slide .brand {{ position:absolute; top:16px; left:20px; z-index:3; display:flex; align-items:center; gap:8px; }}
  .slide .brand svg {{ width:20px; height:20px; }}
  .slide .brand::after {{ content:"01fish"; font-size:11px; font-weight:600; letter-spacing:1px; }}
  .slide .brand.dark::after {{ color:rgba(255,255,255,0.5); }}
  .slide .brand.light::after {{ color:rgba(26,51,40,0.5); }}
  .slide .page-num {{ position:absolute; bottom:14px; right:20px; z-index:3; font-size:12px; }}
  .slide .page-num.dark {{ color:rgba(255,255,255,0.35); }}
  .slide .page-num.light {{ color:rgba(26,51,40,0.35); }}
  .slide .section-tag {{ font-size:11px; font-weight:700; letter-spacing:2px; padding:4px 12px; border-radius:4px; margin-bottom:12px; }}
  .slide .section-tag.on-dark {{ background:rgba(255,255,255,0.08); color:rgba(255,255,255,0.5); }}
  .slide .section-tag.on-light {{ background:rgba(26,51,40,0.08); color:rgba(26,51,40,0.5); }}
  .slide .title {{ font-size:28px; font-weight:800; text-align:center; margin-bottom:18px; }}
  .slide .title.on-dark {{ color:#F2EDE3; }}
  .slide .title.on-light {{ color:#1A3328; }}
  .slide .body {{ width:100%; font-size:14px; }}
  .slide .body.on-dark {{ color:rgba(255,255,255,0.8); }}
  .slide .body.on-light {{ color:rgba(26,51,40,0.8); }}
  /* 通用视觉组件 */
  .body table {{ width:100%; border-collapse:collapse; }}
  .body th,.body td {{ border:1px solid currentColor; padding:6px 10px; font-size:13px; opacity:0.85; }}
  .body .flow {{ display:flex; align-items:center; justify-content:center; gap:0; flex-wrap:wrap; }}
  .flow-card {{ width:118px; height:78px; border-radius:10px; display:flex; flex-direction:column; align-items:center; justify-content:center; border:1.5px solid rgba(196,69,54,0.3); background:rgba(196,69,54,0.15); text-align:center; }}
  .flow-card .t {{ font-size:13px; font-weight:800; }}
  .flow-card .s {{ font-size:10px; opacity:0.55; margin-top:4px; }}
  .flow-arrow {{ color:rgba(196,69,54,0.6); font-size:20px; padding:0 6px; }}
  .grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }}
  .grid-card {{ border-radius:8px; padding:10px; text-align:center; font-size:13px; font-weight:700; }}
  .slide.dark .grid-card {{ background:rgba(255,255,255,0.06); }}
  .slide.light .grid-card {{ background:rgba(26,51,40,0.06); }}
  .big-num {{ font-size:64px; font-weight:900; color:#C44536; text-align:center; }}
  .big-label {{ font-size:20px; font-weight:700; text-align:center; margin-top:6px; }}
  .info-row {{ display:flex; align-items:center; gap:10px; margin-bottom:12px; font-size:13px; }}
  .info-row .dot {{ width:6px; height:6px; border-radius:50%; background:#C44536; flex-shrink:0; }}
  .quote {{ font-size:20px; font-weight:700; text-align:center; padding:18px 24px; border-left:4px solid #C44536; line-height:1.6; }}
  .compare {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  .compare-col {{ border-radius:8px; padding:14px; }}
  .slide.dark .compare-col {{ background:rgba(255,255,255,0.06); }}
  .slide.light .compare-col {{ background:rgba(26,51,40,0.06); }}
  .compare-col .h {{ font-weight:800; font-size:14px; margin-bottom:8px; color:#C44536; }}
  .compare-col li {{ font-size:12px; margin-bottom:4px; }}
  .rating-row {{ display:flex; align-items:center; gap:12px; margin-bottom:10px; font-size:13px; }}
  .stars {{ color:#C44536; letter-spacing:2px; }}
  .timeline {{ display:flex; align-items:center; justify-content:center; flex-wrap:wrap; }}
  .tl-node {{ display:flex; flex-direction:column; align-items:center; gap:4px; }}
  .tl-dot {{ width:10px; height:10px; border-radius:50%; background:#C44536; }}
  .tl-label {{ font-size:12px; font-weight:700; }}
  .tl-desc {{ font-size:10px; opacity:0.6; }}
  .tl-line {{ width:30px; height:2px; background:rgba(196,69,54,0.5); }}
</style>
</head>
<body>
  <div class="toolbar">
    <button onclick="downloadAll()">全部下载 (ZIP)</button>
    <button onclick="downloadOne()">下载当前</button>
    <input id="range" type="range" min="1" max="{len(pages)}" value="1" oninput="goSlide(this.value)">
    <span style="color:rgba(255,255,255,0.5);font-size:13px;" id="cur">1/{len(pages)}</span>
  </div>

  {slides}

  <script src="https://cdn.jsdelivr.net/npm/html2canvas@1.4.1/dist/html2canvas.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/file-saver@2.0.5/dist/FileSaver.min.js"></script>
  <script>
    function downloadOne() {{
      const el = document.querySelector('#slide-' + document.getElementById('range').value);
      html2canvas(el, {{ scale: 2 }}).then(c => c.toBlob(b => saveAs(b, '配图-{len(pages)}-current.png')));
    }}
    function downloadAll() {{
      const zip = new JSZip();
      const slides = document.querySelectorAll('.slide');
      const promise = Array.from(slides).map((el, i) => {{
        return html2canvas(el, {{ scale: 2 }}).then(c => new Promise(res => c.toBlob(blob => {{
          const t = el.querySelector('.title').textContent;
          zip.file(`配图-${{String(i+1).padStart(2,'0')}}-${{t}}.png`, blob); res();
        }})));
      }});
      Promise.all(promise).then(() => zip.generateAsync({{type:'blob'}}).then(b => saveAs(b, '配图合集.zip')));
    }}
    function goSlide(n) {{
      const el = document.getElementById('slide-' + n);
      document.getElementById('cur').textContent = n + '/' + {len(pages)};
      if (el) el.scrollIntoView({{behavior:'smooth', block:'center'}});
    }}
  </script>
</body>
</html>'''


# ════════════════════════════════════════════════════════════════
# 编排器
# ════════════════════════════════════════════════════════════════

class ArticleImgOrchestrator:
    """协调配图规划、内容生成、HTML 组装与 PNG 导出。"""

    def __init__(self, args):
        self.args = args
        self.agent = ArticleImgAgent(args)
        self.article = self._read_article(args.input)
        self.title = self._guess_title(self.article)

    def _read_article(self, fname: str) -> str:
        if not path.isfile(fname):
            raise ValueError(f'文章文件不存在：{fname}')
        return open(fname, encoding='utf8').read()

    @staticmethod
    def _guess_title(article: str) -> str:
        m = re.search(r'^#\s+(.+)$', article, flags=re.M)
        if m:
            return m.group(1).strip()
        return '文章配图'

    def run(self):
        logger.info(self.args)
        if not self.article.strip():
            raise ValueError('文章内容为空')

        # 1. 规划配图
        logger.info('[1] 规划配图')
        plan: ImagePlanList = self.agent.plan_article(self.article)
        total = plan.total_images
        plans = plan.plans
        # 自动修正底色交替
        prev_dark = plans[0].is_dark if plans else True
        for i, p in enumerate(plans[1:], 1):
            p.is_dark = not prev_dark
            prev_dark = p.is_dark
        # 补全 section_tag
        for p in plans:
            if not getattr(p, 'section_tag', None):
                p.section_tag = IMAGE_TYPE_TAGS.get(p.image_type, 'INFO')

        # 2. 生成每张配图内容
        logger.info(f'[2] 生成配图内容（{total} 张）')
        pages: List[ImagePage] = []
        for i, p in enumerate(plans, 1):
            logger.info(f'[2] 配图 {p.index}/{total} · {p.image_type}')
            page = self.agent.build_image(p, total)
            if not page.section_tag:
                page.section_tag = IMAGE_TYPE_TAGS.get(page.image_type, 'INFO')
            pages.append(page)

        # 3. 组装 HTML
        out_fname = self._output_fname()
        html = render_html(pages, self.title)
        write_text(out_fname, html)
        logger.info(f'[3] HTML 已生成：{out_fname}')

        # 4. 导出 PNG
        if self.args.png:
            self._export_png(out_fname, pages)

        return out_fname, pages

    def _output_fname(self) -> str:
        if self.args.output:
            return self.args.output
        base = path.splitext(self.args.input)[0]
        return f'{base}-配图.html'

    def _export_png(self, html_fname: str, pages: List[ImagePage]):
        """用 Playwright 打开 HTML，逐张截取 .slide 为 2x PNG。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warn('[4] 未安装 playwright，跳过 PNG 导出（仅生成 HTML）')
            return
        png_dir = path.splitext(html_fname)[0] + '-png'
        os.makedirs(png_dir, exist_ok=True)
        from urllib.request import pathname2url
        url = 'file:///' + pathname2url(path.abspath(html_fname))
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(device_scale_factor=2, viewport={'width': 900, 'height': 1200})
            page.goto(url)
            page.wait_for_load_state('networkidle')
            for i, pg in enumerate(pages, 1):
                sel = f'#slide-{pg.index}'
                el = page.query_selector(sel)
                if not el:
                    logger.warn(f'[4] 未找到 {sel}，跳过')
                    continue
                out = path.join(png_dir, f'配图-{pg.index:02d}-{_slug(pg.title)}.png')
                el.screenshot(path=out)
                logger.info(f'[4] {out}')
            browser.close()
        logger.info(f'[4] PNG 已导出到 {png_dir}')


# ════════════════════════════════════════════════════════════════
# 入口 & 子命令注册
# ════════════════════════════════════════════════════════════════

def article_img_handle(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    ArticleImgOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'art-img',
        help='文章配图生成器（01fish 风格，HTML 可下载 / PNG 导出）',
    )
    parser.add_argument('input', help='文章 Markdown 文件路径')
    parser.add_argument('-o', '--output', help='输出 HTML 路径（默认：输入文件名-配图.html）')
    parser.add_argument('--png', action='store_true', help='用 Playwright 导出 PNG')
    parser.add_argument('-D', '--debug', action='store_true', help='调试模式')
    parser.set_defaults(func=article_img_handle)
    return parser

# -*- coding: utf-8 -*-
"""
gzh_art_agent.py —— 公众号出稿的 LLM 智能体
"""

import json

from .openai import call_llm_retry, set_openai_props
from .gzh_art_pmt import GEN_ARTICLE_PMT
from .util import render_prompt, ext_cont_block, read_text


class GzhArtAgent:
    """封装公众号出稿（素材 → 文章 Markdown）的 LLM 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def generate_article(self, materials, *, owner='', model=None) -> str:
        """根据素材生成公众号文章 Markdown（第一行为 # 标题）。"""
        topic = materials.get('topic', '')
        items = materials.get('materials', [])
        content_lines = []
        for m in items:
            content_lines.append(
                f"- [{m.get('time', '')}] ({m.get('type', '素材')}) {m.get('content', '')}"
                + (f" — 备注：{m['context']}" if m.get('context') else '')
            )
        material_text = '\n'.join(content_lines) or '（暂无素材）'
        ctype = self._detect_type(items)
        style = self._style_guide() or '无明显风格指南可读，请按中文优质公众号文章写作。'

        ques = render_prompt(
            GEN_ARTICLE_PMT,
            ctype=ctype, style=style,
            topic=topic or '（待你从素材提炼）',
            material=material_text,
        )
        parse_output = lambda s: ext_cont_block(s)
        return call_llm_retry(
            ques, model or self.model,
            retry=self.args.retry, temp=self.args.temp,
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            presence_penalty=self.args.presence_penalty,
            max_tokens=self.args.max_tokens,
            extra_body=self.args.extra_body,
            parse_output=parse_output,
        )

    # ── 内部辅助 ──────────────────────────────────────────────

    @staticmethod
    def _detect_type(materials) -> str:
        """根据素材内容简单判断内容类型（说明书/教程/深度长文）。"""
        text = json.dumps(materials, ensure_ascii=False)
        if any(k in text for k in ('介绍', '无限推荐', '开源', '工具', '产品', '数据集', '｜指南')):
            return '说明书类'
        if any(k in text for k in ('怎么', '安装', '配置', '使用', '教程', '步骤', 'skill', '实战')):
            return '教程类'
        return '深度长文'

    @staticmethod
    def _style_guide() -> str:
        """读取内置写作风格指南（01fish 写作风格）。"""
        from os import path
        from .util import read_text
        fname = path.join(path.dirname(path.abspath(__file__)),
                          'gzh_art_assets', 'references', 'writing-style.md')
        if path.isfile(fname):
            return read_text(fname)
        return ''
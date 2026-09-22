# -*- coding: utf-8 -*-
"""
ppt_agent.py —— 网页 PPT 子命令（ppt）的智能体。

参照 AGENTS.md 与 pdf_ocr_agent.py：本类封装整个子命令的全部能力——
大模型调用（render_prompt + ask_chatgpt_retry + parse_output 回调）、
素材/参考读取、模板组装、文件缓存与写盘。编排器（ppt.py）只负责按固定顺序
调用本类的方法，不直接碰 IO。
"""

import json
import os
import re
import shutil
from os import path
from typing import Type

from pydantic import BaseModel, parse_obj_as

from .util import (
    extname,
    gen_objs_md5,
    ext_code_block,
    ext_cont_block,
    render_prompt,
    json_dump_model,
    read_text,
    write_text,
    read_yaml_model,
    write_yaml_model,
    d,
)
from .openai import (
    ask_chatgpt_retry,
    set_openai_props,
)
from .ppt_models import *
from .ppt_pmt import *


class PptAgent:
    """封装所有 LLM 调用的智能体类。

    每个大模型的调用实现为一个方法，负责：
    1. 用 render_prompt 渲染提示词（参数由编排器传入）；
    2. 调用 ask_chatgpt_retry（内部 call_llm_retry，带重试）；
    3. 通过 parse_output 回调解析输出（ext_code_block / ext_cont_block / parse_obj_as）。
    """

    def __init__(self, args) -> None:
        self.args = args
        self.model = args.model
        set_openai_props(args)
        # 项目输出目录（素材/参考读取、缓存与写盘都在这里）
        self.assets_dir = d('ppt_assets')
        self.refs_dir = path.join(self.assets_dir, 'references')
        self.pj_dir = (
            path.dirname(args.fname) + '_ppt'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_ppt'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(path.join(self.pj_dir, 'images'), exist_ok=True)

    # ── 模板占位符正则 ──────────────────────────────────
    RE_TITLE = re.compile(r'<title>[^<]*</title>')
    RE_DECK_REGION = re.compile(
        r'<!--\s*SLIDES_HERE[\s\S]*?(?=\s*</div>\s*<div id="nav">)'
    )
    RE_NOTES = re.compile(
        r'const\s+SPEAKER_NOTES\s*=\s*\[[\s\S]*?\];\n(?=window\.__SPEAKER_NOTES__)'
    )

    # ── 内部辅助 ──────────────────────────────────────

    @staticmethod
    def _json(schema: Type[BaseModel], prompt: str, args) -> BaseModel:
        """调用 LLM 并把 ```json 代码块解析为 pydantic 对象。"""
        return ask_chatgpt_retry(
            prompt, args.model, args,
            parse_output=lambda s: parse_obj_as(
                schema, json.loads(ext_code_block(s))
            ),
        )

    @staticmethod
    def _text(prompt: str, args) -> str:
        """调用 LLM 并提取 [content]...[/content] 中的正文。"""
        return ask_chatgpt_retry(
            prompt, args.model, args,
            parse_output=ext_cont_block,
        )

    # ── 智能体方法（每个对应一次 LLM 调用）──────────────

    def gen_plan(
        self,
        material: str,
        themes_a: str,
        themes_b: str,
        audience: str = '',
        duration_minutes: str = '',
    ) -> DeckPlan:
        """根据素材与两套主题色预设生成整份 Deck 的规划（DeckPlan），自动选定风格 A/B。"""
        prompt = render_prompt(
            DECK_PLAN_PMT,
            MATERIAL=material,
            AUDIENCE=audience or '未提供，请根据素材推断受众',
            DURATION=duration_minutes or '未提供',
            THEMES_A=themes_a,
            THEMES_B=themes_b,
        )
        return self._json(DeckPlan, prompt, self.args)

    def write_deck(self, plan: DeckPlan, themes: str, layouts: str) -> str:
        """根据规划、主题色与版式参考生成全部幻灯片 HTML（仅 <section class="slide"> 块）。

        返回 HTML 字符串，由调用方负责填入模板并写盘。
        """
        prompt = render_prompt(
            DECK_WRITE_PMT,
            PLAN=json_dump_model(plan),
            THEMES=themes,
            LAYOUTS=layouts,
        )
        return self._text(prompt, self.args)

    # ── IO 工具：素材读取 ────────────────────────────────

    def list_input_files(self) -> list:
        """列出输入素材文件（命令行 fname 参数，支持文件或目录，过滤 md/txt/markdown）。"""
        if path.isfile(self.args.fname):
            fnames = [self.args.fname]
        elif path.isdir(self.args.fname):
            fnames = [
                path.join(root, fname)
                for root, _, files in os.walk(self.args.fname)
                for fname in sorted(files)
            ]
        else:
            fnames = []
        exts = {'md', 'txt', 'markdown'}
        return [
            f.replace('\\', '/')
            for f in fnames
            if extname(f).lower() in exts
        ]

    def read_input_file(self, fname: str) -> str:
        """读取某个输入素材文件（markdown/文本）的全文。"""
        return read_text(fname)

    def read_materials(self) -> str:
        """读取全部输入素材并拼接为全文（无素材时返回空串）。"""
        fnames = self.list_input_files()
        if not fnames:
            return ''
        return '\n\n'.join(read_text(f) for f in fnames)

    # ── IO 工具：内置参考与模板 ──────────────────────────

    def list_references(self) -> list:
        """列出内置设计参考文档（主题色/版式/组件/检查清单/演讲者备注等）的文件名。"""
        return REFERENCE_FILES

    def read_reference(self, fname: str) -> str:
        """读取某份内置设计参考文档的全文（fname 必须是 list_references 列出的文件名）。"""
        if fname not in REFERENCE_FILES:
            raise ValueError(
                f'未知参考文档：{fname}。可选：{", ".join(REFERENCE_FILES)}'
            )
        return read_text(path.join(self.refs_dir, fname))

    def read_all_references(self) -> dict:
        """读取全部内置设计参考文档，返回 {文件名: 全文}。"""
        return {fname: self.read_reference(fname) for fname in REFERENCE_FILES}

    def read_template(self, style: str) -> str:
        """读取模板 HTML 全文（style：A=电子杂志风，B=瑞士国际主义风）。"""
        style = (style or 'A').strip().upper()
        if style not in STYLE_ASSETS:
            raise ValueError('style 只能是 A 或 B')
        template_fname, _, _ = STYLE_ASSETS[style]
        tpl = read_text(path.join(self.assets_dir, template_fname))
        return (
            f'# 模板风格：{style}（{template_fname}）\n'
            f'# 幻灯片区域占位符：<!-- SLIDES_HERE -->\n'
            f'# 演讲备注数组：SPEAKER_NOTES\n'
            f'# 标题占位符：<title>[必填] 替换为 PPT 标题 · Deck Title</title>\n\n'
            f'{tpl}'
        )

    # ── 生成工具（内部 LLM + 文件缓存）───────────────────

    def gen_plan(
        self,
        material: str,
        themes_a: str,
        themes_b: str,
        audience: str = '',
        duration_minutes: str = '',
    ) -> DeckPlan:
        """根据素材与两套主题色预设生成整份 Deck 的规划（DeckPlan），自动选定风格 A/B。"""
        cache_fname = path.join(
            self.pj_dir,
            'plan_' + gen_objs_md5(material, audience, duration_minutes) + '.yaml'
        )
        r = read_yaml_model(cache_fname, DeckPlan)
        if r: return r
        prompt = render_prompt(
            DECK_PLAN_PMT,
            MATERIAL=material,
            AUDIENCE=audience or '未提供，请根据素材推断受众',
            DURATION=duration_minutes or '未提供',
            THEMES_A=themes_a,
            THEMES_B=themes_b,
        )
        r: DeckPlan = self._json(DeckPlan, prompt, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def write_deck(self, plan: DeckPlan, themes: str, layouts: str) -> str:
        """根据规划、主题色与版式参考生成全部幻灯片 HTML（仅 <section class="slide"> 块）。

        返回 HTML 字符串，由调用方负责填入模板并写盘。
        """
        prompt = render_prompt(
            DECK_WRITE_PMT,
            PLAN=json_dump_model(plan),
            THEMES=themes,
            LAYOUTS=layouts,
        )
        return self._text(prompt, self.args)

    def assemble(
        self, template_fname: str, slides_html: str, plan: DeckPlan
    ) -> str:
        """将幻灯片 HTML、演讲备注与标题填入模板，返回完整 index.html 源码。"""
        template = read_text(path.join(self.assets_dir, template_fname))
        title = plan.title.strip() or 'Deck'
        template = self.RE_TITLE.sub(f'<title>{title}</title>', template, count=1)
        if not self.RE_DECK_REGION.search(template):
            raise ValueError('模板中未找到 SLIDES_HERE 占位符，模板可能已损坏')
        template = self.RE_DECK_REGION.sub(
            slides_html.strip() + '\n', template, count=1
        )
        notes = []
        for s in plan.slides:
            notes.append({
                'id': s.slide_id,
                'title': s.title,
                'section': s.section or '',
                'minutes': s.minutes or '-',
                'purpose': s.purpose or '',
                'talk': list(s.talk),
                'transition': s.transition or '',
            })
        notes_json = json.dumps(notes, ensure_ascii=False, indent=2)
        template = self.RE_NOTES.sub(
            f'const SPEAKER_NOTES = {notes_json};\n', template, count=1
        )
        return template

    def write_index(
        self, plan: DeckPlan, themes: str, layouts: str
    ) -> str:
        """生成幻灯片 HTML（带缓存），填入模板并写入 index.html，复制动效脚本。

        返回输出路径。缓存键为 plan 的 md5，命中时跳过 LLM 调用。
        """
        if plan.style not in STYLE_ASSETS:
            raise ValueError(
                f'DeckPlan.style 必须是 A 或 B，当前：{plan.style}'
            )
        template_fname, _, _ = STYLE_ASSETS[plan.style]
        cache_fname = path.join(
            self.pj_dir,
            'deck_' + gen_objs_md5(plan) + '.yaml'
        )
        cached = read_yaml_model(cache_fname, None)
        if cached and cached.get('slides'):
            slides_html = cached['slides']
        else:
            slides_html = self.write_deck(plan, themes, layouts)
            write_yaml_model(cache_fname, {'slides': slides_html})

        out_fname = path.join(self.pj_dir, 'index.html')
        html = self.assemble(template_fname, slides_html, plan)
        write_text(out_fname, html)

        motion_src = path.join(self.assets_dir, 'motion.min.js')
        if path.isfile(motion_src):
            motion_dst = path.join(self.pj_dir, 'assets', 'motion.min.js')
            os.makedirs(path.dirname(motion_dst), exist_ok=True)
            shutil.copyfile(motion_src, motion_dst)
        return out_fname
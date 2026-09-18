# -*- coding: utf-8 -*-
"""
ppt_tools.py —— 网页 PPT 子命令（ppt）的工具集。

工具以「开放工具调用循环」的方式被编排器调用：run() 调用 call_llm_with_toolcall_retry，
LLM 在循环中自主决定何时调用下述 tool_* 方法。读取类工具为纯 IO，生成类工具（tool_gen_plan /
tool_write_deck）内部调用大模型并对结果做文件缓存。
"""

import json
import os
import re
import shutil
from os import path
from typing import List, Optional, Dict, Any, Callable

from .openai import *
from .util import *
from .ppt_models import *
from .ppt_pmt import *


# 内置参考文档白名单
REFERENCE_FILES = [
    'checklist.md',
    'components.md',
    'image-prompts.md',
    'layouts.md',
    'layouts-swiss.md',
    'presenter-mode.md',
    'screenshot-framing.md',
    'swiss-layout-lock.md',
    'swiss-map-component.md',
    'themes.md',
    'themes-swiss.md',
]

# 风格 -> (模板文件, 布局参考文件, 主题参考文件)
STYLE_ASSETS = {
    'A': ('template.html', ['layouts.md'], 'themes.md'),
    'B': ('template-swiss.html', ['swiss-layout-lock.md', 'layouts-swiss.md'], 'themes-swiss.md'),
}

# 模板中的可替换占位符
RE_TITLE = re.compile(r'<title>[^<]*</title>')
# 幻灯片区域：从 SLIDES_HERE 注释到 #deck 容器闭合（</div><div id="nav">）之间的全部内容。
# 风格 A 该区域为空，风格 B 含示例页（cover/closing），统一整段替换为生成的幻灯片。
RE_DECK_REGION = re.compile(
    r'<!--\s*SLIDES_HERE[\s\S]*?(?=\s*</div>\s*<div id="nav">)'
)
RE_NOTES = re.compile(r'const\s+SPEAKER_NOTES\s*=\s*\[[\s\S]*?\];\n(?=window\.__SPEAKER_NOTES__)')


class PptTools(ToolsMixin):
    """网页 PPT 工具集：读取素材/参考、生成规划与 Deck。"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、创建项目输出目录与图片目录。"""
        super(ToolsMixin, self).__init__()
        self.args = args
        self.model = args.model
        set_openai_props(args)
        self.assets_dir = d('ppt_assets')
        self.refs_dir = path.join(self.assets_dir, 'references')
        self.pj_dir = (
            path.dirname(args.fname) + '_ppt'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_ppt'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(path.join(self.pj_dir, 'images'), exist_ok=True)

    # ── 内部 LLM 调用辅助 ──────────────────────────────────────
    @staticmethod
    def _json(schema: Type[BaseModel], prompt: str, model: str, args) -> BaseModel:
        """调用 LLM 并把 ```json 代码块解析为 pydantic 对象。"""
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=lambda s: parse_obj_as(schema, json.loads(ext_code_block(s))),
        )

    @staticmethod
    def _text(prompt: str, model: str, args) -> str:
        """调用 LLM 并提取 [content]...[/content] 中的正文。"""
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=ext_cont_block,
        )

    # ── 工具：读取素材 ──────────────────────────────────────────
    def tool_list_input_files(self) -> List[str]:
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

    def tool_read_input_file(self, fname: str) -> str:
        """读取某个输入素材文件（markdown/文本）的全文。"""
        return read_text(fname)

    # ── 工具：读取内置参考与模板 ────────────────────────────────
    def tool_list_references(self) -> List[str]:
        """列出内置设计参考文档（主题色/版式/组件/检查清单/演讲者备注等）的文件名。"""
        return REFERENCE_FILES

    def tool_read_reference(self, fname: str) -> str:
        """读取某份内置设计参考文档的全文（fname 必须是 tool_list_references 列出的文件名）。"""
        if fname not in REFERENCE_FILES:
            raise ValueError(
                f'未知参考文档：{fname}。可选：{", ".join(REFERENCE_FILES)}'
            )
        return read_text(path.join(self.refs_dir, fname))

    def tool_read_template(self, style: str) -> str:
        """读取模板 HTML 全文（style：A=电子杂志风，B=瑞士国际主义风），返回模板源码。"""
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

    # ── 工具：生成规划（内部 LLM + 缓存）─────────────────────────
    def tool_gen_plan(
        self,
        material: str,
        audience: str = '',
        duration_minutes: str = '',
    ) -> DeckPlan:
        """根据素材生成整份 Deck 的规划（DeckPlan），自动选定风格 A/B 与主题色。"""
        cache_fname = path.join(
            self.pj_dir,
            'plan_' + gen_objs_md5(material, audience, duration_minutes) + '.yaml'
        )
        r = read_yaml_model(cache_fname, DeckPlan)
        if r: return r
        themes_a = read_text(path.join(self.refs_dir, 'themes.md'))
        themes_b = read_text(path.join(self.refs_dir, 'themes-swiss.md'))
        prompt = render_prompt(
            DECK_PLAN_PMT,
            MATERIAL=material,
            AUDIENCE=audience or '未提供，请根据素材推断受众',
            DURATION=duration_minutes or '未提供',
            THEMES_A=themes_a,
            THEMES_B=themes_b,
        )
        r: DeckPlan = self._json(DeckPlan, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    # ── 工具：生成 Deck 并写入（内部 LLM + 缓存）─────────────────
    def tool_write_deck(self, plan: DeckPlan) -> DeckResult:
        """根据规划生成幻灯片 HTML，填充进模板并写入 pj_dir/index.html（DeckResult）。"""
        if plan.style not in STYLE_ASSETS:
            raise ValueError(f'DeckPlan.style 必须是 A 或 B，当前：{plan.style}')
        template_fname, layout_fnames, theme_fname = STYLE_ASSETS[plan.style]

        cache_fname = path.join(
            self.pj_dir,
            'deck_' + gen_objs_md5(plan) + '.yaml'
        )
        cached = read_yaml_model(cache_fname, None)
        if cached and cached.get('slides'):
            slides_html = cached['slides']
        else:
            template = read_text(path.join(self.assets_dir, template_fname))
            themes = read_text(path.join(self.refs_dir, theme_fname))
            layouts = '\n\n'.join(
                read_text(path.join(self.refs_dir, lf)) for lf in layout_fnames
            )
            prompt = render_prompt(
                DECK_WRITE_PMT,
                PLAN=json_dump_model(plan),
                THEMES=themes,
                LAYOUTS=layouts,
            )
            slides_html = self._text(prompt, self.model, self.args)
            write_yaml_model(cache_fname, {'slides': slides_html})

        out_fname = path.join(self.pj_dir, 'index.html')
        html = self._assemble(template_fname, slides_html, plan)
        write_text(out_fname, html)

        # 复制动效脚本到输出目录，保证离线可用（模板引用 ./assets/motion.min.js）
        motion_src = path.join(self.assets_dir, 'motion.min.js')
        if path.isfile(motion_src):
            motion_dst = path.join(self.pj_dir, 'assets', 'motion.min.js')
            os.makedirs(path.dirname(motion_dst), exist_ok=True)
            shutil.copyfile(motion_src, motion_dst)

        return DeckResult(
            output_path=out_fname,
            style=plan.style,
            theme_name=plan.theme_name,
            slide_count=len(plan.slides),
            status='成功写入 index.html',
        )

    # ── 内部：把生成的幻灯片 HTML 组装进模板 ─────────────────────
    def _assemble(self, template_fname: str, slides_html: str, plan: DeckPlan) -> str:
        """将幻灯片 HTML、演讲备注与标题填入模板，返回完整可运行的 index.html 源码。"""
        template = read_text(path.join(self.assets_dir, template_fname))

        # 1. 标题
        title = plan.title.strip() or 'Deck'
        template = RE_TITLE.sub(f'<title>{title}</title>', template, count=1)

        # 2. 幻灯片区域（风格 B 会顺带清掉模板内置的示例页）
        if not RE_DECK_REGION.search(template):
            raise ValueError('模板中未找到 SLIDES_HERE 占位符，模板可能已损坏')
        template = RE_DECK_REGION.sub(slides_html.strip() + '\n', template, count=1)

        # 3. 演讲备注（由规划逐页组装，页面 ID 与 slide_id 一致）
        notes = []
        for s in plan.slides:
            item = {
                'id': s.slide_id,
                'title': s.title,
                'section': s.section or '',
                'minutes': s.minutes or '-',
                'purpose': s.purpose or '',
                'talk': list(s.talk),
                'transition': s.transition or '',
            }
            notes.append(item)
        notes_json = json.dumps(notes, ensure_ascii=False, indent=2)
        template = RE_NOTES.sub(
            f'const SPEAKER_NOTES = {notes_json};\n', template, count=1
        )
        return template

    # ── 工具参数 Schema ─────────────────────────────────────────
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # ── 素材读取 ──────────────────────────────────────────
        "tool_list_input_files": params_schema(),
        "tool_read_input_file": params_schema(
            required=['fname'],
            fname=base_schema('string', '要读取的输入素材文件路径'),
        ),
        # ── 内置参考与模板 ────────────────────────────────────
        "tool_list_references": params_schema(),
        "tool_read_reference": params_schema(
            required=['fname'],
            fname=base_schema('string', '参考文档文件名（tool_list_references 返回的）'),
        ),
        "tool_read_template": params_schema(
            required=['style'],
            style=base_schema('string', '模板风格：A（电子杂志风）或 B（瑞士国际主义风）'),
        ),
        # ── 规划与 Deck 生成 ──────────────────────────────────
        "tool_gen_plan": params_schema(
            required=['material'],
            material=base_schema('string', '素材全文（从输入文件读取）'),
            audience=base_schema('string', '受众与分享场景，缺省留空'),
            duration_minutes=base_schema('string', '分享时长（分钟），缺省留空'),
        ),
        "tool_write_deck": params_schema(
            required=['plan'],
            plan=model_schema(DeckPlan, 'Deck 规划（tool_gen_plan 返回的 DeckPlan）'),
        ),
    }

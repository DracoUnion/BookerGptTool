# -*- coding: utf-8 -*-
"""
ppt.py —— 网页 PPT 子命令（ppt）的编排器。

将 guizang-ppt-skill 的网页 PPT 生成流程封装为命令行子命令：读取素材 → 生成 Deck 规划 →
生成幻灯片 HTML 并写入 index.html。整个流程为**固定工作流**，由编排器按顺序调用各 step_* 方法，
每个大模型调用封装在 PptAgent（ppt_agent.py）中。
"""

import json
import logging
import os
import re
import shutil
from os import path

from .util import (
    extname,
    read_text,
    write_text,
    read_yaml_model,
    write_yaml_model,
    d,
)
from .ppt_agent import PptAgent
from .ppt_models import *
from .ppt_pmt import *

# 模板中的可替换占位符
RE_TITLE = re.compile(r'<title>[^<]*</title>')
# 幻灯片区域：从 SLIDES_HERE 注释到 #deck 容器闭合（</div><div id="nav">）之间的全部内容。
# 风格 A 该区域为空，风格 B 含示例页（cover/closing），统一整段替换为生成的幻灯片。
RE_DECK_REGION = re.compile(
    r'<!--\s*SLIDES_HERE[\s\S]*?(?=\s*</div>\s*<div id="nav">)'
)
RE_NOTES = re.compile(r'const\s+SPEAKER_NOTES\s*=\s*\[[\s\S]*?\];\n(?=window\.__SPEAKER_NOTES__)')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class PptOrchestrator:
    """编排器：按固定顺序驱动网页 PPT 的完整生成流程。

    流程为固定工作流（读素材 → 读参考 → 生成规划 → 生成 Deck → 写出），
    每个步骤实现为 step_* 方法，由 run() 顺序调用。
    所有大模型调用封装在 PptAgent（ppt_agent.py）中，本类只负责 IO 与文件缓存。
    """

    def __init__(self, args):
        """根据命令行参数初始化编排器、智能体与输出目录。"""
        self.args = args
        self.agent = PptAgent(args)
        self.assets_dir = d('ppt_assets')
        self.refs_dir = path.join(self.assets_dir, 'references')
        self.pj_dir = (
            path.dirname(args.fname) + '_ppt'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_ppt'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(path.join(self.pj_dir, 'images'), exist_ok=True)

    # ── 固定工作流步骤 ──────────────────────────────────

    def step_read_materials(self) -> str:
        """[1] 读取输入素材（文件或目录下所有 md/txt/markdown），拼接为全文。"""
        logger.info('[1] 读取素材')
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
        fnames = [f for f in fnames if extname(f).lower() in exts]
        if not fnames:
            return ''
        return '\n\n'.join(read_text(f) for f in fnames)

    def step_read_refs(self) -> dict:
        """[2] 读取内置设计参考文档（主题色 / 版式 / 检查清单等）。"""
        logger.info('[2] 读取设计参考')
        refs = {}
        for fname in REFERENCE_FILES:
            refs[fname] = read_text(path.join(self.refs_dir, fname))
        return refs

    def step_gen_plan(self, material: str, refs: dict) -> DeckPlan:
        """[3] 生成整份 Deck 规划（DeckPlan），缓存到 plan.yaml。"""
        logger.info('[3] 生成 Deck 规划')
        cache_fname = path.join(self.pj_dir, 'plan.yaml')
        r = read_yaml_model(cache_fname, DeckPlan)
        if r: return r
        r = self.agent.gen_plan(
            material=material,
            themes_a=refs['themes.md'],
            themes_b=refs['themes-swiss.md'],
            audience=getattr(self.args, 'audience', '') or '',
            duration_minutes=getattr(self.args, 'duration', '') or '',
        )
        write_yaml_model(cache_fname, r)
        return r

    def step_write_deck(self, plan: DeckPlan, refs: dict) -> DeckResult:
        """[4] 生成幻灯片 HTML，填入模板并写入 index.html。"""
        logger.info('[4] 生成 Deck 并写入')
        if plan.style not in STYLE_ASSETS:
            raise ValueError(
                f'DeckPlan.style 必须是 A 或 B，当前：{plan.style}'
            )
        template_fname, layout_fnames, theme_fname = STYLE_ASSETS[plan.style]

        cache_fname = path.join(self.pj_dir, 'deck.yaml')
        cached = read_yaml_model(cache_fname, None)
        if cached and cached.get('slides'):
            slides_html = cached['slides']
        else:
            slides_html = self.agent.write_deck(
                plan,
                refs[theme_fname],
                '\n\n'.join(refs[lf] for lf in layout_fnames),
            )
            write_yaml_model(cache_fname, {'slides': slides_html})

        out_fname = path.join(self.pj_dir, 'index.html')
        html = self._assemble(template_fname, slides_html, plan)
        write_text(out_fname, html)

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

    def _assemble(self, template_fname: str, slides_html: str, plan: DeckPlan) -> str:
        """将幻灯片 HTML、演讲备注与标题填入模板，返回完整 index.html 源码。"""
        template = read_text(path.join(self.assets_dir, template_fname))
        title = plan.title.strip() or 'Deck'
        template = RE_TITLE.sub(f'<title>{title}</title>', template, count=1)
        if not RE_DECK_REGION.search(template):
            raise ValueError('模板中未找到 SLIDES_HERE 占位符，模板可能已损坏')
        template = RE_DECK_REGION.sub(slides_html.strip() + '\n', template, count=1)
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
        template = RE_NOTES.sub(
            f'const SPEAKER_NOTES = {notes_json};\n', template, count=1
        )
        return template

    # ── 主流程 ─────────────────────────────────────────

    def run(self) -> None:
        """按固定顺序执行：读素材 → 读参考 → 生成规划 → 生成 Deck → 写出。"""
        logger.info(self.args)

        material = self.step_read_materials()
        refs = self.step_read_refs()
        plan = self.step_gen_plan(material, refs)
        result = self.step_write_deck(plan, refs)

        logger.info(f'[*] 已完成：{result.status}，输出 {result.output_path}')


def ppt(args):
    """入口函数：创建编排器并运行完整流程。"""
    return PptOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'ppt',
        help='根据素材生成单文件 HTML 的横向翻页网页 PPT（电子杂志风 / 瑞士国际主义风）',
    )
    parser.add_argument('fname', help='素材文件或目录（markdown/文本，作为 PPT 内容来源）')
    parser.add_argument('-a', '--audience', default='', help='受众与分享场景')
    parser.add_argument('-t', '--duration', default='', help='分享时长（分钟）')
    parser.set_defaults(func=ppt)

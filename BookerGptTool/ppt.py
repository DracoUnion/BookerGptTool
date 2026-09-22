# -*- coding: utf-8 -*-
"""
ppt.py —— 网页 PPT 子命令（ppt）的编排器。

将 guizang-ppt-skill 的网页 PPT 生成流程封装为命令行子命令：读取素材 → 生成 Deck 规划 →
生成幻灯片 HTML 并写入 index.html。整个流程为**固定工作流**，由编排器按顺序调用各 step_* 方法，
素材读取、模板组装、文件缓存与写盘都封装在 PptAgent（ppt_agent.py）中。
"""

import logging
from os import path

from .ppt_agent import PptAgent
from .ppt_models import *
from .ppt_pmt import *

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class PptOrchestrator:
    """编排器：按固定顺序驱动网页 PPT 的完整生成流程。

    流程为固定工作流（读素材 → 读参考 → 生成规划 → 生成 Deck → 写出），
    每个步骤实现为 step_* 方法，由 run() 顺序调用。
    素材/参考读取、模板组装、文件缓存与写盘都封装在 PptAgent（ppt_agent.py）中，
    本类只负责按顺序调度。
    """

    def __init__(self, args):
        """根据命令行参数初始化编排器与智能体。"""
        self.args = args
        self.agent = PptAgent(args)
        self.pj_dir = self.agent.pj_dir

    # ── 固定工作流步骤 ──────────────────────────────────

    def step_read_materials(self) -> str:
        """[1] 读取输入素材（文件或目录下所有 md/txt/markdown），拼接为全文。"""
        logger.info('[1] 读取素材')
        return self.agent.read_materials()

    def step_read_refs(self) -> dict:
        """[2] 读取内置设计参考文档（主题色 / 版式 / 检查清单等）。"""
        logger.info('[2] 读取设计参考')
        return self.agent.read_all_references()

    def step_gen_plan(self, material: str, refs: dict) -> DeckPlan:
        """[3] 生成整份 Deck 规划（DeckPlan），缓存到 plan.yaml。"""
        logger.info('[3] 生成 Deck 规划')
        return self.agent.gen_plan(
            material=material,
            themes_a=refs['themes.md'],
            themes_b=refs['themes-swiss.md'],
            audience=getattr(self.args, 'audience', '') or '',
            duration_minutes=getattr(self.args, 'duration', '') or '',
        )

    def step_write_deck(self, plan: DeckPlan, refs: dict) -> DeckResult:
        """[4] 生成幻灯片 HTML，填入模板并写入 index.html。"""
        logger.info('[4] 生成 Deck 并写入')
        if plan.style not in STYLE_ASSETS:
            raise ValueError(
                f'DeckPlan.style 必须是 A 或 B，当前：{plan.style}'
            )
        _, layout_fnames, theme_fname = STYLE_ASSETS[plan.style]
        themes = refs[theme_fname]
        layouts = '\n\n'.join(refs[lf] for lf in layout_fnames)

        out_fname = self.agent.write_index(plan, themes, layouts)

        return DeckResult(
            output_path=out_fname,
            style=plan.style,
            theme_name=plan.theme_name,
            slide_count=len(plan.slides),
            status='成功写入 index.html',
        )

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
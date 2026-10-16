import logging
import os
import re
from os import path
from typing import List, Optional

from .util import read_text, write_text
from .openai import logger as oai_logger
from .write_novel_models import *
from .write_novel_agent import WriteNovelAgent

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def _tail(text: str, max_len: int = 2000) -> str:
    """截取正文末尾 max_len 字，用于上一章承接。"""
    return text[-max_len:] if text else ''


class WriteNovelOrchestrator:
    """编排器：把中文网文写作 skill 的固定工作流硬编码为步骤序列，模型不决定流程。

    固定流程：
      1. 前置规划（concept_planning）
      2. 开头计划（opening）
      3. 分卷章纲（volume_outline）
      4. 黄金三章正文（opening content）
      5. 逐章正文：正文执行链（plot_logic / character_consistency / transition /
         dialogue / chapter_ending / anti_ai_voice 约束）写正文
      6. 逐章完稿收口（consistency_review）：每章必过六种一致性复查，不通过则返修
    """

    def __init__(self, args):
        self.args = args
        self.agent = WriteNovelAgent(args)
        self.pj_dir = self.agent.pj_dir
        self.asset_dir = self.agent.asset_dir
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(self.asset_dir, exist_ok=True)
        self.chapter_count = args.chapters

    # ── 步骤 1：前置规划 ──────────────────────────────────

    def step_concept_planning(self) -> ConceptPlanningResult:
        logger.info('[1] 前置规划：题材、卖点、hook、premise、故事引擎、第一卷方向')
        concept = self.agent.gen_concept_planning(self.args.blurb)
        logger.info(f'[1] 题材: {concept.genre}/{concept.sub_genre}')
        logger.info(f'[1] hook: {concept.hook}')
        logger.info(f'[1] 目标长度: {concept.target_length}（{concept.length_reasoning}）')
        return concept

    # ── 步骤 2：开头计划 ──────────────────────────────────

    def step_opening_plan(self, concept: ConceptPlanningResult) -> OpeningPlanResult:
        logger.info('[2] 开头计划：黄金三章递进、抓手、卖点落地、主角亮相')
        opening_plan = self.agent.gen_opening_plan(concept)
        logger.info(f'[2] 第一章抓手: {opening_plan.chapter_one_hook}')
        return opening_plan

    # ── 步骤 3：分卷章纲 ──────────────────────────────────

    def step_volume_outline(
        self, concept: ConceptPlanningResult, opening_plan: OpeningPlanResult,
    ) -> VolumeOutlineResult:
        logger.info(f'[3] 分卷章纲：黄金三章、分卷、前 {self.chapter_count} 章章纲')
        volume_outline = self.agent.gen_volume_outline(
            concept, opening_plan, self.chapter_count,
        )
        logger.info(f'[3] 分卷数: {len(volume_outline.volumes)}，章纲数: {len(volume_outline.chapter_outlines)}')
        return volume_outline

    # ── 步骤 4：黄金三章正文 ──────────────────────────────

    def step_opening_content(
        self, concept: ConceptPlanningResult, opening_plan: OpeningPlanResult,
    ) -> List[str]:
        logger.info('[4] 编写黄金三章正文')
        opening_content = self.agent.gen_opening_content(concept, opening_plan)
        chapters = self._split_chapters(opening_content)
        logger.info(f'[4] 黄金三章拆分为 {len(chapters)} 章')
        return chapters

    @staticmethod
    def _split_chapters(text: str) -> List[str]:
        """按 `# 第X章` 标题把多章正文切分为单章列表。"""
        parts = re.split(r'(?=^#\s*第\s*\d+\s*章)', text, flags=re.M)
        return [p.strip() for p in parts if p.strip()]

    # ── 步骤 5：单章正文（正文执行链）─────────────────────

    def _get_chapter_outline(
        self, idx: int, volume_outline: VolumeOutlineResult,
        concept: ConceptPlanningResult,
    ) -> ChapterOutlineItem:
        """取第 idx 章章纲；若卷纲未覆盖，则从概念里生成兜底章纲。"""
        outlines = volume_outline.chapter_outlines
        if idx < len(outlines):
            return outlines[idx]
        return ChapterOutlineItem(
            no=idx + 1,
            title=f'第{idx+1}章 推进{concept.story_engine}',
            core_event=f'局面进一步推进：{concept.volume_one_goal}',
            protagonist_goal=concept.protagonist_goal,
            obstacle=concept.main_conflict_source,
            situation_change=concept.volume_one_ending_change,
            hook_next='下一章继续推进故事引擎',
        )

    def _gen_character_state(
        self, idx: int, concept: ConceptPlanningResult, chapter_text: str,
    ) -> CharacterState:
        """生成第 idx 章结束的人物状态快照（带缓存）。"""
        return self.agent.gen_character_state(idx + 1, concept, chapter_text)

    def _review_fix_chapter(
        self, idx: int, chapter_text: str,
        concept: ConceptPlanningResult, ch: ChapterOutlineItem,
    ) -> str:
        """一致性复查（consistency_review）：不通过则返修，最多轮 args.check 次。"""
        logger.info(f'[6] 完稿收口：第{idx+1}章一致性复查')
        for _ in range(self.args.check):
            review = self.agent.check_consistency(
                idx + 1, concept, ch,
                self._read_prev_body(idx), chapter_text,
            )
            if review.overall_pass:
                logger.info(f'[6] 第{idx+1}章一致性复查通过')
                return chapter_text
            logger.warning(f'[6] 第{idx+1}章未通过，返修: {review.issues}')
            chapter_text = self.agent.fix_chapter_body(
                idx + 1, ch, chapter_text, review.issues,
            )
        return chapter_text

    def step_write_one_chapter(
        self, idx: int,
        concept: ConceptPlanningResult, volume_outline: VolumeOutlineResult,
        opening_chapters: List[str], character_state: CharacterState,
    ) -> CharacterState:
        """写单章正文 + 完稿收口，返回本章结束的人物状态快照。"""
        ch = self._get_chapter_outline(idx, volume_outline, concept)
        chapter_no = idx + 1
        prev_body = self._read_prev_body(idx)

        cached = self._read_chapter(idx)
        if cached:
            logger.info(f'[5] 第{chapter_no}章已存在，跳过写作')
            chapter_text = cached
        elif idx < len(opening_chapters) and opening_chapters[idx]:
            chapter_text = opening_chapters[idx]
        else:
            logger.info(f'[5] 编写第{chapter_no}章：{ch.title}')
            chapter_text = self.agent.gen_chapter_body(
                chapter_no, ch.title, ch,
                concept, volume_outline,
                _tail(prev_body, self.args.preview_len), character_state,
            )

        chapter_text = self._review_fix_chapter(idx, chapter_text, concept, ch)
        self._write_chapter(idx, chapter_text)

        new_state = self._gen_character_state(idx, concept, chapter_text)
        logger.info(f'[6] 第{chapter_no}章人物状态快照已更新')
        return new_state

    def step_write_all_chapters(
        self, concept: ConceptPlanningResult, volume_outline: VolumeOutlineResult,
        opening_chapters: List[str],
    ) -> None:
        """逐章写作：黄金三章优先用开头正文，其余由模型生成；每章必过完稿收口。"""
        character_state = CharacterState(
            chapter_no=0, goal=concept.protagonist_goal,
            emotion='', key_relationships={}, physical_state='',
            knowledge_state='', voice_style='',
        )
        for idx in range(self.chapter_count):
            character_state = self.step_write_one_chapter(
                idx, concept, volume_outline, opening_chapters, character_state,
            )

    # ── 文件读写 ──────────────────────────────────────────

    def _chapter_fname(self, idx: int) -> str:
        l = len(str(self.chapter_count))
        return path.join(self.pj_dir, f'article_{idx+1:0{l}d}.md')

    def _read_chapter(self, idx: int) -> Optional[str]:
        fname = self._chapter_fname(idx)
        return read_text(fname)

    def _write_chapter(self, idx: int, text: str) -> None:
        write_text(self._chapter_fname(idx), text)

    def _read_prev_body(self, idx: int) -> str:
        prev = self._read_chapter(idx - 1)
        return prev or ''

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        logger.info(f'工作目录: {self.pj_dir}（{self.chapter_count} 章）')

        concept = self.step_concept_planning()
        opening_plan = self.step_opening_plan(concept)
        volume_outline = self.step_volume_outline(concept, opening_plan)
        opening_chapters = self.step_opening_content(concept, opening_plan)
        self.step_write_all_chapters(concept, volume_outline, opening_chapters)

        logger.info('[*] 已完成')
        self._write_readme(concept, volume_outline)

    def _write_readme(self, concept: ConceptPlanningResult, volume_outline: VolumeOutlineResult) -> None:
        """写 README.md 汇总全书信息。"""
        lines = [
            f'# {concept.title}', '',
            f'- 题材：{concept.genre}/{concept.sub_genre}',
            f'- 目标长度：{concept.target_length}',
            f'- Hook：{concept.hook}',
            f'- Premise：{concept.premise}',
            f'- 故事引擎：{concept.story_engine}',
            f'- 第一卷目标：{concept.volume_one_goal}',
            f'- 卷末变化：{concept.volume_one_ending_change}',
            '',
            '## 分卷', '',
        ]
        for vol in volume_outline.volumes:
            lines.append(f'- 第{vol.no}卷 {vol.title}：{vol.goal}')
        lines += ['', '## 章纲', '']
        for ch in volume_outline.chapter_outlines:
            lines.append(f'- 第{ch.no}章 {ch.title}：{ch.core_event}')
        write_text(path.join(self.pj_dir, 'README.md'), '\n'.join(lines) + '\n')


def write_novel(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    orchestrator = WriteNovelOrchestrator(args)
    orchestrator.run()


def reg_subparser(subparsers):
    write_novel_parser = subparsers.add_parser(
        "write-novel", help="中文网文写作：从简介生成网文",
    )
    write_novel_parser.add_argument("blurb", help="小说简介")
    write_novel_parser.add_argument("-o", "--out", default="novel", help="输出目录名（默认 novel）")
    write_novel_parser.add_argument("-c", "--chapters", type=int, default=20, help="计划写作章数（默认 20）")
    write_novel_parser.add_argument("-ck", "--check", type=int, default=3, help="一致性复查/返修轮数上限（默认 3）")
    write_novel_parser.add_argument("-pv", "--preview-len", type=int, default=2000, help="上一章结尾供承接的截取字数（默认 2000）")
    write_novel_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    write_novel_parser.set_defaults(func=write_novel)

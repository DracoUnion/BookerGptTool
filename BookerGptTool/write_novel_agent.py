import json
import logging
import os
import json_repair
from os import path
from typing import List, Type

from pydantic import BaseModel, parse_obj_as

from .util import (
    render_prompt,
    gen_objs_md5,
    read_yaml_model,
    write_yaml_model,
    read_text,
    write_text,
    ext_code_block,
    ext_cont_block,
    json_dump_model,
)
from .openai import ask_chatgpt_retry, set_openai_props
from .write_novel_pmt import *
from .write_novel_models import *

logger = logging.getLogger(__name__)


class WriteNovelAgent:
    """封装所有 LLM 调用，每个方法对应一个独立的 prompt 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        self.pj_dir = args.out
        self.asset_dir = path.join(self.pj_dir, 'asset')
        set_openai_props(self.args)

    @staticmethod
    def _json_parse(raw: str, model: Type[BaseModel]):
        return parse_obj_as(model, json_repair.loads(ext_code_block(raw)))

    @staticmethod
    def _str_ctx(ctx: dict) -> dict:
        """把 ctx 中的 pydantic 模型序列化为 JSON 字符串，用于缓存命中和渲染。"""
        return {
            k: json_dump_model(v) if isinstance(v, BaseModel) else v
            for k, v in ctx.items()
        }

    def _call_json(self, pmt: str, model: Type[BaseModel], key: str, **ctx):
        """渲染提示词，发起结构化 LLM 调用（JSON 输出），带 YAML 文件缓存。"""
        ctx = self._str_ctx(ctx)
        cache_fname = path.join(self.asset_dir, key + '_' + gen_objs_md5(ctx) + '.yaml')
        r = read_yaml_model(cache_fname, model)
        if r is not None:
            return r
        ques = render_prompt(pmt, **ctx)
        res = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=lambda s: self._json_parse(s, model),
        )
        write_yaml_model(cache_fname, res)
        return res

    def _call_cont(self, pmt: str, key: str, **ctx):
        """渲染提示词，发起文本 LLM 调用（[content]...[/content] 输出），带文本文件缓存。"""
        ctx = self._str_ctx(ctx)
        cache_fname = path.join(self.asset_dir, key + '_' + gen_objs_md5(ctx) + '.md')
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(pmt, **ctx)
        res = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )
        write_text(cache_fname, res)
        return res

    # ── 步骤 1：前置规划 ──────────────────────────────────

    def gen_concept_planning(self, blurb: str) -> ConceptPlanningResult:
        return self._call_json(
            CONCEPT_PLANNING_PMT, ConceptPlanningResult,
            'concept', blurb=blurb,
        )

    # ── 步骤 2：开头计划 ──────────────────────────────────

    def gen_opening_plan(self, concept: ConceptPlanningResult) -> OpeningPlanResult:
        return self._call_json(
            OPENING_PLAN_PMT, OpeningPlanResult,
            'opening_plan', concept=concept,
        )

    # ── 步骤 3：黄金三章正文 ──────────────────────────────

    def gen_opening_content(
        self, concept: ConceptPlanningResult, opening_plan: OpeningPlanResult,
    ) -> str:
        return self._call_cont(
            OPENING_CONTENT_PMT,
            'opening_content',
            concept=concept, opening_plan=opening_plan,
        )

    # ── 步骤 4：分卷章纲 ──────────────────────────────────

    def gen_volume_outline(
        self, concept: ConceptPlanningResult, opening_plan: OpeningPlanResult,
        chapter_count: int,
    ) -> VolumeOutlineResult:
        return self._call_json(
            VOLUME_OUTLINE_PMT, VolumeOutlineResult,
            'volume_outline',
            concept=concept, opening_plan=opening_plan,
            chapter_count=str(chapter_count),
        )

    # ── 步骤 5：单章正文 ──────────────────────────────────

    def gen_chapter_body(
        self, chapter_no: int, chapter_title: str,
        chapter_outline: ChapterOutlineItem,
        concept: ConceptPlanningResult, volume_outline: VolumeOutlineResult,
        prev_body_tail: str, character_state: CharacterState,
    ) -> str:
        return self._call_cont(
            CHAPTER_BODY_PMT,
            'chapter_body',
            chapter_no=str(chapter_no),
            chapter_title=chapter_title,
            chapter_outline=chapter_outline,
            concept=concept, volume_outline=volume_outline,
            prev_body_tail=prev_body_tail,
            character_state=character_state,
            protagonist_goal=chapter_outline.protagonist_goal,
            obstacle=chapter_outline.obstacle,
            core_event=chapter_outline.core_event,
            hook_next=chapter_outline.hook_next,
        )

    # ── 步骤 6a：人物状态快照 ─────────────────────────────

    def gen_character_state(
        self, chapter_no: int, concept: ConceptPlanningResult, chapter_text: str,
    ) -> CharacterState:
        return self._call_json(
            CHARACTER_STATE_PMT, CharacterState,
            'char_state',
            chapter_no=str(chapter_no), concept=concept, chapter_text=chapter_text,
        )

    # ── 步骤 6b：一致性复查 ───────────────────────────────

    def check_consistency(
        self, chapter_no: int,
        concept: ConceptPlanningResult, chapter_outline: ChapterOutlineItem,
        prev_body: str, chapter_text: str,
    ) -> ConsistencyReviewResult:
        return self._call_json(
            CONSISTENCY_REVIEW_PMT, ConsistencyReviewResult,
            'consistency_review',
            chapter_no=str(chapter_no),
            concept=concept, chapter_outline=chapter_outline,
            prev_body=prev_body, chapter_text=chapter_text,
        )

    # ── 步骤 6c：返修单章正文 ─────────────────────────────

    def fix_chapter_body(
        self, chapter_no: int,
        chapter_outline: ChapterOutlineItem, chapter_text: str, issues: List[str],
    ) -> str:
        return self._call_cont(
            CHAPTER_FIX_PMT,
            'chapter_fix',
            chapter_no=str(chapter_no),
            chapter_outline=chapter_outline,
            chapter_text=chapter_text,
            issues='\n'.join(issues),
        )
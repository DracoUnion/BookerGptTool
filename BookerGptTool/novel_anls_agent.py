import json

from os import path

import logging

from typing import List, Type

from pydantic import BaseModel, parse_obj_as

from .novel_anls_models import (
    BookAnalysisReport, BookMeta, Chapter, ChapterSummary,
    MODULE_CLASS_MAP,
)

from .novel_anls_pmt import (
    SCAN_PMT, AGGREGATE_PROMPT_MAP,
    SCAN_SYSTEM, AGGREGATE_SYSTEM,
)

from .openai import call_llm_retry, set_openai_props
from .util import render_prompt, json_dump_model, ext_code_block


class NovelAnlsAgent:
    """统一封装小说分析的结构化 LLM 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(args)

    def _call(
        self,
        pmt: str,
        response_model: Type[BaseModel],
        system_prompt: str,
        **ctx,
    ):
        """渲染提示词，发起一次结构化 LLM 调用并解析为 Pydantic 响应。"""
        user_prompt = render_prompt(pmt, **ctx)

        def parse(raw):
            return parse_obj_as(response_model, json.loads(ext_code_block(raw)))

        return call_llm_retry(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            self.model,
            retry=self.args.retry,
            temp=getattr(self.args, 'temp', 0.3),
            max_tokens=self.args.max_tokens,
            parse_output=parse,
        )

    def scan_chapter(
        self,
        chapter_index: int,
        chapter_title: str,
        chapter_text: str,
    ) -> ChapterSummary:
        """扫描单章，返回结构化摘要。"""
        return self._call(
            SCAN_PMT,
            ChapterSummary,
            SCAN_SYSTEM,
            chapter_index=str(chapter_index),
            chapter_title=json_dump_model(chapter_title),
            chapter_text=chapter_text,
        )

    def aggregate_module(
        self,
        module_name: str,
        summaries: List[ChapterSummary],
        book_meta: BookMeta,
    ):
        """聚合指定模块，返回对应的 Pydantic 模型。"""
        return self._call(
            AGGREGATE_PROMPT_MAP[module_name],
            MODULE_CLASS_MAP[module_name],
            AGGREGATE_SYSTEM,
            all_chapter_summaries=json_dump_model(summaries),
            book_meta=json_dump_model(book_meta),
        )

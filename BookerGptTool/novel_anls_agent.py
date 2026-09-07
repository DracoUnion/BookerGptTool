from os import path

import json

import logging

import re

from concurrent.futures import ThreadPoolExecutor, as_completed

from typing import Any, Dict, List, Optional

import json_repair as json_repair

from ebooklib import epub

from pyquery import PyQuery

from tqdm import tqdm

from .novel_anls_models import (
    BookAnalysisReport, BookMeta, Chapter, ChapterSummary,
    MODULE_CLASS_MAP,
)

from .novel_anls_pmt import (
    SCAN_SYSTEM_PROMPT, SCAN_PROMPT,
    AGGREGATE_SYSTEM_PROMPT, AGGREGATE_PROMPT_MAP,
)

from .openai import call_llm_retry, set_openai_props



class NovelAnlsAgent:
    """统一封装小说分析的结构化 LLM 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(args)

    def _call(
        self,
        user_prompt: str,
        response_model,
        system_prompt: str,
    ):
        """发起一次结构化 LLM 调用并解析 JSON 为 Pydantic 响应。"""
        msgs = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        def parse(raw):
            data = json_repair.loads(raw)
            return response_model.model_validate(data)

        return call_llm_retry(
            msgs, self.model,
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
        user_prompt = SCAN_PROMPT.format(
            chapter_index=chapter_index,
            chapter_title=json.dumps(chapter_title, ensure_ascii=False),
            chapter_text=chapter_text,
        )
        return self._call(
            user_prompt,
            ChapterSummary,
            SCAN_SYSTEM_PROMPT,
        )

    def aggregate_module(
        self,
        module_name: str,
        summaries: List[ChapterSummary],
        book_meta: BookMeta,
    ):
        """聚合指定模块，返回对应的 Pydantic 模型。"""
        prompt_template = AGGREGATE_PROMPT_MAP[module_name]
        user_prompt = prompt_template.format(
            all_chapter_summaries=json.dumps(
                [summary.model_dump() for summary in summaries],
                ensure_ascii=False,
            ),
            book_meta=json.dumps(book_meta.model_dump(), ensure_ascii=False),
        )
        return self._call(
            user_prompt,
            MODULE_CLASS_MAP[module_name],
            AGGREGATE_SYSTEM_PROMPT,
        )

import gc

import tqdm

import copy

import traceback

import os

import json

import logging

from os import path

import re

import yaml

from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor

from imgyaso.quant import pngquant

from .trans_epub_pmt import *

from .tomd import tomd

from .util import (
    to_kebab,
    read_zip,
    is_pic,
    get_md_title,
    epub2html_pandoc,
    group_chunks,
    split_md_lines,
    ext_cont_block,
    ext_code_block,
    render_prompt,
    malloc_trim_linux,
)

from .openai import logger as oai_logger

from .openai import set_openai_props, ask_chatgpt_retry

from .fmt import fmt_zh, fmt_publisher

from .clean_heading import clean_md_llm

from .trans_epub_models import *



class EpubTranslatorAgent:
    def __init__(self, args):
        self.args = args
        set_openai_props(args)

    def translate_title(self, text: str) -> str:
        ques = render_prompt(TRANS_TITLE_PMT, text=text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def format_text(self, text: str) -> str:
        ques = render_prompt(FMT_PMT, text=text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def translate_body(self, text: str) -> str:
        ques = render_prompt(TRANS_BODY_PMT, text=text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_toc(self, text: str) -> str:
        ques = render_prompt(TOC_PMT, text=text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def extract_chapter_toc(self, titles: list) -> List[TocExtResult]:
        ques = render_prompt(TOC_EXT_PMT, titles=json.dumps(titles, ensure_ascii=False))
        parse_output = lambda s: parse_obj_as(
            List[TocExtResult],
            json.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=parse_output,
        )

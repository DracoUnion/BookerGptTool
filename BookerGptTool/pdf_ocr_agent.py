import base64

import gc

import argparse

import logging

import traceback

import copy

import numpy as np

from io import BytesIO

from os import path

import re

import os

import hashlib

import shutil

import yaml

import pymupdf as pymu

import functools

import cv2

from concurrent.futures import Future, ThreadPoolExecutor, as_completed, ProcessPoolExecutor

from typing import Any, Callable, Iterator, List, Optional, Tuple

import json

import json_repair

import tqdm

from imgyaso.quant import pngquant

from pydantic import BaseModel, parse_obj_as

from .clean_heading import clean_md_llm

from .pdf_ocr_pmt import *

from .pdf_ocr_models import *

from .tomd import tomd

from .util import (
    extname,
    to_kebab,
    ext_code_block,
    ext_cont_block,
    render_prompt,
    malloc_trim_linux,
)

from .openai import (
    call_vlm_retry,
    ask_chatgpt_retry,
    set_openai_props,
)

from .openai import logger as oai_logger



class PdfOcrAgent:
    """封装所有 LLM 调用的智能体类。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        set_openai_props(args)

    def ocr(self, img: bytes) -> str:
        parse_output = lambda ans: OCRResult(
            **json_repair.loads(ext_code_block(ans))
        )
        res: OCRResult = call_vlm_retry(
            img, OCR_PMT,
            model_name=self.args.vmodel,
            args=self.args,
            parse_output=parse_output,
        )
        return self._res2md(res)

    def _res2md(self, r: OCRResult) -> str:
        mds = []
        for seg in r.contents:
            if seg.type == 'image':
                bbox = seg.bbox
                md = f'![](bbox={bbox})'
            elif seg.type == 'title':
                md = '# ' + seg.markdown
            elif seg.type == 'list':
                md = '+   ' + seg.markdown
            elif seg.type == 'code':
                md = '```\n' + seg.markdown + '\n```'
            elif seg.type == 'quote':
                md = '> ' + seg.markdown
            else:
                md = seg.markdown
            mds.append(md)
        return '\n\n'.join(mds).strip() \
            or '<!-- no content -->'

    def merge(self, prev_line: str, next_line: str) -> int:
        ques = render_prompt(MERGE_PMT, prev=prev_line, next=next_line)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        return int('[TRUE]' in ans)

    def post_proc(self, text: str) -> str:
        ques = render_prompt(POSTPROC_PMT, text=text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def translate(self, text: str) -> str:
        ques = render_prompt(TRANS_BODY_PMT, text=text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_toc(self, toc_text: str) -> List[List[str]]:
        ques = render_prompt(TOC_PMT, text=toc_text)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        return re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)

    def trans_title(self, title: str) -> str:
        ques = render_prompt(TRANS_TITLE_PMT, text=title)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

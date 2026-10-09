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

from .resplit_agent import ResplitAgent
from .tomd import tomd

from .util import (
    extname,
    to_kebab,
    ext_code_block,
    ext_cont_block,
    render_prompt,
    malloc_trim_linux,
    gen_objs_md5,
    read_yaml_model,
    write_yaml_model,
    read_text,
    write_text,
)

from .openai import (
    call_vlm_retry,
    ask_chatgpt_retry,
    set_openai_props,
)

from .openai import logger as oai_logger

class PdfOcrSplitAgent(ResplitAgent):

    def __init__(self, args):
        super().__init__(args)
        self.asset_dir = \
            PdfOcrAgent.resolve_paths(args)['meta_dir']


class PdfOcrAgent:
    """封装所有 LLM 调用的智能体类。"""

    @staticmethod
    def resolve_paths(args: argparse.Namespace) -> dict:
        """根据 args 计算所有输出路径，返回字典。"""
        name = path.basename(args.fname)[:-4]
        slug = to_kebab(name)
        d = path.dirname(args.fname)
        pj_dir = path.join(d, slug) if args.mkdir else d
        img_dir = (
            path.join(pj_dir, 'img')
            if args.mkdir else args.fname[:-4] + '_imgs'
        )
        meta_dir = (
            path.join(pj_dir, 'asset')
            if args.mkdir else args.fname[:-4] + '_asset'
        )
        md_fname = (
            path.join(pj_dir, f'{slug}.md')
            if args.mkdir else args.fname[:-4] + '.md'
        )
        page_fname = path.join(meta_dir, 'pages.yaml')
        group_fname = path.join(meta_dir, 'groups.yaml')
        toc_fname = path.join(meta_dir, 'toc.yaml')
        return {
            'name': name,
            'slug': slug,
            'pj_dir': pj_dir,
            'meta_dir': meta_dir,
            'img_dir': img_dir,
            'md_fname': md_fname,
            'page_fname': page_fname,
            'group_fname': group_fname,
            'toc_fname': toc_fname,
        }

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        paths = self.resolve_paths(args)
        self.pj_dir = paths['pj_dir']
        self.asset_dir = paths['meta_dir']
        set_openai_props(args)

    def ocr(self, img: bytes) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'ocr_' + hashlib.md5(img).hexdigest() + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        parse_output = lambda ans: OCRResult(
            **json_repair.loads(ext_code_block(ans))
        )
        res: OCRResult = call_vlm_retry(
            img, OCR_PMT,
            model_name=self.args.vmodel,
            args=self.args,
            parse_output=parse_output,
        )
        md = self._res2md(res)
        write_text(cache_fname, md)
        return md

    def _res2md(self, r: OCRResult) -> str:
        mds = []
        for seg in r.contents:
            if seg.type == 'image':
                bbox = seg.bbox
                md = f'![](bbox={bbox})'
            elif seg.type == 'title':
                md = '# ' + seg.markdown
            elif seg.type == 'list':
                md = re.sub('^', '+   ', seg.markdown, flags=re.M)
            elif seg.type == 'code':
                md = '```\n' + seg.markdown + '\n```'
            elif seg.type == 'quote':
                md = re.sub('^', '> ', seg.markdown, flags=re.M)
            elif seg.type in ['header', 'footer']:
                md = ''
            else:
                md = seg.markdown
            mds.append(md)
        return '\n\n'.join(mds).strip() \
            or '<!-- no content -->'

    def merge(self, prev_line: str, next_line: str) -> int:
        cache_fname = path.join(
            self.asset_dir,
            'merge_' + gen_objs_md5(prev_line, next_line) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return int('[TRUE]' in read_text(cache_fname))
        ques = render_prompt(MERGE_PMT, prev=prev_line, next=next_line)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        write_text(cache_fname, ans)
        return int('[TRUE]' in ans)

    def post_proc(self, text: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'post_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(POSTPROC_PMT, text=text)
        res = ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )
        write_text(cache_fname, res)
        return res

    def translate(self, text: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'trans_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(TRANS_BODY_PMT, text=text)
        res = ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )
        write_text(cache_fname, res)
        return res

    def fix_toc(self, toc_text: str) -> List[List[str]]:
        cache_fname = path.join(
            self.asset_dir,
            'toc_' + gen_objs_md5(toc_text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, List[List[str]])
        if r is not None:
            return r
        ques = render_prompt(TOC_PMT, text=toc_text)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        res = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
        write_yaml_model(cache_fname, res)
        return res

    def trans_title(self, title: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'ttl_' + gen_objs_md5(title) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(TRANS_TITLE_PMT, text=title)
        res = ask_chatgpt_retry(ques, self.args.model, self.args)
        write_text(cache_fname, res)
        return res

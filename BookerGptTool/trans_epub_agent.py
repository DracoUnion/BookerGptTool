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
    gen_objs_md5,
    read_yaml_model,
    write_yaml_model,
    read_text,
    write_text,
)

from .openai import logger as oai_logger

from .openai import set_openai_props, ask_chatgpt_retry

from .fmt import fmt_zh, fmt_publisher

from .clean_heading import clean_md_llm
from .resplit_agent import ResplitAgent
from .trans_epub_models import *


class EpubTranslatorSplitAgent(ResplitAgent):

    def __init__(self, args):
        super().__init__(args)
        self.asset_dir = \
            EpubTranslatorAgent.resolve_paths(args)['meta_dir']

class EpubTranslatorAgent:

    @staticmethod
    def resolve_paths(args):
        name = path.basename(args.fname)[:-5]
        slug = to_kebab(name)
        proj_dir = path.join(path.dirname(args.fname), slug)
        meta_dir = path.join(proj_dir, 'asset')
        img_dir = path.join(proj_dir, 'img')
        return dict(
            name=name,
            slug=slug,
            proj_dir=proj_dir,
            meta_dir=meta_dir,
            img_dir=img_dir,
            meta_fname=path.join(meta_dir, 'meta.yaml'),
            html_fname=path.join(meta_dir, 'all.html'),
            md_fname=path.join(meta_dir, 'all.md'),
            chunk_fname=path.join(meta_dir, 'chunks.yaml'),
            chs_fname=path.join(meta_dir, 'chs.yaml'),
            readme_fname=path.join(proj_dir, 'README.md'),
            summary_fname=path.join(proj_dir, 'SUMMARY.md'),
        )

    def __init__(self, args):
        self.args = args
        paths = self.resolve_paths(args)
        self.pj_dir = paths['proj_dir']
        self.asset_dir = paths['meta_dir']
        set_openai_props(args)

    def translate_title(self, text: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'ttl_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(TRANS_TITLE_PMT, text=text)
        res = ask_chatgpt_retry(ques, self.args.model, self.args)
        write_text(cache_fname, res)
        return res

    def format_text(self, text: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'fmt_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(FMT_PMT, text=text)
        res = ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )
        write_text(cache_fname, res)
        return res

    def translate_body(self, text: str) -> str:
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

    def fix_toc(self, text: str) -> str:
        cache_fname = path.join(
            self.asset_dir,
            'toc_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        ques = render_prompt(TOC_PMT, text=text)
        res = ask_chatgpt_retry(ques, self.args.model, self.args)
        write_text(cache_fname, res)
        return res

    def extract_chapter_toc(self, titles: list) -> List[TocExtResult]:
        cache_fname = path.join(
            self.asset_dir,
            'toc_ext_' + gen_objs_md5(titles) + '.yaml'
        )
        r = read_yaml_model(cache_fname, List[TocExtResult])
        if r is not None:
            return r
        ques = render_prompt(TOC_EXT_PMT, titles=json.dumps(titles, ensure_ascii=False))
        parse_output = lambda s: parse_obj_as(
            List[TocExtResult],
            json.loads(ext_code_block(s)),
        )
        res = ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=parse_output,
        )
        write_yaml_model(cache_fname, res)
        return res

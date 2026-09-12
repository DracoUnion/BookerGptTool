import openai

import os

from os import path

import yaml

import json_repair

import json

import logging

import functools

from concurrent.futures import ThreadPoolExecutor, as_completed, Future

from threading import Lock

from typing import List

from pydantic import parse_obj_as

from tqdm import tqdm

from .util import (
    extname,
    ext_code_block,
    ext_cont_block,
    render_prompt,
)

from .openai import logger as oai_logger

from .openai import ask_chatgpt_retry, set_openai_props

from .code2book_pmt import *

from .code2book_models import *


def expand_stars(ptns, files):
    res = []
    for p in ptns:
        if p.endswith('**'):
            res += [f for f in files if f.startswith(p[:-2])]
        elif p.endswith('*'):
            res += [f for f in files if f.startswith(p[:-1])]
        else:
            res.append(p)
    return res

class Code2BookAgent:
    """封装所有 LLM 调用，每个方法对应一个独立的 prompt 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def fix_parts(
        self, files: List[str], 
        parts: List[PartClusResult], problem: str
    ) -> List[PartClusResult]:
        parts_str = json.dumps([p.dict() for p in parts])
        ques = render_prompt(
            PT_FIX_PMT,
            files='\n'.join(files),
            problem=problem,
            parts=parts_str,
        )
        parse_output = lambda s: parse_obj_as(
            List[PartClusResult],
            json_repair.loads(ext_code_block(s))
        )
        parts =  ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for pt in parts:
            pt.files = expand_stars(pt.files, files)
        return parts

    def cluster_parts(self, files: List[str]) -> List[PartClusResult]:
        ques = render_prompt(PT_CLUS_PMT, files='\n'.join(files))
        parse_output = lambda s: parse_obj_as(
            List[PartClusResult],
            json_repair.loads(ext_code_block(s))
        )
        parts =  ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for pt in parts:
            pt.files = expand_stars(pt.files, files)
        return parts

    def gen_code_desc(self, fname: str, code: str) -> ClsFuncExtResult:
        """根据源码提取类、方法和全局函数描述。"""
        parse_output = lambda s: ClsFuncExtResult(
            **json_repair.loads(ext_code_block(s))
        )
        res = ClsFuncExtResult(desc="", classes=[], funcs=[])
        step = self.args.code_limit - self.args.code_overlap
        start_line = 1
        for i in range(0, len(code), step):
            chunk = code[i: i + self.args.code_limit]
            ques = render_prompt(
                CLS_FUNC_EXT_PMT, 
                fname=fname, code=chunk,
                start=str(start_line)
            )
            chunk_res: ClsFuncExtResult =  ask_chatgpt_retry(
                ques, self.model, self.args,
                parse_output=parse_output,
            )
            res.desc += chunk_res.desc
            res.classes += chunk_res.classes
            res.funcs += chunk_res.funcs
            start_line += chunk.count('\n')
        return res

    def gen_outline(
        self, fnames: List[str], code_desc: List[CodeDescItemResult], readme: str,
    ) -> List[OutlineChapterResult]:
        """根据项目结构和源码描述生成书籍大纲。"""
        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        ques = render_prompt(
            OUTLINE_PMT,
            struct=fnames_li,
            code_desc=code_desc_str,
            readme=readme,
        )
        parse_output = lambda s: parse_obj_as(
            List[OutlineChapterResult],
            json_repair.loads(ext_code_block(s))
        )
        res: List[OutlineChapterResult] = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for o in res:
            for n in o.nodes:
                n.src = expand_stars(n.src, fnames)
        return res

    def fix_outline(
        self, outline: List[OutlineChapterResult], fnames: List[str],
        code_desc: List[CodeDescItemResult], readme: str, problem: str,
    ) -> List[OutlineChapterResult]:
        """校验大纲未覆盖所有文件时，补充缺少的源码文件重写大纲。"""
        outline_str = json.dumps(
            [o.dict() for o in outline], 
            ensure_ascii=False
        )
        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        ques = render_prompt(
            OUTLINE_FIX_PMT,
            struct=fnames_li,
            code_desc=code_desc_str,
            readme=readme,
            outline=outline_str,
            problem=problem,
        )
        parse_output = lambda s: parse_obj_as(
            List[OutlineChapterResult],
            json_repair.loads(ext_code_block(s))
        )
        res: List[OutlineChapterResult] = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for o in res:
            for n in o.nodes:
                n.src = expand_stars(n.src, fnames)
        return res

    def gen_src_anls_detail(
        self, idx: int, 
        outline_chs: List[OutlineChapterResult], 
        code_desc: List[CodeDescItemResult],
    ) -> SrcAnlsDetailResult:
        """生成第 idx 章细纲的源码解析部分。"""
        outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )
        code_desc_str = json.dumps(
            [d.dict() for d in code_desc], 
            ensure_ascii=False
        )
        ques = render_prompt(
            SRC_ANLS_DETAIL_PMT,
            i=str(idx + 1),
            outline=outline_str,
            code_desc=code_desc_str,
        )
        parse_output = lambda s: SrcAnlsDetailResult(
            **json_repair.loads(ext_code_block(s))
        )
        res: SrcAnlsDetailResult = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        return res

    def gen_rest_detail(
        self, idx: int, 
        detail: Detail, 
        outline_chs: List[OutlineChapterResult], 
        code_desc: List[CodeDescItemResult],
    ) -> RestDetailResult:
        """生成第 idx 章细纲的剩余部分（学习目标、类比、练习等）。"""
        outline_str =  outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )
        code_desc_str = json.dumps(
            [d.dict() for d in code_desc], 
            ensure_ascii=False
        )
        ques = render_prompt(
            REST_DETAIL_PMT,
            detail=detail.json(),
            outline=outline_str,
            i=str(idx + 1),
            code_desc=code_desc_str,
        )
        parse_output = lambda s: RestDetailResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def fix_detail(
        self, idx: int, detail: Detail, 
        outline_chs: List[OutlineChapterResult],
        code_desc: List[CodeDescItemResult], 
        problem: str,
    ) -> Detail:
        """校验细纲未覆盖所有函数时，补充缺少的函数重写细纲。"""
        outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )        
        code_desc_str = json.dumps(
            [d.dict() for d in code_desc], 
            ensure_ascii=False
        )
        ques = render_prompt(
            DETAIL_FIX_PMT,
            i=str(idx),
            outline=outline_str,
            detail=detail.json(),
            code_desc=code_desc_str,
            problem=problem,
        )
        parse_output = lambda s: Detail(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_body(
        self, idx: int, 
        detail: Detail, 
        outline_chs: List[OutlineChapterResult], 
        code_desc: List[CodeDescItemResult],
    ) -> str:
        """根据大纲和细纲生成第 idx 章正文。"""
        outline_str = json.dumps([o.dict() for o in outline_chs], ensure_ascii=False)
        detail_str = detail.json()
        code_desc_str = json.dumps(
            [d.dict() for d in code_desc], 
            ensure_ascii=False
        )
        ques = render_prompt(
            BODY_PMT,
            detail=detail_str,
            outline=outline_str,
            code_desc=code_desc_str,
            i=str(idx + 1),
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def check_body(self, body: str, detail: Detail) -> str:
        """校验正文是否符合格式规范，返回修改意见或 [PERFECT/]。"""
        detail_str = detail.json()
        ques = render_prompt(BODY_CHK_PMT, body=body, detail=detail_str)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_body(
        self, 
        detail: Detail, 
        body: str, 
        comment: str, 
        code_desc: List[CodeDescItemResult],
    ) -> str:
        """根据修改意见和对应源码修改正文。"""
        detail_str = detail.json()
        code_desc_str = json.dumps(
            [d.dict() for d in code_desc], 
            ensure_ascii=False
        )
        ques = render_prompt(
            BODY_FIX_PMT,
            detail=detail_str,
            body=body,
            comment=comment,
            code_desc=code_desc_str,
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

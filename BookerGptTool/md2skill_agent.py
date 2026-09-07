import torch

import requests

import tarfile

import numpy as np

from io import BytesIO

from os import path

import re

import os

import shutil

import yaml

import json_repair as json

from concurrent.futures import ThreadPoolExecutor

from threading import Lock

import functools

from sentence_transformers import SentenceTransformer

from typing import Any, Dict, Optional, List, Callable

from .util import ngram_jaccard, ext_code_block, ext_cont_block, render_prompt

from .openai import ask_chatgpt_retry, set_openai_props

from .md2skill_pmt import *

from .md2skill_gen import generate_claude_skills

from .md2skill_chunker import chunk_markdown

from .md2skill_models import BookSchema, RawSkill, ChunkSkill, SKUType



def parse_raw_skill(raw_skill: str) -> Optional[RawSkill]:
    """从 LLM 输出的 YAML Frontmatter + Markdown Body 中解析出 RawSkill。"""
    RE_RAW_SKILL = r'---\n([\s\S]+?)\n---\n([\s\S]+)'
    m = re.search(RE_RAW_SKILL, raw_skill)
    if not m: return None
    try:
        meta = yaml.safe_load(m.group(1))
    except:
        return None
    body = m.group(2)

    # 补全缺失字段
    if 'name' not in meta:
        first_line = body.split("\n")[0].strip("# ").strip()
        meta["name"] = re.sub(
            r"[^a-zA-Z0-9一-鿿]+", "-", first_line
        ).strip("-").lower()[:50]

    slug = _to_kebab(meta['name'])
    trigger = meta.pop('trigger', "通用知识查询")

    return RawSkill(
        name=meta['name'],
        slug=slug,
        trigger=trigger,
        domain=meta.get('domain', ''),
        body=body,
        raw_text=raw_skill,
        prerequisites=meta.get('prerequisites', []),
        source_ref=meta.get('source_ref', ''),
        confidence=meta.get('confidence', 0.0),
        characters=meta.get('characters', []),
        timeline=meta.get('timeline', ''),
        prompt_version=meta.get('prompt_version', ''),
    )




def get_pmt_by_type(tp):
    """根据 book_type 解析出 prompt 模板"""
    # 精确匹配
    if tp in TYPE_PMT_MAP:
        return TYPE_PMT_MAP[tp]

    # 模糊匹配
    bt = tp.lower()
    if any(kw in bt for kw in ("叙事", "小说", "故事", "fiction", "narrative")):
        return TYPE_PMT_MAP["叙事类"]
    if any(kw in bt for kw in ("方法", "框架", "methodology", "framework")):
        return TYPE_PMT_MAP["方法论"]
    if any(kw in bt for kw in ("教材", "学术", "academic", "textbook")):
        return TYPE_PMT_MAP["学术教材"]
    if any(kw in bt for kw in ("保险", "保单", "保障", "理赔", "insurance")):
        return TYPE_PMT_MAP["保险合同"]
    if any(kw in bt for kw in ("报告", "研报", "白皮书", "report")):
        return TYPE_PMT_MAP["行业报告"]
    if any(kw in bt for kw in ("医学", "法律", "金融", "medical", "legal")):
        return TYPE_PMT_MAP["医学法律"]
    if any(kw in bt for kw in ("规范", "标准", "规程", "条例", "手册", "manual", "guide", "操作")):
        return TYPE_PMT_MAP["技术手册"]

    return DFT_EXT_PMT




def _to_kebab(name: str) -> str:
    """将技能名转为 kebab-case slug"""
    s = re.sub(r"[^\w\s一-鿿-]", "", name)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "unnamed-skill"




class Md2SkillAgent:
    """封装所有 LLM 调用，一个方法对应一次调用。"""

    def __init__(self, model, args):
        self.model = model
        self.args = args

    def generate_schema(self, toc, preface) -> BookSchema:
        """Step 1: 从目录和前言推断知识结构 schema"""
        prompt = render_prompt(SCHEMA_PMT, toc=toc, preface=preface)
        parse_output = lambda s: BookSchema.model_validate(
            json.loads(ext_code_block(schema_raw)))
        schema_raw = ask_chatgpt_retry(prompt, self.model, self.args, parse_output)
        return schema_raw

    def generate_raw_skills(
        self, book_type: str, content: str, context: str
    ) -> List[RawSkill]:
        """Step 2: 从一个文本块中提取原始技能"""
        prompt = render_prompt(
            get_pmt_by_type(book_type),
            content=content,
            context=context,
        )
        parse_output = lambda s: ext_cont_block(s).split('[split/]')
        raw_texts = ask_chatgpt_retry(prompt, self.model, self.args, parse_output)
        return [rs for rs in (parse_raw_skill(rt) for rt in raw_texts) if rs]

    def merge_cluster(self, cluster: List[RawSkill]) -> Optional[RawSkill]:
        """Step 3: 将相似技能集群合并为一个"""
        text = '\n\n[split/]\n\n'.join([s.raw_text for s in cluster])
        prompt = render_prompt(
            REDUCE_PMT,
            count=str(len(cluster)),
            skills=text,
        )
        merged_text = ask_chatgpt_retry(prompt, self.model, self.args, ext_cont_block)
        return parse_raw_skill(merged_text)

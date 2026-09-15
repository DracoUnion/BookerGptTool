# -*- coding: utf-8 -*-
"""
paper2textbook_models.py —— 论文→教科书的数据模型
与 paper2textbook_pmt.py 中的提示词格式一一对应。
"""

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


# ============================================================
# 一、概念卡片（单篇论文拆解）
# ============================================================

class ConceptCard(_Base):
    name: str = ''
    desc: str = ''
    is_original: bool = False
    excerpt: List[str] = []
    page: str = ''


class MethodCard(_Base):
    name: str = ''
    desc: str = ''
    excerpt: List[str] = []
    page: str = ''


class TheoremCard(_Base):
    name: str = ''
    desc: str = ''
    excerpt: List[str] = []
    page: str = ''


class FindingCard(_Base):
    name: str = ''
    desc: str = ''
    excerpt: List[str] = []
    page: str = ''


class PaperConcepts(_Base):
    """单篇论文的拆解结果：一张可复用的概念卡片集合。"""
    paper: str = ''
    desc: str = ''
    concepts: List[ConceptCard] = []
    methods: List[MethodCard] = []
    theorems: List[TheoremCard] = []
    findings: List[FindingCard] = []


# ============================================================
# 二、论文聚类
# ============================================================

class PartClus(_Base):
    no: int = 0
    title: str = ''
    desc: str = ''
    papers: List[str] = []


class PaperClusResult(_Base):
    parts: List[PartClus] = []


# ============================================================
# 三、全书大纲（章 - 知识点）
# ============================================================

class OutlineSrc(_Base):
    paper: str = ''
    page: str = ''


class OutlineNode(_Base):
    no: int = 0
    name: str = ''
    desc: str = ''
    role: str = ''
    src: List[OutlineSrc] = []


class OutlineChapter(_Base):
    no: int = 0
    name: str = ''
    desc: str = ''
    nodes: List[OutlineNode] = []


class OutlineResult(_Base):
    title: str = ''
    chapters: List[OutlineChapter] = []


# ============================================================
# 四、章节细纲
# ============================================================

class DetailSource(_Base):
    paper: str = ''
    page: str = ''
    role: str = ''


class ConceptUnit(_Base):
    no: int = 0
    name: str = ''
    points: List[str] = []
    sources: List[DetailSource] = []
    excerpts: List[str] = []


class ConceptAnlsResult(_Base):
    units: List[ConceptUnit] = []


class RestDetailResult(_Base):
    learning_targets: List[str] = []
    concept_map: List[str] = []
    life_analogy: List[str] = []
    summary: List[Dict[str, str]] = []
    exercises: List[Dict[str, Any]] = []


class ChapterDetail(ConceptAnlsResult, RestDetailResult):
    no: int = 0


# ============================================================
# 五、辅助产物
# ============================================================

class GlossaryEntry(_Base):
    canonical: str = ''
    aliases: List[str] = []
    first_seen: str = ''


class CitationAudit(_Base):
    citation_stats: List[Dict[str, Any]] = []
    unsupported_claims: List[Dict[str, Any]] = []
    missing_concepts: List[Dict[str, Any]] = []


class PaperMeta(_Base):
    id: str = ''
    file: str = ''
    title: str = ''
    authors: str = ''
    abstract: str = ''
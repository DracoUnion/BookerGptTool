# -*- coding: utf-8 -*-
"""
paper2textbook_models.py —— 论文→教科书的数据模型
与 paper2textbook_pmt.py 中的提示词格式一一对应。
"""

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


# ============================================================
# 一、概念卡片（单篇论文拆解）
# ============================================================

class ConceptCard(_Base):
    name: str = Field(..., description='概念名称')
    desc: str = Field(..., description='概念描述')
    is_original: bool = Field(..., description='是否原创')
    excerpt: List[str] = Field(..., description='支撑该概念的原文摘录列表')


class MethodCard(_Base):
    name: str = Field(..., description='方法名称')
    desc: str = Field(..., description='方法描述')
    excerpt: List[str] = Field(..., description='支撑该方法定义的原文摘录列表')


class TheoremCard(_Base):
    name: str = Field(..., description='定理名称')
    desc: str = Field(..., description='定理陈述')
    excerpt: List[str] = Field(..., description='支撑该定理的原文摘录列表')


class FindingCard(_Base):
    name: str = Field(..., description='发现/结论名称')
    desc: str = Field(..., description='发现/结论描述')
    excerpt: List[str] = Field(..., description='支撑该发现的原文摘录列表')


class PaperConcepts(_Base):
    """单篇论文的拆解结果：一张可复用的概念卡片集合。"""
    paper: str = Field(..., description='论文标识（文件名或 ID）')
    desc: str = Field(..., description='整篇论文的概要描述')
    concepts: List[ConceptCard] = Field(..., description='概念卡片列表')
    methods: List[MethodCard] = Field(..., description='方法卡片列表')
    theorems: List[TheoremCard] = Field(..., description='定理卡片列表')
    findings: List[FindingCard] = Field(..., description='发现/结论卡片列表')


# ============================================================
# 二、论文聚类
# ============================================================

class PartClus(_Base):
    no: int = Field(..., description='聚类/分部的序号')
    title: str = Field(..., description='聚类标题')
    desc: str = Field(..., description='聚类描述')
    papers: List[str] = Field(..., description='归属于该聚类的论文标识列表')


# ============================================================
# 三、全书大纲（章 - 知识点）
# ============================================================

class OutlineSrc(_Base):
    paper: str = Field(..., description='知识点来源的论文标识')


class OutlineNode(_Base):
    no: int = Field(..., description='知识点序号')
    name: str = Field(..., description='知识点名称')
    desc: str = Field(..., description='知识点描述')
    role: str = Field(..., description='该知识点在书中的角色（如概念/方法/定理）')
    src: List[OutlineSrc] = Field(..., description='知识点来源论文列表')


class OutlineChapter(_Base):
    no: int = Field(..., description='章序号')
    name: str = Field(..., description='章标题')
    desc: str = Field(..., description='章描述')
    nodes: List[OutlineNode] = Field(..., description='章下的知识点列表')

class OutlineParts(_Base):
    no: int = Field(..., description='分部/卷序号')
    chapters: List[OutlineChapter] = Field(..., description='分部下的章列表')

# ============================================================
# 四、章节细纲
# ============================================================

class DetailSource(_Base):
    paper: str = Field(..., description='来源论文标识')
    role: str = Field(..., description='该来源在知识单元中的作用')


class ConceptUnit(_Base):
    no: int = Field(..., description='知识单元序号')
    name: str = Field(..., description='知识单元名称')
    points: List[str] = Field(..., description='要点列表')
    sources: List[DetailSource] = Field(..., description='来源论文及作用列表')
    excerpts: List[str] = Field(..., description='原文摘录列表')


class ConceptAnlsResult(_Base):
    units: List[ConceptUnit] = Field(..., description='概念分析得到的知识单元列表')


class RestDetailResult(_Base):
    learning_targets: List[str] = Field(..., description='学习目标列表')
    concept_map: List[str] = Field(..., description='概念关系图描述列表')
    life_analogy: List[str] = Field(..., description='生活化类比列表')
    summary: List[Dict[str, str]] = Field(..., description='章节小结条目列表')
    exercises: List[Dict[str, Any]] = Field(..., description='习题条目列表')


class ChapterDetail(ConceptAnlsResult, RestDetailResult):
    no: int = Field(..., description='章序号')


# ============================================================
# 五、辅助产物
# ============================================================

class GlossaryEntry(_Base):
    canonical: str = Field(..., description='规范术语名')
    aliases: List[str] = Field(..., description='同义词/别名列表')
    first_seen: str = Field(..., description='首次出现的位置')


class CitationAudit(_Base):
    citation_stats: List[Dict[str, Any]] = Field(..., description='引用统计条目列表')
    unsupported_claims: List[Dict[str, Any]] = Field(..., description='无支撑观点的列表')
    missing_concepts: List[Dict[str, Any]] = Field(..., description='缺失概念列表')


class PaperMeta(_Base):
    id: str = Field(..., description='论文唯一标识')
    file: str = Field(..., description='论文文件路径')
    title: str = Field(..., description='论文标题')
    authors: str = Field(..., description='论文作者')
    abstract: str = Field(..., description='论文摘要')

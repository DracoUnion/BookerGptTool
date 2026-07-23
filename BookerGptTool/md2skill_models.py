"""md2skill 数据模型 — Pydantic 对象，替代裸 dict/list。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SKUType(str, Enum):
    """知识单元类型"""

    FACTUAL = "factual"
    PROCEDURAL = "procedural"
    RELATIONAL = "relational"


class BookSchema(BaseModel):
    """Step 1: 从目录和前言推断出的知识结构"""

    book_type: str
    domains: List[str] = Field(default_factory=list)
    core_components: List[str] = Field(default_factory=list)
    skill_types: List[str] = Field(default_factory=list)


class RawSkill(BaseModel):
    """从 LLM 输出中解析出的单个技能"""

    name: str
    slug: str
    trigger: str
    domain: str = ""
    body: str = ""
    raw_text: str = ""

    # 可选：prompt 模板中的字段
    prerequisites: List[str] = Field(default_factory=list)
    source_ref: str = ""
    confidence: float = 0.0

    # 可选：叙事类 prompt 的字段
    characters: List[str] = Field(default_factory=list)
    timeline: str = ""
    prompt_version: str = ""

    # 编排器填充的元信息
    chunk_idx: int = -1
    raw_context: str = ""
    raw_content: str = ""

    # 分类后填充
    type: Optional[str] = None


class ChunkSkill(BaseModel):
    """Step 2 中间态：一个 chunk 及其提取出的技能列表"""

    content: str
    context: str
    raw_skills: List[RawSkill] = Field(default_factory=list)
    generated: bool = False

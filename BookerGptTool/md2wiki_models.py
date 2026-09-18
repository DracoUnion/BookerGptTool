from typing import List
from pydantic import BaseModel, Field


class WikiItem(BaseModel):
    """候选词条"""
    name: str = Field(..., description="词条名")
    type: str = Field(..., description="类型：person|event|concept|location|term")
    title: str = Field(default="", description="章节标题路径，如 'X > Y > Z'")
    origin: List[str] = Field(default_factory=list, description="原文引述段落列表")
    chunks: List[str] = Field(default_factory=list, description="相关原文素材（原始文本块）")
    draft: str = Field(default="", description="生成的 Wiki 词条草稿（Markdown）")


class WikiChunk(BaseModel):
    """切分后的文本块"""
    id: str = Field(..., description="文本块ID，如 chunk_001")
    chunk: str = Field(..., description="文本块内容")
    title: str = Field(default="", description="标题路径")
    items: List[WikiItem] = Field(default_factory=list, description="从该块抽取的候选词条")
    generated: bool = Field(default=False, description="是否已抽取候选词条")


class ChunkList(BaseModel):
    """切分结果"""
    chunks: List[WikiChunk] = Field(default_factory=list, description="切分后的文本块列表")


class CandidateItems(BaseModel):
    """候选词条抽取结果"""
    items: List[WikiItem] = Field(default_factory=list, description="从文本块抽取的候选词条列表")

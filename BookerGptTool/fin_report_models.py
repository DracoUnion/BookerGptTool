from typing import List, Dict, Optional
from pydantic import BaseModel, Field


class ReportMeta(BaseModel):
    """研报元数据"""
    title: Optional[str] = None
    publisher: Optional[str] = None
    time: Optional[str] = None
    industry: Optional[str] = None


class Fact(BaseModel):
    """研究员提取的单条事实"""
    fact_id: Optional[str] = None
    category: Optional[str] = None
    content: Optional[str] = None
    value: Optional[str] = None
    source: Optional[str] = None


class ResearcherOutput(BaseModel):
    """研究员 Agent 的完整提取结果"""
    report_meta: ReportMeta = Field(default_factory=ReportMeta)
    facts: List[Fact] = Field(default_factory=list)
    explicit_rating: Optional[str] = None
    explicit_risks: List[str] = Field(default_factory=list)


class DivergencePoint(BaseModel):
    """融合后的分歧点"""
    topic: Optional[str] = None
    bull_view: Optional[str] = None
    bear_view: Optional[str] = None


class FusionOutput(BaseModel):
    """融合仲裁官的完整输出结果"""
    consensus_facts: List[Fact] = Field(default_factory=list)
    divergence_points: List[DivergencePoint] = Field(default_factory=list)
    rating_distribution: Dict[str, int] = Field(default_factory=dict)
    merged_risks: List[str] = Field(default_factory=list)

from typing import List, Dict, Optional
from pydantic import BaseModel


class ReportMeta(BaseModel):
    """研报元数据"""
    title: Optional[str]
    publisher: Optional[str]
    time: Optional[str]
    industry: Optional[str]


class Fact(BaseModel):
    """研究员提取的单条事实"""
    fact_id: Optional[str]
    category: Optional[str]
    content: Optional[str]
    value: Optional[str]
    source: Optional[str]


class ResearcherOutput(BaseModel):
    """研究员 Agent 的完整提取结果"""
    report_meta: ReportMeta
    facts: List[Fact]
    explicit_rating: Optional[str]
    explicit_risks: List[str]


class DivergencePoint(BaseModel):
    """融合后的分歧点"""
    topic: Optional[str]
    bull_view: Optional[str]
    bear_view: Optional[str]


class FusionOutput(BaseModel):
    """融合仲裁官的完整输出结果"""
    consensus_facts: List[Fact]
    divergence_points: List[DivergencePoint]
    rating_distribution: Dict[str, int]
    merged_risks: List[str]


class OrchestratorResult(BaseModel):
    """多报告协调器的完整输出"""
    fused_data: FusionOutput
    bull_history: List[str]
    bear_history: List[str]
    final_verdict: str

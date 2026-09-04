from typing import List, Dict, Optional, Literal
from pydantic import BaseModel

class FundAnlsResult(BaseModel):
    institution: str
    industry: str
    date: str
    revenue_growth: int
    profit_growth: int
    growth_evidences: List[str]
    roe_trend: Literal["improving", "stable", "declining", "unknown"]
    capex_trend: Literal["expanding", "stable", "contracting", "unknown"]
    capex_evidences: List[str]
    margin_trend: Literal["improving", "stable", "declining", "unknown"]
    roe_margin_evidences: List[str]
    earnings_revision: Literal["upgraded", "unchanged", "downgraded", "unknown"]
    prospect_score: int
    key_risks: List[str]
    risk_evidences: List[str]

class ValueAnlsResult(BaseModel):
    institution: str
    industry: str
    date: str
    pe_percentile: int
    pb_percentile: int
    valuation_assessment: Literal["undervalued", "fair", "overvalued", "unknown"]
    valuation_evidences: List[str]
    institutional_flow: Literal["inflow", "neutral", "outflow", "unknown"]
    retail_flow: Literal["inflow", "neutral", "outflow", "unknown"]
    flow_evidences: List[str]
    crowding_status: Literal["low", "moderate", "high", "unknown"]
    crowding_evidences: List[str]
    valuation_score: int


class SentiAnlsResult(BaseModel):
    institution: str
    industry: str
    date: str
    market_style: Literal["growth", "value", "neutral", "unknown"]
    style_evidences: List[str]
    size_preference: Literal["large", "small", "neutral", "unknown"]
    turnover_heat: Literal["cold", "normal", "hot", "unknown"]
    turnover_evidences: List[str]
    analyst_consensus: Literal["bullish", "neutral", "bearish", "unknown"]
    analyst_evidences: List[str]
    momentum_direction: Literal["up", "down", "consolidating", "unknwon"]
    sentiment_score: int

####################################################################

class AnlsOutput(BaseModel):
    """研究员 Agent 的完整提取结果"""
    fundamental: FundAnlsResult
    value: ValueAnlsResult
    sentiment: SentiAnlsResult



class OrchestratorResult(BaseModel):
    """多报告协调器的完整输出"""
    analysis: AnlsOutput
    bull_history: List[str]
    bear_history: List[str]
    final_verdict: str

from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field

class FundAnlsResult(BaseModel):
    institution: str = Field(..., description="机构名称")
    industry: str = Field(..., description="行业名称")
    date: str = Field(..., description="报告日期")
    revenue_growth: int = Field(..., description="营收增长率（百分比，如10表示10%）")
    profit_growth: int = Field(..., description="利润增长率（百分比）")
    growth_evidences: List[str] = Field(default_factory=list, description="增长相关的原文证据")
    roe_trend: Literal["improving", "stable", "declining", "unknown"] = Field(..., description="ROE趋势：improving改善/stable稳定/declining下降/unknown未知")
    capex_trend: Literal["expanding", "stable", "contracting", "unknown"] = Field(..., description="资本支出趋势：expanding扩张/stable稳定/contracting收缩/unknown未知")
    capex_evidences: List[str] = Field(default_factory=list, description="资本支出相关证据")
    margin_trend: Literal["improving", "stable", "declining", "unknown"] = Field(..., description="毛利率趋势：improving改善/stable稳定/declining下降/unknown未知")
    roe_margin_evidences: List[str] = Field(default_factory=list, description="ROE和毛利率相关证据")
    earnings_revision: Literal["upgraded", "unchanged", "downgraded", "unknown"] = Field(..., description="盈利预测调整：upgraded上调/unchanged不变/downgraded下调/unknown未知")
    prospect_score: int = Field(..., description="前景评分（0-100）")
    key_risks: List[str] = Field(default_factory=list, description="关键风险点列表")
    risk_evidences: List[str] = Field(default_factory=list, description="风险相关证据")

class ValueAnlsResult(BaseModel):
    institution: str = Field(..., description="机构名称")
    industry: str = Field(..., description="行业名称")
    date: str = Field(..., description="报告日期")
    pe_percentile: int = Field(..., description="PE百分位（0-100）")
    pb_percentile: int = Field(..., description="PB百分位（0-100）")
    valuation_assessment: Literal["undervalued", "fair", "overvalued", "unknown"] = Field(..., description="估值判断：undervalued低估/fair合理/overvalued高估/unknown未知")
    valuation_evidences: List[str] = Field(default_factory=list, description="估值相关证据")
    institutional_flow: Literal["inflow", "neutral", "outflow", "unknown"] = Field(..., description="机构资金流向：inflow流入/neutral中性/outflow流出/unknown未知")
    retail_flow: Literal["inflow", "neutral", "outflow", "unknown"] = Field(..., description="散户资金流向：inflow流入/neutral中性/outflow流出/unknown未知")
    flow_evidences: List[str] = Field(default_factory=list, description="资金流向相关证据")
    crowding_status: Literal["low", "moderate", "high", "unknown"] = Field(..., description="拥挤度：low低/moderate中等/high高/unknown未知")
    crowding_evidences: List[str] = Field(default_factory=list, description="拥挤度相关证据")
    valuation_score: int = Field(..., description="估值评分（0-100）")


class SentiAnlsResult(BaseModel):
    institution: str = Field(..., description="机构名称")
    industry: str = Field(..., description="行业名称")
    date: str = Field(..., description="报告日期")
    market_style: Literal["growth", "value", "neutral", "unknown"] = Field(..., description="市场风格：growth成长/value价值/neutral中性/unknown未知")
    style_evidences: List[str] = Field(default_factory=list, description="市场风格相关证据")
    size_preference: Literal["large", "small", "neutral", "unknown"] = Field(..., description="市值偏好：large大盘/small小盘/neutral中性/unknown未知")
    turnover_heat: Literal["cold", "normal", "hot", "unknown"] = Field(..., description="换手率热度：cold冷淡/normal正常/hot火热/unknown未知")
    turnover_evidences: List[str] = Field(default_factory=list, description="换手率相关证据")
    analyst_consensus: Literal["bullish", "neutral", "bearish", "unknown"] = Field(..., description="分析师共识：bullish看好/neutral中性/bearish看空/unknown未知")
    analyst_evidences: List[str] = Field(default_factory=list, description="分析师观点相关证据")
    momentum_direction: Literal["up", "down", "consolidating", "unknown"] = Field(..., description="动量方向：up上涨/down下跌/consolidating震荡/unknown未知")
    sentiment_score: int = Field(..., description="情绪评分（0-100）")

####################################################################

class AnlsOutput(BaseModel):
    """研究员 Agent 的完整提取结果"""
    fundamental: FundAnlsResult = Field(..., description="基本面分析结果")
    value: ValueAnlsResult = Field(..., description="估值分析结果")
    sentiment: SentiAnlsResult = Field(..., description="情绪分析结果")


class JudgeResult(BaseModel):
    institution: str = Field(..., description="机构名称")
    industry: str = Field(..., description="行业名称")
    date: str = Field(..., description="报告日期")
    overall_score: int = Field(..., description="综合评分（0-100）")
    recommendation: Literal["overweight", "neutral", "underweight"] = Field(..., description="投资建议：overweight增持/neutral中性/underweight减持")
    key_drivers: List[str] = Field(default_factory=list, description="核心驱动因素")
    key_risks: List[str] = Field(default_factory=list, description="核心风险因素")
    conclusion: str = Field(..., description="结论总结")
    conclusio_evidences: List[str] = Field(default_factory=list, description="结论支持证据")


class OrchestratorResult(BaseModel):
    """多报告协调器的完整输出"""
    analysis: AnlsOutput = Field(..., description="分析结果")
    bull_history: List[str] = Field(default_factory=list, description="看多观点历史")
    bear_history: List[str] = Field(default_factory=list, description="看空观点历史")
    final_verdict: JudgeResult = Field(..., description="最终裁决结果")

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# ── 文献检索模型 ──────────────────────────────


class Author(BaseModel):
    """作者"""
    name: str = Field(..., description="作者姓名")
    source_id: str = Field('', description="作者ID")


class Citation(BaseModel):
    """一条真实文献引用记录（仅来自检索工具的返回）"""
    source_id: str = Field(..., description="来源ID，如 arXiv ID 或 S2 paperId")
    title: str = Field(..., description="论文标题")
    authors: List[str] = Field(default_factory=list, description="作者列表")
    year: Optional[int] = Field(None, description="发表年份")
    venue: str = Field('', description="发表刊物/会议")
    url: str = Field('', description="原文链接")
    doi: str = Field('', description="DOI")
    abstract: str = Field('', description="摘要")
    categories: List[str] = Field(default_factory=list, description="论文分类")
    verified: bool = Field(True, description="是否为检索工具返回的真实元数据")


class PaperSearchResult(BaseModel):
    """文献检索结果"""
    query: str = Field(..., description="检索词")
    papers: List[Citation] = Field(default_factory=list, description="检索到的论文列表")


class CitationGraphResult(BaseModel):
    """引用图谱（施引/被引）结果"""
    paper_id: str = Field(..., description="论文ID")
    direction: str = Field(..., description="citations（施引）或 references（被引）")
    papers: List[Citation] = Field(default_factory=list, description="关联论文列表")


# ── 知识检索模型 ──────────────────────────────


class KnowledgeHit(BaseModel):
    """一条命中的知识片段"""
    source: str = Field(..., description="来源文件")
    score: float = Field(0.0, description="相关度得分")
    snippet: str = Field('', description="命中片段")


class KnowledgeResult(BaseModel):
    """知识库检索结果"""
    query: str = Field(..., description="检索词")
    hits: List[KnowledgeHit] = Field(default_factory=list, description="命中的知识片段")


# ── 假设管理模型 ──────────────────────────────


class HypothesisCard(BaseModel):
    """候选假设卡"""
    id: str = Field(..., description="假设ID，如 hyp_001")
    question: str = Field(..., description="研究问题")
    prediction: str = Field(..., description="可检验预测")
    baselines: List[str] = Field(default_factory=list, description="基线方法")
    metrics: List[str] = Field(default_factory=list, description="评估指标")
    risks: List[str] = Field(default_factory=list, description="已知风险/威胁有效性")
    contribution: str = Field('', description="潜在贡献点")
    references: List[str] = Field(default_factory=list, description="支撑的引用 source_id 列表")


class HypothesisList(BaseModel):
    """候选假设卡列表"""
    hypotheses: List[HypothesisCard] = Field(default_factory=list, description="假设卡列表")


# ── 实验模型 ──────────────────────────────────


class ExperimentRun(BaseModel):
    """一次沙箱实验运行记录"""
    run_id: str = Field(..., description="实验运行ID")
    command: str = Field('', description="执行的命令")
    params: Dict[str, Any] = Field(default_factory=dict, description="实验参数")
    metrics: Dict[str, Any] = Field(default_factory=dict, description="实验指标")
    artifacts: List[str] = Field(default_factory=list, description="产物文件路径")
    logs: str = Field('', description="运行日志（stdout+stderr）")
    returncode: int = Field(-1, description="退出码，-1 表示超时")
    status: str = Field('pending', description="状态：pending/ok/error/timeout")
    commit: str = Field('', description="关联的 git commit")


class LogResult(BaseModel):
    """日志/实验记录保存结果"""
    run_id: str = Field(..., description="记录号")
    saved: bool = Field(True, description="是否保存成功")


# ── 引用校验 / LaTeX 模型 ─────────────────────


class CitationValidation(BaseModel):
    """LaTeX 引用 vs bibtex 校验结果"""
    valid: bool = Field(..., description="是否所有引用都有对应条目")
    missing_citations: List[str] = Field(default_factory=list, description="LaTeX 引用但 bibtex 缺失的 key")
    unused_citations: List[str] = Field(default_factory=list, description="bibtex 存在但未被引用的 key")
    issues: List[str] = Field(default_factory=list, description="其他问题")


class LatexCompileResult(BaseModel):
    """LaTeX 编译结果"""
    success: bool = Field(..., description="是否编译成功")
    pdf_path: str = Field('', description="生成的 PDF 路径")
    log: str = Field('', description="编译日志")


# ── 流水线状态模型 ────────────────────────────


class GateStatus(BaseModel):
    """单个阶段门禁状态"""
    stage: str = Field(..., description="门禁所属阶段")
    passed: bool = Field(False, description="是否通过")
    note: str = Field('', description="说明")


class PipelineState(BaseModel):
    """科研流水线状态机"""
    project_id: str = Field(..., description="项目ID")
    stage: str = Field('PROJECT_INIT', description="当前阶段")
    gates: Dict[str, bool] = Field(default_factory=dict, description="各门禁（G1~G6）是否通过")
    artifacts: List[str] = Field(default_factory=list, description="已产出产物文件路径")
    citations: List[str] = Field(default_factory=list, description="已确认引用 source_id 列表")
    results: List[str] = Field(default_factory=list, description="已确认实验结果 run_id 列表")
    approvals: List[str] = Field(default_factory=list, description="已获得的人类审批记录")
    next_action: str = Field('', description="下一步动作")


class ApprovalResult(BaseModel):
    """人类审批结果"""
    stage: str = Field(..., description="申请审批的阶段")
    approved: bool = Field(..., description="是否批准")
    comment: str = Field('', description="用户意见")
    record: str = Field('', description="审批记录标识")


# ── 最终交付模型 ──────────────────────────────


class FinalReport(BaseModel):
    """流水线最终交付报告"""
    status: str = Field(..., description="done|blocked|needs_approval")
    stage: str = Field(..., description="最终阶段")
    decision_summary: str = Field(..., description="决策摘要")
    artifacts: List[str] = Field(default_factory=list, description="产物列表")
    blockers: List[str] = Field(default_factory=list, description="未决阻塞")
    next_action: str = Field(..., description="下一步动作")

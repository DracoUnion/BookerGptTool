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


# ══════════════════════════════════════════════════════════════
# 精选固化工具集输出模型（四域：写作/润色、审稿/评审、学位论文、数模/期刊）
# ══════════════════════════════════════════════════════════════

# ── A. 写作/润色 ─────────────────────────────────────────────

class OptimizedRequest(BaseModel):
    """优化后的结构化请求（审稿/写作意图）"""
    purpose: str = Field(..., description="请求用途：review / writing / polish 等")
    perspective: str = Field('', description="视角，如学术期刊匿名审稿人/导师/同行")
    focus_points: List[str] = Field(default_factory=list, description="核心关注点")
    paper_type: str = Field('', description="论文类型")
    strictness: str = Field('', description="严格程度")
    task: str = Field(..., description="一段清晰完整的任务描述")


class TranslationResult(BaseModel):
    """翻译结果（含直译核对与修改日志）"""
    translation: str = Field(..., description="翻译后的文本")
    backcheck: str = Field('', description="中文直译核对（信息完整性核对）")
    change_log: List[str] = Field(default_factory=list, description="修改记录")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class PolishResult(BaseModel):
    """润色/缩写/扩写结果"""
    result: str = Field(..., description="处理后的文本")
    mode: str = Field(..., description="polish/condense/expand")
    change_log: List[str] = Field(default_factory=list, description="修改记录")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class DeaiResult(BaseModel):
    """去 AI 味/人味化结果"""
    result: str = Field(..., description="去除 AI 味后的文本")
    removed_blacklist: List[str] = Field(default_factory=list, description="清除或替换的 AI 高频词")
    aigc_score_before: float = Field(0.0, ge=0.0, le=1.0, description="处理前 AI 味得分")
    aigc_score_after: float = Field(0.0, ge=0.0, le=1.0, description="处理后 AI 味得分")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class LogicIssue(BaseModel):
    """单个逻辑/校对问题"""
    severity: str = Field(..., description="fatal/major/minor")
    loc: str = Field('', description="问题位置/段落")
    problem: str = Field(..., description="问题描述")
    suggestion: str = Field('', description="修改建议")


class LogicReport(BaseModel):
    """逻辑一致性/校对报告"""
    issues: List[LogicIssue] = Field(default_factory=list, description="问题清单")
    passed: bool = Field(False, description="是否有实质问题（无则通过）")
    summary: str = Field('', description="小结")


class StyleDna(BaseModel):
    """写作风格 DNA 画像"""
    signature_phrases: List[str] = Field(default_factory=list, description="个人签名短语")
    blacklist: List[str] = Field(default_factory=list, description="应避免的词/表达")
    avg_sentence_len: int = Field(0, description="平均句长")
    tone: str = Field('', description="语气特征")
    notes: str = Field('', description="风格要点说明")


class Abstract(BaseModel):
    """自包含摘要"""
    abstract: str = Field(..., description="150~250 词的自包含摘要")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class Section(BaseModel):
    """论文章节/小节"""
    heading: str = Field(..., description="标题")
    text: str = Field(..., description="正文")
    binding: List[str] = Field(default_factory=list, description="绑定的 citation_id / result_id")


class Introduction(BaseModel):
    """引言（按蓝图逐段）"""
    sections: List[Section] = Field(default_factory=list, description="引言段落")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class LitCluster(BaseModel):
    """文献综述聚类"""
    theme: str = Field(..., description="主题/流派")
    papers: List[str] = Field(default_factory=list, description="论文 source_id 列表")
    summary: str = Field('', description="该聚类综述")


class LitReview(BaseModel):
    """文献综述（聚类式）"""
    clusters: List[LitCluster] = Field(default_factory=list, description="聚类综述")
    gap_statement: str = Field('', description="研究空白陈述")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class PlanSection(BaseModel):
    """论文规划中的一节"""
    title: str = Field(..., description="节标题")
    duty: str = Field(..., description="本节职责")
    evidence: List[str] = Field(default_factory=list, description="所需证据")
    figures: List[str] = Field(default_factory=list, description="所需图表")


class PaperPlan(BaseModel):
    """论文规划（PAPER_PLAN）"""
    problem_lock: str = Field('', description="锁定的问题定义")
    contributions: List[str] = Field(default_factory=list, description="贡献点")
    outline: List[PlanSection] = Field(default_factory=list, description="逐节规划")
    figure_plan: List[str] = Field(default_factory=list, description="图表计划")
    citation_plan: List[str] = Field(default_factory=list, description="引用脚手架")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class SectionDraft(BaseModel):
    """生成的论文/章节草稿"""
    section_title: str = Field('', description="章节标题")
    content: str = Field(..., description="正文草稿（绑定 citation/result）")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class ReverseOutline(BaseModel):
    """反向大纲测试（抽每段首句验证连贯性）"""
    sentences: List[str] = Field(default_factory=list, description="每节/每段首句")
    flags: List[str] = Field(default_factory=list, description="发现的连贯性问题")
    coherent: bool = Field(True, description="是否连贯")


# ── B. 论文审稿/评审 ─────────────────────────────────────────

class ReviewDim(BaseModel):
    """审稿单维度评估"""
    name: str = Field(..., description="维度名")
    assessment: str = Field(..., description="评估")
    issues: List[str] = Field(default_factory=list, description="该维度问题")


class ReviewReport(BaseModel):
    """七大维度审稿报告"""
    dimensions: List[ReviewDim] = Field(default_factory=list, description="各维度评估")
    overall: str = Field('', description="总体判断")
    recommendation: str = Field('', description="accept/minor/major/reject")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class ClassifiedIssue(BaseModel):
    """结构性（A 类）问题"""
    desc: str = Field(..., description="问题描述")
    loc: str = Field('', description="位置/当前表述")
    nature: str = Field('', description="问题本质")
    fix: str = Field('', description="具体修改建议")
    effect: str = Field('', description="修改后效果")


class IssueClassification(BaseModel):
    """审稿问题 A/B 分类"""
    structural: List[ClassifiedIssue] = Field(default_factory=list, description="A 类结构性（必须处理）")
    expandable: List[str] = Field(default_factory=list, description="B 类无止境扩展型（丢弃）")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class DimScore(BaseModel):
    """成熟度五维单项评分"""
    dimension: str = Field(..., description="维度")
    score: float = Field(0.0, ge=0.0, le=1.0, description="0-1 分")
    note: str = Field('', description="说明")


class MaturityScore(BaseModel):
    """论文成熟度评分与可终止信号"""
    five_dim: List[DimScore] = Field(default_factory=list, description="五维评分")
    contribution_tag: str = Field('', description="贡献判断")
    limitation_tag: str = Field('', description="局限判断")
    terminable: bool = Field(False, description="贡献是否大于局限、可终止修改")
    signal_text: str = Field('', description="可终止信号文本")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class RebuttalPoint(BaseModel):
    """对单个审稿点的回复"""
    point: str = Field(..., description="审稿点")
    reply: str = Field(..., description="回复（缺数据标 [TBD]）")


class ReviewerReply(BaseModel):
    """对一位审稿人的回复"""
    reviewer: str = Field(..., description="审稿人标识")
    points: List[RebuttalPoint] = Field(default_factory=list, description="逐条回复")


class Rebuttal(BaseModel):
    """Rebuttal 回复信"""
    common_response: str = Field('', description="对共同关切的回应")
    per_reviewer: List[ReviewerReply] = Field(default_factory=list, description="分审稿人回应")
    notes: List[str] = Field(default_factory=list, description="备注/待补数据")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class AigcDim(BaseModel):
    """AIGC 检测单维度得分"""
    name: str = Field(..., description="维度：句式规整度/逻辑词密度/语态/词汇多样性/论证深度")
    score: float = Field(0.0, ge=0.0, le=1.0, description="0-1，越高越像 AI")
    note: str = Field('', description="说明")


class AigcScore(BaseModel):
    """AIGC 风险评分"""
    dimensions: List[AigcDim] = Field(default_factory=list, description="五维得分")
    overall: float = Field(0.0, ge=0.0, le=1.0, description="综合 AI 味得分")
    flagged_paras: List[str] = Field(default_factory=list, description="高风险段落摘录")
    priority: str = Field('', description="改写优先级 high/medium/low")
    ai_generated: bool = Field(True, description="该评分结果由 AI 辅助生成")


class CitationAuditEntry(BaseModel):
    """单条引用审计"""
    cite_key: str = Field(..., description="引用 key")
    decision: str = Field(..., description="KEEP/FIX/REPLACE/REMOVE")
    exists: bool = Field(False, description="bibtex 是否存在该条目")
    context_ok: str = Field('', description="上下文是否支撑声明")
    reason: str = Field('', description="判定理由")


class CitationAudit(BaseModel):
    """引用三元审计"""
    audits: List[CitationAuditEntry] = Field(default_factory=list, description="逐条审计")
    summary: str = Field('', description="审计小结")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class FraudFinding(BaseModel):
    """学术造假检测发现"""
    type: str = Field(..., description="图片复用/数据造假/拼接/统计异常/产出异常/引用异常")
    detail: str = Field(..., description="说明")
    evidence: str = Field('', description="证据")
    severity: str = Field('', description="high/medium/low")


class FraudReport(BaseModel):
    """学术打假/数据异常检测报告"""
    risk_level: str = Field(..., description="清白/存疑/高度可疑/实锤")
    findings: List[FraudFinding] = Field(default_factory=list, description="发现清单")
    disclaimer: str = Field('', description="免责声明")
    ai_generated: bool = Field(True, description="是否 AI 生成")


# ── C. 学位论文 ──────────────────────────────────────────────

class Chapter(BaseModel):
    """论文章"""
    title: str = Field(..., description="章标题")
    duties: str = Field('', description="本章职责")
    subsections: List[str] = Field(default_factory=list, description="小节规划")


class Outline(BaseModel):
    """论文大纲"""
    title: str = Field(..., description="论文题目")
    chapters: List[Chapter] = Field(default_factory=list, description="章节规划")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class LiteraturePool(BaseModel):
    """已验证文献池"""
    topic: str = Field(..., description="主题")
    papers: List[Citation] = Field(default_factory=list, description="文献（DOI 校验标记）")
    total: int = Field(0, description="总数")
    sources: List[str] = Field(default_factory=list, description="来源：arxiv/s2")


class ThreeLineTable(BaseModel):
    """三线表"""
    caption: str = Field('', description="表题")
    latex: str = Field('', description="LaTeX 三线表源码")
    csv: str = Field('', description="CSV 形式数据")


class FigureResult(BaseModel):
    """图生成结果"""
    path: str = Field('', description="输出路径")
    engine: str = Field('', description="matplotlib/PIL")
    spec: str = Field('', description="生成规格")
    notes: str = Field('', description="说明")


class MergeResult(BaseModel):
    """多份草稿合并结果"""
    final_md: str = Field('', description="合并后的 Markdown 全文")
    files_merged: List[str] = Field(default_factory=list, description="合并的文件")
    references_dedup: int = Field(0, description="去重的引用数")


class ReferenceEntry(BaseModel):
    """单条格式化参考文献"""
    style: str = Field('', description="gb/ieee/apa")
    text: str = Field('', description="格式化条目")


class FormattedRefs(BaseModel):
    """格式化参考文献列表"""
    style: str = Field(..., description="gb/ieee/apa")
    entries: List[ReferenceEntry] = Field(default_factory=list, description="条目")
    full_text: str = Field('', description="整体文本")


class Slide(BaseModel):
    """答辩 PPT 幻灯片"""
    title: str = Field(..., description="页标题")
    bullets: List[str] = Field(default_factory=list, description="要点")
    figure_hint: str = Field('', description="配图提示")


class DefenseDeck(BaseModel):
    """答辩 PPT 提纲（12-16 页）"""
    title: str = Field('', description="论文题目")
    slides: List[Slide] = Field(default_factory=list, description="幻灯片")
    ai_generated: bool = Field(True, description="是否 AI 生成")


# ── D. 数学建模/期刊图/排版 ─────────────────────────────────

class SubProblem(BaseModel):
    """数模子问题"""
    id: str = Field('', description="子问题编号")
    description: str = Field(..., description="子问题描述")
    data_needs: str = Field('', description="所需数据")
    approach: str = Field('', description="建议方法")


class ModelingAnalysis(BaseModel):
    """数模问题分析"""
    sub_problems: List[SubProblem] = Field(default_factory=list, description="子问题拆解")
    assumptions: List[str] = Field(default_factory=list, description="假设/敏感性")
    overall_route: str = Field('', description="总体建模路线")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class ModelPlan(BaseModel):
    """建模方案"""
    objective: str = Field(..., description="目标函数/目标")
    variables: List[str] = Field(default_factory=list, description="变量")
    constraints: List[str] = Field(default_factory=list, description="约束")
    chosen_method: str = Field('', description="选定方法")
    metrics: List[str] = Field(default_factory=list, description="评估指标")
    risks: List[str] = Field(default_factory=list, description="风险/灵敏度")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class ModelCode(BaseModel):
    """生成的模型代码"""
    code: str = Field(..., description="Python 代码")
    filenames: List[str] = Field(default_factory=list, description="建议文件名")
    run_command: str = Field('', description="运行命令")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class DiagramCode(BaseModel):
    """流程图源码"""
    kind: str = Field(..., description="mermaid/plantuml/dot")
    source: str = Field(..., description="图源码")
    render_hint: str = Field('', description="渲染提示")


class FigurePromptLayer(BaseModel):
    """AI 生图提示词分层"""
    scope: str = Field(..., description="global/section/label/style")
    prompt: str = Field('', description="该层英文提示词")


class AiFigurePrompt(BaseModel):
    """AI 生图四层提示词"""
    palette: str = Field('', description="配色方案")
    layers: List[FigurePromptLayer] = Field(default_factory=list, description="提示词分层")
    style_specs: str = Field('', description="风格规格")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class FigureCheckItem(BaseModel):
    """单张图质量检查"""
    path: str = Field(..., description="图路径")
    width_px: int = Field(0, description="宽")
    height_px: int = Field(0, description="高")
    dpi: float = Field(0.0, description="DPI")
    ok: bool = Field(True, description="是否合规")
    issues: List[str] = Field(default_factory=list, description="问题")


class FigureCheck(BaseModel):
    """图表 QA 审计"""
    files: List[FigureCheckItem] = Field(default_factory=list, description="逐图检查")


class VenueTeX(BaseModel):
    """按期刊/会议模板输出的 LaTeX"""
    preamble: str = Field('', description="导言区/模板设置")
    body: str = Field('', description="正文 LaTeX")
    notes: List[str] = Field(default_factory=list, description="说明/待办")
    ai_generated: bool = Field(True, description="是否 AI 生成")

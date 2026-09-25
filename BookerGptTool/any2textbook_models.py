# -*- coding: utf-8 -*-
"""
paper2textbook_models.py —— 论文→教科书的数据模型
与 paper2textbook_pmt.py 中的提示词格式一一对应。
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
from enum import Enum

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
    paper: str = Field('', description='论文标识（文件名或 ID）')
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


########################################################
# 素材内容类型判断模型
########################################################

class MaterialContentType(str, Enum):
    """输入素材的整体内容类型，决定使用哪个工作流"""
    ACADEMIC_PAPERS = "academic_papers"      # 多篇同领域学术论文 -> workflow 1: paper2textbook
    ARTICLES_NOTES = "articles_notes"        # 零散文章/笔记/转录 -> workflow 2: article2book
    SINGLE_PAPER = "single_paper"            # 单篇论文 -> workflow 3: paper2course
    LONG_DOCUMENT = "long_document"          # 单篇长文档/研报/白皮书 -> workflow 4: report2lecture
    TEACHING_PAPER = "teaching_paper"        # 单篇论文用于教学 -> workflow 5: teachfrompaper
    REQUIREMENTS_ONLY = "requirements_only"  # 仅有主题/受众/课时需求，无现成素材 -> workflow 6: kougiforge


class MaterialTypeJudgment(_Base):
    """素材整体内容类型判断结果"""
    content_type: MaterialContentType = Field(..., description='判断出的素材内容类型')
    confidence: float = Field(ge=0.0, le=1.0, description='置信度 0-1')
    reason: str = Field(..., description='判断理由')
    workflow: str = Field(..., description='推荐使用的工作流标识')
    workflow_desc: str = Field(..., description='工作流中文描述')
    material_count: int = Field(..., description='素材文件数量')
    total_word_count: int = Field(0, description='预估总字数')
    key_characteristics: List[str] = Field(default_factory=list, description='关键特征列表')
    suggested_preprocess: List[str] = Field(default_factory=list, description='建议的预处理步骤')


########################################################
# 七、Textbook Anything 兼容模型
########################################################


class Tier(str, Enum):
    LITE = "lite"
    STANDARD = "standard"
    DEEP = "deep"

    @property
    def review_rounds(self) -> int:
        return {Tier.LITE: 1, Tier.STANDARD: 2, Tier.DEEP: 3}[self]

    @property
    def label_zh(self) -> str:
        return {Tier.LITE: "精简", Tier.STANDARD: "标准", Tier.DEEP: "深入"}[self]

    @classmethod
    def parse(cls, value: Union[str, "Tier"]) -> "Tier":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        aliases = {
            "lite": cls.LITE,
            "精简": cls.LITE,
            "standard": cls.STANDARD,
            "标准": cls.STANDARD,
            "deep": cls.DEEP,
            "深入": cls.DEEP,
        }
        if normalized not in aliases:
            raise ValueError(f"Unknown tier: {value}")
        return aliases[normalized]


class Language(str, Enum):
    ZH = "zh"
    ZH_CN = "zh-CN"
    EN = "en"
    EN_US = "en-US"

    @classmethod
    def parse(cls, value: Union[str, "Language"]) -> "Language":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        aliases = {
            "zh": cls.ZH,
            "zh-cn": cls.ZH_CN,
            "cn": cls.ZH_CN,
            "中文": cls.ZH_CN,
            "en": cls.EN,
            "en-us": cls.EN_US,
            "english": cls.EN,
            "英文": cls.EN,
        }
        if normalized not in aliases:
            raise ValueError(f"Unknown language: {value}")
        return aliases[normalized]

    @property
    def prompt_name(self) -> str:
        return "Chinese" if self.value.startswith("zh") else "English"


class GapStatus(str, Enum):
    DEMONSTRATED = "demonstrated"
    PARTIAL = "partial"
    UNASSESSED = "unassessed"


class RequirementStatus(str, Enum):
    COMPLETE = "complete"
    NEEDS_REVISION = "needs revision"
    BLOCKED = "blocked"


class FindingSeverity(str, Enum):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"
    CHECKED = "checked"


class SourceKind(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    LATEX = "latex"
    PDF = "pdf"
    HTML = "html"
    JSON = "json"
    OTHER = "other"


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    HTML = "html"
    JSON = "json"
    ZIP = "zip"


class ReaderProfile(_Base):
    baseline: str = Field(
        "University STEM learner with general mathematical literacy; specialist prerequisites are unassessed.",
        description='读者基线水平描述',
    )
    goals: List[str] = Field(default_factory=list, description='读者学习目标列表')
    language: Language = Field(Language.ZH_CN, description='教程语言')
    study_time_minutes: Optional[int] = Field(None, description='可用的学习时长（分钟）')
    constraints: List[str] = Field(default_factory=list, description='约束条件列表')
    assumptions: List[str] = Field(default_factory=list, description='假设列表')


class SourceRef(_Base):
    id: str = Field(..., description='来源唯一标识')
    path: Optional[str] = Field(None, description='来源文件路径')
    title: str = Field(..., description='来源标题')
    kind: SourceKind = Field(SourceKind.TEXT, description='来源类型')
    sha256: str = Field(..., description='来源内容的 SHA256 校验值')
    text: str = Field(..., description='来源原文文本')
    source_cutoff: Optional[str] = Field(None, description='来源内容截止时间')


class PrerequisiteGap(_Base):
    concept: str = Field(..., description='前置概念名称')
    why_needed: str = Field(..., description='为什么需要该前置概念')
    evidence: str = Field(..., description='存在缺口的证据')
    status: GapStatus = Field(GapStatus.UNASSESSED, description='缺口评估状态')
    teaching_action: str = Field(..., description='应对缺口的教学动作')


class Requirement(_Base):
    id: str = Field(..., description='需求唯一标识')
    outcome: str = Field(..., description='学习成果描述')
    foundation: str = Field(..., description='依赖的基础')
    source_ids: List[str] = Field(default_factory=list, description='支撑来源 ID 列表')
    emphasis: str = Field(..., description='教学侧重点')
    explanation_or_visual: str = Field(..., description='解释或视觉呈现方式')
    practice: str = Field(..., description='练习方式')
    assessment: str = Field(..., description='评估方式')
    status: RequirementStatus = Field(RequirementStatus.COMPLETE, description='需求完成状态')


class SourceRecord(_Base):
    id: str = Field(..., description='来源唯一标识')
    title: str = Field(..., description='来源标题')
    version: str = Field(..., description='来源版本')
    location: str = Field(..., description='来源位置/出处')
    sections_read: List[str] = Field(default_factory=list, description='已读章节列表')
    supported_claims: List[str] = Field(default_factory=list, description='支持的论断列表')
    access_limits: str = Field("", description='访问限制说明')


class VisualSpec(_Base):
    id: str = Field(..., description='视觉规格唯一标识')
    purpose: str = Field(..., description='视觉的目的/用途')
    visual_type: str = Field(..., description='视觉类型')
    alt_text: str = Field(..., description='替代文本')
    caption: str = Field(..., description='图注')
    data_source: str = Field(..., description='数据来源')
    chapter_ids: List[str] = Field(default_factory=list, description='关联章节 ID 列表')


class Exercise(_Base):
    id: str = Field(..., description='练习唯一标识')
    prompt: str = Field(..., description='练习提示')
    hint: str = Field("", description='提示')
    solution: str = Field(..., description='解答')
    rationale: str = Field(..., description='设计理由')
    changed_condition: str = Field("", description='变体条件')
    chapter_ids: List[str] = Field(default_factory=list, description='关联章节 ID 列表')


class ChapterPlan(_Base):
    id: str = Field(..., description='章节计划唯一标识')
    title: str = Field(..., description='章节标题')
    focus_question: str = Field(..., description='章节焦点问题')
    outcome: str = Field(..., description='章节学习成果')
    prerequisite_ids: List[str] = Field(default_factory=list, description='前置章节 ID 列表')
    source_ids: List[str] = Field(default_factory=list, description='来源 ID 列表')
    estimated_words: int = Field(ge=200, description='预计字数')
    visual_ids: List[str] = Field(default_factory=list, description='视觉 ID 列表')
    exercise_ids: List[str] = Field(default_factory=list, description='练习 ID 列表')


class TeachingBrief(_Base):
    id: str = Field(..., description='教学简报唯一标识')
    subject: str = Field(..., description='教学主题')
    scope: str = Field(..., description='教学范围')
    out_of_scope: List[str] = Field(default_factory=list, description='范围之外的主题列表')
    reader: ReaderProfile = Field(default_factory=ReaderProfile, description='读者画像')
    tier: Tier = Field(Tier.STANDARD, description='模型预算层级')
    language: Language = Field(Language.ZH_CN, description='教程语言')
    source_cutoff: Optional[str] = Field(None, description='来源内容截止时间')
    inputs: List[str] = Field(default_factory=list, description='输入材料列表')
    deliverables: List[str] = Field(default_factory=list, description='交付物列表')
    hard_limits: List[str] = Field(default_factory=list, description='硬性限制列表')
    interview_skipped: bool = Field(False, description='是否跳过访谈')
    interview_answers: List[str] = Field(default_factory=list, description='访谈答案列表')
    assumptions: List[str] = Field(default_factory=list, description='假设列表')
    review_rounds: int = Field(2, description='审查轮次数')
    gaps: List[PrerequisiteGap] = Field(default_factory=list, description='前置缺口列表')
    requirements: List[Requirement] = Field(default_factory=list, description='需求列表')
    sources: List[SourceRecord] = Field(default_factory=list, description='来源记录列表')


class DependencyNode(_Base):
    concept: str = Field(..., description='概念名称')
    used_by: str = Field(..., description='被谁使用')
    required: bool = Field(..., description='是否必需')
    source_ids: List[str] = Field(default_factory=list, description='支撑来源 ID 列表')
    teach_at: str = Field(..., description='在何处讲授')


class ResearchPlan(_Base):
    dependency_map: List[DependencyNode] = Field(..., description='依赖图节点列表')
    sources: List[SourceRecord] = Field(..., description='来源记录列表')
    unresolved_questions: List[str] = Field(default_factory=list, description='未解决的问题列表')


class TutorialDesign(_Base):
    chapters: List[ChapterPlan] = Field(..., description='章节计划列表')
    visuals: List[VisualSpec] = Field(..., description='视觉规格列表')
    exercises: List[Exercise] = Field(..., description='练习列表')
    cross_references: List[str] = Field(default_factory=list, description='交叉引用列表')


class SectionDraft(_Base):
    chapter_id: str = Field(..., description='所属章节 ID')
    heading: str = Field(..., description='标题')
    body_markdown: str = Field(..., description='正文（Markdown）')
    citations: List[str] = Field(default_factory=list, description='引用列表')
    checks: List[str] = Field(default_factory=list, description='检查项列表')


class SectionRevision(_Base):
    chapter_id: str = Field(..., description='所属章节 ID')
    replacement_markdown: Optional[str] = Field(None, description='替换后的 Markdown')
    corrections: List[str] = Field(default_factory=list, description='修正内容列表')


class RevisionRequest(_Base):
    revisions: List[SectionRevision] = Field(..., description='章节修订列表')
    unresolved_items: List[str] = Field(default_factory=list, description='未解决项列表')


class ReviewFinding(_Base):
    severity: FindingSeverity = Field(..., description='问题严重程度')
    location: str = Field(..., description='问题位置')
    issue: str = Field(..., description='问题描述')
    evidence: str = Field(..., description='证据')
    correction: str = Field(..., description='修正建议')
    status: RequirementStatus = Field(RequirementStatus.COMPLETE, description='需求完成状态')


class RoundRecord(_Base):
    number: int = Field(..., description='审查轮次序号')
    focus: str = Field(..., description='本轮审查焦点')
    findings: List[ReviewFinding] = Field(..., description='审查发现列表')
    requirements_status: List[Requirement] = Field(..., description='需求状态列表')
    artifact_checks: List[str] = Field(default_factory=list, description='产物检查项列表')
    learner_checks: List[str] = Field(default_factory=list, description='学习者检查项列表')
    blocked_items: List[str] = Field(default_factory=list, description='阻塞项列表')


class TutorialDocument(_Base):
    title: str = Field(..., description='教程标题')
    subtitle: str = Field(..., description='教程副标题')
    language: Language = Field(..., description='教程语言')
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description='生成时间（ISO 8601）',
    )
    brief: TeachingBrief = Field(..., description='教学简报')
    research: ResearchPlan = Field(..., description='研究计划')
    design: TutorialDesign = Field(..., description='教程设计')
    sections: List[SectionDraft] = Field(..., description='章节草稿列表')
    review_records: List[RoundRecord] = Field(default_factory=list, description='审查记录列表')
    delivery_notes: List[str] = Field(default_factory=list, description='交付说明列表')


class DeliveryFile(_Base):
    path: str = Field(..., description='文件路径')
    format: OutputFormat = Field(..., description='输出格式')
    bytes: int = Field(..., description='文件大小（字节）')
    sha256: str = Field(..., description='文件 SHA256 校验值')


class DeliveryManifest(_Base):
    output_dir: str = Field(..., description='输出目录')
    formats: List[OutputFormat] = Field(..., description='输出格式列表')
    files: List[DeliveryFile] = Field(..., description='交付文件列表')
    checks: List[str] = Field(..., description='校验项列表')
    unresolved_limits: List[str] = Field(default_factory=list, description='未解决的限制列表')


class CLIResult(_Base):
    success: bool = Field(..., description='是否成功')
    output_dir: Optional[str] = Field(None, description='输出目录')
    manifest: Optional[str] = Field(None, description='交付清单路径')
    message: str = Field(..., description='结果消息')


########################################################
# article2book 兼容模型：内容资产重组
########################################################

class SourceKindArticle(str, Enum):
    """article2book 支持的素材来源类型"""
    MARKDOWN = "markdown"
    MDX = "mdx"
    TXT = "txt"
    SRT = "srt"
    VTT = "vtt"
    DOCX = "docx"
    PDF = "pdf"
    NOTE = "note"           # 短笔记/卡片/Obsidian
    TRANSCRIPT = "transcript"  # 字幕/逐字稿/直播稿
    INTERVIEW = "interview"    # 访谈稿/问答记录
    SLIDES = "slides"          # 课件/大纲/讲义
    MINUTES = "minutes"        # 会议纪要/项目记录
    CASE = "case"              # 案例材料
    OTHER = "other"


class PreprocessStatus(str, Enum):
    """预处理状态"""
    READY = "ready"              # 可直接通读
    NEED_TRANSCRIBE = "need_transcribe"  # 需转写(音频/视频)
    NEED_OCR = "need_ocr"        # 需 OCR(扫描件/图片型PDF)
    NEED_CONVERT = "need_convert"  # 需转换格式
    SKIP = "skip"                # 暂不纳入(配图/附件/自动生成)


class ArticleReadingNote(_Base):
    """单份素材的 Agent 通读笔记"""
    file_path: str = Field(..., description='素材文件路径')
    title: str = Field(..., description='素材标题')
    summary: str = Field(..., description='一句话摘要')
    core_question: str = Field(..., description='核心问题：它在回答什么')
    key_judgment: str = Field(..., description='关键判断：真正有价值的观点')
    depth: Literal["high", "medium", "low"] = Field(..., description='深度判断')
    suggested_destination: Literal["chapter", "course_unit", "handbook_entry", "kb_entry", "case", "appendix", "exclude"] = Field(..., description='建议去向')
    possible_shape: Literal["book", "booklet", "course", "series", "handbook", "kb", "pool"] = Field(..., description='可能形态')
    risks: List[str] = Field(default_factory=list, description='风险提示: 时效性/重复/口语化/格式预处理/其他')
    screening_conclusion: Literal["retain", "demote", "exclude"] = Field(..., description='筛选结论: 保留/降权/排除')
    reason: str = Field(..., description='判断理由')


class ContentScreeningResult(_Base):
    """内容筛选结果：保留/降权/排除"""
    retained: List[ArticleReadingNote] = Field(..., description='保留的素材')
    demoted: List[ArticleReadingNote] = Field(..., description='降权的素材(仅作案例/附录/练习)')
    excluded: List[ArticleReadingNote] = Field(..., description='排除的素材')
    screening_principles: str = Field(..., description='筛选原则说明')


class ContentShapeJudgment(_Base):
    """内容形态判断结果"""
    best_shape: Literal["book", "booklet", "course", "series", "handbook", "kb", "pool"] = Field(..., description='最佳内容形态')
    reason: str = Field(..., description='推荐理由')
    not_recommended_shapes: List[str] = Field(default_factory=list, description='不建议的形态')
    not_recommended_reasons: List[str] = Field(default_factory=list, description='不建议理由')
    if_force_book_need: str = Field("", description='如果一定要成书，需要先补足什么')


class BookViabilityDimension(_Base):
    """成书可行性评估维度"""
    name: str = Field(..., description='维度名称')
    score: int = Field(ge=1, le=5, description='1-5分')
    evidence: str = Field(..., description='具体素材依据')


class BookViabilityAssessment(_Base):
    """成书可行性评估"""
    dimensions: List[BookViabilityDimension] = Field(..., description='7个评分维度')
    total_score: int = Field(..., description='总分')
    conclusion: Literal["ready", "potential", "defer", "not_recommended"] = Field(..., description='成书结论')
    recommended_shape: str = Field(..., description='推荐内容形态')
    alternative_path: str = Field(..., description='替代路径建议')
    next_steps: List[str] = Field(..., description='进入下一阶段前最需要补写/删改/预处理的内容')


class PlanningOpinion(_Base):
    """书稿策划意见"""
    # 一、结论
    best_shape: str = Field(..., description='最佳内容形态')
    book_worthiness: Literal["worth", "potential", "not_recommended"] = Field(..., description='是否值得成书')
    conclusion_type: Literal["ready", "needs_rewrite", "not_recommended"] = Field(..., description='结论类型')
    one_line_judgment: str = Field(..., description='一句话总判断')

    # 二、这批素材真正适合做成什么
    recommended_shape: str = Field(..., description='推荐主形态')
    shape_reason: str = Field(..., description='推荐理由')
    not_recommended_shapes: List[str] = Field(default_factory=list, description='不建议走的形态')
    not_recommended_reasons: List[str] = Field(default_factory=list, description='不建议理由')
    if_force_book_need: str = Field("", description='如果一定要成书，需要先补足什么')

    # 三、主命题、目标读者与定位
    core_proposition: str = Field(..., description='推荐主命题')
    target_reader: str = Field(..., description='目标读者')
    reader_problem: str = Field(..., description='读者最想解决的问题')
    differentiation: str = Field(..., description='与常见同类内容的差异')

    # 四、推荐标题或产品名方向
    recommended_title: str = Field(..., description='推荐名称')
    subtitle: str = Field("", description='副标题')
    alt_title_1: str = Field("", description='备选1')
    alt_title_2: str = Field("", description='备选2')

    # 五、推荐结构草案
    shape_description: str = Field(..., description='推荐产物')
    structure_logic: str = Field(..., description='结构逻辑')
    toc_draft: List[str] = Field(..., description='目录/单元/栏目草案')

    # 六、最重要的删改动作
    to_retain: List[str] = Field(default_factory=list, description='建议保留')
    to_delete: List[str] = Field(default_factory=list, description='建议删除')
    to_merge_rewrite: List[str] = Field(default_factory=list, description='建议合并重写')
    to_supplement: List[str] = Field(default_factory=list, description='建议补写')
    retain_principles: str = Field(..., description='保留/合并/排除原则')

    # 七、转化路径
    steps: List[str] = Field(..., description='转化步骤')
    risks: List[str] = Field(default_factory=list, description='风险点')

    # 八、如果确认推进，第二阶段将怎么写
    next_product: str = Field(..., description='下一步产物')
    default_output_file: str = Field(..., description='默认输出文件')
    writing_approach: str = Field(..., description='写作方式')
    will_split: bool = Field(False, description='是否拆分')
    start_from: List[str] = Field(default_factory=list, description='预计先从哪几章/单元/条目起草')


class SourceMaterial(_Base):
    """素材清单项"""
    file_path: str = Field(..., description='文件路径')
    title: str = Field("", description='标题')
    source_kind: SourceKindArticle = Field(..., description='素材类型')
    preprocess_status: PreprocessStatus = Field(..., description='预处理状态')
    preprocess_note: str = Field("", description='预处理备注')
    word_count: int = Field(0, description='字数估算')
    batch_no: int = Field(0, description='所属批次')


class ArticleInventory(_Base):
    """素材清单（脚本生成的基础索引）"""
    materials: List[SourceMaterial] = Field(..., description='素材列表')
    total_count: int = Field(..., description='总数')
    readable_count: int = Field(..., description='可直接通读数')
    need_preprocess_count: int = Field(..., description='需预处理数')
    skip_count: int = Field(..., description='暂不纳入数')


# ============================================================
# 九、paper-to-course 兼容工作流：论文 → 交互式 HTML 课程 + Markdown + PPTX
# ============================================================

class CoursePaperInfo(_Base):
    """论文主题验证结果（paper-to-course Step 0）"""
    title: str = Field(..., description='论文原标题')
    authors: str = Field(..., description='作者/机构')
    abstract: str = Field(..., description='摘要（核心发现一句话）')
    keywords: List[str] = Field(default_factory=list, description='关键词 / CCS Concepts')
    domain: str = Field(..., description='判断领域，如 CV / NLP / RL / 安全')


class CourseModuleSpec(_Base):
    """单个课程模块的规划（paper-to-course Step 2）"""
    id: str = Field(..., description='模块ID，如 module-01')
    slug: str = Field(..., description='模块slug，如 problem')
    title: str = Field(..., description='模块标题，如 问题与动机')
    outline: List[str] = Field(default_factory=list, description='模块内容要点列表')


class CoursePlan(_Base):
    """课程目录结构规划（paper-to-course Step 2）"""
    course_name: str = Field(..., description='课程目录名（英文，如 3dgs-course）')
    course_title: str = Field(..., description='课程标题')
    subtitle: str = Field('', description='副标题 / 会议 / 年份')
    modules: List[CourseModuleSpec] = Field(..., description='6 个模块规划（problem/evolution/comparison/method/experiments/limitations）')


class CourseModule(_Base):
    """单个 HTML 课程模块（paper-to-course Step 3）"""
    id: str = Field(..., description='模块ID，如 module-01')
    slug: str = Field(..., description='模块slug，如 problem')
    title: str = Field(..., description='模块标题')
    html: str = Field(..., description='模块 HTML 内容（使用设计系统 CSS class，禁止内联样式）')


class CourseSlide(_Base):
    """单页幻灯片配置（paper-to-course Step 4）"""
    type: str = Field(..., description='类型：title/outline/content/flow/table/bars/stats/formula/timeline/summary/limitations')
    title: str = Field('', description='标题')
    subtitle: str = Field('', description='副标题')
    note: str = Field('', description='演讲者备注')
    layout: str = Field('', description='content 页布局：bullets/cards-2/cards-3/cards-4/steps/grid-2x2')
    items: List[Any] = Field(default_factory=list, description='outline/content/bars/timeline/summary 等页的列表数据')
    cards: List[Any] = Field(default_factory=list, description='content 页卡片数据')
    steps: List[Any] = Field(default_factory=list, description='flow 页步骤数据')
    headers: List[str] = Field(default_factory=list, description='table 页表头')
    rows: List[List[Any]] = Field(default_factory=list, description='table 页行数据')
    highlightRows: List[int] = Field(default_factory=list, description='table 页高亮行索引')
    stats: List[Any] = Field(default_factory=list, description='stats 页大数字统计')
    formula: str = Field('', description='formula 页公式')
    lines: List[Any] = Field(default_factory=list, description='formula 页逐行通俗解释')
    limitations: List[str] = Field(default_factory=list, description='limitations 页当前局限性')
    futureWork: List[str] = Field(default_factory=list, description='limitations 页未来研究方向')


class SlidesConfig(_Base):
    """PPTX 演示文稿配置（paper-to-course Step 4，通常 16 页）"""
    title: str = Field(..., description='论文标题')
    subtitle: str = Field('', description='副标题 / 会议 / 年份')
    slides: List[CourseSlide] = Field(..., description='幻灯片配置列表')


class CourseBundle(_Base):
    """课程交付包（paper-to-course Step 5 渲染结果）"""
    course_name: str = Field(..., description='课程目录名')
    index_html: str = Field(..., description='index.html 全文（含 6 个模块 HTML 与设计系统内联样式）')
    readme_md: str = Field(..., description='README.md 全文（Markdown 版课程文档）')
    slides_config_json: str = Field(..., description='slides-config.json 全文')
    build_sh: str = Field('', description='build.sh 打包脚本内容')


# ============================================================
# 十、report-to-lecture 兼容工作流：文章/研报/论文/白皮书 → 高保真讲义
# ============================================================

class LectureConfig(_Base):
    """讲义生成护栏（保真/长度/覆盖/粒度/展开参数）"""
    fidelity_mode: str = Field('balanced', description='保真模式：balanced / full-literal / teaching-first')
    min_length_ratio: float = Field(0.8, description='输出长度与原文的最小比例下限')
    min_coverage_ratio: float = Field(0.8, description='信息点覆盖率的最小比例下限')
    coverage_granularity: str = Field('medium', description='覆盖粒度：coarse / medium / fine')
    expansion_depth: str = Field('standard', description='展开深度：minimal / standard / deep')


class LectureSection(_Base):
    """讲义：原文的一个章节/小节"""
    title: str = Field(..., description='章节标题')
    purpose: str = Field(..., description='该部分讲什么、目的是什么')
    key_points: List[str] = Field(default_factory=list, description='关键点')


class LectureDocStructure(_Base):
    """讲义：文档结构拆分（报告/论文/白皮书）"""
    doc_title: str = Field(..., description='文档标题')
    author_source: str = Field('', description='作者 / 机构 / 发布时间')
    sections: List[LectureSection] = Field(..., description='章节结构列表')
    figures: List[str] = Field(default_factory=list, description='图表 / 表格 / 图形清单')
    key_conclusions: List[str] = Field(default_factory=list, description='关键结论段')


class LectureInfoPoint(_Base):
    """讲义：单个原子信息点"""
    id: str = Field(..., description='信息点ID，如 P1 / P2')
    kind: str = Field(..., description='类型：关键结论/关键事实/定义/因果链/方法步骤/风险提示/对比与取舍')
    text: str = Field(..., description='信息点内容')
    location: str = Field('', description='章节 / 小节定位')


class CoverageLedger(_Base):
    """讲义：信息点覆盖率账本（Coverage Ledger）"""
    points: List[LectureInfoPoint] = Field(..., description='原子信息点列表')
    total_count: int = Field(..., description='信息点总数')


class ClaimEvidenceItem(_Base):
    """讲义：Claim-Evidence 映射项"""
    claim: str = Field(..., description='论断（Claim）')
    evidence: str = Field(..., description='证据（Evidence）')
    location: str = Field(..., description='在原文中的定位')
    notes: str = Field('', description='备注 / 假设 / 证据强度分级')


class ClaimEvidenceMap(_Base):
    """讲义：Claim-Evidence 映射账本"""
    items: List[ClaimEvidenceItem] = Field(default_factory=list, description='映射列表')


class LectureCoverageReport(_Base):
    """讲义：覆盖率与长度检查报告"""
    length_ratio: float = Field(..., description='输出长度 / 原文长度比例')
    coverage_ratio: float = Field(..., description='已覆盖信息点比例')
    length_ok: bool = Field(..., description='是否达到长度下限')
    coverage_ok: bool = Field(..., description='是否达到覆盖率下限')
    missing_points: List[str] = Field(default_factory=list, description='缺失或覆盖不足的信息点')
    suggestions: List[str] = Field(default_factory=list, description='补全建议')
    passed: bool = Field(False, description='是否通过全部护栏')


class LectureDeliverable(_Base):
    """讲义：最终交付物"""
    title: str = Field(..., description='讲义标题')
    lecture: str = Field(..., description='讲义主体（Markdown，按输出模板）')
    coverage: LectureCoverageReport = Field(..., description='覆盖率报告')


# ============================================================
# 十一、teach-from-paper 兼容工作流：论文 → 教学包（讲义/要点/幻灯片骨架/讨论题/习题简介）
# ============================================================

class TeachingAudience(_Base):
    """教学受众设定与 Pre-Flight 报告（Phase 0）"""
    paper_title: str = Field(..., description='论文标题')
    authors: str = Field('', description='作者')
    year: str = Field('', description='年份')
    thesis: str = Field(..., description='一句话核心论点')
    audience_level: str = Field(..., description='受众级别：undergrad / phd / seminar')
    time_minutes: int = Field(60, description='课时预算（分钟）')
    prerequisites: List[str] = Field(default_factory=list, description='假定的前置知识')
    running_example: str = Field('', description='贯穿讲座的示例候选')


class TeachingResult(_Base):
    """单个值得讲授的结果"""
    id: str = Field(..., description='编号，如 R1')
    name: str = Field(..., description='结果名称')
    statement: str = Field(..., description='形式化陈述（按受众裁剪）')
    intuition: str = Field(..., description='直觉解释（一句话，无代数）')
    failure_mode: str = Field(..., description='何时失效')
    method_vs_takeaway: str = Field('', description='方法（如何得到）与结论（我们相信什么）之辨析')


class TeachingResults(_Base):
    """Phase 1：值得讲授的 3-5 个结果"""
    results: List[TeachingResult] = Field(..., description='结果列表（3-5 个）')
    notation_notes: List[str] = Field(default_factory=list, description='符号映射 / 易混符号提醒')


class TeachingSlide(_Base):
    """单页幻灯片骨架条目"""
    num: int = Field(..., description='页码')
    title: str = Field(..., description='标题')
    content_note: str = Field(..., description='一行内容要点')
    figure: str = Field('', description='配图 / 图表占位')


class TeachingOutline(_Base):
    """Phase 2：讲义主线 + 幻灯片骨架"""
    arc_motivation: str = Field('', description='动机（Motivation）')
    arc_setup: str = Field('', description='设定（Setup）')
    arc_key_result: str = Field('', description='核心结果（Key Result）')
    arc_method: str = Field('', description='方法（Method）')
    arc_takeaways: str = Field('', description='可迁移结论（Takeaways）')
    slides: List[TeachingSlide] = Field(..., description='幻灯片骨架（约 课时/2 页）')


class TeachingQuestion(_Base):
    """讨论题"""
    depth: str = Field(..., description='深度：comprehension / application / critique')
    text: str = Field(..., description='题目')


class TeachingQuestions(_Base):
    """Phase 3a：4-6 道分级讨论题"""
    questions: List[TeachingQuestion] = Field(..., description='讨论题列表（按 comprehension→application→critique 排序）')


class TeachingExercise(_Base):
    """习题简介（Brief，非完整解答）"""
    id: str = Field(..., description='编号，如 E1')
    prompt: str = Field(..., description='题干')
    drills: str = Field(..., description='训练的技能')
    answer_shape: str = Field(..., description='期望答案形态')


class TeachingExercises(_Base):
    """Phase 3b：2-4 个习题简介"""
    exercises: List[TeachingExercise] = Field(default_factory=list, description='习题简介列表')


class TeachingPackage(_Base):
    """教学包：最终交付物"""
    audience: TeachingAudience = Field(..., description='受众设定')
    results: TeachingResults = Field(..., description='值得讲授的结果')
    outline: TeachingOutline = Field(..., description='讲义主线与幻灯片骨架')
    questions: TeachingQuestions = Field(..., description='讨论题')
    exercises: TeachingExercises = Field(..., description='习题简介')


# ============================================================
# 十二、kougi-forge 兼容工作流：需求分析 → 蓝图规划 → 样章确认 → 逐章生产 → 全书组装与一致性检查
# ============================================================

class KougiRequirements(_Base):
    """需求分析结果（Phase 1）"""
    project_def: str = Field(..., description='项目定义：教材主题、受众、课时、输出格式')
    questions: List[str] = Field(default_factory=list, description='澄清问题列表')
    is_sufficient: bool = Field(..., description='需求是否已充分')


class KougiBlueprint(_Base):
    """单个蓝图方案"""
    id: str = Field(..., description='蓝图 ID，如 bp-1')
    title: str = Field(..., description='教材标题')
    chapters: List[str] = Field(..., description='章节标题列表')
    rationale: str = Field(..., description='设计理由')


class KougiBlueprints(_Base):
    """多版本蓝图（Phase 2）"""
    blueprints: List[KougiBlueprint] = Field(..., description='生成的多个蓝图方案')
    merged: KougiBlueprint = Field(..., description='合并后的蓝图')
    confirmed: bool = Field(False, description='是否已确认')


class KougiSampleChapter(_Base):
    """样章（Phase 3）"""
    chapter_index: int = Field(..., description='章节索引')
    title: str = Field(..., description='章节标题')
    draft: str = Field(..., description='样章草稿')
    confirmed: bool = Field(False, description='是否已确认')


class KougiChapterDraft(_Base):
    """章节草稿变体（Phase 4）"""
    variant_id: str = Field(..., description='变体 ID，如 v1/v2/v3')
    content: str = Field(..., description='草稿内容')
    score: float = Field(0.0, description='质量评分')


class KougiChapter(_Base):
    """单个完成的章节"""
    index: int = Field(..., description='章节序号')
    title: str = Field(..., description='章节标题')
    content: str = Field(..., description='最终内容')
    exercises: List[str] = Field(default_factory=list, description='练习题')


class KougiChapterProduction(_Base):
    """章节生产结果（Phase 4）"""
    chapters: List[KougiChapter] = Field(default_factory=list, description='已完成章节列表')
    current_chapter: int = Field(0, description='当前处理到的章节索引')


class KougiBookAssembly(_Base):
    """全书组装结果（Phase 5）"""
    full_markdown: str = Field(..., description='完整教材 Markdown')
    glossary: str = Field('', description='术语表')
    exercises_collection: str = Field('', description='练习题汇总')
    consistency_report: str = Field('', description='一致性检查报告')
    final_approved: bool = Field(False, description='是否最终确认')


class KougiFullResult(_Base):
    """kougi-forge 完整流程结果"""
    requirements: KougiRequirements = Field(..., description='需求分析')
    blueprints: KougiBlueprints = Field(..., description='蓝图规划')
    sample_chapter: KougiSampleChapter = Field(..., description='样章确认')
    chapter_production: KougiChapterProduction = Field(..., description='逐章生产')
    assembly: KougiBookAssembly = Field(..., description='全书组装')

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

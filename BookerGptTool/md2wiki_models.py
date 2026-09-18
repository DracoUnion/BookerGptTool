# -*- coding: utf-8 -*-
"""
md2wiki_models.py —— 完整的 LLM Wiki 工作流模型。

涵盖：配置、原始素材、发现历史、Wiki 词条（实体/概念/来源/综合/变更）、
INDEX、LOG、gaps、queries、syntheses 以及特殊命令输出。
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================================
# 配置模型
# ============================================================================
class TopicConfig(BaseModel):
    name: str
    keywords: List[str]
    priority: str = "medium"  # high | medium | low


class FeedConfig(BaseModel):
    github_trending: Dict[str, Any] = Field(default_factory=dict)
    github_orgs: List[Dict[str, Any]] = Field(default_factory=list)
    github_people: List[Dict[str, Any]] = Field(default_factory=list)
    github_repos: List[Dict[str, Any]] = Field(default_factory=list)
    reddit: Dict[str, Any] = Field(default_factory=dict)
    rss: List[Dict[str, Any]] = Field(default_factory=list)
    hackernews: Dict[str, Any] = Field(default_factory=dict)
    twitter_accounts: List[Dict[str, Any]] = Field(default_factory=list)


class ScheduleConfig(BaseModel):
    run: Dict[str, Any] = Field(default_factory=dict)
    ingest: Dict[str, Any] = Field(default_factory=dict)
    lint: Dict[str, Any] = Field(default_factory=dict)
    recompile: Dict[str, Any] = Field(default_factory=dict)


class DiscoveryConfig(BaseModel):
    strategies: List[str] = Field(default_factory=list)
    web_search: Dict[str, Any] = Field(default_factory=dict)
    scraping: Dict[str, Any] = Field(default_factory=dict)
    dedup: Dict[str, Any] = Field(default_factory=dict)


class OutputsConfig(BaseModel):
    formats: List[str] = Field(default_factory=list)
    save_queries: bool = True
    save_syntheses: bool = True


class WikiConfig(BaseModel):
    wiki: Dict[str, Any] = Field(default_factory=dict)
    topics: List[TopicConfig] = Field(default_factory=list)
    feeds: FeedConfig = Field(default_factory=FeedConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)
    book_mode: bool = False
    current_chapter: int = 0
    change_detection: bool = False


# ============================================================================
# 原始素材 / 发现历史
# ============================================================================
class RawSource(BaseModel):
    """原始素材文件（raw/ 下的文件）"""
    path: str = Field(..., description="相对路径，如 raw/articles/2025-01-15-slug.md")
    title: str = Field(..., description="标题")
    url: str = Field(default="", description="来源 URL")
    discovered: str = Field(..., description="发现日期 YYYY-MM-DD")
    topic: str = Field(default="", description="所属话题")
    content: str = Field(default="", description="全文内容（可选，大文件时不载入）")
    file_size: int = Field(default=0, description="文件大小")
    file_hash: str = Field(default="", description="内容哈希，用于变更检测")


class DiscoveryHistory(BaseModel):
    """发现历史（.discoveries/history.json）"""
    processed: List[str] = Field(default_factory=list, description="已处理的原始文件路径列表")
    last_updated: str = Field(default="", description="最后更新时间")


class DiscoveryGaps(BaseModel):
    """知识缺口（.discoveries/gaps.json）"""
    gaps: List[str] = Field(default_factory=list, description="需要填补的知识点描述")
    last_lint: str = Field(default="", description="最后执行 lint 的时间")


# ============================================================================
# Wiki 词条基类与各类型
# ============================================================================
class WikiPageBase(BaseModel):
    """Wiki 页面基类"""
    type: str = Field(..., description="页面类型：entity|concept|source|synthesis|change|index|log")
    name: str = Field(..., description="页面名称（文件名不含 .md）")
    title: str = Field(..., description="显示标题")
    created: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    updated: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    sources: List[str] = Field(default_factory=list, description="引用的原始素材文件路径")


class WikiEntity(WikiPageBase):
    """实体：人物/组织/工具/项目"""
    category: str = Field(..., description="person|organization|tool|project")
    description: str = Field(default="", description="一句话描述")
    overview: str = Field(default="", description="概览")
    key_points: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list, description="[[cross-ref]] 列表")


class WikiConcept(WikiPageBase):
    """概念"""
    domain: str = Field(default="", description="领域：ai|engineering|business|...")
    definition: str = Field(default="", description="清晰定义")
    how_it_works: str = Field(default="", description="运作机制")
    examples: List[str] = Field(default_factory=list)
    links: List[str] = Field(default_factory=list)


class WikiSourceSummary(WikiPageBase):
    """来源摘要"""
    format: str = Field(default="article", description="article|paper|note|video|podcast")
    raw_path: str = Field(..., description="raw/ 下的原始文件路径")
    ingested: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    summary: str = Field(default="", description="2-3 段摘要")
    key_takeaways: List[str] = Field(default_factory=list)
    entities_mentioned: List[str] = Field(default_factory=list)
    concepts_mentioned: List[str] = Field(default_factory=list)
    notable_quotes: List[str] = Field(default_factory=list)


class WikiSynthesis(WikiPageBase):
    """综合分析"""
    topic: str = Field(..., description="分析主题")
    question: str = Field(default="", description="分析问题")
    analysis: str = Field(default="", description="综合内容")
    conclusion: str = Field(default="", description="结论")
    sources_used: List[str] = Field(default_factory=list, description="引用的 source 页面名")
    sources_count: int = Field(default=0)


class WikiChange(WikiPageBase):
    """变更记录"""
    entity_name: str = Field(..., description="发生变更的实体名")
    change_description: str = Field(..., description="变更描述")
    old_content: str = Field(default="", description="旧内容摘要")
    new_content: str = Field(default="", description="新内容摘要")


class WikiIndex(WikiPageBase):
    """INDEX.md：词条目录"""
    entries: List[Dict[str, str]] = Field(default_factory=list, description="[{name, title, type, category}]")


class WikiLogEntry(BaseModel):
    """LOG.md 单条记录"""
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    action: str = Field(..., description="动作：discover|ingest|query|lint|book-summary|competitive-brief|interview-prep")
    details: str = Field(default="", description="详情")
    files: List[str] = Field(default_factory=list, description="涉及的文件")


class WikiLog(WikiPageBase):
    """LOG.md：活动日志"""
    entries: List[WikiLogEntry] = Field(default_factory=list)


# ============================================================================
# 现有模型（保留兼容）
# ============================================================================
class WikiItem(BaseModel):
    """候选词条（兼容旧流程）"""
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


# ============================================================================
# 查询与综合输出
# ============================================================================
class QueryResult(BaseModel):
    question: str
    answer: str
    citations: List[str] = Field(default_factory=list)
    saved_as: str = Field(default="", description="若保存为综合，存放路径")


class BookSummary(BaseModel):
    """book-summary 命令输出"""
    characters: List[Dict[str, str]] = Field(default_factory=list)
    timeline: List[Dict[str, str]] = Field(default_factory=list)
    factions: List[Dict[str, str]] = Field(default_factory=list)
    locations: List[Dict[str, str]] = Field(default_factory=list)
    themes: List[str] = Field(default_factory=list)
    mysteries: List[str] = Field(default_factory=list)
    quotes: List[str] = Field(default_factory=list)
    saved_path: str = Field(default="")


class CompetitiveBrief(BaseModel):
    """competitive-brief 命令输出"""
    competitor: str
    positioning: str = ""
    pricing: List[Dict[str, str]] = Field(default_factory=list)
    features: List[str] = Field(default_factory=list)
    recent_moves: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    job_signals: List[str] = Field(default_factory=list)
    differentiation: str = ""
    saved_path: str = Field(default="")


class InterviewPrep(BaseModel):
    """interview-prep 命令输出"""
    company: str
    overview: str = ""
    tech_stack: List[str] = Field(default_factory=list)
    culture_signals: List[str] = Field(default_factory=list)
    recent_news: List[str] = Field(default_factory=list)
    interview_process: List[str] = Field(default_factory=list)
    known_questions: List[str] = Field(default_factory=list)
    compensation: str = ""
    green_flags: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    questions_to_ask: List[str] = Field(default_factory=list)
    saved_path: str = Field(default="")


# ============================================================================
# 静态检查 / 知识图谱
# ============================================================================
class LintIssue(BaseModel):
    """lint 单条问题"""
    category: str = Field(..., description="orphan|broken_link|index_inconsistency|missing_entity|contradiction|outdated|underdeveloped|gap")
    severity: str = Field("medium", description="low|medium|high")
    page: str = Field(default="", description="所在页面")
    title: str = Field(..., description="问题标题")
    detail: str = Field(default="", description="问题详情")


class LintReport(BaseModel):
    """lint 结果报告"""
    issues: List[LintIssue] = Field(default_factory=list)
    orphaned_pages: List[str] = Field(default_factory=list)
    broken_links: List[str] = Field(default_factory=list)
    index_inconsistent: List[str] = Field(default_factory=list)
    missing_entities: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    health_score: float = Field(default=100.0, ge=0.0, le=100.0)
    saved_path: str = Field(default="")


class GraphNode(BaseModel):
    """知识图谱节点"""
    id: str = Field(..., description="页面路径，如 wiki/concepts/RAG.md")
    label: str = Field(..., description="显示标签")
    type: str = Field(..., description="source|entity|concept|synthesis")
    community: int = Field(default=0)
    degree: int = Field(default=0)


class GraphEdge(BaseModel):
    """知识图谱边"""
    source: str = Field(..., description="起始节点 id")
    target: str = Field(..., description="目标节点 id")
    type: str = Field(..., description="EXTRACTED|INFERRED")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    label: str = Field(default="", description="关系描述")


class GraphResult(BaseModel):
    """知识图谱构建结果"""
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)
    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)
    build_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    graph_json_path: str = Field(default="")
    graph_html_path: str = Field(default="")


class IngestSummary(BaseModel):
    """一次 ingest 的交付摘要"""
    source_file: str = Field(..., description="原始文件路径")
    source_slug: str = Field(default="", description="来源页面 slug")
    pages_created: List[str] = Field(default_factory=list)
    pages_updated: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    index_updated: bool = False
    overview_updated: bool = False
    log_appended: bool = False
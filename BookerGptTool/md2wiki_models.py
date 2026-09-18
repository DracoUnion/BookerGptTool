# -*- coding: utf-8 -*-
"""
md2wiki_models.py —— LLM Wiki 工作流模型。

涵盖：配置、原始素材、发现历史、Wiki 词条（实体/概念/来源/综合/变更）、
INDEX、LOG、gaps、queries、syntheses、静态检查、知识图谱以及特殊命令输出。
所有字段均带中文 description。
"""

from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


# ============================================================================
# 配置模型
# ============================================================================
class TopicConfig(BaseModel):
    """话题配置"""
    name: str = Field(..., description="话题名称")
    keywords: List[str] = Field(default_factory=list, description="用于自动发现来源的关键词列表")
    priority: str = Field("medium", description="优先级：high|medium|low")


class FeedConfig(BaseModel):
    """订阅源配置"""
    github_trending: Dict[str, Any] = Field(default_factory=dict, description="GitHub Trending 配置")
    github_orgs: List[Dict[str, Any]] = Field(default_factory=list, description="要关注的组织列表")
    github_people: List[Dict[str, Any]] = Field(default_factory=list, description="要关注的人列表")
    github_repos: List[Dict[str, Any]] = Field(default_factory=list, description="要跟踪的仓库列表")
    reddit: Dict[str, Any] = Field(default_factory=dict, description="Reddit 订阅配置")
    rss: List[Dict[str, Any]] = Field(default_factory=list, description="RSS 订阅列表")
    hackernews: Dict[str, Any] = Field(default_factory=dict, description="Hacker News 配置")
    twitter_accounts: List[Dict[str, Any]] = Field(default_factory=list, description="关注的 Twitter 账号")


class ScheduleConfig(BaseModel):
    """调度配置"""
    run: Dict[str, Any] = Field(default_factory=dict, description="循环运行配置")
    ingest: Dict[str, Any] = Field(default_factory=dict, description="摄入调度配置")
    lint: Dict[str, Any] = Field(default_factory=dict, description="lint 调度配置")
    recompile: Dict[str, Any] = Field(default_factory=dict, description="重编译调度配置")


class DiscoveryConfig(BaseModel):
    """发现策略配置"""
    strategies: List[str] = Field(default_factory=list, description="发现策略列表（web_search/feed_poll/gap_fill 等）")
    web_search: Dict[str, Any] = Field(default_factory=dict, description="网络搜索配置")
    scraping: Dict[str, Any] = Field(default_factory=dict, description="抓取配置")
    dedup: Dict[str, Any] = Field(default_factory=dict, description="去重配置")


class OutputsConfig(BaseModel):
    """输出配置"""
    formats: List[str] = Field(default_factory=list, description="输出格式列表")
    save_queries: bool = Field(True, description="是否保存查询答案")
    save_syntheses: bool = Field(True, description="是否保存综合分析")


class WikiConfig(BaseModel):
    """整个 wiki 系统的配置（config.yaml）"""
    wiki: Dict[str, Any] = Field(default_factory=dict, description="wiki 基本信息（名称、描述、语言、每批页数上限）")
    topics: List[TopicConfig] = Field(default_factory=list, description="话题列表")
    feeds: FeedConfig = Field(default_factory=FeedConfig, description="订阅源配置")
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig, description="调度配置")
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig, description="发现策略配置")
    outputs: OutputsConfig = Field(default_factory=OutputsConfig, description="输出配置")
    book_mode: bool = Field(False, description="是否书籍模式（按章节推进、防剧透）")
    current_chapter: int = Field(0, description="当前书籍章节，book_mode 下跳过 chapter 更大的文件")
    change_detection: bool = Field(False, description="是否开启变更检测")


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
    type: str = Field("page", description="页面类型：entity|concept|source|synthesis|change|index|log")
    name: str = Field(..., description="页面名称（文件名不含 .md）")
    title: str = Field(..., description="显示标题")
    created: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), description="创建日期 YYYY-MM-DD")
    updated: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), description="更新日期 YYYY-MM-DD")
    sources: List[str] = Field(default_factory=list, description="引用的原始素材文件路径列表")


class WikiEntity(WikiPageBase):
    """实体：人物/组织/工具/项目"""
    type: str = Field("entity", description="页面类型")
    category: str = Field(..., description="person|organization|tool|project")
    description: str = Field(default="", description="一句话描述")
    overview: str = Field(default="", description="概览")
    key_points: List[str] = Field(default_factory=list, description="关键要点列表")
    links: List[str] = Field(default_factory=list, description="[[cross-ref]] 交叉引用列表")


class WikiConcept(WikiPageBase):
    """概念"""
    type: str = Field("concept", description="页面类型")
    domain: str = Field(default="", description="领域：ai|engineering|business|...")
    definition: str = Field(default="", description="清晰定义")
    how_it_works: str = Field(default="", description="运作机制")
    examples: List[str] = Field(default_factory=list, description="具体例子列表")
    links: List[str] = Field(default_factory=list, description="[[cross-ref]] 交叉引用列表")


class WikiSourceSummary(WikiPageBase):
    """来源摘要"""
    type: str = Field("source", description="页面类型")
    format: str = Field(default="article", description="article|paper|note|video|podcast")
    raw_path: str = Field(..., description="raw/ 下的原始文件路径")
    ingested: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), description="摄入日期 YYYY-MM-DD")
    summary: str = Field(default="", description="2-3 段摘要")
    key_takeaways: List[str] = Field(default_factory=list, description="关键要点列表")
    entities_mentioned: List[str] = Field(default_factory=list, description="提到的实体名（将作为 [[链接]]）")
    concepts_mentioned: List[str] = Field(default_factory=list, description="提到的概念名（将作为 [[链接]]）")
    notable_quotes: List[str] = Field(default_factory=list, description="关键引言（≤125 字符）")


class WikiSynthesis(WikiPageBase):
    """综合分析"""
    type: str = Field("synthesis", description="页面类型")
    topic: str = Field(..., description="分析主题")
    question: str = Field(default="", description="分析问题")
    analysis: str = Field(default="", description="综合内容")
    conclusion: str = Field(default="", description="结论")
    sources_used: List[str] = Field(default_factory=list, description="引用的 source 页面名列表")
    sources_count: int = Field(default=0, description="引用来源数量")


class WikiChange(WikiPageBase):
    """变更记录"""
    type: str = Field("change", description="页面类型")
    entity_name: str = Field(..., description="发生变更的实体名")
    change_description: str = Field(..., description="变更描述")
    old_content: str = Field(default="", description="旧内容摘要")
    new_content: str = Field(default="", description="新内容摘要")


class WikiIndex(WikiPageBase):
    """INDEX.md：词条目录"""
    entries: List[Dict[str, str]] = Field(default_factory=list, description="[{name, title, type, category}] 目录条目列表")


class WikiLogEntry(BaseModel):
    """LOG.md 单条记录"""
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"), description="时间戳")
    action: str = Field(..., description="动作：discover|ingest|query|lint|book-summary|competitive-brief|interview-prep")
    details: str = Field(default="", description="详情")
    files: List[str] = Field(default_factory=list, description="涉及的文件列表")


class WikiLog(WikiPageBase):
    """LOG.md：活动日志"""
    entries: List[WikiLogEntry] = Field(default_factory=list, description="日志条目列表")


# ============================================================================
# 现有模型（保留兼容）
# ============================================================================
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


# ============================================================================
# 查询与综合输出
# ============================================================================
class QueryResult(BaseModel):
    """一次查询的结果"""
    question: str = Field("", description="查询问题")
    answer: str = Field(..., description="综合答案（含 [[PageName]] 引用）")
    citations: List[str] = Field(default_factory=list, description="引用页面路径列表")
    saved_as: str = Field(default="", description="若保存为综合，存放 slug（否则空）")


class BookSummary(BaseModel):
    """book-summary 命令输出"""
    characters: List[Dict[str, str]] = Field(default_factory=list, description="人物表：[{name, role, faction, status}]")
    timeline: List[Dict[str, str]] = Field(default_factory=list, description="事件时间线：[{date, event}]")
    factions: List[Dict[str, str]] = Field(default_factory=list, description="派系与目标：[{name, goals}]")
    locations: List[Dict[str, str]] = Field(default_factory=list, description="关键地点：[{name, description}]")
    themes: List[str] = Field(default_factory=list, description="主题列表")
    mysteries: List[str] = Field(default_factory=list, description="未解之谜/开放问题")
    quotes: List[str] = Field(default_factory=list, description="名言列表")
    saved_path: str = Field(default="", description="输出文件路径")


class CompetitiveBrief(BaseModel):
    """competitive-brief 命令输出"""
    competitor: str = Field("", description="竞品名称")
    positioning: str = Field("", description="一句话定位")
    pricing: List[Dict[str, str]] = Field(default_factory=list, description="定价表：[{tier, price, notes}]")
    features: List[str] = Field(default_factory=list, description="核心功能列表")
    recent_moves: List[str] = Field(default_factory=list, description="近 30 天动向")
    weaknesses: List[str] = Field(default_factory=list, description="已知弱点")
    job_signals: List[str] = Field(default_factory=list, description="招聘信号（推断路线图）")
    differentiation: str = Field("", description="与我们的差异")
    saved_path: str = Field(default="", description="输出文件路径")


class InterviewPrep(BaseModel):
    """interview-prep 命令输出"""
    company: str = Field("", description="公司名称")
    overview: str = Field("", description="公司概览")
    tech_stack: List[str] = Field(default_factory=list, description="技术栈列表")
    culture_signals: List[str] = Field(default_factory=list, description="工程文化信号")
    recent_news: List[str] = Field(default_factory=list, description="近期动态")
    interview_process: List[str] = Field(default_factory=list, description="面试流程")
    known_questions: List[str] = Field(default_factory=list, description="已知面试题")
    compensation: str = Field("", description="薪资范围")
    green_flags: List[str] = Field(default_factory=list, description="绿旗信号")
    red_flags: List[str] = Field(default_factory=list, description="红旗信号")
    questions_to_ask: List[str] = Field(default_factory=list, description="要问的问题")
    saved_path: str = Field(default="", description="输出文件路径")


# ============================================================================
# 静态检查 / 知识图谱
# ============================================================================
class LintIssue(BaseModel):
    """lint 单条问题"""
    category: str = Field(..., description="orphan|broken_link|index_inconsistency|missing_entity|contradiction|outdated|underdeveloped|gap")
    severity: str = Field("medium", description="严重度：low|medium|high")
    page: str = Field(default="", description="所在页面")
    title: str = Field(..., description="问题标题")
    detail: str = Field(default="", description="问题详情")


class LintReport(BaseModel):
    """lint 结果报告"""
    issues: List[LintIssue] = Field(default_factory=list, description="问题列表")
    orphaned_pages: List[str] = Field(default_factory=list, description="孤儿页列表（无人链接）")
    broken_links: List[str] = Field(default_factory=list, description="断链页面列表")
    index_inconsistent: List[str] = Field(default_factory=list, description="index 中缺失的页面列表")
    missing_entities: List[str] = Field(default_factory=list, description="被多处提及但无独立页面的实体")
    suggestions: List[str] = Field(default_factory=list, description="改进建议列表")
    health_score: float = Field(default=100.0, ge=0.0, le=100.0, description="健康度评分 0-100")
    saved_path: str = Field(default="", description="报告保存路径")


class GraphNode(BaseModel):
    """知识图谱节点"""
    id: str = Field(..., description="页面路径，如 wiki/concepts/RAG.md")
    label: str = Field(..., description="显示标签")
    type: str = Field(..., description="source|entity|concept|synthesis")
    community: int = Field(default=0, description="Louvain 社区编号")
    degree: int = Field(default=0, description="节点度（连接数）")


class GraphEdge(BaseModel):
    """知识图谱边"""
    source: str = Field(..., description="起始节点 id")
    target: str = Field(..., description="目标节点 id")
    type: str = Field(..., description="EXTRACTED|INFERRED")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度 0-1")
    label: str = Field(default="", description="关系描述")


class GraphResult(BaseModel):
    """知识图谱构建结果"""
    nodes: List[GraphNode] = Field(default_factory=list, description="节点列表")
    edges: List[GraphEdge] = Field(default_factory=list, description="边列表")
    node_count: int = Field(default=0, description="节点数量")
    edge_count: int = Field(default=0, description="边数量")
    build_date: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"), description="构建日期")
    graph_json_path: str = Field(default="", description="graph.json 路径")
    graph_html_path: str = Field(default="", description="graph.html 路径")


class IngestSummary(BaseModel):
    """一次 ingest 的交付摘要"""
    source_file: str = Field(..., description="原始文件路径")
    source_slug: str = Field(default="", description="来源页面 slug")
    pages_created: List[str] = Field(default_factory=list, description="新建页面列表")
    pages_updated: List[str] = Field(default_factory=list, description="更新页面列表")
    contradictions: List[str] = Field(default_factory=list, description="发现的矛盾列表")
    index_updated: bool = Field(False, description="是否更新了 index.md")
    overview_updated: bool = Field(False, description="是否更新了 overview.md")
    log_appended: bool = Field(False, description="是否追加了 log.md")
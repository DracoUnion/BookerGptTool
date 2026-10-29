from typing import List, Dict, Any
from pydantic import BaseModel, Field


class Entity(BaseModel):
    """实体模型"""
    id: str = Field(..., description="临时ID，如 ent_001")
    name: str = Field(..., description="原文实体名称")
    canonical_name: str = Field(..., description="规范化后的名称")
    type: str = Field(..., description="类型：人物/组织/地点/概念/事件/作品/技术/时间")
    description: str = Field(..., description="实体描述")
    mentions: List[Dict[str, str]] = Field(default_factory=list, description="提及位置，包含上下文片段和位置信息")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，0-1之间")


class Relation(BaseModel):
    """关系模型"""
    id: str = Field(..., description="临时ID，如 rel_001")
    source_entity_id: str = Field(..., description="源实体ID")
    source_entity_name: str = Field(..., description="源实体名称（便于阅读）")
    target_entity_id: str = Field(..., description="目标实体ID")
    target_entity_name: str = Field(..., description="目标实体名称")
    relation_type: str = Field(..., description="关系类型，如：属于、位于、创建、投资等")
    relation_description: str = Field(..., description="关系描述，详细说明实体间的关系")
    evidence: str = Field(..., description="原文证据，支持该关系的文本片段")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，0-1之间")


class EntityList(BaseModel):
    """实体抽取结果"""
    entities: List[Entity] = Field(default_factory=list, description="抽取到的实体列表")


class RelationList(BaseModel):
    """关系抽取结果"""
    relationships: List[Relation] = Field(default_factory=list, description="抽取到的关系列表")


class GlobalEntity(BaseModel):
    """全局合并后的实体"""
    canonical_id: str = Field(..., description="全局唯一规范ID")
    name: str = Field(..., description="规范化后的实体名称")
    type: str = Field(..., description="实体类型")
    description: str = Field(..., description="实体描述")
    merged_from: List[str] = Field(default_factory=list, description="合并来源的临时实体ID列表")
    confidence: float = Field(..., ge=0.0, le=1.0, description="合并后的置信度，0-1之间")


class GlobalRelation(BaseModel):
    """全局合并后的关系"""
    id: str = Field(..., description="关系全局唯一ID")
    source: str = Field(..., description="源实体规范ID")
    target: str = Field(..., description="目标实体规范ID")
    relation_type: str = Field(..., description="关系类型")
    evidence: List[str] = Field(default_factory=list, description="支持该关系的多条原文证据")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度，0-1之间")
    conflicts_resolved: List[str] = Field(default_factory=list, description="冲突解决记录，记录合并时解决的冲突")


class ResolvedGraph(BaseModel):
    """消解后的全局图谱"""
    entities: List[GlobalEntity] = Field(default_factory=list, description="全局实体列表")
    relationships: List[GlobalRelation] = Field(default_factory=list, description="全局关系列表")
    resolution_log: List[str] = Field(default_factory=list, description="实体消解过程日志")


class AlignedEntity(BaseModel):
    """Schema对齐后的实体"""
    canonical_id: str = Field(..., description="规范实体ID")
    name: str = Field(..., description="实体名称")
    original_type: str = Field(..., description="原始类型")
    aligned_type: str = Field(..., description="对齐后的标准类型")
    is_aligned: bool = Field(..., description="是否成功对齐到标准Schema")
    confidence: float = Field(..., ge=0.0, le=1.0, description="对齐置信度，0-1之间")
    reason: str = Field(default="", description="对齐原因或未对齐的说明")


class AlignedRelation(BaseModel):
    """Schema对齐后的关系"""
    id: str = Field(..., description="关系ID")
    source: str = Field(..., description="源实体ID")
    target: str = Field(..., description="目标实体ID")
    original_type: str = Field(..., description="原始关系类型")
    aligned_type: str = Field(..., description="对齐后的标准关系类型")
    is_aligned: bool = Field(..., description="是否成功对齐到标准Schema")
    confidence: float = Field(..., ge=0.0, le=1.0, description="对齐置信度，0-1之间")
    reason: str = Field(default="", description="对齐原因或未对齐的说明")


class SchemaAlignmentResult(BaseModel):
    """Schema对齐结果"""
    aligned_entities: List[AlignedEntity] = Field(default_factory=list, description="对齐后的实体列表")
    aligned_relations: List[AlignedRelation] = Field(default_factory=list, description="对齐后的关系列表")
    alignment_log: List[str] = Field(default_factory=list, description="对齐过程日志")
    unaligned_count: int = Field(default=0, description="未成功对齐的实体/关系数量")


class EvaluatedTriplet(BaseModel):
    """评估后的三元组"""
    id: str = Field(..., description="三元组唯一ID")
    source: str = Field(..., description="源实体名称")
    target: str = Field(..., description="目标实体名称")
    relation_type: str = Field(..., description="关系类型")
    evidence: List[str] = Field(default_factory=list, description="支持证据列表")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="置信度得分，0-1之间")
    clarity_score: float = Field(..., ge=0.0, le=1.0, description="清晰度得分，0-1之间")
    relevance_score: float = Field(..., ge=0.0, le=1.0, description="相关性得分，0-1之间")
    overall_score: float = Field(..., ge=0.0, le=1.0, description="综合得分，0-1之间")
    should_integrate: bool = Field(..., description="是否应集成到知识图谱中")
    rejection_reason: str = Field(default="", description="拒绝集成的原因，若集成则为空")


class EvaluationResult(BaseModel):
    """评估结果"""
    triplets: List[EvaluatedTriplet] = Field(default_factory=list, description="评估后的三元组列表")
    accepted_count: int = Field(default=0, description="接受集成的三元组数量")
    rejected_count: int = Field(default=0, description="拒绝集成的三元组数量")
    average_score: float = Field(default=0.0, ge=0.0, le=1.0, description="平均综合得分，0-1之间")
    evaluation_log: List[str] = Field(default_factory=list, description="评估过程日志")

class Chunk(BaseModel):
    id: str
    content: str
    summary: str

class Result(BaseModel):
    resolved_graph: ResolvedGraph
    schema_alignment: SchemaAlignmentResult
    evaluation: EvaluationResult


# Schema归纳结果模型
class SchemaInductionResult(BaseModel):
    entity_types: List[str] = Field(..., description="归纳出的实体类型列表")
    relation_types: List[str] = Field(..., description="归纳出的关系类型列表")
    induction_log: List[str] = Field(default_factory=list, description="归纳过程日志")



# ============================================================
# 十二、AutoSchemaKG 兼容工作流：文本 → 三元组抽取 → 概念归纳 → KG 构建
# ============================================================

class AtlasTriple(BaseModel):
    """AutoSchemaKG：单个三元组"""
    head: str = Field(..., description='头实体')
    head_type: str = Field(..., description='头实体类型')
    relation: str = Field(..., description='关系')
    tail: str = Field(..., description='尾实体')
    tail_type: str = Field(..., description='尾实体类型')
    sentence: str = Field(..., description='原文支撑句')
    confidence: float = Field(1.0, description='置信度')


class AtlasTripleList(BaseModel):
    """三元组抽取结果"""
    triples: List[AtlasTriple] = Field(default_factory=list, description='三元组列表')


class AtlasConcept(BaseModel):
    """AutoSchemaKG：概念"""
    name: str = Field(..., description='概念名称')
    description: str = Field(..., description='概念描述')
    parent: str = Field('', description='父概念')
    children: List[str] = Field(default_factory=list, description='子概念列表')
    entities: List[str] = Field(default_factory=list, description='归属实体')


class AtlasConceptList(BaseModel):
    """概念归纳结果"""
    concepts: List[AtlasConcept] = Field(default_factory=list, description='概念列表')


class AtlasKGConfig(BaseModel):
    """AutoSchemaKG 处理配置"""
    batch_size_triple: int = Field(3, description='三元组抽取批大小')
    batch_size_concept: int = Field(16, description='概念生成批大小')
    max_new_tokens: int = Field(2048, description='最大生成 token')
    max_workers: int = Field(3, description='并行工作进程数')
    remove_doc_spaces: bool = Field(True, description='去除文档重复空格')


class AtlasPipelineResult(BaseModel):
    """AutoSchemaKG 完整管道结果"""
    triples_json: str = Field('', description='三元组 JSON 路径')
    triples_csv: str = Field('', description='三元组 CSV 路径')
    concepts_csv: str = Field('', description='概念 CSV 路径')
    graphml_path: str = Field('', description='GraphML 图文件路径')


# ============================================================
# 十三、BookGraph 兼容工作流：多模态摄入 → LLM 富化 → 图构建 → 发现引擎
# ============================================================

class BookGraphIngestionSource(BaseModel):
    """BookGraph：摄入源"""
    source_type: str = Field(..., description='openlibrary / googlebooks / arxiv / local_pdf')
    identifier: str = Field(..., description='ISBN / arXiv ID / 文件路径')
    metadata: Dict[str, Any] = Field(default_factory=dict, description='原始元数据')


class BookGraphEnrichment(BaseModel):
    """BookGraph：LLM 富化结果"""
    core_concepts: List[str] = Field(default_factory=list, description='核心概念')
    fields: List[str] = Field(default_factory=list, description='学科领域')
    bibliographic: Dict[str, Any] = Field(default_factory=dict, description='书目信息')
    relationships: List[str] = Field(default_factory=list, description='推断关系类型')


class BookGraphNode(BaseModel):
    """BookGraph：图节点"""
    id: str = Field(..., description='节点 ID')
    type: str = Field(..., description='Book / Paper / Author / Concept / Field')
    properties: Dict[str, Any] = Field(default_factory=dict, description='节点属性')


class BookGraphRelationship(BaseModel):
    """BookGraph：图关系"""
    source: str = Field(..., description='源节点 ID')
    target: str = Field(..., description='目标节点 ID')
    rel_type: str = Field(..., description='WRITTEN_BY / MENTIONS / BELONGS_TO / RELATED_TO / INFLUENCED_BY / CONTRADICTS / EXPANDS')
    properties: Dict[str, Any] = Field(default_factory=dict, description='关系属性')


class BookGraphDiscovery(BaseModel):
    """BookGraph：自动发现洞察"""
    type: str = Field(..., description='thematic_cluster / reading_path / author_influence')
    description: str = Field(..., description='洞察描述')
    node_ids: List[str] = Field(default_factory=list, description='涉及节点')


class BookGraphResult(BaseModel):
    """BookGraph 完整结果"""
    nodes: List[BookGraphNode] = Field(default_factory=list, description='所有节点')
    relationships: List[BookGraphRelationship] = Field(default_factory=list, description='所有关系')
    discoveries: List[BookGraphDiscovery] = Field(default_factory=list, description='发现洞察')
    ingestion_log: List[str] = Field(default_factory=list, description='摄入日志')


# ============================================================
# 十四、DeepRead 兼容工作流：书籍 → 智能解析 → 实体/关系抽取 → Wiki 知识库
# ============================================================

class DeepReadEntity(BaseModel):
    """DeepRead：实体"""
    name: str = Field(..., description='实体名称')
    type: str = Field(..., description='人物 / 事件 / 概念 / 地点 / 组织 / 作品')
    description: str = Field(..., description='实体描述')
    aliases: List[str] = Field(default_factory=list, description='别名')
    chapter_refs: List[int] = Field(default_factory=list, description='出现章节')


class DeepReadRelation(BaseModel):
    """DeepRead：关系"""
    source: str = Field(..., description='源实体')
    target: str = Field(..., description='目标实体')
    rel_type: str = Field(..., description='关系类型')
    evidence: str = Field(..., description='原文证据')


class DeepReadChapter(BaseModel):
    """DeepRead：章节摘要"""
    chapter: int = Field(..., description='章节号')
    title: str = Field('', description='章节标题')
    summary: str = Field(..., description='章节摘要')
    key_entities: List[str] = Field(default_factory=list, description='关键实体')
    key_events: List[str] = Field(default_factory=list, description='关键事件')


class DeepReadBook(BaseModel):
    """DeepRead：整本书"""
    title: str = Field(..., description='书名')
    author: str = Field('', description='作者')
    entities: List[DeepReadEntity] = Field(default_factory=list, description='所有实体')
    relations: List[DeepReadRelation] = Field(default_factory=list, description='所有关系')
    chapters: List[DeepReadChapter] = Field(default_factory=list, description='章节摘要')
    stats: Dict[str, int] = Field(default_factory=dict, description='统计：节点数/关系数/章节数')


class DeepReadWikiPage(BaseModel):
    """DeepRead：Wiki 页面"""
    title: str = Field(..., description='页面标题')
    content: str = Field(..., description='页面内容（Markdown）')
    backlinks: List[str] = Field(default_factory=list, description='反向链接')


class DeepReadWikiIndex(BaseModel):
    """DeepRead：Wiki 索引"""
    book_title: str = Field(..., description='书名')
    pages: List[DeepReadWikiPage] = Field(default_factory=list, description='所有页面')
    homepage: str = Field('', description='首页内容')

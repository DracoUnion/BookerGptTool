from typing import List, Dict
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


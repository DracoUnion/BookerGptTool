from typing import List, Dict
from pydantic import BaseModel, Field


class Entity(BaseModel):
    """实体模型"""
    id: str = Field(..., description="临时ID，如 ent_001")
    name: str = Field(..., description="原文实体名称")
    canonical_name: str = Field(..., description="规范化后的名称")
    type: str = Field(..., description="类型：人物/组织/地点/概念/事件/作品/技术/时间")
    description: str = Field(..., description="实体描述")
    mentions: List[Dict[str, str]] = Field(default_factory=list, description="提及位置")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度")


class Relation(BaseModel):
    """关系模型"""
    id: str = Field(..., description="临时ID，如 rel_001")
    source_entity_id: str = Field(..., description="源实体ID")
    source_entity_name: str = Field(..., description="源实体名称（便于阅读）")
    target_entity_id: str = Field(..., description="目标实体ID")
    target_entity_name: str = Field(..., description="目标实体名称")
    relation_type: str = Field(..., description="关系类型")
    relation_description: str = Field(..., description="关系描述")
    evidence: str = Field(..., description="原文证据")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度")


class EntityList(BaseModel):
    """实体抽取结果"""
    entities: List[Entity]


class RelationList(BaseModel):
    """关系抽取结果"""
    relationships: List[Relation]


class GlobalEntity(BaseModel):
    """全局合并后的实体"""
    canonical_id: str
    name: str
    type: str
    description: str
    merged_from: List[str] = Field(default_factory=list)
    confidence: float


class GlobalRelation(BaseModel):
    """全局合并后的关系"""
    id: str
    source: str
    target: str
    relation_type: str
    evidence: List[str]
    confidence: float
    conflicts_resolved: List[str] = Field(default_factory=list)


class ResolvedGraph(BaseModel):
    """消解后的全局图谱"""
    entities: List[GlobalEntity]
    relationships: List[GlobalRelation]
    resolution_log: List[str]

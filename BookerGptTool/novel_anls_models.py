from typing import Any, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, Field


class CharacterBrief(BaseModel):
    name: str = Field(description="角色名")
    role: str = Field(description="本章中呈现的角色定位")
    traits: List[str] = Field(default_factory=list, description="性格/特征")


class CharacterUpdate(BaseModel):
    name: str = Field(description="已有角色名")
    change: str = Field(description="本章中的具体变化")


class ChapterSummary(BaseModel):
    module: Literal["章节摘要"] = "章节摘要"
    chapter: int = Field(description="章节序号")
    title: Optional[str] = Field(None, description="章节标题")
    summary: str = Field(description="50字以内核心摘要")
    key_events: List[str] = Field(default_factory=list, description="本章关键事件")
    new_characters: List[CharacterBrief] = Field(default_factory=list)
    character_updates: List[CharacterUpdate] = Field(default_factory=list)
    emotional_tone: int = Field(ge=0, le=10, description="情绪基调 0-10")
    chapter_end_hook: Optional[str] = Field(None, description="章尾钩子")
    foreshadowing_planted: List[str] = Field(default_factory=list, description="新埋伏笔")
    foreshadowing_payoff: List[str] = Field(default_factory=list, description="本章回收的伏笔")
    conflict_level: int = Field(ge=0, le=10, description="冲突烈度 0-10")
    word_count: int = Field(description="本章字数")


class GenreModule(BaseModel):
    module: Literal["题材与定位"] = "题材与定位"
    genre: Optional[str] = None
    target_audience: Optional[str] = None
    market_position: Optional[str] = None
    unique_selling_point: Optional[str] = None


class TitleModule(BaseModel):
    module: Literal["书名与简介"] = "书名与简介"
    title_analysis: Optional[Dict[str, Any]] = None
    blurb_hooks: Optional[List[str]] = None
    blurb_effectiveness: Optional[str] = None


class WorldModule(BaseModel):
    module: Literal["世界观设定"] = "世界观设定"
    core_rules: Optional[str] = None
    factions: Optional[List[Dict[str, str]]] = None
    unique_elements: Optional[List[str]] = None
    logic_consistency: Optional[str] = None
    world_immersion_score: Optional[int] = None


class CharacterModule(BaseModel):
    module: Literal["人物图谱"] = "人物图谱"
    protagonist: Optional[Dict[str, Any]] = None
    supporting_chars: Optional[List[Dict[str, Any]]] = None
    antagonist: Optional[Dict[str, Any]] = None
    relationship_map: Optional[str] = None


class StructureModule(BaseModel):
    module: Literal["主线与结构"] = "主线与结构"
    main_storyline: Optional[str] = None
    structure: Optional[Dict[str, Any]] = None
    subplots: Optional[List[Dict[str, str]]] = None
    structure_score: Optional[int] = None


class RhythmModule(BaseModel):
    module: Literal["节奏与爽点"] = "节奏与爽点"
    climax_points: Optional[List[Dict[str, Any]]] = None
    satisfaction_interval: Optional[str] = None
    flat_periods: Optional[List[Dict[str, str]]] = None
    conflict_escalation: Optional[str] = None
    emotion_curve: Optional[str] = None
    rhythm_score: Optional[int] = None


class GimmickModule(BaseModel):
    module: Literal["金手指/核心创意"] = "金手指/核心创意"
    unique_setting: Optional[str] = None
    innovation: Optional[str] = None
    core_gimmick: Optional[str] = None
    creativity_score: Optional[int] = None
    execution_score: Optional[int] = None


class OpeningModule(BaseModel):
    module: Literal["开篇钩子"] = "开篇钩子"
    first_500_words_hook: Optional[str] = None
    protagonist_first_impression: Optional[str] = None
    chapter_end_hooks: Optional[List[Dict[str, Any]]] = None
    golden_three_effectiveness: Optional[str] = None
    drop_off_risk: Optional[str] = None


class ForeshadowModule(BaseModel):
    module: Literal["伏笔与铺垫"] = "伏笔与铺垫"
    foreshadowing_list: Optional[List[Dict[str, Any]]] = None
    information_asymmetry: Optional[str] = None
    continuity: Optional[str] = None
    foreshadowing_score: Optional[int] = None


class PatternModule(BaseModel):
    module: Literal["可迁移模式"] = "可迁移模式"
    structural_patterns: Optional[List[str]] = None
    character_patterns: Optional[List[str]] = None
    rhythm_patterns: Optional[List[str]] = None
    writing_techniques: Optional[List[str]] = None
    imitation_advice: Optional[str] = None


MODULE_CLASS_MAP: Dict[str, Type[BaseModel]] = {
    "题材与定位": GenreModule,
    "书名与简介": TitleModule,
    "世界观设定": WorldModule,
    "人物图谱": CharacterModule,
    "主线与结构": StructureModule,
    "节奏与爽点": RhythmModule,
    "金手指/核心创意": GimmickModule,
    "开篇钩子": OpeningModule,
    "伏笔与铺垫": ForeshadowModule,
    "可迁移模式": PatternModule,
}

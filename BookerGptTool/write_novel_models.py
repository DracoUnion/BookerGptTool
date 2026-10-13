from pydantic import BaseModel, Field
from typing import List, Optional


class ConceptPlanningResult(BaseModel):
    """前置规划：把一句简介压成题材、卖点、hook、premise、故事引擎和第一卷方向。"""

    title: str = Field(..., description="书名")
    genre: str = Field(..., description="题材大类")
    sub_genre: str = Field(..., description="子题材/细分标签")
    main_consumption_points: List[str] = Field(..., description="主要消费点/爽点列表")
    hook: str = Field(..., description="一句话 Hook，用于成交")
    premise: str = Field(..., description="Premise，包含主角、异常局面、眼前麻烦")
    protagonist_goal: str = Field(..., description="主角当前最紧迫的目标/麻烦")
    main_conflict_source: str = Field(..., description="主要阻力来源")
    story_engine: str = Field(..., description="故事引擎说明：接下来10-20章靠什么持续推进")
    target_length: str = Field(..., description="目标长度判断（短篇/中篇/长篇/连载）")
    length_reasoning: str = Field(..., description="长度判断理由")
    volume_one_goal: str = Field(..., description="第一卷最小目标")
    volume_one_ending_change: str = Field(..., description="卷末核心变化/兑现点")
    confidence_score: float = Field(..., description="可写性置信度 0-1", ge=0, le=1)
    diagnosis_notes: List[str] = Field(default_factory=list, description="诊断备注：卡在哪层、缺什么")


class OpeningPlanResult(BaseModel):
    """开头计划：黄金三章推进、抓手、卖点落地、主角亮相、异常局面。"""

    golden_three_chapters: List[str] = Field(..., description="黄金三章每章核心事件")
    chapter_one_hook: str = Field(..., description="第一章抓手（前300-500字）")
    chapter_one_selling_point: str = Field(..., description="第一章卖点落地（前1500-3000字）")
    chapter_one_protagonist_reveal: str = Field(..., description="主角亮相方式")
    abnormal_situation: str = Field(..., description="异常局面设置")
    first_6000_words_plan: str = Field(..., description="前6000字整体推进计划")


class OpeningContentResult(BaseModel):
    """开头正文：第1-3章（黄金三章）。"""

    chapters: List[str] = Field(..., description="第1、2、3章正文，每章以 # 第X章 开头")


class VolumeOutlineItem(BaseModel):
    """分卷规划项。"""

    no: int = Field(..., description="卷号")
    title: str = Field(..., description="卷标题")
    goal: str = Field(..., description="卷目标")
    core_conflict: str = Field(..., description="卷核心冲突")
    climax: str = Field(..., description="卷高潮")
    ending_change: str = Field(..., description="卷末变化")


class ChapterOutlineItem(BaseModel):
    """单章章纲。"""

    no: int = Field(..., description="章号")
    title: str = Field(..., description="章标题")
    core_event: str = Field(..., description="核心事件")
    protagonist_goal: str = Field(..., description="主角本章目标")
    obstacle: str = Field(..., description="阻力/冲突")
    situation_change: str = Field(..., description="本章结束局面变化")
    hook_next: str = Field(..., description="章末钩子/下章承接")


class VolumeOutlineResult(BaseModel):
    """分卷章纲结果。"""

    golden_three_chapters_detail: List[str] = Field(..., description="黄金三章详细拆解")
    volumes: List[VolumeOutlineItem] = Field(..., description="分卷列表")
    chapter_outlines: List[ChapterOutlineItem] = Field(..., description="前 N 章章纲")


class CharacterState(BaseModel):
    """人物状态快照：用于跨章保持一致。"""

    chapter_no: int = Field(..., description="章号")
    goal: str = Field(..., description="当前目标")
    emotion: str = Field(..., description="当前情绪")
    key_relationships: dict = Field(default_factory=dict, description="关键人物关系温度")
    physical_state: str = Field(..., description="身体/伤势状态")
    knowledge_state: str = Field(..., description="已知信息/秘密状态")
    voice_style: str = Field(..., description="说话风格/口吻特征")


class ConsistencyReviewResult(BaseModel):
    """完稿一致性复查：六种一致性，任意两项不稳需返修。"""

    chapter_no: int = Field(..., description="章号")
    plot_logic_ok: bool = Field(..., description="剧情逻辑一致性")
    character_goal_ok: bool = Field(..., description="人物目标一致性")
    emotion_relationship_ok: bool = Field(..., description="情绪与关系一致性")
    physical_info_ok: bool = Field(..., description="身体与信息状态一致性")
    scene_transition_ok: bool = Field(..., description="场景与转场一致性")
    chapter_end_hook_ok: bool = Field(..., description="章末承接一致性")
    overall_pass: bool = Field(..., description="整体是否通过；六项中任意两项不稳即为 false")
    issues: List[str] = Field(default_factory=list, description="需返工的问题汇总")

# -*- coding: utf-8 -*-
"""
ppt_models.py —— guizang-ppt-skill 转换后的网页 PPT 子命令所用的 Pydantic 模型。

模型覆盖：主题选择、单页幻灯片规划、整份 Deck 规划、以及最终交付结果。
所有字段均带有中文 description，供 LLM 生成与解析使用。
"""

from typing import List
from pydantic import BaseModel, Field


class Theme(BaseModel):
    """一套主题（风格 A 5 套 / 风格 B 4 套，只允许从预设里选）。"""
    style: str = Field(..., description="风格：A（电子杂志风）或 B（瑞士国际主义风）")
    name: str = Field(..., description="主题名称，来自 themes.md 或 themes-swiss.md 的预设")
    description: str = Field(..., description="主题的一句话描述，说明适合什么内容")


class Slide(BaseModel):
    """单页幻灯片规划：包含稳定页面 ID、版式、主题节奏与演讲备注。"""
    slide_id: str = Field(..., description="稳定且唯一的页面 ID（data-slide-id），跨页不随重排变化")
    layout: str = Field(..., description="版式：风格 A 用 Layout 1-10；风格 B 用登记的 S01-S22 编号")
    theme_class: str = Field(..., description="该页主题节奏：hero light / hero dark / light / dark 之一")
    title: str = Field(..., description="该页主标题（观众可见）")
    section: str = Field(default="", description="所属章节名，仅当大纲已给出章节或连续页明显同章节时填写")
    purpose: str = Field(default="", description="这一页在整场叙事中要达成的任务（演讲备注）")
    talk: List[str] = Field(default_factory=list, description="演讲者补充信息列表，不复述页面可见文字")
    minutes: str = Field(default="-", description="建议讲述时长（分钟）；未知用横杠")
    transition: str = Field(default="", description="为什么下一页紧接着出现（演讲备注）")


class DeckPlan(BaseModel):
    """整份 Deck 的规划：标题、风格、主题、受众、时长与逐页明细。"""
    title: str = Field(..., description="Deck 标题，用于 <title> 与封面")
    style: str = Field(..., description="风格：A（电子杂志风）或 B（瑞士国际主义风）")
    theme_name: str = Field(..., description="所选主题名（themes.md / themes-swiss.md 预设）")
    audience: str = Field(default="", description="受众与分享场景（行业内部 / 商业发布 / demo day / 私享会）")
    duration_minutes: str = Field(default="-", description="总分享时长（分钟），用于控制页数")
    slides: List[Slide] = Field(default_factory=list, description="逐页幻灯片规划列表")


class DeckResult(BaseModel):
    """Deck 生成结果：交付文件路径与基本信息。"""
    output_path: str = Field(..., description="生成的 index.html 绝对路径")
    style: str = Field(..., description="实际采用的风格 A 或 B")
    theme_name: str = Field(..., description="实际采用的主题名")
    slide_count: int = Field(default=0, description="生成的幻灯片页数")
    status: str = Field(..., description="状态：成功写入 index.html，或失败说明")

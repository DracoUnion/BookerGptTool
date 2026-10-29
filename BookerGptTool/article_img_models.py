# -*- coding: utf-8 -*-
"""
article_img_models.py —— 文章配图的数据模型
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


# ── 配图计划 ──────────────────────────────────────────────
class ImagePlan(_Base):
    """单张配图的计划"""
    index: int = Field(..., description="配图序号（从1开始）")
    section_title: str = Field(..., description="对应文章的小节标题")
    section_summary: str = Field(..., description="小节内容摘要")
    image_type: Literal[
        "flow",      # 流程图：展示 Pipeline / 工作流
        "compare",   # 对比图：A vs B
        "pipeline",  # 链路图：从输入到产出的完整路径
        "grid",      # 网格卡片：分类展示多个项目
        "rating",    # 星级评分：适配度 / 推荐度
        "timeline",  # 时间线
        "quote",     # 语录气泡
        "big_number",# 大数字 + 描述
        "table",     # 表格/矩阵
        "closing",   # 金句终页
        "cover",     # 封面图
    ] = Field(..., description="配图类型")
    visual_concept: str = Field(..., description="视觉概念描述（给 LLM 生成 prompt 用）")
    key_elements: List[str] = Field(default_factory=list, description="关键视觉元素列表")
    is_dark: bool = Field(..., description="是否暗底（True=暗底#1A3328，False=浅底#F2EDE3）")


class ImagePlanList(_Base):
    """整篇文章的配图计划列表"""
    article_title: str = Field(..., description="文章标题")
    total_images: int = Field(..., description="配图总数")
    plans: List[ImagePlan] = Field(..., description="配图计划列表")


# ── 生成的配图页面 ────────────────────────────────────────
class ImagePage(_Base):
    """生成的单张配图页面数据（用于渲染 HTML）"""
    index: int = Field(..., description="配图序号")
    total: int = Field(..., description="配图总数")
    section_title: str = Field(..., description="对应小节标题")
    image_type: str = Field(..., description="配图类型")
    is_dark: bool = Field(..., description="是否暗底")
    section_tag: str = Field(..., description="英文大写标签（如 PIPELINE, COMPARE 等）")
    title: str = Field(..., description="中文标题")
    content_html: str = Field(..., description="页面内容 HTML（已渲染好的卡片/流程/对比等）")


# ── 类型映射：配图类型 → section_tag ──────────────────────
IMAGE_TYPE_TAGS = {
    "flow": "PIPELINE",
    "compare": "COMPARE",
    "pipeline": "LINKAGE",
    "grid": "GRID",
    "rating": "RATING",
    "timeline": "TIMELINE",
    "quote": "QUOTE",
    "big_number": "STATS",
    "table": "MATRIX",
    "closing": "CLOSING",
    "cover": "COVER",
}

# ── 颜色常量（来自 cover-template.md）──────────────────────
class Colors:
    DARK_BG = "#1A3328"
    LIGHT_BG = "#F2EDE3"
    DARK_TEXT = "#F2EDE3"
    LIGHT_TEXT = "#1A3328"
    ACCENT = "#C44536"      # 鱼红
    ACCENT_LIGHT = "rgba(196,69,54,0.15)"
    ACCENT_BORDER = "rgba(196,69,54,0.3)"
    BRAND_DARK = "rgba(255,255,255,0.5)"
    BRAND_LIGHT = "rgba(26,51,40,0.4)"
    MUTED_DARK = "rgba(255,255,255,0.25)"
    MUTED_LIGHT = "rgba(26,51,40,0.35)"
    SUBTLE_DARK = "rgba(255,255,255,0.08)"
    SUBTLE_LIGHT = "rgba(26,51,40,0.08)"
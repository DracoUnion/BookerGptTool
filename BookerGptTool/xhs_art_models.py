# -*- coding: utf-8 -*-
"""
xhs_art_models.py —— 小红书轮播图 + 发布文案的数据模型
"""

from typing import List, Literal
from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class XhsCard(_Base):
    """一张小红书轮播卡片"""
    type: Literal[
        "cover",      # 封面（大标题+hook）
        "concept",    # 核心概念
        "flow",       # 流程/实战
        "highlight",  # 亮点
        "method",     # 方法论
        "cta",        # 行动召唤
    ] = Field(..., description="卡片类型")
    title: str = Field(..., description="页内大标题")
    body: str = Field(..., description="页内正文")
    sub: str = Field(default='', description="可选副文字")


class XhsCopy(_Base):
    """小红书发布文案：标题 + 正文 + 标签"""
    title: str = Field(..., description="文案标题")
    body: str = Field(..., description="文案正文")
    tags: List[str] = Field(default_factory=list, description="标签列表")

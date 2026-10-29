# -*- coding: utf-8 -*-
"""
jike_art_models.py —— 即刻发布文案的数据模型
"""

from typing import List
from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class JikeCopy(_Base):
    """即刻发布文案：正文 + 话题圈"""
    body: str = Field(..., description="动态正文")
    circles: List[str] = Field(default_factory=list, description="话题圈列表")

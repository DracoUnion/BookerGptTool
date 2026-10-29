# -*- coding: utf-8 -*-
"""
podcast_models.py —— 播客/小宇宙发布文案的数据模型
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class PodcastCopy(_Base):
    """小宇宙发布文案：标题 + 简介 + 文稿"""
    title: str = Field(..., description="节目标题")
    description: str = Field(..., description="节目简介（100-200 字）")
    show_notes: str = Field(..., description="文稿/shownotes")
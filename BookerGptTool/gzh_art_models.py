# -*- coding: utf-8 -*-
"""
gzh_art_models.py —— 公众号出稿的数据模型
"""

from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class GzhArticle(_Base):
    """生成的公众号文章：标题 + 完整 Markdown 正文"""
    title: str = Field(..., description="文章标题")
    article: str = Field(..., description="完整文章 Markdown（第一行为 # 标题）")
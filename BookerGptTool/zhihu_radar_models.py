# -*- coding: utf-8 -*-
"""zhihu-radar 子命令的 Pydantic 数据模型。"""
from typing import List, Optional
from pydantic import BaseModel, Field


class ZhihuQAItem(BaseModel):
    """知乎推荐流中的一条「问题 + 回答」。"""
    feed_id: str = Field(..., description='信息流条目 ID')
    page: int = Field(..., description='来源页码')
    question_id: str = Field('', description='问题 ID')
    question_title: str = Field('', description='问题标题')
    question_url: str = Field('', description='问题链接')
    answer_id: str = Field('', description='回答 ID')
    answer_url: str = Field('', description='回答链接')
    author: str = Field('', description='答主昵称')
    excerpt: str = Field('', description='回答摘要')
    content: str = Field('', description='回答正文（已清洗、截断）')
    voteup_count: int = Field(0, description='回答点赞数')


class Verdict(BaseModel):
    """大模型对一条「问题 + 回答」是否违背核心观点的判断。"""
    index: int = Field(..., description='在输入数组中的下标')
    violates: bool = Field(..., description='是否违背核心观点')
    violated_point_ids: List[str] = Field(default_factory=list, description='被违背的核心观点编号列表')
    reason: str = Field('', description='判断理由（具体说明是问题还是回答、如何违背了哪条观点）')


class AnswerOutline(BaseModel):
    """为违背核心观点的问题+回答拟定的符合观点的回答大纲。"""
    title: str = Field(..., description='回答标题（一句话概括立场）')
    outline: List[str] = Field(default_factory=list, description='回答大纲要点，可直接展开成完整回答')


class RadarResult(BaseModel):
    """一条最终输出：违背核心观点的问题+回答 + 判断理由 + 回答大纲。"""
    question_title: str = Field(..., description='问题标题')
    question_url: str = Field('', description='问题链接')
    answer_id: str = Field('', description='回答 ID')
    answer_url: str = Field('', description='回答链接')
    author: str = Field('', description='答主昵称')
    excerpt: str = Field('', description='回答摘要')
    content: str = Field('', description='回答正文（已清洗、截断）')
    violates: bool = Field(..., description='是否违背核心观点')
    violated_point_ids: List[str] = Field(default_factory=list, description='被违背的核心观点编号')
    reason: str = Field('', description='判断理由')
    outline_title: str = Field('', description='拟定回答的标题')
    outline: List[str] = Field(default_factory=list, description='拟定回答大纲')

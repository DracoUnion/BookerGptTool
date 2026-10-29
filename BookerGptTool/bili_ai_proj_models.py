from typing import List, Optional
from pydantic import BaseModel, Field


class BiliVideo(BaseModel):
    """B站搜索结果中的一个视频"""
    keyword: str = Field(..., description='搜索关键词')
    bvid: str = Field(..., description='B站视频 BV 号')
    title: str = Field(..., description='视频标题（已去除 HTML 标签）')
    author: str = Field(..., description='UP主名')
    url: str = Field(..., description='视频链接')
    pubdate: Optional[int] = Field(None, description='发布时间时间戳')
    duration: str = Field('', description='视频时长，如 12:34')
    play: int = Field(0, description='播放量')
    like: int = Field(0, description='点赞数')
    description: str = Field('', description='视频简介')
    tags: str = Field('', description='视频标签')


class BiliSearchResult(BaseModel):
    """某个关键词的搜索结果"""
    keyword: str = Field(..., description='搜索关键词')
    videos: List[BiliVideo] = Field(default_factory=list, description='搜索结果视频列表')


class AppItem(BaseModel):
    """筛选出的精品 AI 应用"""
    rank: int = Field(..., description='排名（1-20，请按推荐程度排序）')
    name: str = Field(..., description='应用名称（根据视频标题提炼）')
    category: str = Field(..., description='分类：游戏/项目/应用/软件/网站')
    url: str = Field(..., description='B站介绍视频链接')
    author: str = Field(..., description='UP主')
    play: int = Field(0, description='视频播放量')
    duration: str = Field('', description='视频时长')
    reason: str = Field(..., description='推荐理由（为什么这是值得关注的精品 AI 应用）')
    source_keyword: str = Field('', description='来源搜索关键词')


class AppList(BaseModel):
    """精选 AI 应用列表"""
    apps: List[AppItem] = Field(default_factory=list, description='精选出的精品 AI 应用')
from pydantic import BaseModel, Field
from typing import List


class WikiPagePlan(BaseModel):
    """一篇归档源编译时，LLM 规划出的单个页面沉淀项。"""
    file: str = Field(
        ...,
        description='要创建或更新的页面文件名，必须以 .md 结尾且不含目录分隔符（不得使用 /）',
    )
    action: str = Field(
        ...,
        description='对页面的操作，create 表示新建，update 表示合并更新原有页面',
    )
    focus: str = Field(
        ...,
        description='本页面的核心主题，用一句话概括，用于抽取 frontmatter 的 description',
    )
    category: str = Field(
        '',
        description='页面所属分类，须取自给定的分类列表，若不在列表内则留空',
    )
    tags: List[str] = Field(
        [],
        description='页面标签列表，全部转为小写，用于分类索引聚合',
    )


class WikiFixDecision(BaseModel):
    """坏链修复时，LLM 对单个坏链目标做出的处理决策。"""
    target: str = Field(
        ...,
        description='需要处理的坏链目标名（wikilink 指向但页面不存在的名字）',
    )
    action: str = Field(
        ...,
        description='处理动作：rename 为改名为现有页面，drop 为删除该链接，keep 为保留并标记（未建）',
    )
    to: str = Field(
        '',
        description='rename 动作时改成的目标页面名，必须为现有页面，其余动作留空',
    )
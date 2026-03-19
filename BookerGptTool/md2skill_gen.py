from typing import *

def generate_reference_md(skill: List[Dict[str, str]]) -> str:
    """生成 references/source.md：原始提取文本"""
    return f"""# {skill['name']} — 参考资料

> 来源 chunk #{skill.source_chunk_index}
> 上下文: {skill.source_context}

---

{skill['raw_text']}
"""


def generate_skill_md(skill: List[Dict[str, str]]) -> str:
    """
    生成符合 Anthropic Claude Code Skills 规范的 SKILL.md。

    格式:
    ---
    name: kebab-case-name
    description: 一句话描述何时使用此技能
    ---
    # 标题
    ## When to Use
    ## Core Logic
    ## References
    """
    prereqs = skill.get('prerequisites', '无')
    prereqs = ", ".join(prereqs)
    conf = skill.get('confidence', 0)
    confidence_pct = f"{conf:.0%}"

    # 构建描述：触发条件就是最佳的 description
    description = skill['trigger'].rstrip("。.") if skill.trigger else skill['name']

    frontmatter = f"""---
name: {skill['name']}
description: {description}
---"""

    body_section = skill.body.strip() if skill.body else "（内容待补充）"

    return f"""{frontmatter}

# {skill['name']}

## When to Use

{skill['trigger']}description

## Core Logic

{body_section}

## Metadata

| 属性 | 值 |
|------|-----|
| 领域 | {skill['domain']} |
| 类型 | {skill['type']} ({skill['type']}) |
| 置信度 | {confidence_pct} |
| 前置条件 | {prereqs} |
| 来源 | {skill.source_ref} |
"""

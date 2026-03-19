from typing import *
from collections import defaultdict
from datetime import datetime
from enum import Enum
import json
import zipfile
import re
from io import BytesIO

class SKUType(Enum):
    """知识单元类型"""

    FACTUAL = "factual"          # 事实型：人物档案、数据、事件、设定
    PROCEDURAL = "procedural"    # 程序型：流程、策略、战术、操作规范
    RELATIONAL = "relational"    # 关系型：标签树、派系网络、术语表

def generate_claude_skills(
    skills: list[Dict[str, str]],
    zip_fname: str,
):
    """
    将 ValidatedSkill 列表转为 Claude Code Skills 标准目录结构。

    生成后自动扫描注册到 SkillRegistry（热插拔）。

    Args:
        skills: 最终的 ValidatedSkill 列表
        book_name: 书名/文档名
        output_dir: 输出根目录（默认使用配置）

    Returns:
        生成的 skills 目录路径
    """
    book_name = zip_fname[:-4]
    bio = BytesIO()
    zip = zipfile.ZipFile(bio, 'w')
    # safe_name = re.sub(r'[^\w\-]', '_', book_name)

    # 生成每个技能的 SKILL.md + scripts/ 模板
    for skill in skills:
        print(f'[5] {skill["name"]}')
        slug = skill['slug']
        zip.writestr(f'{slug}/SKILL.md', generate_skill_md(skill))
        zip.writestr(f'{slug}/references/source.md', generate_reference_md(skill))
        run_py = (
            f'"""Skill 执行脚本模板 — {skill["name"]}"""\n\n'
            f"# 触发条件: {skill['trigger']}\n"
            f'# 领域: {skill["domain"]}\n\n'
            f'def main():\n'
            f'    """实现 Skill 逻辑"""\n'
            f'    pass\n\n'
            f'if __name__ == "__main__":\n'
            f'    main()\n',
        )
        zip.writestr(f'{slug}/scripts/run.py', run_py)

    # 生成索引
    zip.writestr(f'index.md', generate_index(skills, book_name))
    # 生成 manifest.json
    zip.writestr(f'manifest.json', json.dumps(
        generate_manifest(skills, book_name),
        ensure_ascii=False,
        indent=2,
    ))

    zip.close()
    open(zip_fname, 'wb').write(bio.getvalue())

def generate_manifest(
    skills: list[Dict[str, str]],
    book_name: str,
) -> dict:
    """生成 manifest.json 能力摘要"""
    return {
        "name": book_name,
        "generated_at": datetime.now().isoformat(),
        "total_skills": len(skills),
        "domains": list({s['domain'] for s in skills}),
        "type_distribution": {
            t.value: sum(1 for s in skills if s['type'] == t.value)
            for t in SKUType
        },
        "skills": [
            {
                "slug": s['slug'],
                "name": s['name'],
                "domain": s['domain'],
                "type": s['type'],
                "trigger": s['trigger'],
                "confidence": s['confidence'],
            }
            for s in skills
        ],
    }

def generate_index(
    skills: list[Dict[str, str]],
    book_name: str,
) -> str:
    """生成 index.md 技能导航索引"""
    by_domain =  defaultdict(list)
    for s in skills:
        by_domain[s['domain']].append(s)

    by_type: dict[str, int] = defaultdict(int)
    for s in skills:
        by_type[s['type']] += 1

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {book_name} — Claude Skills 索引",
        "",
        f"> 生成时间：{now}",
        f"> 技能总数：{len(skills)}",
        f"> 事实型：{by_type.get('factual', 0)} | "
        f"程序型：{by_type.get('procedural', 0)} | "
        f"关系型：{by_type.get('relational', 0)}",
        "",
        "---",
        "",
    ]

    for domain in sorted(by_domain.keys()):
        domain_skills = by_domain[domain]
        lines.append(f"## {domain} ({len(domain_skills)})")
        lines.append("")
        lines.append("| 技能 | 触发条件 | 类型 | 置信度 |")
        lines.append("|------|---------|------|--------|")
        for s in sorted(domain_skills, key=lambda x: x.name):
            slug = s['slug']
            trigger = (
                s['trigger'][:50] + "…" if len(s['trigger']) > 50 else s['trigger']
            )
            lines.append(
                f"| [{s['name']}](./{slug}/SKILL.md) "
                f"| {trigger} "
                f"| {s['type']} "
                f"| {s['confidence']:.0%} |"
            )
        lines.append("")

    return "\n".join(lines)

def generate_reference_md(skill: List[Dict[str, str]]) -> str:
    """生成 references/source.md：原始提取文本"""
    return f"""# {skill['name']} — 参考资料

> 来源 chunk #{skill['chunk_idx']}
> 上下文: {skill['raw_context']}

---

{skill['raw_content']}
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
    description = skill['trigger'].rstrip("。.") if skill['trigger'] else skill['name']

    frontmatter = f"""---
name: {skill['name']}
description: {description}
---"""

    body_section = skill['body'].strip() if skill['body'] else "（内容待补充）"

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

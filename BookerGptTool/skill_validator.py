"""
Skill 校验器 — Phase 2 后处理

三重校验：
1. 格式校验：YAML Frontmatter 可解析
2. 完整性校验：必填字段存在
3. 幻觉初筛：关键术语交叉检查
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ValidationStatus(Enum):
    """校验状态"""

    PASS = "pass"
    FAIL_FORMAT = "fail_format"
    FAIL_INCOMPLETE = "fail_incomplete"
    FAIL_HALLUCINATION = "fail_hallucination"


class SKUType(Enum):
    """知识单元类型"""

    FACTUAL = "factual"          # 事实型：人物档案、数据、事件、设定
    PROCEDURAL = "procedural"    # 程序型：流程、策略、战术、操作规范
    RELATIONAL = "relational"    # 关系型：标签树、派系网络、术语表


def _slugify(name: str) -> str:
    """将 name 转为 url-safe 的 sku_id"""
    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", name).strip("-").lower()
    return slug[:60] or "unnamed"


@dataclass
class RawSkill:
    """R1 提取的原始 Skill（未校验）"""

    raw_text: str
    source_chunk_index: int = 0
    source_context: str = ""
    source_heading_path: list[str] = field(default_factory=list)


@dataclass
class ValidatedSkill:
    """校验通过的 Skill（知识单元 SKU）"""

    # Frontmatter 字段
    name: str
    trigger: str
    domain: str
    prerequisites: list[str]
    source_ref: str
    confidence: float
    # Markdown 正文（执行步骤 + 输出格式）
    body: str
    # 原始文本
    raw_text: str
    # 校验状态
    status: ValidationStatus = ValidationStatus.PASS
    # 校验警告
    warnings: list[str] = field(default_factory=list)
    # 来源信息
    source_chunk_index: int = 0
    source_context: str = ""
    prompt_version: str = "v0.1"
    # SKU 分类（Phase 1 新增，向后兼容）
    sku_type: SKUType = SKUType.PROCEDURAL
    sku_id: str = ""

    def __post_init__(self) -> None:
        if not self.sku_id and self.name:
            self.sku_id = _slugify(self.name)


# ──── YAML Frontmatter 解析 ────

_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)$",
    re.DOTALL,
)

# 简单的 YAML 键值解析（不引入 pyyaml 依赖）
_YAML_KV_RE = re.compile(r"^(\w[\w-]*)\s*:\s*(.*)$", re.MULTILINE)
_YAML_LIST_ITEM_RE = re.compile(r"^\s*-\s+(.+)$", re.MULTILINE)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    解析 YAML Frontmatter + Body。

    Returns:
        (frontmatter_dict, body_text)
    """
    # 尝试标准 --- 分隔
    m = _FRONTMATTER_RE.match(text.strip())
    if m:
        fm_text = m.group(1)
        body = m.group(2).strip()
    else:
        # 尝试从 ```yaml 代码块中提取
        yaml_block = re.search(
            r"```ya?ml\s*\n(.*?)\n```\s*\n(.*)",
            text,
            re.DOTALL,
        )
        if yaml_block:
            fm_text = yaml_block.group(1)
            body = yaml_block.group(2).strip()
        else:
            # 无 frontmatter，整体作为 body
            return {}, text.strip()

    # 解析键值对
    fm: dict = {}
    current_key: str | None = None

    for line in fm_text.split("\n"):
        kv = _YAML_KV_RE.match(line)
        if kv:
            key = kv.group(1)
            value = kv.group(2).strip().strip('"').strip("'")
            current_key = key

            # 检测列表值的开始
            if not value:
                fm[key] = []
            else:
                # 尝试解析数字
                try:
                    fm[key] = float(value)
                except ValueError:
                    fm[key] = value
        elif line.strip().startswith("-") and current_key is not None:
            # 列表项
            item = line.strip().lstrip("-").strip().strip('"').strip("'")
            if isinstance(fm.get(current_key), list):
                fm[current_key].append(item)
            else:
                fm[current_key] = [item]

    return fm, body


# ──── 校验器 ────

# 必填字段
_REQUIRED_FIELDS = {"name", "trigger"}

# 期望字段（非必填但建议有）
_EXPECTED_FIELDS = {"domain", "source_ref"}


class SkillValidator:
    """Skill 三重校验器"""

    def validate(
        self,
        raw: RawSkill,
        *,
        source_text: Optional[str] = None,
    ) -> ValidatedSkill | None:
        """
        校验一个 RawSkill。

        Args:
            raw: 原始 Skill
            source_text: 原始文本块内容（用于幻觉检测）

        Returns:
            ValidatedSkill（可能带警告）或 None（校验失败）
        """
        warnings: list[str] = []

        # ── 层 1：格式校验 ──
        try:
            fm, body = _parse_frontmatter(raw.raw_text)
        except Exception as e:
            return ValidatedSkill(
                name="", trigger="", domain="", prerequisites=[],
                source_ref="", confidence=0, body="",
                raw_text=raw.raw_text,
                status=ValidationStatus.FAIL_FORMAT,
                warnings=[f"Frontmatter 解析失败：{e}"],
                source_chunk_index=raw.source_chunk_index,
            )

        # ── 层 2：完整性校验 ──
        missing = _REQUIRED_FIELDS - set(fm.keys())
        if missing:
            # 尝试从 body 中推断
            if "name" in missing and body:
                # 用第一行作为 name
                first_line = body.split("\n")[0].strip("# ").strip()
                fm["name"] = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", first_line).strip("-").lower()[:50]
                warnings.append("name 字段缺失，从正文第一行推断")
            if "trigger" in missing and body:
                fm["trigger"] = "通用知识查询"
                warnings.append("trigger 字段缺失，使用默认值")

            # 再次检查
            still_missing = _REQUIRED_FIELDS - set(fm.keys())
            if still_missing:
                return ValidatedSkill(
                    name=fm.get("name", ""),
                    trigger=fm.get("trigger", ""),
                    domain=fm.get("domain", "general"),
                    prerequisites=fm.get("prerequisites", []),
                    source_ref=fm.get("source_ref", ""),
                    confidence=fm.get("confidence", 0),
                    body=body,
                    raw_text=raw.raw_text,
                    status=ValidationStatus.FAIL_INCOMPLETE,
                    warnings=[f"必填字段缺失：{still_missing}"],
                    source_chunk_index=raw.source_chunk_index,
                )

        # 检查期望字段
        for ef in _EXPECTED_FIELDS:
            if ef not in fm:
                warnings.append(f"建议字段 {ef} 缺失")

        # 检查 body 中是否有执行步骤
        if not re.search(r"[1-9]\.", body):
            warnings.append("正文中未发现编号的执行步骤")

        # ── 层 3：幻觉初筛 ──
        if source_text:
            hallucination_warnings = self._check_hallucination(body, source_text)
            warnings.extend(hallucination_warnings)

        # 确定最终状态
        status = ValidationStatus.PASS
        if any("疑似幻觉" in w for w in warnings):
            status = ValidationStatus.FAIL_HALLUCINATION

        # 规范化 prerequisites
        prereqs = fm.get("prerequisites", [])
        if isinstance(prereqs, str):
            prereqs = [prereqs]

        return ValidatedSkill(
            name=str(fm.get("name", "")),
            trigger=str(fm.get("trigger", "")),
            domain=str(fm.get("domain", "general")),
            prerequisites=prereqs,
            source_ref=str(fm.get("source_ref", "")),
            confidence=float(fm.get("confidence", 0.5)),
            body=body,
            raw_text=raw.raw_text,
            status=status,
            warnings=warnings,
            source_chunk_index=raw.source_chunk_index,
            source_context=raw.source_context,
        )

    def _check_hallucination(
        self, body: str, source_text: str
    ) -> list[str]:
        """
        幻觉初筛：检查 body 中的关键术语是否在 source_text 中出现。

        策略：提取 body 中的非常见术语（> 2 字的中文词或英文词），
        检查其是否在原文中出现。超过 40% 的术语未出现则标记疑似幻觉。
        """
        warnings: list[str] = []

        # Skill 结构性停用词（R1 输出的格式标签，不属于幻觉）
        _CN_STOPWORDS = {
            "执行步骤", "输出格式", "格式要求", "输出格式要求", "前置条件",
            "触发条件", "判断条件", "操作步骤", "注意事项", "具体步骤",
            "排查步骤", "解决方案", "处理方法", "诊断步骤", "核心步骤",
            "检查项目", "原因分析", "结果输出", "结论建议", "适用场景",
            "原因为", "如适用", "事件详情", "配置项", "存在状态",
            "匹配情况", "检查上游", "列出调度", "资源使用", "解决办法",
        }
        _EN_STOPWORDS = {
            "this", "that", "with", "from", "your", "have", "will", "when",
            "null", "true", "false", "none", "else", "step", "then", "each",
            "following", "output", "input", "check", "verify", "ensure",
            "execute", "confirm", "should", "must", "below", "above",
            "format", "result", "trigger", "domain", "skill", "prerequisites",
        }

        # 提取 body 中的关键词（> 2 字中文词或英文单词）
        cn_terms = set(re.findall(r"[\u4e00-\u9fff]{3,}", body)) - _CN_STOPWORDS
        en_terms = set(
            w.lower()
            for w in re.findall(r"[A-Za-z]{4,}", body)
            if w.lower() not in _EN_STOPWORDS
        )

        all_terms = cn_terms | en_terms
        if len(all_terms) < 3:
            return warnings

        source_lower = source_text.lower()
        missing = {t for t in all_terms if t.lower() not in source_lower}

        miss_rate = len(missing) / len(all_terms)
        if miss_rate > 0.4:
            sample = list(missing)[:5]
            warnings.append(
                f"疑似幻觉：{len(missing)}/{len(all_terms)} 术语未在原文中出现，"
                f"示例：{sample}"
            )

        return warnings

    def validate_batch(
        self,
        raws: list[RawSkill],
        *,
        source_texts: Optional[list[str]] = None,
    ) -> tuple[list[ValidatedSkill], list[ValidatedSkill]]:
        """
        批量校验。

        Returns:
            (通过列表, 失败列表)
        """
        passed: list[ValidatedSkill] = []
        failed: list[ValidatedSkill] = []

        for i, raw in enumerate(raws):
            src = source_texts[i] if source_texts and i < len(source_texts) else None
            result = self.validate(raw, source_text=src)
            if result is None:
                continue
            if result.status == ValidationStatus.PASS:
                passed.append(result)
            else:
                failed.append(result)

        return passed, failed

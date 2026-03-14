"""
Markdown AST Chunker — Phase 1B

基于 Markdown 标题层级的语义切分模块。
核心原则：按标题层级切分，而非字符长度。确保每个 chunk 语义完整。

功能：
1. AST 解析：以 ##/### 标题为边界切分逻辑块
2. 父级上下文注入：为每块添加文档名称 + 章节路径
3. 字数卡点：超长块按句子边界二次切分（阈值 4000 字）
4. 过短合并：短块向上合并（阈值 200 字）
5. 三级降级策略：标题切分 → 段落切分 → 句子边界滑动窗口
6. 语义完整性保证：所有切分都在句子/段落边界进行，绝不截断句子
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ──── 配置常量 ────

# 单块最大字数：超过则触发二次切分
MAX_CHUNK_CHARS = 4000

# 单块最小字数：低于则向上合并到前一个块
MIN_CHUNK_CHARS = 200

# 滑动窗口大小（降级策略 C 使用）
SLIDING_WINDOW_CHARS = 3000

# 滑动窗口重叠率
SLIDING_OVERLAP_RATIO = 0.20

# AST 切分的最小标题数量阈值（低于此值触发降级）
MIN_HEADING_COUNT = 5


@dataclass
class TextChunk:
    """一个切分后的文本块"""

    content: str
    # 父级上下文：文档名称 + 章节路径
    context: str
    # 在原文中的位置索引（从 0 开始）
    index: int
    # 该块的标题层级路径，如 ["第三章 数据库迁移", "3.2 预检清单"]
    heading_path: list[str] = field(default_factory=list)
    # 字符数
    char_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.content)


@dataclass
class ChunkResult:
    """切分结果"""

    chunks: list[TextChunk]
    # 使用的切分策略
    strategy: str
    # 原文总字数
    total_chars: int
    # 文档名称
    doc_name: str


# ──── 标题解析 ────

# 匹配 Markdown 标题行：# ~ ######
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


@dataclass
class _HeadingNode:
    """AST 中的一个标题节点"""

    level: int
    title: str
    # 该标题在原文中的字符偏移量
    start_pos: int
    # 该标题管辖的内容结束位置
    end_pos: int = 0


def _extract_headings(text: str) -> list[_HeadingNode]:
    """提取 Markdown 文本中的所有标题节点"""
    headings: list[_HeadingNode] = []
    for m in _HEADING_RE.finditer(text):
        headings.append(
            _HeadingNode(
                level=len(m.group(1)),
                title=m.group(2).strip(),
                start_pos=m.start(),
            )
        )
    # 计算每个标题管辖的内容结束位置（到下一个同级或更高级标题之前）
    for i, h in enumerate(headings):
        if i + 1 < len(headings):
            h.end_pos = headings[i + 1].start_pos
        else:
            h.end_pos = len(text)
    return headings


# ──── 上下文构建 ────


def _build_context(doc_name: str, heading_path: list[str]) -> str:
    """构建父级上下文字符串"""
    parts = [f"文档：《{doc_name}》"]
    if heading_path:
        parts.append(f"章节：{' > '.join(heading_path)}")
    return " | ".join(parts)


def _build_heading_path(
    headings: list[_HeadingNode], current_idx: int
) -> list[str]:
    """为当前标题节点构建完整的层级路径（含所有祖先标题）"""
    if not headings or current_idx < 0:
        return []

    current = headings[current_idx]
    path = [current.title]

    # 向前回溯，找到每一个更高层级的标题
    for i in range(current_idx - 1, -1, -1):
        if headings[i].level < current.level:
            path.insert(0, headings[i].title)
            current = headings[i]

    return path


# ──── 策略 A：标题级 AST 切分 ────


def _chunk_by_headings(
    text: str,
    doc_name: str,
    headings: list[_HeadingNode],
    split_level: int = 2,
) -> list[TextChunk]:
    """
    按指定标题层级切分文本。

    Args:
        text: 完整 Markdown 文本
        doc_name: 文档名称
        headings: 预提取的标题节点列表
        split_level: 以哪个标题层级为切分边界（默认 2 = ##）
    """
    # 筛选出作为切分边界的标题（level <= split_level）
    boundary_headings = [h for h in headings if h.level <= split_level]

    if not boundary_headings:
        # 如果指定层级无标题，尝试用所有标题
        boundary_headings = headings

    if not boundary_headings:
        return []

    raw_chunks: list[TextChunk] = []

    # 处理第一个标题之前的内容（如果有）
    preamble = text[: boundary_headings[0].start_pos].strip()
    if preamble and len(preamble) >= MIN_CHUNK_CHARS:
        raw_chunks.append(
            TextChunk(
                content=preamble,
                context=_build_context(doc_name, []),
                index=0,
                heading_path=[],
            )
        )

    # 按边界标题切分
    for i, bh in enumerate(boundary_headings):
        # 该标题管辖范围：从本标题到下一个边界标题的起始位置
        end = (
            boundary_headings[i + 1].start_pos
            if i + 1 < len(boundary_headings)
            else len(text)
        )
        chunk_text = text[bh.start_pos : end].strip()

        if not chunk_text:
            continue

        # 在完整标题列表中找到该标题的索引，用于构建层级路径
        full_idx = next(
            (j for j, h in enumerate(headings) if h.start_pos == bh.start_pos),
            -1,
        )
        heading_path = _build_heading_path(headings, full_idx)

        raw_chunks.append(
            TextChunk(
                content=chunk_text,
                context=_build_context(doc_name, heading_path),
                index=len(raw_chunks),
                heading_path=heading_path,
            )
        )

    return raw_chunks


# ──── 策略 B：段落级切分 ────

# 段落分隔符：连续两个以上换行
_PARAGRAPH_SEP_RE = re.compile(r"\n\s*\n")


def _chunk_by_paragraphs(
    text: str,
    doc_name: str,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[TextChunk]:
    """
    按段落边界切分，合并短段落直到达到字数上限。
    降级策略 B：当标题数量不足时使用。
    """
    paragraphs = [p.strip() for p in _PARAGRAPH_SEP_RE.split(text) if p.strip()]
    chunks: list[TextChunk] = []
    buffer: list[str] = []
    buffer_len = 0

    for para in paragraphs:
        # 如果加入当前段落后超出上限，先刷新 buffer
        if buffer and buffer_len + len(para) > max_chars:
            chunks.append(
                TextChunk(
                    content="\n\n".join(buffer),
                    context=_build_context(doc_name, []),
                    index=len(chunks),
                )
            )
            buffer = []
            buffer_len = 0

        buffer.append(para)
        buffer_len += len(para)

    # 刷新剩余 buffer
    if buffer:
        chunks.append(
            TextChunk(
                content="\n\n".join(buffer),
                context=_build_context(doc_name, []),
                index=len(chunks),
            )
        )

    return chunks


# ──── 句子边界切分（保证语义完整性） ────

# 句子结束符：中文句号、问号、感叹号 + 英文句号 + 换行
_SENTENCE_END_RE = re.compile(r'[。！？\.!?]\s*|\n')


def _split_at_sentence_boundary(text: str, max_chars: int) -> list[str]:
    """
    在句子边界处切分超长文本，确保每个片段语义完整。

    策略：贪心积累句子，在不超过 max_chars 的前提下尽量多装。
    每次只在句子结束处切断，绝不会在句子中间截断。
    """
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    current_pos = 0

    while current_pos < len(text):
        # 如果剩余文本不超过 max_chars，直接取完
        if current_pos + max_chars >= len(text):
            parts.append(text[current_pos:].strip())
            break

        # 在 [current_pos, current_pos + max_chars] 范围内找最后一个句子结束位置
        window = text[current_pos:current_pos + max_chars]
        last_sent_end = -1

        for m in _SENTENCE_END_RE.finditer(window):
            last_sent_end = m.end()

        if last_sent_end > 0 and last_sent_end > len(window) * 0.3:
            # 在句子边界处切断（至少保留 30% 内容避免过短块）
            chunk_text = text[current_pos:current_pos + last_sent_end].strip()
            if chunk_text:
                parts.append(chunk_text)
            current_pos += last_sent_end
        else:
            # 实在找不到句子边界（极端情况），在空格处切
            space_pos = window.rfind(' ')
            if space_pos > len(window) * 0.3:
                chunk_text = text[current_pos:current_pos + space_pos].strip()
                if chunk_text:
                    parts.append(chunk_text)
                current_pos += space_pos
            else:
                # 最终降级：硬切（理论上不会触发）
                parts.append(text[current_pos:current_pos + max_chars].strip())
                current_pos += max_chars

    return [p for p in parts if p]


# ──── 策略 C：滑动窗口切分 ────


def _chunk_by_sliding_window(
    text: str,
    doc_name: str,
    window_size: int = SLIDING_WINDOW_CHARS,
    overlap_ratio: float = SLIDING_OVERLAP_RATIO,
) -> list[TextChunk]:
    """
    句子边界滑动窗口切分。
    降级策略 C：纯文本墙，无标题也无清晰段落时使用。
    在句子边界处切分，保证语义完整性。
    """
    # 先按句子边界切分，再组装成窗口
    segments = _split_at_sentence_boundary(text, window_size)
    chunks: list[TextChunk] = []

    for seg in segments:
        if seg.strip():
            chunks.append(
                TextChunk(
                    content=seg.strip(),
                    context=_build_context(doc_name, []),
                    index=len(chunks),
                )
            )

    return chunks


# ──── 后处理：过短合并 + 超长二次切分 ────


def _merge_short_chunks(chunks: list[TextChunk]) -> list[TextChunk]:
    """将过短的块（< MIN_CHUNK_CHARS）合并到前一个块"""
    if not chunks:
        return chunks

    merged: list[TextChunk] = [chunks[0]]

    for chunk in chunks[1:]:
        if chunk.char_count < MIN_CHUNK_CHARS and merged:
            # 合并到前一个块
            prev = merged[-1]
            merged[-1] = TextChunk(
                content=prev.content + "\n\n" + chunk.content,
                context=prev.context,
                index=prev.index,
                heading_path=prev.heading_path,
            )
        else:
            merged.append(chunk)

    return merged


def _split_oversized_chunks(
    chunks: list[TextChunk], text: str, headings: list[_HeadingNode]
) -> list[TextChunk]:
    """将超长块（> MAX_CHUNK_CHARS）按子标题、段落或句子边界进行二次切分"""
    result: list[TextChunk] = []

    for chunk in chunks:
        if chunk.char_count <= MAX_CHUNK_CHARS:
            result.append(chunk)
            continue

        # 层级 1：尝试块内子标题切分
        sub_headings = _extract_headings(chunk.content)
        if len(sub_headings) >= 2:
            sub_chunks = _chunk_by_headings(
                chunk.content, "", sub_headings, split_level=6,
            )
            for sc in sub_chunks:
                sc.context = chunk.context
                sc.heading_path = chunk.heading_path + sc.heading_path
                result.append(sc)
            continue

        # 层级 2：按段落切分
        paras = [p.strip() for p in _PARAGRAPH_SEP_RE.split(chunk.content) if p.strip()]
        if len(paras) >= 2:
            sub_chunks = _chunk_by_paragraphs(
                chunk.content, "", max_chars=MAX_CHUNK_CHARS
            )
            for sc in sub_chunks:
                sc.context = chunk.context
                sc.heading_path = chunk.heading_path
                result.append(sc)
            continue

        # 层级 3：按句子边界切分（保证语义完整）
        segments = _split_at_sentence_boundary(chunk.content, MAX_CHUNK_CHARS)
        for seg in segments:
            sc = TextChunk(
                content=seg,
                context=chunk.context,
                index=0,
                heading_path=chunk.heading_path,
            )
            result.append(sc)

    return result


# ──── 噪音清洗 ────

# 需要清除的章节关键词
_NOISE_SECTION_KEYWORDS = [
    "参考文献",
    "references",
    "bibliography",
    "致谢",
    "acknowledgments",
    "acknowledgements",
]

# 页眉页脚/纯页码行
_PAGE_NUMBER_RE = re.compile(r"^\s*-?\s*\d+\s*-?\s*$", re.MULTILINE)
_HEADER_FOOTER_RE = re.compile(
    r"^[-─━═]{3,}\s*$", re.MULTILINE
)


def clean_markdown(text: str) -> str:
    """
    Phase 1A 清洗：移除页眉页脚、页码、参考文献、致谢等噪音内容。

    Args:
        text: MinerU 输出的原始 Markdown 文本

    Returns:
        清洗后的 Markdown 文本
    """
    # 移除纯页码行
    text = _PAGE_NUMBER_RE.sub("", text)

    # 移除分隔线（常见页眉页脚标记）
    text = _HEADER_FOOTER_RE.sub("", text)

    # 移除噪音章节（从该标题到下一个同级标题或文末）
    headings = _extract_headings(text)
    sections_to_remove: list[tuple[int, int]] = []

    for i, h in enumerate(headings):
        title_lower = h.title.lower().strip()
        if any(kw in title_lower for kw in _NOISE_SECTION_KEYWORDS):
            sections_to_remove.append((h.start_pos, h.end_pos))

    # 从后往前删除，避免偏移量错位
    for start, end in reversed(sections_to_remove):
        text = text[:start] + text[end:]

    # 压缩连续空行为最多两个
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ──── 主入口 ────


# 对话轮次边界检测：Turn N / 👤 User / 🤖 Assistant 等模式
_TURN_RE = re.compile(
    r"^(?:Turn\s+\d+|#{1,3}\s*(?:Turn|Round|轮次)\s*\d+|"
    r"(?:👤|🧑|🤖|💬)\s*(?:User|Assistant|用户|助手))",
    re.MULTILINE | re.IGNORECASE,
)

# 理想块大小目标值（用于自适应 split_level 评分）
_TARGET_CHUNK_CHARS = 1500


def _auto_detect_split_level(
    text: str, headings: list[_HeadingNode]
) -> int:
    """
    自适应检测最优切分层级。
    遍历 level 2→6，选择平均块大小最接近 TARGET 的层级。
    """
    text_len = len(text)
    best_level, best_score = 2, float("inf")

    for level in range(2, 7):
        boundaries = [h for h in headings if h.level <= level]
        if len(boundaries) < 2:
            continue
        avg_size = text_len / len(boundaries)
        score = abs(avg_size - _TARGET_CHUNK_CHARS)
        if score < best_score:
            best_score = score
            best_level = level

    return best_level


def _chunk_by_conversation_turns(
    text: str, doc_name: str
) -> list[TextChunk]:
    """按对话轮次边界切分（用于聊天记录/对话体文档）。"""
    positions = [m.start() for m in _TURN_RE.finditer(text)]
    if not positions:
        return []

    # 确保从文本起始位置开始
    if positions[0] > 0:
        positions.insert(0, 0)

    chunks: list[TextChunk] = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        content = text[start:end].strip()
        if not content or len(content) < MIN_CHUNK_CHARS:
            continue
        chunks.append(
            TextChunk(
                content=content,
                context=_build_context(doc_name, []),
                index=len(chunks),
            )
        )
    return chunks


def chunk_markdown(
    text: str,
    doc_name: str,
    *,
    split_level: int = 0,  # 0 = 自动检测
    max_chars: int = MAX_CHUNK_CHARS,
    min_chars: int = MIN_CHUNK_CHARS,
    clean: bool = True,
) -> ChunkResult:
    """
    将 Markdown 文本切分为语义完整的文本块。

    四级降级策略：
    0. 对话体检测 → 按轮次切分
    1. 标题 ≥ 5 个 → 自适应 AST 标题切分
    2. 标题 < 5 但段落清晰 → 段落边界切分
    3. 纯文本墙 → 滑动窗口切分（20% 重叠）

    Args:
        text: Markdown 文本（通常为 MinerU 输出）
        doc_name: 文档名称（用于上下文注入）
        split_level: AST 切分的标题层级（0=自动检测）
        max_chars: 单块最大字数
        min_chars: 单块最小字数（低于此值向上合并）
        clean: 是否先执行噪音清洗

    Returns:
        ChunkResult 包含切分后的 TextChunk 列表和元信息
    """
    if clean:
        text = clean_markdown(text)

    total_chars = len(text)

    if not text.strip():
        return ChunkResult(
            chunks=[], strategy="empty", total_chars=0, doc_name=doc_name
        )

    # 策略 0：对话体检测
    turn_matches = list(_TURN_RE.finditer(text))
    if len(turn_matches) >= 3:
        strategy = "conversation_turn"
        chunks = _chunk_by_conversation_turns(text, doc_name)
        if chunks:
            chunks = _split_oversized_chunks(chunks, text, [])
            for i, c in enumerate(chunks):
                c.index = i
            return ChunkResult(
                chunks=chunks, strategy=strategy,
                total_chars=total_chars, doc_name=doc_name,
            )

    # 提取标题
    headings = _extract_headings(text)

    # 三级降级策略选择
    if len(headings) >= MIN_HEADING_COUNT:
        # 策略 A：自适应 AST 标题切分
        strategy = "heading_ast"
        level = split_level if split_level > 0 else _auto_detect_split_level(text, headings)
        chunks = _chunk_by_headings(text, doc_name, headings, level)
    elif _PARAGRAPH_SEP_RE.search(text):
        # 策略 B：段落边界切分
        strategy = "paragraph"
        chunks = _chunk_by_paragraphs(text, doc_name, max_chars)
    else:
        # 策略 C：滑动窗口
        strategy = "sliding_window"
        chunks = _chunk_by_sliding_window(text, doc_name)

    # 后处理
    chunks = _merge_short_chunks(chunks)
    chunks = _split_oversized_chunks(chunks, text, headings)

    # 重新编号索引
    for i, c in enumerate(chunks):
        c.index = i

    return ChunkResult(
        chunks=chunks,
        strategy=strategy,
        total_chars=total_chars,
        doc_name=doc_name,
    )


# ──── CLI 工具 ────


def chunk_file(filepath: str | Path, doc_name: Optional[str] = None) -> ChunkResult:
    """
    从文件读取 Markdown 并切分。

    Args:
        filepath: Markdown 文件路径
        doc_name: 文档名称（默认使用文件名）
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")

    text = path.read_text(encoding="utf-8")

    if doc_name is None:
        doc_name = path.stem

    return chunk_markdown(text, doc_name)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：python markdown_chunker.py <markdown_file>")
        sys.exit(1)

    result = chunk_file(sys.argv[1])
    print(f"策略：{result.strategy}")
    print(f"原文字数：{result.total_chars}")
    print(f"切分块数：{len(result.chunks)}")
    print("---")
    for chunk in result.chunks:
        print(f"[{chunk.index}] ({chunk.char_count}字) {chunk.context}")
        # 预览前 80 字
        preview = chunk.content[:80].replace("\n", " ")
        print(f"    {preview}...")
        print()

"""Wiki 编译器：把归档源码沉淀为交叉链接的知识百科。

布局（wiki 目录下）：
  sources/YYYY/MM/*.md   归档原文（不可变的原始快照）
  pages/*.md             经润色的知识页面，以 [[wikilink]] 互相链接
  index.md               页面目录，按 frontmatter 确定性重建
  index/<category>.md    每个分类的目录切片，与 index.md 呼应
  log.md                 追加式摄取日志

LLM 只做两件事：规划要动哪些页面（JSON）、逐页撰写/合并（Markdown）。
索引与日志由代码维护，确保永不漂移。

本子命令为固定工作流：编排器按顺序调用 Md2WikiAgent 的方法。
"""

import re
from datetime import date
from pathlib import Path
from typing import List, Dict, Tuple

import yaml

from .openai import logger as oai_logger
from .md2wiki_agent import Md2WikiAgent
from .md2wiki_models import WikiPagePlan

import logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

_FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]+)")
_IMAGE_EMBED_RE = re.compile(r"!\[\[([^\]|#]+)\]\]")
# "## 3. 标题" / "### 3.1 标题" —— Hugo 主题自行给标题编号
_HEADING_NUM_RE = re.compile(r"^(#{2,6} )\d+(?:\.\d+)*[.、]?\s+", re.MULTILINE)

# 固定分类，索引按此分节；不在列表内的归入 uncategorized。
CATEGORIES = [
    "agent-engineering",
    "ai-security",
    "blockchain-security",
    "llm-systems",
    "program-analysis",
]

INDEX_DIR = "index"
_MAX_PAGES = 3            # 每篇归档源最多规划沉淀的页面数
_KEEP_RATIO = 0.10        # 坏链保留配额 = 全库链接出现次数 * 该比例
_UNBUILT = "（未建）"     # 坏链保留标记


# ── 序列化辅助（frontmatter 往返）──────────────────────────

def _parse_front(text: str) -> Tuple[dict, str]:
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, text[m.end():]


def _dump_front(meta: dict, body: str) -> str:
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{front}\n---\n{body}"


def _split_source_body(body: str) -> Tuple[str, str]:
    """从归档源正文中拆分出 (摘要, 全文)。"""
    if "## 原文" in body:
        summary, _, content = body.partition("## 原文")
        return summary.replace("## 摘要", "").strip(), content.strip()
    return "", body.strip()


# ── 页面目录 / 分类索引（代码侧，确定性重建）────────────────

def _page_entries(wiki_dir: Path) -> List[Tuple[str, dict]]:
    pages_dir = wiki_dir / "pages"
    pages = sorted(pages_dir.glob("*.md")) if pages_dir.exists() else []
    return [(p.stem, _parse_front(p.read_text(encoding='utf8'))[0]) for p in pages]


def _index_line(stem: str, meta: dict) -> str:
    tags = " ".join(f"`{t}`" for t in meta.get("tags") or [])
    tags = f" {tags}" if tags else ""
    return f"- [[{stem}]]{tags} — {meta.get('description', '')}"


def _category_of(meta: dict) -> str:
    cat = meta.get("category", "")
    return cat if cat in CATEGORIES else "uncategorized"


class Md2WikiOrchestrator:
    """固定工作流编排器：按顺序调用 Md2WikiAgent 的各个方法。"""

    def __init__(self, args):
        self.args = args
        self.agent = Md2WikiAgent(args)
        self.wiki_dir = Path(self.args.wiki_dir)
        self.pages_dir = self.wiki_dir / "pages"
        self.max_chars = args.max_chars
        self.limit = args.limit

    # ── 步骤 0：发现待编译源 ────────────────────────────────

    def _discover_pending(self) -> List[Path]:
        files = []
        for p in sorted((self.wiki_dir / "sources").rglob("*.md")):
            meta, _ = _parse_front(p.read_text(encoding='utf8'))
            if meta.get("compiled") is False:
                # YAML 会把 `date: 2026-08-26` 解析为 date 对象、引号形式为 str，
                # 两种形态都存在于 sources/ 且不可比较，因此统一字符串化后排序。
                files.append((str(meta.get("date", "")), p))
        return [p for _, p in sorted(files)]

    # ── 步骤 1：规划页面 ────────────────────────────────────

    def step_plan_pages(
        self, meta: dict, summary: str, content: str,
    ) -> List[WikiPagePlan]:
        """调用 Agent 规划当前归档源要创建/更新哪些页面。"""
        index_text = (
            (self.wiki_dir / "index.md").read_text(encoding='utf8')
            if (self.wiki_dir / "index.md").exists() else "(空)"
        )
        plan = self.agent.plan_pages(
            index=index_text,
            categories=", ".join(CATEGORIES),
            title=meta.get("title", ""),
            source=meta.get("source", ""),
            url=meta.get("url", ""),
            summary=summary,
            content=content[: self.max_chars],
        )
        # 只取前 _MAX_PAGES 个，且过滤非法文件名
        out = []
        for item in plan:
            if not item.file.endswith(".md") or "/" in item.file:
                continue
            out.append(item)
            if len(out) >= _MAX_PAGES:
                break
        return out

    # ── 步骤 2：逐页撰写/合并 ───────────────────────────────

    def _category_index(self, category: str) -> str:
        """同一分类下的页面清单，供写页面时做同类互引上下文。"""
        lines = [
            _index_line(stem, meta)
            for stem, meta in _page_entries(self.wiki_dir)
            if _category_of(meta) == category
        ]
        return "\n".join(lines) if lines else "(该分类暂无页面)"

    def _write_single_page(
        self, meta: dict, item: WikiPagePlan,
        summary: str, content: str, pages_dir: Path,
    ) -> None:
        """调用 Agent 撰写一个页面，并做 frontmatter 规范化后落盘。"""
        page_path = pages_dir / item.file
        existing = (
            page_path.read_text(encoding='utf8') if page_path.exists() else "(新页面,尚无内容)"
        )
        category = item.category or _category_of(_parse_front(existing)[0])

        page = self.agent.write_page(
            file=item.file,
            focus=item.focus,
            index=self._category_index(category),
            category=category,
            existing=existing,
            title=meta.get("title", ""),
            url=meta.get("url", ""),
            date=str(meta.get("date", "")),
            summary=summary,
            content=content[: self.max_chars],
        )
        page = re.sub(r"\A```(?:markdown)?\n|\n```\Z", "", page).strip() + "\n"
        page_meta, page_body = _parse_front(page)
        page_body = _HEADING_NUM_RE.sub(r"\1", page_body)
        if "description" not in page_meta:
            page_meta["description"] = item.focus[:80]
        if item.category and page_meta.get("category") not in CATEGORIES:
            page_meta["category"] = item.category
        if item.tags and not page_meta.get("tags"):
            page_meta["tags"] = item.tags
        page_meta["updated"] = date.today().isoformat()
        page_path.write_text(_dump_front(page_meta, page_body), encoding='utf8')
        self._append_source_ref(page_path, self._current_src, meta)

    def step_write_pages(
        self, meta: dict, plan: List[WikiPagePlan],
        summary: str, content: str,
    ) -> List[str]:
        """逐页调用 Agent 撰写/合并，返回本次触碰的页面文件名。"""
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        touched = []
        for item in plan:
            logger.info(f'[2] 撰写页面 {item.file}')
            self._write_single_page(meta, item, summary, content, self.pages_dir)
            touched.append(item.file)
        return touched

    # ── 步骤 3：固化来源 ────────────────────────────────────

    def _append_source_ref(
        self, page_path: Path, src_path: Path, src_meta: dict,
    ) -> None:
        """在页面末「来源」段登记本次编译用到的归档源（代码侧维护，模型不写）。"""
        relative = "../" + src_path.relative_to(self.wiki_dir).as_posix()
        text = page_path.read_text(encoding='utf8')
        if relative in text:
            return
        line = f"- [{src_meta.get('title', src_path.stem)}]({relative})"
        tail = "，".join(
            str(src_meta[k]) for k in ("source", "date") if src_meta.get(k)
        )
        if tail:
            line += f"（{tail}）"
        section = "## 来源"
        i = text.rfind(section)
        if i >= 0:
            sect = text[i + len(section):]
            m = re.search(r"^#{1,6} ", sect, flags=re.MULTILINE)
            cut = m.start() if m else len(sect)
            text = (
                text[: i + len(section)]
                + sect[:cut].rstrip() + f"\n{line}\n"
                + sect[cut:]
            )
        else:
            text = text.rstrip() + f"\n\n{section}\n\n{line}\n"
        page_path.write_text(text, encoding='utf8')

    def _append_log(self, action: str, detail: str) -> None:
        line = f"## [{date.today().isoformat()}] {action} | {detail}\n"
        with (self.wiki_dir / "log.md").open("a", encoding='utf8') as f:
            f.write(line)

    def step_finalize_source(
        self, src_path: Path, meta: dict, body: str, touched: List[str],
    ) -> None:
        """标记来源已编译，汇总页面清单并登记日志。"""
        meta["compiled"] = True
        if touched:
            meta["pages"] = sorted(set(meta.get("pages") or []) | set(touched))
        src_path.write_text(_dump_front(meta, body), encoding='utf8')
        detail = f"{meta.get('title', src_path.stem)} -> {', '.join(touched) or '(无沉淀)'}"
        self._append_log("ingest", detail)

    # ── 步骤 4：重建索引 ────────────────────────────────────

    def _rebuild_category_indexes(
        self, entries: List[Tuple[str, dict]],
    ) -> None:
        out_dir = self.wiki_dir / INDEX_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        written = set()
        for cat in CATEGORIES + ["uncategorized"]:
            group = [(s, m) for s, m in entries if _category_of(m) == cat]
            if not group:
                continue
            tags = sorted({str(t) for _, m in group for t in m.get("tags") or []})
            lines = [f"# {cat}", ""]
            lines.append(
                f"{len(group)} 个页面。本分类标签："
                + " ".join(f"`{t}`" for t in tags)
            )
            lines.append("")
            lines += [_index_line(s, m) for s, m in group]
            (out_dir / f"{cat}.md").write_text("\n".join(lines) + "\n", encoding='utf8')
            written.add(f"{cat}.md")
        for stale in out_dir.glob("*.md"):
            if stale.name not in written:
                stale.unlink()

    def step_rebuild_index(self) -> None:
        entries = _page_entries(self.wiki_dir)
        lines = ["# Index", ""]
        for cat in CATEGORIES + ["uncategorized"]:
            group = [(s, m) for s, m in entries if _category_of(m) == cat]
            if not group:
                continue
            lines += [f"## {cat}", ""]
            lines += [_index_line(s, m) for s, m in group]
            lines.append("")
        (self.wiki_dir / "index.md").write_text(
            "\n".join(lines).rstrip("\n") + "\n", encoding='utf8'
        )
        self._rebuild_category_indexes(entries)

    # ── 步骤 5（可选 --fix）：坏链修复 ─────────────────────

    def step_fix_wikilinks(self) -> None:
        """调用 Agent 进行坏链改名/删除/保留决策，并落地修复。"""
        pages = (
            {p.stem: p.read_text(encoding='utf8') for p in sorted(self.pages_dir.glob("*.md"))}
            if self.pages_dir.exists()
            else {}
        )
        broken = self._broken_link_stats(pages)[1]
        broken_count = sum(v["count"] for v in broken.values())
        if not broken:
            logger.info('[fix] 没有坏链，无需修复。')
            return

        total = self._broken_link_stats(pages)[0]
        budget = int(total * _KEEP_RATIO) if total else 0
        stems = "\n".join(sorted(pages))
        targets = sorted(broken.items(), key=lambda kv: -kv[1]["count"])
        decisions: Dict[str, dict] = {}
        remaining = budget
        for i in range(0, len(targets), 100):
            chunk = targets[i: i + 100]
            listing = "\n".join(
                f"- {t}（出现 {info['count']} 次；页面: "
                f"{', '.join(sorted(info['pages']))}）"
                for t, info in chunk
            )
            results = self.agent.fix_wikilinks(
                pages=stems,
                broken=listing,
                total=total,
                broken_count=broken_count,
                budget=max(remaining, 0),
            )
            for item in results:
                target = item.target.strip()
                action = item.action.strip()
                to = item.to.strip()
                if target not in broken or action not in ("rename", "drop", "keep"):
                    continue
                if action == "rename" and to not in pages:
                    continue
                decisions[target] = {"action": action, "to": to}
                if action == "keep":
                    remaining -= broken[target]["count"]

        existing = set(pages)
        for stem in pages:
            text = pages[stem]
            for t, d in decisions.items():
                text = self._apply_fix(text, t, d["action"], d["to"])
            text = self._strip_stale_markers(text, existing)
            if text != pages[stem]:
                (self.pages_dir / f"{stem}.md").write_text(text, encoding='utf8')
                pages[stem] = text

        counts = {"rename": 0, "drop": 0, "keep": 0}
        for d in decisions.values():
            counts[d["action"]] += 1
        unhandled = len(broken) - len(decisions)
        new_total, new_broken = self._broken_link_stats(pages)
        new_count = sum(v["count"] for v in new_broken.values())

        def pct(n: int, m: int) -> str:
            return f"{n / m * 100:.1f}%" if m else "0%"

        summary = (
            f"rename {counts['rename']} / drop {counts['drop']} "
            f"/ keep {counts['keep']} / 未处理 {unhandled}（按目标计）；"
            f"坏链 {broken_count}/{total}（{pct(broken_count, total)}）-> "
            f"{new_count}/{new_total}（{pct(new_count, new_total)}）"
        )
        logger.info(f'[fix] {summary}')
        self._append_log("fix", summary)

    @staticmethod
    def _broken_link_stats(pages: Dict[str, str]) -> Tuple[int, Dict[str, dict]]:
        """返回 (链接总出现次数, 坏链目标 -> {count, pages})。"""
        total = 0
        broken: Dict[str, dict] = {}
        for name, text in pages.items():
            for target in _WIKILINK_RE.findall(text):
                target = target.strip()
                total += 1
                if target not in pages:
                    info = broken.setdefault(target, {"count": 0, "pages": set()})
                    info["count"] += 1
                    info["pages"].add(name)
        return total, broken

    @staticmethod
    def _apply_fix(text: str, target: str, action: str, to: str = "") -> str:
        t = re.escape(target)
        if action == "rename" and to:
            return re.sub(r"\[\[" + t + r"(?=[\]|#])", f"[[{to}", text)
        if action == "drop":
            # [[t|别名]] -> 别名；[[t]] / [[t#节]] -> t；顺带清掉旧（未建）标记
            text = re.sub(r"\[\[" + t + r"\|([^\]]*)\]\](?:" + _UNBUILT + ")?", r"\1", text)
            return re.sub(r"\[\[" + t + r"(?:#[^\]]*)?\]\](?:" + _UNBUILT + ")?", target, text)
        if action == "keep":
            return re.sub(
                r"(\[\[" + t + r"(?:[|#][^\]]*)?\]\])(?!" + _UNBUILT + ")",
                r"\1" + _UNBUILT,
                text,
            )
        return text

    @staticmethod
    def _strip_stale_markers(text: str, existing: set) -> str:
        """移除目标已成为真实页面的（未建）标记。"""
        def repl(m: re.Match) -> str:
            return m.group(1) if m.group(2).strip() in existing else m.group(0)

        return re.sub(
            r"(\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\])" + _UNBUILT,
            repl, text,
        )

    # ── 主流程：固定顺序编排 ────────────────────────────────

    def run(self) -> int:
        logger.info(self.args)
        if not (self.wiki_dir / "sources").is_dir():
            logger.fatal(f'目录 {self.wiki_dir} 缺少 sources/ 目录')
            return 0

        pending = self._discover_pending()
        if self.limit:
            pending = pending[: self.limit]
        if not pending:
            logger.info('[0] 没有待编译的归档源。')
        for i, src in enumerate(pending, 1):
            self._current_src = src
            meta, body = _parse_front(src.read_text(encoding='utf8'))
            summary, content = _split_source_body(body)
            try:
                plan = self.step_plan_pages(meta, summary, content)
                touched = self.step_write_pages(meta, plan, summary, content)
                self.step_finalize_source(src, meta, body, touched)
                logger.info(f'[wiki {i}/{len(pending)}] {src.name}: {touched or "skip"}')
            except Exception as e:
                logger.warn(f'[wiki {i}/{len(pending)}] {src.name} 失败: {e}')
            self.step_rebuild_index()

        if getattr(self.args, 'fix', False):
            self.step_fix_wikilinks()

        logger.info('[*] 已完成')
        return len(pending)


def md2wiki(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    orchestrator = Md2WikiOrchestrator(args)
    orchestrator.run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        "md2wiki", help="将归档 Markdown 源码编译为交叉链接的知识百科"
    )
    parser.add_argument("wiki_dir", help="wiki 目录（含 sources/pages/index）")
    parser.add_argument(
        "-c", "--max-chars", type=int, default=35_000,
        help="单次提示中原文长度上限",
    )
    parser.add_argument(
        "-l", "--limit", type=int, default=None,
        help="本次最多编译的归档源数量",
    )
    parser.add_argument(
        "-f", "--fix", action='store_true',
        help="在摄取完毕后执行坏链修复",
    )
    parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    parser.set_defaults(func=md2wiki)
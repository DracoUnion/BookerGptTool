"""小红书信息图卡片系列生成器 — 整合自 baoyu-xhs-images skill。

12 种视觉风格 · 8 种布局 · 3 种配色 · 25+ 预设
分析内容 → 生成大纲 → 组装 Prompt → 调用 OpenAI TTI 生成图片
"""

from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from .xhs_img_models import (
    Style, Layout, Palette, Preset, ImagePage, Outline,
    AudienceProfile, ContentAnalysis,
    STYLES, LAYOUTS, PALETTES, PRESETS, AUTO_SELECTION_TABLE,
)
from .util import call_tti_retry

logger = logging.getLogger(__name__)

STYLE_NAMES = list(STYLES.keys())
LAYOUT_NAMES = list(LAYOUTS.keys())
PALETTE_NAMES = list(PALETTES.keys())
PRESET_NAMES = list(PRESETS.keys())

# ════════════════════════════════════════════════════════════════════
# 内容分析
# ════════════════════════════════════════════════════════════════════


def _detect_language(text: str) -> str:
    cn = len(re.findall(r"[一-鿿]", text))
    total = max(len(text.strip()), 1)
    if cn / total > 0.3:
        return "zh"
    if len(re.findall(r"[぀-ゟ゠-ヿ]", text)) / total > 0.1:
        return "ja"
    if len(re.findall(r"[가-힯]", text)) / total > 0.1:
        return "ko"
    return "en"


def _count_images(text: str) -> int:
    n = len([l for l in text.strip().splitlines() if l.strip()])
    if n <= 5: return 3
    if n <= 15: return 4
    if n <= 30: return 6
    if n <= 60: return 8
    return 10


def _auto_select(text: str) -> tuple[Style, Layout, str | None]:
    lower = text.lower()
    for entry in AUTO_SELECTION_TABLE:
        for sig in entry["signals"]:
            if sig.lower() in lower:
                return (STYLES[entry["style"]],
                        LAYOUTS[entry["layouts"][0]],
                        entry["presets"][0] if entry["presets"] else None)
    return STYLES["cute"], LAYOUTS["balanced"], "cute-share"


def _detect_hooks(text: str) -> tuple[int, list[str], str]:
    found, score = [], 2
    lower = text.lower()
    for htype, pat in {
        "数字钩子": r"\d+", "痛点钩子": r"(踩过|后悔|别再|坑|错误)",
        "好奇钩子": r"(原来|竟然|没想到|居然)", "利益钩子": r"(省钱|变美|效率|翻倍|免费)",
        "身份钩子": r"(打工人|学生党|新手|妈妈|必看)",
    }.items():
        if re.search(pat, lower):
            found.append(htype)
            score += 1
    score = min(score, 5)
    suggestion = "建议增强标题钩子：加入数字、身份标签或利益点" if score < 4 else ""
    return score, found, suggestion


def _detect_content_type(text: str) -> str:
    lower = text.lower()
    for kw, ct in [
        (["种草", "安利", "推荐", "product"], "种草/安利"),
        (["教程", "步骤", "tutorial", "how-to"], "教程步骤"),
        (["测评", "对比", "review", "comparison"], "测评对比"),
        (["清单", "排行", "list", "checklist"], "清单合集"),
        (["避坑", "注意", "warning", "别"], "避坑指南"),
        (["故事", "经历", "分享", "story"], "个人故事"),
        (["干货", "知识", "技巧", "tips"], "干货分享"),
    ]:
        if any(k in lower for k in kw):
            return ct
    return "干货分享"


def _detect_audience(text: str) -> AudienceProfile:
    lower = text.lower()
    for kw, name, interests in [
        (["学生", "校园", "考试", "学习"], "学生党", "省钱、学习、校园"),
        (["职场", "效率", "办公", "工具", "打工人"], "打工人", "效率、职场、减压"),
        (["育儿", "宝宝", "家居", "妈妈"], "宝妈", "育儿、家居、省心"),
        (["美妆", "穿搭", "护肤", "精致"], "精致女孩", "美妆、穿搭、仪式感"),
        (["代码", "编程", "技术", "AI", "工具"], "技术宅", "工具、效率、极客"),
        (["美食", "食谱", "好吃", "探店"], "美食爱好者", "探店、食谱、测评"),
        (["旅行", "攻略", "打卡"], "旅行达人", "攻略、打卡、小众"),
    ]:
        if any(k in lower for k in kw):
            return AudienceProfile(name, "通用用户", interests)
    return AudienceProfile("通用用户", "", "广泛兴趣")


def _detect_strategy(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["教程", "步骤", "清单", "工具", "技巧", "干货"]):
        return "b"
    if any(w in lower for w in ["氛围", "美感", "ins风", "vibe", "mood"]):
        return "c"
    return "a"


def analyze_content(text: str) -> ContentAnalysis:
    text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    language = _detect_language(text)
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    title = re.sub(r"^#+\s*", "", lines[0] if lines else "未命名主题")

    content_type = _detect_content_type(text)
    audience = _detect_audience(text)
    hook_score, hook_types, hook_suggestion = _detect_hooks(text)
    image_count = _count_images(text)
    strategy = _detect_strategy(text)
    style, layout, preset = _auto_select(text)

    palette = None
    if style.default_palette and style.default_palette in PALETTES:
        palette = PALETTES[style.default_palette]

    key_points = []
    for l in lines[1:11]:
        cleaned = re.sub(r"^[#\-*•→>]\s*", "", l).strip()
        if cleaned and len(cleaned) < 50:
            key_points.append(cleaned)
    if not key_points:
        key_points = [title]

    visual_opps = []
    if re.search(r"\d+", text):
        visual_opps.append("数据/统计 → 高亮数字")
    if any(w in text.lower() for w in ["对比", "vs", "比较"]):
        visual_opps.append("对比 → 左右分屏")
    if any(w in text.lower() for w in ["步骤", "流程", "方法"]):
        visual_opps.append("步骤 → 编号流程图")

    return ContentAnalysis(
        title=title, topic=title, content_type=content_type,
        source_language=language, key_points=key_points,
        hook_score=hook_score, hook_types_found=hook_types,
        hook_suggestion=hook_suggestion, audience=audience,
        recommended_image_count=image_count,
        recommended_style=style, recommended_layout=layout,
        recommended_palette=palette, recommended_preset=preset,
        recommended_strategy=strategy, visual_opportunities=visual_opps,
        save_value="高" if any(w in text.lower() for w in ["清单", "教程", "工具", "推荐"]) else "中",
        share_triggers=[],
    )


# ════════════════════════════════════════════════════════════════════
# 大纲生成（策略 A/B/C）
# ════════════════════════════════════════════════════════════════════


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:30].rstrip("-") or "topic"


def _gen_outline_a(topic, content_lines, style, layout, palette, n):
    slug = _slugify(topic)
    pages = [ImagePage(1, n, "cover", "sparse", topic, f"{slug}-cover",
                       f"01-cover-{slug}.png", topic, "亲测好用，建议收藏",
                       visual_concept=f"{topic}主题封面", swipe_hook="第一个就很精彩")]
    for i, line in enumerate(content_lines[:n - 2]):
        pn = i + 2
        ps = _slugify(line[:20])
        pages.append(ImagePage(pn, n, "content", layout, "", ps,
                               f"{pn:02d}-content-{ps}.png", line.strip()[:30],
                               core_message=line.strip(),
                               visual_concept=f"展示: {line.strip()[:50]}",
                               swipe_hook="下一页更精彩" if pn < n - 1 else "最后总结"))
    pages.append(ImagePage(n, n, "ending", "sparse", "", f"{slug}-summary",
                           f"{n:02d}-ending-{slug}.png", "总结",
                           subtitle="收藏备用 | 转发给需要的朋友",
                           visual_concept="简洁背景，大字标题，互动引导"))
    return Outline("a", "Story-Driven", style, layout, palette, n, pages)


def _gen_outline_b(topic, content_lines, style, layout, palette, n):
    slug = _slugify(topic)
    pages = [ImagePage(1, n, "cover", "sparse", topic, f"{slug}-cover",
                       f"01-cover-{slug}.png", topic, "干货合集",
                       visual_concept="知识卡片封面", swipe_hook="干货来了")]
    for i, line in enumerate(content_lines[:n - 2]):
        pn = i + 2
        ps = _slugify(line[:20])
        pages.append(ImagePage(pn, n, "content", "dense", "", ps,
                               f"{pn:02d}-content-{ps}.png", line.strip()[:30],
                               core_message=line.strip(),
                               visual_concept=f"知识卡片: {line.strip()[:50]}",
                               swipe_hook="下一个更实用" if pn < n - 1 else "总结来了"))
    pages.append(ImagePage(n, n, "ending", "balanced", "", f"{slug}-summary",
                           f"{n:02d}-ending-{slug}.png", "总结推荐",
                           subtitle="收藏+关注不迷路", visual_concept="总结卡片"))
    return Outline("b", "Information-Dense", style, layout, palette, n, pages)


def _gen_outline_c(topic, content_lines, style, layout, palette, n):
    slug = _slugify(topic)
    pages = [ImagePage(1, n, "cover", "sparse", topic, f"{slug}-hero",
                       f"01-cover-{slug}.png", topic,
                       visual_concept="视觉冲击力封面", swipe_hook="来看看")]
    for i, line in enumerate(content_lines[:n - 2]):
        pn = i + 2
        ps = _slugify(line[:20])
        pages.append(ImagePage(pn, n, "content", "balanced", "", ps,
                               f"{pn:02d}-content-{ps}.png", line.strip()[:30],
                               core_message=line.strip(),
                               visual_concept=f"视觉场景: {line.strip()[:50]}",
                               swipe_hook="还有更多" if pn < n - 1 else "最后"))
    pages.append(ImagePage(n, n, "ending", "sparse", "", f"{slug}-cta",
                           f"{n:02d}-ending-{slug}.png", "关注我",
                           subtitle="更多精彩内容", visual_concept="CTA封面"))
    return Outline("c", "Visual-First", style, layout, palette, n, pages)


# ════════════════════════════════════════════════════════════════════
# Prompt 组装
# ════════════════════════════════════════════════════════════════════

_BASE = """\
Create a Xiaohongshu (Little Red Book) style infographic following these guidelines:

## Image Specifications
- **Type**: Infographic
- **Orientation**: Portrait (vertical)
- **Aspect Ratio**: 3:4
- **Style**: Hand-drawn illustration

## Core Principles
{core}

## Text Style (CRITICAL)
{text}

## Language
- Use the same language as the content provided below
- Match punctuation style to the content language (Chinese: ""，。！)

---

{style_sec}

---

{layout_sec}

---

{content_sec}

---

{wm_sec}

---

Please generate the infographic based on the specifications above."""

DEFAULT_CORE = ("- Hand-drawn quality throughout - NO realistic or photographic elements\n"
                "- Keep information concise, highlight keywords and core concepts\n"
                "- Use ample whitespace for easy visual scanning\n"
                "- Maintain clear visual hierarchy")
DEFAULT_TEXT = ("- **ALL text MUST be hand-drawn style**\n"
                "- Main titles should be prominent and eye-catching\n"
                "- Key text should be bold and enlarged\n"
                "- Use highlighter effects to emphasize keywords\n"
                "- **DO NOT use realistic or computer-generated fonts**")
SCREEN_CORE = ("- Screen print / silkscreen poster art — flat color blocks, NO gradients\n"
               "- Bold silhouettes and symbolic shapes over detailed rendering\n"
               "- Negative space as active storytelling element\n"
               "- One iconic focal point per image — conceptual, not literal")
SCREEN_TEXT = ("- Bold condensed sans-serif or Art Deco influenced lettering\n"
               "- Typography INTEGRATED into composition as design element\n"
               "- High contrast with background, stencil-cut quality\n"
               "- **DO NOT use delicate, thin, or handwritten fonts**")


def _build_prompt(page: ImagePage, style: Style, layout: Layout,
                  palette: Palette | None = None, language: str = "zh") -> str:
    if style.name == "screen-print":
        core, text = SCREEN_CORE, SCREEN_TEXT
    else:
        core, text = DEFAULT_CORE, DEFAULT_TEXT

    sl = [f"## Style: {style.name.title()}\n"]
    if palette:
        sl.append(f"**Color Palette** (overridden by {palette.name} palette):")
        for role, hx in palette.colors.items():
            sl.append(f"- {role}: {hx}")
        sl.append(f"- Background: {palette.background} ({palette.background_hex})")
        sl.append(f"\n**Palette Constraint**: {palette.semantic_constraint}")
    else:
        sl.append("**Color Palette**:")
        for role, hx in style.colors.items():
            sl.append(f"- {role}: {hx}")
    sl.append(f"\n**Visual Elements**:\n{style.visual_elements}")
    sl.append(f"\n**Typography**:\n{style.typography}")

    ly = (f"## Layout: {layout.name.title()}\n\n"
          f"**Information Density**: {layout.info_density}\n"
          f"**Whitespace**: {layout.whitespace_pct}\n\n"
          f"**Structure**:\n{layout.structure}\n\n"
          f"**Best For**:\n{layout.best_for}")

    cs = [f"## Content\n\n**Position**: {page.position.title()} (Page {page.number} of {page.total})"]
    if page.core_message:
        cs.append(f"**Core Message**: {page.core_message}")
    cs.append(f"\n**Text Content**:\n- Title: 「{page.title}」")
    if page.subtitle:
        cs.append(f"- Subtitle: {page.subtitle}")
    if page.points:
        cs.append("- Points:\n" + "\n".join(f"  - {p}" for p in page.points))
    if page.visual_concept:
        cs.append(f"\n**Visual Concept**:\n{page.visual_concept}")

    return _BASE.format(core=core, text=text, style_sec="\n".join(sl),
                        layout_sec=ly, content_sec="\n".join(cs), wm_sec="(No watermark)")


# ════════════════════════════════════════════════════════════════════
# TTI 图片生成（使用全局 openai 模块，由 set_openai_props 配置）
# ════════════════════════════════════════════════════════════════════

DEFAULT_TTI_MODEL = "gpt-image-1"
ASPECT_3_4 = {"gpt-image-1": "1024x1536", "dall-e-3": "1024x1792"}


def _resolve_model(cli_model: str | None = None) -> str:
    if cli_model:
        return cli_model
    env = os.environ.get("OPENAI_TTI_MODEL")
    if env:
        return env.strip()
    return DEFAULT_TTI_MODEL


def _generate_image(prompt: str, output_path: Path, model: str, size: str,
                    ref_image_path: str | None = None) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"生成中... model={model}, size={size}")
    ref_img = Path(ref_image_path).read_bytes() if ref_image_path and Path(ref_image_path).is_file() else None
    data = call_tti_retry(prompt, model, size, ref_img=ref_img, retry=3, nothrow=False)
    output_path.write_bytes(data)
    kb = output_path.stat().st_size / 1024
    logger.info(f"[OK] {output_path.name} ({kb:.0f} KB)")
    return output_path


def generate_series(prompt_files: list[Path], output_dir: Path,
                    model: str | None = None, size: str | None = None) -> list[Path]:
    model = _resolve_model(model)
    if size is None:
        size = ASPECT_3_4.get(model, "1024x1536")

    results: list[Path] = []
    ref_path: str | None = None

    for i, pf in enumerate(prompt_files):
        prompt = pf.read_text(encoding="utf-8")
        out_path = output_dir / (pf.stem + ".png")
        logger.info(f"[{i + 1}/{len(prompt_files)}] {pf.name}")
        try:
            path = _generate_image(prompt, out_path, model, size, ref_path)
            results.append(path)
            if i == 0:
                ref_path = str(path)
        except Exception as ex:
            logger.error(f"跳过失败项: {ex}")
            results.append(out_path)
    return results


# ════════════════════════════════════════════════════════════════════
# CLI Handler
# ════════════════════════════════════════════════════════════════════


def _outline_to_md(outline: Outline) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = [
        f"# Xiaohongshu Infographic Series Outline\n",
        f"---\nstrategy: {outline.strategy}\nname: {outline.name}\n"
        f"style: {outline.style}\nlayout: {outline.default_layout}\n"
        f"palette: {outline.palette or '~'}\ncount: {outline.image_count}\n"
        f"generated: {ts}\n---\n",
    ]
    for p in outline.pages:
        parts.append(f"\n## Image {p.number} of {p.total}\n")
        parts.append(f"**Position**: {p.position.title()}\n**Layout**: {p.layout}\n")
        parts.append(f"**Slug**: {p.slug}\n**Filename**: {p.filename}\n")
        parts.append(f"\n**Title**: 「{p.title}」")
        if p.subtitle:
            parts.append(f"**Subtitle**: {p.subtitle}")
        if p.core_message:
            parts.append(f"**Core Message**: {p.core_message}")
        parts.append(f"\n**Visual**: {p.visual_concept}")
        if p.swipe_hook:
            parts.append(f"**Swipe Hook**: {p.swipe_hook}")
        parts.append("\n---")
    return "\n".join(parts) + "\n"


def xhs_img_handle(args):
    """xhs-img 子命令入口。"""
    if args.input == "-":
        text = sys.stdin.read()
    else:
        p = Path(args.input)
        if not p.is_file():
            print(f"文件不存在: {args.input}")
            return
        text = p.read_text(encoding="utf-8")

    if not text.strip():
        print("输入内容为空")
        return

    analysis = analyze_content(text)
    print(analysis.summary_text())

    preset = PRESETS.get(args.preset) if args.preset else None
    style = STYLES.get(args.style) or (STYLES[preset.style] if preset else analysis.recommended_style)
    layout = LAYOUTS.get(args.layout) or (LAYOUTS[preset.layout] if preset else analysis.recommended_layout)
    palette_name = args.palette or (preset.palette if preset else None)
    palette = PALETTES.get(palette_name) if palette_name else analysis.recommended_palette
    if not palette and style.default_palette and style.default_palette in PALETTES:
        palette = PALETTES[style.default_palette]

    count = args.count or analysis.recommended_image_count
    count = max(2, min(10, count))
    strategy = args.strategy

    print(f"\n选定方案:")
    print(f"  风格: {style.name} · 布局: {layout.name} · 配色: {palette.name if palette else '默认'}")
    print(f"  图片: {count}张 · 策略: {strategy.upper()}")
    print(f"  模型: {_resolve_model(args.tti_model)}")

    slug = _slugify(analysis.topic)
    out_dir = Path(args.output_dir) if args.output_dir else Path("image-cards") / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = out_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)

    content_lines = [l.strip() for l in text.strip().splitlines()
                     if l.strip() and not l.strip().startswith("#")]
    content_lines = [l for l in content_lines if len(l) > 2]

    gen = {"a": _gen_outline_a, "b": _gen_outline_b, "c": _gen_outline_c}
    if strategy == "all":
        for k, fn in gen.items():
            ol = fn(analysis.topic, content_lines, style.name, layout.name,
                    palette.name if palette else None, count)
            (out_dir / f"outline-strategy-{k}.md").write_text(
                _outline_to_md(ol), encoding="utf-8")
        chosen = _gen_outline_a(analysis.topic, content_lines, style.name, layout.name,
                                palette.name if palette else None, count)
    else:
        chosen = gen[strategy](analysis.topic, content_lines, style.name, layout.name,
                               palette.name if palette else None, count)
        (out_dir / "outline.md").write_text(_outline_to_md(chosen), encoding="utf-8")

    for page in chosen.pages:
        prompt = _build_prompt(page, style, layout, palette, analysis.source_language)
        (prompts_dir / f"{page.number:02d}-{page.position}-{page.slug}.md").write_text(
            prompt, encoding="utf-8")

    print(f"\n[Prompt] 文件已生成: {prompts_dir}")

    if args.generate:
        prompt_files = sorted(prompts_dir.glob("*.md"))
        if not prompt_files:
            print("[Error] 未找到 prompt 文件")
            return
        print(f"\n[TTI] 开始生成图片 ({len(prompt_files)} 张)")
        results = generate_series(prompt_files, out_dir, args.tti_model)
        ok = sum(1 for p in results if p.is_file() and p.stat().st_size > 0)
        print(f"\n[Done] {ok}/{len(prompt_files)} 张图片 -> {out_dir}")
    else:
        print(f"[Hint] 添加 --generate 参数可调用 TTI 生成图片")


def register_xhs_img(subparsers):
    """在 BookerGptTool 的 subparsers 中注册 xhs-img 子命令。"""
    p = subparsers.add_parser("xhs-img", help="小红书信息图卡片系列生成器")
    p.add_argument("input", help="输入文件路径，或 - 表示 stdin")
    p.add_argument("--style", choices=STYLE_NAMES, help="视觉风格")
    p.add_argument("--layout", choices=LAYOUT_NAMES, help="信息布局")
    p.add_argument("--palette", choices=PALETTE_NAMES, help="配色方案")
    p.add_argument("--preset", choices=PRESET_NAMES, help="预设组合 (style+layout+palette)")
    p.add_argument("--count", type=int, help="图片数量 (2-10)")
    p.add_argument("--output-dir", "-o", help="输出目录 (默认: ./image-cards/{slug})")
    p.add_argument("--strategy", choices=["a", "b", "c", "all"], default="a",
                   help="大纲策略: a=故事驱动, b=信息密集, c=视觉优先, all=全部")
    p.add_argument("--generate", "-g", action="store_true",
                   help="生成 prompt 后调用 TTI 生成图片")
    p.add_argument("--tti-model", default=None,
                   help="TTI 模型 (默认: gpt-image-1，或通过 OPENAI_TTI_MODEL 设置)")
    p.set_defaults(func=xhs_img_handle)
    return p

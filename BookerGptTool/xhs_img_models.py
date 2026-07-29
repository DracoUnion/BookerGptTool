"""小红书信息图卡片系列 — 数据模型与预设数据。"""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Style:
    name: str
    category: str
    description: str
    colors: dict[str, str]
    visual_elements: str
    typography: str
    best_for: str
    default_palette: str | None = None
    layout_compat: dict[str, str] = field(default_factory=dict)


@dataclass
class Layout:
    name: str
    description: str
    info_density: str
    whitespace_pct: str
    items_per_image: str
    structure: str
    best_for: str


@dataclass
class Palette:
    name: str
    description: str
    background: str
    background_hex: str
    colors: dict[str, str]
    semantic_constraint: str
    best_paired_with: list[str]


@dataclass
class Preset:
    name: str
    style: str
    layout: str
    palette: str | None
    description: str
    category: str


@dataclass
class ImagePage:
    number: int
    total: int
    position: str
    layout: str
    hook: str
    slug: str
    filename: str
    title: str
    subtitle: str = ""
    points: list[str] = field(default_factory=list)
    visual_concept: str = ""
    swipe_hook: str = ""
    core_message: str = ""


@dataclass
class Outline:
    strategy: str
    name: str
    style: str
    default_layout: str
    palette: str | None
    image_count: int
    pages: list[ImagePage] = field(default_factory=list)


@dataclass
class AudienceProfile:
    primary: str
    secondary: str
    interests: str


@dataclass
class ContentAnalysis:
    title: str
    topic: str
    content_type: str
    source_language: str
    key_points: list[str]
    hook_score: int
    hook_types_found: list[str]
    hook_suggestion: str
    audience: AudienceProfile
    recommended_image_count: int
    recommended_style: Style
    recommended_layout: Layout
    recommended_palette: Palette | None
    recommended_preset: str | None
    recommended_strategy: str
    visual_opportunities: list[str]
    save_value: str
    share_triggers: list[str]

    def summary_text(self) -> str:
        sn = {"a": "Story-Driven", "b": "Information-Dense", "c": "Visual-First"}.get(
            self.recommended_strategy, "Story-Driven"
        )
        return "\n".join([
            "内容分析",
            f"  主题：{self.topic} | 类型：{self.content_type}",
            f"  要点：{', '.join(self.key_points[:5])}",
            f"  受众：{self.audience.primary}",
            "",
            "推荐方案（自动匹配）",
            f"  策略：{self.recommended_strategy.upper()} ({sn})",
            f"  风格：{self.recommended_style.name} · 布局：{self.recommended_layout.name}",
            f"  配色：{self.recommended_palette.name if self.recommended_palette else '默认'}",
            f"  预设：{self.recommended_preset or '无'}",
            f"  图片：{self.recommended_image_count}张",
        ])


# ════════════════════════════════════════════════════════════════════
# 12 Styles
# ════════════════════════════════════════════════════════════════════

STYLES: dict[str, Style] = {
    "cute": Style(
        name="cute", category="sweet",
        description="甜美可爱少女风 — 经典小红书美学",
        colors={"primary1": "#FED7E2", "primary2": "#FEEBC8", "primary3": "#C6F6D5",
                "primary4": "#E9D8FD", "background": "#FFFAF0", "background2": "#FFF5F7",
                "accent1": "#FF69B4", "accent2": "#FF6B6B"},
        visual_elements="Hearts, stars, sparkles, cute faces; ribbon decorations, sticker-style; cute stickers, emoji icons; soft, rounded shapes",
        typography="Rounded, bubbly hand lettering; soft shadows, playful decorations; pink/pastel color accents on text",
        best_for="Lifestyle, beauty, fashion, daily tips, personal shares",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "works_well",
                       "list": "highly_recommended", "comparison": "works_well", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "fresh": Style(
        name="fresh", category="natural",
        description="清新自然风格 — 干净、清爽、自然",
        colors={"primary1": "#9AE6B4", "primary2": "#90CDF4", "primary3": "#FAF089",
                "background": "#FFFFFF", "background2": "#F0FFF4",
                "accent1": "#48BB78", "accent2": "#4299E1"},
        visual_elements="Plant leaves, clouds, water drops; simple geometric shapes; breathing room, open composition; natural, organic elements",
        typography="Clean, light hand lettering with breathing room; airy spacing; fresh color accents",
        best_for="Health, wellness, minimalist lifestyle, nature, clean living",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "works_well",
                       "list": "works_well", "comparison": "works_well", "flow": "highly_recommended",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "warm": Style(
        name="warm", category="cozy",
        description="温暖舒适风格 — 友好、亲切、温馨",
        colors={"primary1": "#ED8936", "primary2": "#F6AD55", "primary3": "#C05621",
                "background": "#FFFAF0", "background2": "#FED7AA",
                "accent1": "#744210", "accent2": "#E57373"},
        visual_elements="Sun rays, coffee cups, cozy items; warm lighting effects; friendly, inviting decorations; soft, comfortable shapes",
        typography="Friendly, rounded hand lettering; warm color accents; comfortable, approachable feel",
        best_for="Personal stories, life lessons, emotional content, comfort & lifestyle",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "works_well",
                       "list": "works_well", "comparison": "highly_recommended", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "bold": Style(
        name="bold", category="impact",
        description="高冲击力风格 — 引人注目、强烈视觉冲击",
        colors={"primary1": "#E53E3E", "primary2": "#DD6B20", "primary3": "#F6E05E",
                "background": "#000000", "background2": "#1A1A1A",
                "accent1": "#FFFFFF", "accent2": "#F7FF00"},
        visual_elements="Exclamation marks, arrows, warning icons; strong shapes, high contrast elements; dramatic compositions; bold geometric forms",
        typography="Bold, impactful hand lettering with shadows; high contrast text treatments; large, commanding headlines",
        best_for="Warnings, must-know content, rankings, comparisons, attention hooks",
        layout_compat={"sparse": "highly_recommended", "balanced": "works_well", "dense": "works_well",
                       "list": "highly_recommended", "comparison": "highly_recommended", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "highly_recommended"}),
    "minimal": Style(
        name="minimal", category="elegant",
        description="极简风格 — 超干净、精致、高端",
        colors={"primary1": "#000000", "primary2": "#FFFFFF",
                "background": "#FAFAFA", "background2": "#FFFFFF", "accent1": "#4299E1"},
        visual_elements="Single focal point, thin lines; maximum whitespace; simple, clean decorations; restrained visual elements",
        typography="Clean, simple hand lettering; minimal weight variations; elegant spacing",
        best_for="Professional content, serious topics, elegant presentations, business",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "highly_recommended",
                       "list": "works_well", "comparison": "works_well", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "retro": Style(
        name="retro", category="vintage",
        description="复古风格 — 怀旧、经典、潮流",
        colors={"primary1": "#E07A4D", "primary2": "#D4A5A5", "primary3": "#6B9999",
                "background": "#F5E6D3", "background2": "#E8DCC8",
                "accent1": "#C55A5A", "accent2": "#B8860B"},
        visual_elements="Halftone dots, vintage badges; classic icons, tape effects; aged texture overlays; nostalgic decorative elements",
        typography="Vintage-style hand lettering; classic feel with imperfections; aged texture on text",
        best_for="Throwback content, classic tips, timeless advice, vintage aesthetics",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "works_well",
                       "list": "highly_recommended", "comparison": "works_well", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "pop": Style(
        name="pop", category="energetic",
        description="活泼风格 — 鲜艳、充满活力、抢眼",
        colors={"primary1": "#F56565", "primary2": "#ECC94B", "primary3": "#4299E1",
                "primary4": "#48BB78", "background": "#FFFFFF", "background2": "#F7FAFC",
                "accent1": "#FF69B4", "accent2": "#9F7AEA"},
        visual_elements="Bold shapes, speech bubbles; comic-style effects, starburst; dynamic, energetic compositions; high-energy decorations",
        typography="Dynamic, energetic hand lettering with outlines; bold color combinations; playful, expressive forms",
        best_for="Exciting announcements, fun facts, entertainment, youth-oriented",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "works_well",
                       "list": "highly_recommended", "comparison": "highly_recommended", "flow": "works_well",
                       "mindmap": "works_well", "quadrant": "works_well"}),
    "notion": Style(
        name="notion", category="minimal",
        description="极简手绘线条风 — 知识感、理性、学术",
        colors={"primary1": "#1A1A1A", "primary2": "#4A4A4A",
                "background": "#FFFFFF", "background2": "#FAFAFA",
                "accent1": "#A8D4F0", "accent2": "#F9E79F", "accent3": "#FADBD8"},
        visual_elements="Simple line doodles, hand-drawn wobble effect; geometric shapes, stick figures; maximum whitespace, single-weight ink lines; clean, uncluttered compositions",
        typography="Clean hand-drawn lettering; simple sans-serif labels; minimal decoration on text",
        best_for="Knowledge sharing, concept explanations, SaaS, productivity, tech tutorials",
        layout_compat={k: "highly_recommended" for k in
                       ["sparse", "balanced", "dense", "list", "comparison", "flow", "mindmap", "quadrant"]}),
    "chalkboard": Style(
        name="chalkboard", category="educational",
        description="黑板粉笔风格 — 彩色粉笔手绘、教育感",
        colors={"background": "#1A1A1A", "background2": "#1C2B1C", "text": "#F5F5F5",
                "accent1": "#FFE566", "accent2": "#FF9999", "accent3": "#66B3FF",
                "accent4": "#90EE90", "accent5": "#FFB366"},
        visual_elements="Hand-drawn chalk illustrations with sketchy, imperfect lines; chalk dust effects; doodles: stars, arrows, underlines, circles, checkmarks; mathematical formulas; eraser smudges; stick figures; connection lines with hand-drawn feel",
        typography="Hand-drawn chalk lettering style; visible chalk texture on all text; imperfect baseline adds authenticity; white or bright colored chalk for emphasis",
        best_for="Educational content, tutorials, how-to's, classroom, workshops, knowledge sharing",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "highly_recommended",
                       "list": "highly_recommended", "comparison": "works_well", "flow": "highly_recommended",
                       "mindmap": "highly_recommended", "quadrant": "works_well"}),
    "study-notes": Style(
        name="study-notes", category="realistic",
        description="手写笔记照片风格 — 蓝色圆珠笔+红色标注+黄色荧光笔",
        colors={"primary1": "#1E3A5F", "primary2": "#1A1A1A", "highlight": "#FFFF00",
                "accent1": "#CC0000", "background": "#FFFFFF"},
        visual_elements="Realistic photo perspective: top-down view of study desk; hand holding blue ballpoint pen; extremely dense handwritten content; red pen annotations; yellow highlighter marking key terms; correction marks; simple hand-drawn symbols",
        typography="Authentic student handwriting; messy but readable, clear structure maintained; varying font sizes; CJK optimized",
        best_for="Study guides, exam notes, knowledge organization, tutorial summaries",
        layout_compat={"sparse": "avoid", "balanced": "works_well", "dense": "highly_recommended",
                       "list": "highly_recommended", "comparison": "works_well", "flow": "works_well",
                       "mindmap": "highly_recommended", "quadrant": "works_well"}),
    "screen-print": Style(
        name="screen-print", category="poster",
        description="丝网印刷海报风格 — 大胆色块、半调纹理、有限色彩、象征性叙事",
        colors={"primary1": "#E8751A", "primary2": "#0A6E6E",
                "background": "#121212", "background2": "#F5E6D0",
                "accent1": "#C0392B", "accent2": "#F4A623"},
        visual_elements="Bold silhouettes and symbolic shapes; halftone dot patterns; slight color layer misregistration; geometric framing; figure-ground inversion; stencil-cut edges, no outlines; typography integrated as design element; vintage poster border treatments",
        typography="Bold condensed sans-serif or hand-drawn lettering; Art Deco influences; typography as integral part of composition; high contrast with background",
        best_for="Opinion pieces, cultural commentary, movie/music/book recs, dramatic announcements",
        layout_compat={"sparse": "highly_recommended", "balanced": "highly_recommended", "dense": "avoid",
                       "list": "works_well", "comparison": "highly_recommended", "flow": "works_well",
                       "mindmap": "avoid", "quadrant": "highly_recommended"}),
    "sketch-notes": Style(
        name="sketch-notes", category="educational",
        description="手绘教育信息图 — 线条微颤、马卡龙色块、温暖奶油底",
        colors={"primary1": "#A8D8EA", "primary2": "#D5C6E0", "primary3": "#B5E5CF",
                "primary4": "#F8D5C4", "background": "#F5F0E8", "accent1": "#E8655A",
                "text": "#2C3E50", "text_secondary": "#6B6B6B"},
        visual_elements="Hand-drawn wobble on all lines and shapes; simple stick-figure characters; rounded cards with pastel color blocks; color fills do NOT completely fill outlines; doodle decorations; wavy hand-drawn arrows; thought bubbles and speech bubbles; simple conceptual icons; generous whitespace between zones",
        typography="Bold hand-drawn lettering for titles; bold keywords within content zones; smaller annotations in secondary text color; hand-drawn quality on ALL text; clear information hierarchy",
        best_for="Educational content, tutorials, process diagrams, knowledge summaries, visual summaries",
        default_palette="macaron",
        layout_compat={"sparse": "works_well", "balanced": "highly_recommended", "dense": "highly_recommended",
                       "list": "highly_recommended", "comparison": "works_well", "flow": "highly_recommended",
                       "mindmap": "highly_recommended", "quadrant": "works_well"}),
}

# ════════════════════════════════════════════════════════════════════
# 8 Layouts
# ════════════════════════════════════════════════════════════════════

LAYOUTS: dict[str, Layout] = {
    "sparse": Layout("sparse", "稀疏布局 — 1-2 个要点，最大冲击力", "Low", "60-70%", "1-2",
                     "Single focal point centered, breathing room on all sides, symmetrical composition",
                     "Covers, quotes, impactful statements"),
    "balanced": Layout("balanced", "均衡布局 — 3-4 个要点，标准", "Medium", "40-50%", "3-4",
                       "Top-weighted title, evenly distributed content below, clear visual hierarchy",
                       "Standard content, tutorials"),
    "dense": Layout("dense", "密集布局 — 5-8 个要点，知识卡片风格", "High", "20-30%", "5-8",
                    "Organized grid structure, clear section boundaries, compact but readable spacing",
                    "Knowledge cards, cheat sheets"),
    "list": Layout("list", "列表布局 — 枚举/排行 (4-7 项)", "Medium-High", "30-40%", "4-7",
                   "Left-aligned items, clear number/bullet hierarchy, consistent item format",
                   "Rankings, checklists, step guides"),
    "comparison": Layout("comparison", "对比布局 — 左右对照", "Medium", "30-40%", "2 sections",
                         "Symmetrical left/right, clear visual contrast, divider between sections",
                         "Before/after, pros/cons"),
    "flow": Layout("flow", "流程布局 — 流程/时间线 (3-6 步)", "Medium", "30-40%", "3-6 steps",
                   "Directional flow (top->bottom or left->right), connected nodes with arrows, clear progression indicators",
                   "Processes, timelines, workflows"),
    "mindmap": Layout("mindmap", "思维导图布局 — 中心放射 (4-8 分支)", "Medium-High", "25-35%", "4-8 branches",
                      "Central topic node, radial branches outward, hierarchical sub-branches, organic curved connections",
                      "Concept maps, brainstorming, topic overview"),
    "quadrant": Layout("quadrant", "四象限布局 — 四区域/圆形分区", "Medium", "25-35%", "4 sections",
                       "4-section grid (2x2), clear axis labels, each quadrant with distinct content",
                       "SWOT analysis, priority matrix, classification"),
}

# ════════════════════════════════════════════════════════════════════
# 3 Palettes
# ════════════════════════════════════════════════════════════════════

PALETTES: dict[str, Palette] = {
    "macaron": Palette(
        name="macaron", description="马卡龙配色 — 柔和粉彩色块，温暖奶油底",
        background="Warm Cream", background_hex="#F5F0E8",
        colors={"text": "#2C3E50", "text_secondary": "#6B6B6B", "zone1": "#A8D8EA",
                "zone2": "#D5C6E0", "zone3": "#B5E5CF", "zone4": "#F8D5C4", "accent": "#E8655A"},
        semantic_constraint="Soft pastel macaron color palette. Use block colors as rounded card backgrounds. Accent coral red sparingly. No saturated or neon tones.",
        best_paired_with=["sketch-notes", "notion", "chalkboard", "warm", "fresh"]),
    "warm": Palette(
        name="warm", description="暖色配色 — 柔和桃色底，大地色系",
        background="Soft Peach", background_hex="#FFECD2",
        colors={"text": "#744210", "text_secondary": "#9C6644", "zone1": "#ED8936",
                "zone2": "#C05621", "zone3": "#F6AD55", "zone4": "#D4A09A", "accent": "#A0522D"},
        semantic_constraint="Warm-only color palette, no cool colors. Earth tones throughout. Evokes comfort, warmth, and trust.",
        best_paired_with=["warm", "cute", "retro", "sketch-notes"]),
    "neon": Palette(
        name="neon", description="霓虹配色 — 深紫底，高能霓虹色，未来感",
        background="Dark Purple", background_hex="#1A1025",
        colors={"text": "#F0F0F0", "text_secondary": "#B8B8D4", "zone1": "#00F5FF",
                "zone2": "#FF00FF", "zone3": "#39FF14", "zone4": "#FF6EC7", "accent": "#FFFF00"},
        semantic_constraint="Vibrant neon color palette on dark background. High contrast, futuristic feel. Use neon sparingly.",
        best_paired_with=["bold", "pop", "minimal", "notion"]),
}

# ════════════════════════════════════════════════════════════════════
# 25+ Presets
# ════════════════════════════════════════════════════════════════════

PRESETS: dict[str, Preset] = {
    "knowledge-card": Preset("knowledge-card", "notion", "dense", None, "干货知识卡、概念科普", "knowledge"),
    "checklist": Preset("checklist", "notion", "list", None, "清单、排行榜", "knowledge"),
    "concept-map": Preset("concept-map", "notion", "mindmap", None, "概念图、知识脉络", "knowledge"),
    "swot": Preset("swot", "notion", "quadrant", None, "SWOT 分析、四象限", "knowledge"),
    "tutorial": Preset("tutorial", "chalkboard", "flow", None, "教程步骤、操作流程", "knowledge"),
    "classroom": Preset("classroom", "chalkboard", "balanced", None, "课堂笔记、知识讲解", "knowledge"),
    "study-guide": Preset("study-guide", "study-notes", "dense", None, "学习笔记、考试重点", "knowledge"),
    "hand-drawn-edu": Preset("hand-drawn-edu", "sketch-notes", "flow", "macaron", "手绘教程、流程图解", "knowledge"),
    "sketch-card": Preset("sketch-card", "sketch-notes", "dense", "macaron", "手绘知识卡", "knowledge"),
    "sketch-summary": Preset("sketch-summary", "sketch-notes", "balanced", "macaron", "手绘总结、图文笔记", "knowledge"),
    "cute-share": Preset("cute-share", "cute", "balanced", None, "少女风分享、日常种草", "lifestyle"),
    "girly": Preset("girly", "cute", "sparse", None, "甜美封面、氛围感", "lifestyle"),
    "cozy-story": Preset("cozy-story", "warm", "balanced", None, "生活故事、情感分享", "lifestyle"),
    "product-review": Preset("product-review", "fresh", "comparison", None, "产品对比、测评", "lifestyle"),
    "nature-flow": Preset("nature-flow", "fresh", "flow", None, "健康流程、自然主题", "lifestyle"),
    "warning": Preset("warning", "bold", "list", None, "避坑指南、重要提醒", "impact"),
    "versus": Preset("versus", "bold", "comparison", None, "正反对比", "impact"),
    "clean-quote": Preset("clean-quote", "minimal", "sparse", None, "金句、极简封面", "impact"),
    "pro-summary": Preset("pro-summary", "minimal", "balanced", None, "专业总结、商务内容", "impact"),
    "retro-ranking": Preset("retro-ranking", "retro", "list", None, "复古排行、经典盘点", "trend"),
    "throwback": Preset("throwback", "retro", "balanced", None, "怀旧分享", "trend"),
    "pop-facts": Preset("pop-facts", "pop", "list", None, "趣味冷知识", "trend"),
    "hype": Preset("hype", "pop", "sparse", None, "炸裂封面、惊叹分享", "trend"),
    "poster": Preset("poster", "screen-print", "sparse", None, "海报风封面、影评书评", "poster"),
    "editorial": Preset("editorial", "screen-print", "balanced", None, "观点文章、文化评论", "poster"),
    "cinematic": Preset("cinematic", "screen-print", "comparison", None, "电影对比、戏剧张力", "poster"),
}

# ════════════════════════════════════════════════════════════════════
# Auto-selection table
# ════════════════════════════════════════════════════════════════════

AUTO_SELECTION_TABLE: list[dict] = [
    {"signals": ["beauty", "fashion", "cute", "girl", "pink", "美妆", "穿搭", "少女"],
     "style": "cute", "layouts": ["sparse", "balanced"], "presets": ["cute-share", "girly"]},
    {"signals": ["health", "nature", "fresh", "organic", "健康", "自然", "清新"],
     "style": "fresh", "layouts": ["balanced", "flow"], "presets": ["product-review", "nature-flow"]},
    {"signals": ["life", "story", "emotion", "warm", "生活", "故事", "情感"],
     "style": "warm", "layouts": ["balanced"], "presets": ["cozy-story"]},
    {"signals": ["warning", "important", "must", "critical", "避坑", "注意", "必看"],
     "style": "bold", "layouts": ["list", "comparison"], "presets": ["warning", "versus"]},
    {"signals": ["professional", "business", "elegant", "专业", "商务", "高端"],
     "style": "minimal", "layouts": ["sparse", "balanced"], "presets": ["clean-quote", "pro-summary"]},
    {"signals": ["classic", "vintage", "traditional", "复古", "经典", "怀旧"],
     "style": "retro", "layouts": ["balanced"], "presets": ["throwback", "retro-ranking"]},
    {"signals": ["fun", "exciting", "wow", "amazing", "趣味", "冷知识"],
     "style": "pop", "layouts": ["sparse", "list"], "presets": ["hype", "pop-facts"]},
    {"signals": ["knowledge", "concept", "productivity", "SaaS", "干货", "知识", "效率"],
     "style": "notion", "layouts": ["dense", "list"], "presets": ["knowledge-card", "checklist"]},
    {"signals": ["education", "tutorial", "learning", "classroom", "教程", "课堂", "学习"],
     "style": "chalkboard", "layouts": ["balanced", "dense"], "presets": ["tutorial", "classroom"]},
    {"signals": ["notes", "handwritten", "study guide", "笔记", "手写", "考试"],
     "style": "study-notes", "layouts": ["dense", "list", "mindmap"], "presets": ["study-guide"]},
    {"signals": ["movie", "poster", "opinion", "editorial", "cinematic", "影评", "书评"],
     "style": "screen-print", "layouts": ["sparse", "comparison"], "presets": ["poster", "editorial"]},
    {"signals": ["hand-drawn", "infographic", "workflow", "手绘", "图解", "流程"],
     "style": "sketch-notes", "layouts": ["flow", "balanced", "dense"],
     "presets": ["hand-drawn-edu", "sketch-card", "sketch-summary"]},
]

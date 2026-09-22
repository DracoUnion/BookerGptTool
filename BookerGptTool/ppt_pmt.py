# -*- coding: utf-8 -*-
"""
ppt_pmt.py —— 网页 PPT 子命令（ppt）的全部提示词模板。

所有提示词均为中文，输出约定：
- 结构化 JSON 输出：用三个反引号包裹；
- 非 JSON 输出（如 HTML）：用 [content]...[/content] 包裹。

模板中的 {大写占位符} 由 util.render_prompt 渲染，占位符命名刻意避免与模板 HTML 中的
CSS/JS 花括号冲突（CSS 形如 `{color:red}`，其中不会出现 `{占位符}` 这种单词花括号）。
"""

# ============================================================================
# 0. 常量：内置参考文档与模板资产
# ============================================================================

# 内置参考文档白名单
REFERENCE_FILES = [
    'checklist.md',
    'components.md',
    'image-prompts.md',
    'layouts.md',
    'layouts-swiss.md',
    'presenter-mode.md',
    'screenshot-framing.md',
    'swiss-layout-lock.md',
    'swiss-map-component.md',
    'themes.md',
    'themes-swiss.md',
]

# 风格 -> (模板文件, 布局参考文件, 主题参考文件)
STYLE_ASSETS = {
    'A': ('template.html', ['layouts.md'], 'themes.md'),
    'B': ('template-swiss.html', ['swiss-layout-lock.md', 'layouts-swiss.md'], 'themes-swiss.md'),
}


# ============================================================================
# 1. Deck 规划提示词（agent.gen_plan 内部调用，JSON 输出）
# ============================================================================


# ============================================================================
# 2. Deck 规划提示词（agent.gen_plan 内部调用，JSON 输出）
# ============================================================================
DECK_PLAN_PMT = """
你是网页 PPT 的策划。请根据用户提供的素材，规划一份完整的 Deck（JSON 输出）。

## 素材全文

[content]
{MATERIAL}
[/content]

## 受众与场景

[content]
{AUDIENCE}
[/content]

## 分享时长

[content]
{DURATION}
[/content]

## 风格 A 主题色预设（电子杂志风 · 衬线 + 流体背景）

{THEMES_A}

## 风格 B 主题色预设（瑞士国际主义风 · 无衬线 + 网格点阵）

{THEMES_B}

## 规划要求

1. **先定风格**：根据素材内容与受众，从风格 A / B 中二选一（例如内容偏人文/故事/行业观察选 A，
   偏数据/科技/工程汇报选 B），并从对应预设中挑选一套主题色（theme_name 必须是上面列出的预设名）。
2. **定页数与节奏**：15 分钟 ≈ 10 页，30 分钟 ≈ 20 页，45 分钟 ≈ 25-30 页。用「叙事弧」搭骨架：
   钩子(1页) → 定调(1-2页) → 主体(3-5页以上) → 转折(1页) → 收束(1-2页)。页数要和总时长匹配。
3. **逐页规划**：每一页都要给出——
   - `slide_id`：稳定唯一的页面 ID（如 `cover`、`sec-01`、`s-data`），用作 data-slide-id 与备注键；
   - `layout`：风格 A 用 Layout 1-10；风格 B 用登记的 S01-S22 编号；
   - `theme_class`：hero light / hero dark / light / dark 之一，遵守主题节奏规则；
   - `title`：观众可见的主标题；
   - `section`：所属章节（可选）；
   - `purpose`：这一页要达成的叙事任务；
   - `talk`：演讲者补充信息列表（不复述页面文字，可给例子/判断依据/语气）；
   - `minutes`：建议讲述分钟（未知用 `-`）；
   - `transition`：为什么下一页紧接着出现。
4. **页内内容要具体**：title、talk、purpose 都基于素材真实信息，不编造没有来源的事实。

只输出 JSON，用三个反引号包裹，字段必须与 DeckPlan 模型一致：

```
{
  "title": "Deck 标题",
  "style": "A",
  "theme_name": "主题名",
  "audience": "受众与场景",
  "duration_minutes": "30",
  "slides": [
    {
      "slide_id": "cover",
      "layout": "Layout 1",
      "theme_class": "hero dark",
      "title": "封面标题",
      "section": "",
      "purpose": "……",
      "talk": ["……"],
      "minutes": "1",
      "transition": "……"
    }
  ]
}
```
"""


# ============================================================================
# 3. Deck 写入提示词（tool_write_deck 内部调用，HTML 输出）
# ============================================================================
DECK_WRITE_PMT = """
你是网页 PPT 的前端工程师。根据下面的 Deck 规划与版式参考，生成该份 Deck 的全部
`<section class="slide ...">...</section>` 页面 HTML，并输出（用 [content]...[/content] 包裹）。

## Deck 规划

[content]
{PLAN}
[/content]

## 主题色预设（务必从中找到所选主题的 `:root` 变量块，用作整份 deck 的主题）

[content]
{THEMES}
[/content]

## 版式参考（class 必须从这里挑选，不要发明新类名）

[content]
{LAYOUTS}
[/content]

## 生成要求

1. **只生成幻灯片区域**：输出内容会插入模板的 `<!-- SLIDES_HERE -->` 处。你**不要**重复整个
   `<html>` 模板，只需输出全部 `<section class="slide ...">` 块。
2. **每页对应规划的一页 slide**：严格按 `DeckPlan.slides` 的顺序生成，逐页使用 `layout` 指定的版式骨架，
   填上 `title` 与真实内容。每页 section 必须带规划里的 `theme_class`，并写唯一稳定的 `data-slide-id`
   （与规划中 slide_id 一致）。
3. **主题变量覆盖**：如果所选主题与模板默认不同，在输出的最前面追加一个 `<style>` 块，用所选主题的
   `:root` 变量（--ink / --paper 等）覆盖模板默认值。格式：`<style>:root{ ...theme vars... }</style>`。
4. **只用模板存在的 class**：参考上面的版式参考；需要微调用 `style="..."` 内联，不要发明未登记 class。
5. **图片**：需要图片时用相对路径 `images/`（如 `images/01-cover.jpg`），并遵守标准比例；
   风格 A 网格图用固定 `height:Nvh`；图片容器直角、不留阴影（风格 B）。
6. **风格铁律**：
   - 风格 A：标题用衬线（用 h-hero 等），正文非衬线，元数据等宽；WebGL 背景只在 hero 页透出。
   - 风格 B：全程无衬线；整份 deck 只用规划主题的一个高亮色；无渐变/阴影/圆角；大字字重 200，
     主标题与正文对比 ≥ 8:1，字号用 `min(Xvw, Yvh)` 双约束；每页一个语义化动效 recipe。
7. **动效标记**：按版式参考在元素上加 `data-anim` / `data-animate` 入场标记（模板已内置 Motion 引擎）。

只输出 HTML（可能带一个前置 `<style>`），用 [content]...[/content] 包裹。不要输出任何解释。
"""

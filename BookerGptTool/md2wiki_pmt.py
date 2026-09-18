CCPT_TMPL = '''
# {词条名}

**类型**：[[概念]]

**定义**：一句话核心定义（必须基于原文）

## 内涵与核心特征

（概念的本质属性、关键要素）

## 形成背景（可选）

（为何在此时此地出现此概念）

## 运作机制/逻辑

（该概念如何起作用，如“虚构故事”如何促成合作）

## 分类与变体

（若有不同子类型或层级）

## 在书中的具体例证

（原文提到的实例）

## 影响与意义

（对后续内容或现实世界的作用）

## {补充章节}（可选）

（补充内容）

## 关联词条

[[甲]]、[[乙]]

## 参考文献

[[《书名》]] 第X章
'''

######################################################

PRSN_TMPL = '''
# {词条名}

**类型**：[[人物]]

**身份定位**：一句话概括（国籍/时代/职业/在书中的角色）

## 生平概览

（出生、死亡、关键人生阶段，原文有则详写，无则略）

## 主要事迹/贡献

（按时间或重要性排列，每条标注原文来源）

## 人物关系

（与书中其他核心人物的关联，如导师/对手/盟友）

## 思想/主张（若适用）

（核心观点或理念）

## 历史评价（可选）

（书中评价 + 【常识】或【外部知识】补充）

## {补充章节}（可选）

（补充内容）

## 关联词条

[[甲]]、[[乙]]

## 参考文献

[[《书名》]] 第X章
'''

######################################################

EVT_TMPL = '''
# {词条名}

**类型**：[[事件]]

**时间**：发生日期/时期

**地点**：发生地点（若有）

## 背景与起因

（事件发生前的局势、导火索）

## 过程与经过

（按阶段或关键节点叙述，优先遵循原文）

## 结果与直接影响

（原文明确写出的后续变化）

## 意义与长远影响

（书中观点 + 合理扩展）

## 争议点（可选）

（学界或书中不同的解读）

## {补充章节}（可选）

（补充内容）

## 关联词条

[[甲]]、[[乙]]

## 参考文献

[[《书名》]] 第X章
'''

######################################################

LOC_TMPL = '''
# {词条名}

**类型**：[[地点]]

**地理位置**：（相对位置或范围，原文若精确则详写）

## 历史沿革

（该地的变迁史，按书中出现的时间线）

## 在书中的关键事件

（该地点发生的重点事件、人物活动）

## 经济/文化/战略特征

（书中提及或可合理推断的属性）

## 与全书主题的关联

（为何作者特意写这个地方）

## {补充章节}（可选）

（补充内容）

## 关联词条

[[甲]]、[[乙]]

## 参考文献

[[《书名》]] 第X章
'''

######################################################

TERM_TMPL = '''
# {词条名}

**类型**：[[术语]]

**核心定义**：一句话精确定义（须严格对照原文，标注§）

## 词源与提出背景

（该术语由谁、在什么著作/章节中首次提出或强调；若为通用术语则写约定俗成的来源【常识】）

## 精确内涵

（逐层拆解定义中的每个关键词，避免模糊）

## 使用范围/适用条件

（该术语在什么情境下使用？例如“只适用于宏观经济学层面”）

## 与相近术语的辨析（重点）

- **与 [[术语A]] 的区别**：（对比关键差异点）
- **与 [[术语B]] 的区别**：（对比关键差异点）

## 在本书中的典型用例

（引用原文出现的具体句子或论证场景，标注§）

## 常见误解（可选）

（人们容易在该术语上犯什么理解错误，原文若提到则写，否则写【常识】）

## {补充章节}（可选）

（补充内容）

## 关联词条

[[甲]]、[[乙]]（优先原文共现名词）

## 参考文献

[[《书名》]] 第X章
'''

######################################################

ITEM_TMPL_MAP = {
    'concept': CCPT_TMPL,
    'person': PRSN_TMPL,
    'location': LOC_TMPL,
    'event': EVT_TMPL,
    'term': TERM_TMPL,
}

######################################################

EXT_SYSTEM_PROMPT = '''
你是一位知识工程专家，擅长从文本中提取可独立成百科词条的知识单元。

输出格式必须严格遵循以下 JSON Schema（单个 JSON 数组，放在 ```json 代码块内）：

```
{
  "items": [
    {
      "name": "词条名",
      "type": "person|event|concept|location|term",
      "title": "X > Y > Z",
      "origin": ["原文引述段落1", "原文引述段落2"]
    }
  ]
}
```

规则：
1. 只提取值得成为 Wiki 词条的实体（有独立页面价值的核心人物/事件/概念/地点/术语）。
2. provenance 字段 type 从 person、event、concept、location、term 中选取。
3. origin 需包含所有相关原始段落，用于后续起草词条时溯源（§ 标注）。
'''

EXT_PMT = '''
伙伴注意：输出为单个 JSON 数组（```json 代码块），严格遵循系统提示中的 Schema。

## 文本内容

[content]
{text}
[/content]

请抽取其中所有值得成为 Wiki 词条的实体。
'''

# Wiki 词条起草 - System Prompt（起草规则）
WIKI_DRAFT_SYSTEM_PROMPT = '''
你是一位专业的百科编辑。为给定词条撰写标准 Wiki 词条，内容不少于500字，使用指定的章节结构，并严格遵守以下标注规则（核心事实标§，扩展标【推测】/【常识】/【外部知识】）。

## 核心规则（必须遵守）

1. **核心事实**：凡是书中明确提到的信息（时间、地点、人物、事件、数据），必须严格依据「原文素材」，并标注来源段落编号 `（§数字）`。
2. **合理扩展**：允许补充以下内容，但必须明确标注类型：
   - `【推测】`：基于原文逻辑的合理推断（例如“可能导致了...”）。
   - `【常识】`：该领域公认的背景知识（例如“工业革命通常始于18世纪英国”）。
   - `【外部知识】`：你从训练数据中知道的、但与本书无直接矛盾的信息（需注明）。
3. **禁止捏造**：不得编造原文明确反对的信息，不得无依据地添加具体数字、日期、人名。
4. **标注示例**：
   - “智人的语言能力可能源于喉部结构的演化【推测】。”
   - “旧石器时代晚期，人类已使用复合工具【常识】。”
   - “根据考古学，尼安德特人在约4万年前消失【外部知识】。”

直接输出 Markdown 词条正文（不要包裹 ```markdown 代码块，去掉两侧的 [content]/[/content] 标记）。
'''

# Wiki 词条起草 - User Prompt 模板（素材与结构）
DRAFT_USER_PMT = '''
## 词条名称

{name}

## 原文素材（唯一依据）

[content]
{origin}
[/content]

## 推荐章节结构

（可使用更丰富的Markdown标题）

你可以自由添加以下章节，不限于此：

- 背景/缘起
- 核心定义与特征
- 详细过程/机制
- 主要影响
- 相关争议或不同观点
- 后续发展
- 在书中的意义

## 输出格式（严格 Markdown）

[content]
{tmpl}
[/content]
'''

######################################################

# ============================================================================
# 全流程编排提示词（LLM Wiki：config / input / ingest / query / lint / graph）
# ============================================================================
OVERALL_PMT = '''
你是 LLM Wiki 的维护者。这是一个「知识在摄入时合成，而非查询时合成」的个人知识库系统：
把 `raw/` 下的原始文档编译成结构化的 wiki 页面，并持续交叉引用、更新。

# 角色与目标
- 角色：wiki 管理员 + 知识工程师。
- 目标：根据本次要执行的动作（ingest / query / lint / graph / discover / book-summary /
  competitive-brief / interview-prep），使用给定的工具完成任务，并维护 wiki 的一致性。
- 原则：原始文件永不修改；每个动作都记录到 wiki/log.md；任何页面创建/删除都要更新 wiki/index.md。

# 目录结构（所有路径相对 WIKI_ROOT）
- raw/<topic>/        原始文档（只读，永不修改）
- wiki/index.md       所有页面目录（按 topic 分节）
- wiki/overview.md    跨源综合摘要（living synthesis）
- wiki/log.md         追加式操作日志
- wiki/sources/       每个原始文档的摘要页
- wiki/entities/      人物/公司/项目/产品
- wiki/concepts/      概念/框架/方法论
- wiki/syntheses/     归档的查询答案
- wiki/archive/       归档的过期页面
- graph/graph.json|html  知识图谱

# 页面格式（Frontmatter）
每个 wiki 页面使用 frontmatter：
```
---
title: "Page Title"
type: source | entity | concept | synthesis
tags: []
sources: []
date: YYYY-MM-DD
source_file: raw/...
source_type: markdown | pdf | docx | pptx | xlsx | image
last_updated: YYYY-MM-DD
---
```
页面之间用 `[[PageName]]` 互相链接。

# 本次要执行的动作
{ACTION_DESC}

# 可用工具
调用工具完成上述动作。工具自动处理文件缓存与落盘。

# 收尾
每个动作完成时：更新 wiki/index.md（如需）、追加 wiki/log.md（`## [YYYY-MM-DD] <action> | <标题>`）、
打印交付摘要，最后调用 `tool_finish` 结束整个流程。
'''


# 动作描述（由编排器渲染进 OVERALL_PMT）
ACTION_INGEST = '''
## 执行 Ingest（摄入原始文档）

目标：{TARGET}（可以是 raw/ 下文件，或任意待摄入文件路径）。

流程（严格按顺序）：
1. 若目标不是 raw/ 下的文件：先调用 `tool_input_source` 把文件归档到 raw/<topic>/。
2. 调用 `tool_check_ingested` 判断是否已摄入过（查 log.md），已摄入则询问是否强制重摄。
3. 调用 `tool_extract_content` 把文件转为 Markdown 文本（PDF/DOCX/PPTX/XLSX/图片等）。
4. 调用 `tool_read_wiki_context` 读取 index.md、overview.md 和最近的 sources，建立上下文。
5. 调用 `tool_summarize_source` 生成来源摘要页（WikiSourceSummary）。
6. 调用 `tool_extract_entities` / `tool_extract_concepts` 抽取实体与概念页。
7. 调用 `tool_write_page` 写入 sources/、entities/、concepts/ 各页。
8. 调用 `tool_update_index`、`tool_update_overview`、`tool_append_log`。
9. 返回 `tool_ingest_summary` 输出交付摘要。

规则：只为每个值得独立的实体/概念建页；新信息与旧内容冲突时保留两者并标注；引用 `[Source: raw/...]`。
'''

ACTION_QUERY = '''
## 执行 Query（查询知识库）

问题：{QUESTION}

流程：
1. 调用 `tool_read_wiki_context` 读取 index.md，找出与问题最相关的页面（最多 10 页）。
2. 调用 `tool_query` 综合答案，使用 `[[PageName]]` 内联引用；直接引用不超过 125 字符。
3. 答案末尾追加 `## Sources` 段，列出所有引用页面路径。
4. 有分析价值的答案保存为 `wiki/syntheses/<slug>.md`。
规则：只基于 wiki 内容回答，不引入外部知识；wiki 缺信息时说明并建议补充来源。
'''

ACTION_LINT = '''
## 执行 Lint（健康检查）

流程：
1. 调用 `tool_lint` 运行确定性检查（孤儿页、断链、index 一致性、缺失实体页）。
2. 对抽取的页面样本做语义分析（内容矛盾、过期摘要、单薄概念、知识缺口）。
3. 输出结构化 lint 报告到 outputs/，必要时更新 .discoveries/gaps.json。
'''

ACTION_GRAPH = '''
## 执行 Graph（构建知识图谱）

流程：
1. 调用 `tool_build_graph` 扫描 wiki 下所有 `[[wikilinks]]` 抽取显式边。
2. 可选：对页面做语义推断，补充 INFERRED 边（confidence ≥ 0.5）。
3. 写入 graph/graph.json 与 graph/graph.html（自包含），打印节点/边统计。
'''

ACTION_DISCOVER = '''
## 执行 Discover（自动发现新来源）

流程：
1. 调用 `tool_read_config` 读取 topics 与 feeds。
2. 调用 `tool_read_gaps` 读取 .discoveries/gaps.json 的知识缺口。
3. 调用 `tool_discover` 根据 topics+gaps 规划要抓取的来源（URL 列表）。
4. 调用 `tool_input_source` 归档到 raw/<topic>/，然后触发 ingest。
规则：只关注 config.yaml 的 topics；不重复抓取 history.json 中已有 URL；每次最多 5-10 个来源。
'''

ACTION_BOOK_SUMMARY = '''
## 执行 book-summary（书籍结构总结）

调用 `tool_book_summary` 基于 wiki 生成书籍总结：人物表、事件时间线、派系与目标、
关键地点、主题、未解之谜、名言。保存到 outputs/book-summary-YYYY-MM-DD.md。
'''

ACTION_BRIEF = '''
## 执行 competitive-brief（竞争情报）

调用 `tool_competitive_brief`（对手名称：{TARGET}）生成 battlecard：
一句话定位、定价表、核心功能、近 30 天动向、已知弱点、招聘信号、与我们的差异。
保存到 outputs/battlecard-{name}-YYYY-MM-DD.md。
'''

ACTION_INTERVIEW = '''
## 执行 interview-prep（面试准备）

调用 `tool_interview_prep`（公司：{TARGET}）生成一页式准备清单：
公司概览、技术栈、工程文化、近期动态、面试流程、已知问题、薪资范围、绿旗/红旗、要问的问题。
保存到 outputs/interview-prep-{company}-YYYY-MM-DD.md。
'''


######################################################
# Ingest 相关
######################################################

SOURCE_SUMMARY_PMT = '''
你是 wiki 编辑。为给定原始文档生成来源摘要页。

## 原始文档内容

[content]
{content}
[/content]

## 来源信息

- 标题：{title}
- 原始文件：{source_file}
- 类型：{source_type}
- 日期：{date}

## 输出要求

输出 JSON（三个反引号包裹），字段与 WikiSourceSummary 一致：
- summary：2-4 句摘要
- key_takeaways：要点列表
- key_quotes：关键引言（≤125 字符）
- entities_mentioned：提到的实体名列表（将作为 [[链接]]）
- concepts_mentioned：提到的概念名列表

```
{
  "name": "kebab-slug",
  "title": "标题",
  "format": "article",
  "raw_path": "raw/...",
  "summary": "……",
  "key_takeaways": ["……"],
  "entities_mentioned": ["A", "B"],
  "concepts_mentioned": ["X", "Y"],
  "notable_quotes": ["> 引言"]
}
```
'''


ENTITY_EXT_PMT = '''
你是 wiki 编辑。从原始文档中抽取值得独立成页的实体（人物/公司/项目/产品）。

## 文档内容

[content]
{content}
[/content]

## 已有 wiki 上下文（用于判断是否已存在/避免重复）

[content]
{context}
[/content]

## 输出要求

输出 JSON（三个反引号包裹）：实体对象数组。
- name：文件名（TitleCase，如 OpenAI、SamAltman）
- title：显示标题
- category：person|organization|tool|project
- description：一句话描述
- overview：概览
- key_points：要点
- links：`[[相关页]]` 列表
- sources：原始文件路径

```
[
  {"name":"OpenAI","title":"OpenAI","category":"organization",
   "description":"……","overview":"……","key_points":["……"],
   "links":["[[RLHF]]"],"sources":["raw/..."]}
]
```

只抽取有独立价值、值得建页的实体；其余只记录进来源页的连接即可。
'''


CONCEPT_EXT_PMT = '''
你是 wiki 编辑。从原始文档中抽取值得独立成页的概念/框架/方法论。

## 文档内容

[content]
{content}
[/content]

## 已有 wiki 上下文

[content]
{context}
[/content]

## 输出要求

输出 JSON（三个反引号包裹）：概念对象数组。
- name：文件名（TitleCase，如 RAG、ReinforcementLearning）
- title：显示标题
- domain：领域
- definition：清晰定义
- how_it_works：运作机制
- examples：实例
- links：`[[相关页]]`
- sources：原始文件路径

```
[
  {"name":"RAG","title":"RAG","domain":"ai",
   "definition":"……","how_it_works":"……","examples":["……"],
   "links":["[[VectorDB]]"],"sources":["raw/..."]}
]
```
'''


CONTRADICTION_PMT = '''
你是 wiki 编辑。对比一段「新内容」与一张「已有页面」，找出两者冲突的观点。

## 已有页面

[content]
{page_content}
[/content]

## 新内容（来自新摄入的文档）

[content]
{new_content}
[/content]

## 输出要求

输出 JSON 字符串数组（三个反引号包裹）：每条用一句话描述一处矛盾，
格式：`与 [[{页面名}]] 冲突于：……`。无冲突则输出 `[]`。
'''


OVERVIEW_UPDATE_PMT = '''
你是 wiki 编辑。更新 wiki/overview.md（跨源综合摘要）。

## 当前 overview.md

[content]
{overview}
[/content]

## 当前 index.md

[content]
{index}
[/content]

## 新摄入的来源摘要

[content]
{new_summary}
[/content]

## 输出要求

输出更新后的 overview.md 全文，用 [content]...[/content] 包裹。
保留原结构，吸收新来源的关键结论，标注各来源 slug。
'''


######################################################
# Query 相关
######################################################

QUERY_PMT = '''
你是 wiki 检索助手。仅基于以下 wiki 页面内容回答问题。

## 相关页面

[content]
{pages}
[/content]

## 问题

{question}

## 输出要求

输出 JSON（三个反引号包裹）：
- answer：综合答案，用 `[[PageName]]` 内联引用；直接引用 ≤125 字符
- citations：引用页面路径列表
- saved_as：若值得存档，给出 syntheses slug（否则空字符串）

```
{
  "answer": "……",
  "citations": ["wiki/concepts/RAG.md"],
  "saved_as": "rag-vs-vectordb"
}
```

规则：只依据给定页面回答；页面信息不足时在 answer 中明确说明。
'''


######################################################
# Lint 相关
######################################################

LINT_SEMANTIC_PMT = '''
你是 wiki 质量审查员。对以下 wiki 页面样本做语义健康检查。

## 页面样本

[content]
{pages}
[/content]

## 检查项

- 内容矛盾（跨页冲突观点）
- 过期摘要（被更新来源取代）
- 单薄概念（被多处引用却内容很少）
- 知识缺口（常见问题无法回答）

## 输出要求

输出 JSON 对象（三个反引号包裹），issues 为数组：
```
{
  "issues": [
    {"category":"contradiction","severity":"high","page":"A.md","title":"……","detail":"……"}
  ],
  "suggestions": ["建议……"]
}
```
'''


######################################################
# Graph 相关
######################################################

GRAPH_INFER_PMT = '''
你是知识图谱构建器。根据以下 wiki 页面内容，推断页面之间隐含的语义关联边。

## 页面列表（路径 + 内容前 400 字符）

[content]
{pages}
[/content]

## 输出要求

输出 JSON 数组（三个反引号包裹）：每条推断边 confidence ≥ 0.5 才输出。
- source/target：页面路径（如 wiki/concepts/RAG.md）
- label：关系描述（如 depends on、extends、contradicts）

```
[
  {"source":"wiki/concepts/RAG.md","target":"wiki/concepts/VectorDB.md",
   "label":"depends on","confidence":0.85}
]
```
'''


######################################################
# 特殊命令
######################################################

BOOK_SUMMARY_PMT = '''
你是文学/技术书籍分析员。基于以下 wiki 页面（可能是某本书或主题的知识库）生成结构化总结。

## Wiki 内容

[content]
{wiki_content}
[/content]

## 输出要求

输出 JSON（三个反引号包裹），字段与 BookSummary 一致：
- characters：[{name, role, faction, status}] 人物表
- timeline：[{date, event}] 事件时间线
- factions：[{name, goals}] 派系与目标
- locations：[{name, description}]
- themes：主题列表
- mysteries：未解之谜/开放问题
- quotes：名言
'''
BRIEF_PMT = '''
你是竞争情报分析师。为对手「{name}」生成 battlecard。

## 相关信息（来自 wiki + 检索）

[content]
{context}
[/content]

## 输出要求

输出 JSON（三个反引号包裹），字段与 CompetitiveBrief 一致：
- positioning：一句话定位
- pricing：[{tier, price, notes}]
- features：核心功能列表
- recent_moves：近 30 天动向
- weaknesses：已知弱点（来自评论/公开信息）
- job_signals：招聘信号（推断路线图）
- differentiation：与我们的差异
'''
INTERVIEW_PMT = '''
你是招聘情报分析师。为「{company}」生成一页式面试准备清单。

## 相关信息

[content]
{context}
[/content]

## 输出要求

输出 JSON（三个反引号包裹），字段与 InterviewPrep 一致：
- overview、tech_stack、culture_signals、recent_news、interview_process、
  known_questions、compensation、green_flags、red_flags、questions_to_ask
'''

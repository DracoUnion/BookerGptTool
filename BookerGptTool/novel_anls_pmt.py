# 提示词模板。
# 渲染使用 util.render_prompt（正则替换 {占位符}），
# 因此字面量花括号需写成 {{ }}，占位符写成 {xxx}。


# ──── 阶段一：章节扫描 ────

SCAN_PMT = '''
你是一位小说章节扫描分析师。你的任务是对给定的**单章正文**进行结构化扫描，提取该章的核心信息。

## 任务

对当前章节正文进行结构化扫描，输出包含章节摘要、关键事件、人物动态、情绪基调、伏笔动态、冲突烈度等信息。

## 要求

1.  `summary` 须为 50 字以内的核心摘要。
2.  `key_events` 提取本章最重要的事件，按发生顺序排列。
3.  `new_characters` 记录本章首次登场的人物，`character_updates` 记录已有角色在本章的变化。
4.  `emotional_tone` 与 `conflict_level` 取 0-10 的整数。
5.  `word_count` 为本章正文的近似字数（数字）。
6.  JSON 格式输出，注意输出包含在三个反引号（```）中。

## 格式

```
{
  "module": "章节摘要",
  "chapter": {chapter_index},
  "title": {chapter_title},
  "summary": "50字以内摘要",
  "key_events": ["事件1", "事件2"],
  "new_characters": [{{"name": "...", "role": "...", "traits": ["..."]}}],
  "character_updates": [{{"name": "...", "change": "..."}}],
  "emotional_tone": 0,
  "chapter_end_hook": "...或null",
  "foreshadowing_planted": ["..."],
  "foreshadowing_payoff": ["..."],
  "conflict_level": 0,
  "word_count": 0
}
```

## 当前章节

序号：{chapter_index}
标题：{chapter_title}

[content]
{chapter_text}
[/content]
'''


# ──── 阶段二：各模块聚合提示词 ────
# 每个模块一个完整提示词，任务说明与输出 Schema 均内联在模板中，
# 渲染时仅替换 {all_chapter_summaries}、{book_meta} 占位符。

AGGREGATE_PMT_GENRE = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"题材与定位"模块。

## 任务

你的任务是判断该书的题材类型、目标读者与市场定位，提炼其独特卖点。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"题材与定位","genre":"...","target_audience":"...","market_position":"...","unique_selling_point":"..."}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_TITLE = '''
你是一位小说拆解聚合架构师。基于全书章节摘要和书籍元信息，生成"书名与简介"模块。

## 任务

你的任务是评估书名的记忆点与相关性，并结合简介提炼其吸引读者的钩子。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"书名与简介","title_analysis":{"memorability":0,"relevance":0,"comment":"..."},"blurb_hooks":["..."],"blurb_effectiveness":"..."}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_WORLD = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"世界观设定"模块。

## 任务

你的任务是梳理世界观的核心规则、阵营势力、独特设定及其逻辑自洽性。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"世界观设定","core_rules":"...","factions":[{"name":"...","desc":"...","chapters_involved":"第X-Y章"}],"unique_elements":["..."],"logic_consistency":"...","world_immersion_score":0}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_CHARACTER = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"人物图谱"模块。

## 任务

你的任务是构建人物图谱，刻画主角弧光、配角关系与反派压力模型。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"人物图谱","protagonist":{"name":"...","desire":"...","flaw":"...","belief":"...","arc":"从...到..."},"supporting_chars":[{"name":"...","role":"...","relation_evolution":"..."}],"antagonist":{"name":"...","pressure_model":"...","motivation":"...","final_confrontation_chapter":0},"relationship_map":"..."}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_STRUCTURE = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"主线与结构"模块。

## 任务

你的任务是概括主线故事，划分起承转合，梳理支线及其收束。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"主线与结构","main_storyline":"...","structure":{"acts":[{"act":"起/承/转/合","chapters":"第X-Y章","summary":"..."}]},"subplots":[{"name":"...","chapters":"...","resolution_chapter":"..."}],"structure_score":0}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_RHYTHM = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"节奏与爽点"模块。

## 任务

你的任务是定位高潮爽点、分析情绪曲线与冲突升级路径，指出节奏洼地。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"节奏与爽点","climax_points":[{"chapter":0,"type":"...","intensity":0}],"satisfaction_interval":"...","flat_periods":[{"chapters":"第X-Y章","desc":"..."}],"conflict_escalation":"...","emotion_curve":"...","rhythm_score":0}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_GIMMICK = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"金手指/核心创意"模块。

## 任务

你的任务是提炼本书的核心创意与金手指设定，评估其创新性与执行度。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"金手指/核心创意","unique_setting":"...","innovation":"...","core_gimmick":"...","creativity_score":0,"execution_score":0}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_OPENING = '''
你是一位小说拆解聚合架构师。基于全书章节摘要（聚焦前3章），生成"开篇钩子"模块。

## 任务

你的任务是聚焦前三章，评估开篇钩子强度与黄金三章效果，提示读者流失风险。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"开篇钩子","first_500_words_hook":"...","protagonist_first_impression":"...","chapter_end_hooks":[{"chapter":0,"hook":"...","strength":0}],"golden_three_effectiveness":"...","drop_off_risk":"..."}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_FORESHADOW = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"伏笔与铺垫"模块。

## 任务

你的任务是盘点全书伏笔的埋设与回收，分析信息差与连续性。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"伏笔与铺垫","foreshadowing_list":[{"id":1,"plant_chapter":0,"payoff_chapter":0,"description":"...","function":"...","is_resolved":true}],"information_asymmetry":"...","continuity":"...","foreshadowing_score":0}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PMT_PATTERN = '''
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"可迁移模式"模块。

## 任务

你的任务是提炼可迁移的结构、人物、节奏与写作技法模式，并给出借鉴建议。按照给定格式以 JSON 输出，包含在三个反引号（```）中。

## 输出格式

```
{"module":"可迁移模式","structural_patterns":["..."],"character_patterns":["..."],"rhythm_patterns":["..."],"writing_techniques":["..."],"imitation_advice":"..."}
```

## 全书章节摘要

[content]
{all_chapter_summaries}
[/content]

## 书籍元信息

[content]
{book_meta}
[/content]
'''

AGGREGATE_PROMPT_MAP = {
    "题材与定位": AGGREGATE_PMT_GENRE,
    "书名与简介": AGGREGATE_PMT_TITLE,
    "世界观设定": AGGREGATE_PMT_WORLD,
    "人物图谱": AGGREGATE_PMT_CHARACTER,
    "主线与结构": AGGREGATE_PMT_STRUCTURE,
    "节奏与爽点": AGGREGATE_PMT_RHYTHM,
    "金手指/核心创意": AGGREGATE_PMT_GIMMICK,
    "开篇钩子": AGGREGATE_PMT_OPENING,
    "伏笔与铺垫": AGGREGATE_PMT_FORESHADOW,
    "可迁移模式": AGGREGATE_PMT_PATTERN,
}


# 系统提示词
SCAN_SYSTEM = "你是一位小说章节扫描分析师。你只对单章正文做结构化扫描，并以 JSON 形式输出，输出包含在三个反引号（```）中。"
AGGREGATE_SYSTEM = "你是一位小说拆解聚合架构师。你基于全书章节摘要生成单一分析模块，并以 JSON 形式输出，输出包含在三个反引号（```）中。"

SCAN_SYSTEM_PROMPT = "你是一位小说章节扫描分析师。只输出 JSON。"
AGGREGATE_SYSTEM_PROMPT = "你是一位小说拆解聚合架构师。只输出 JSON。"


SCAN_PROMPT = """
你是一位小说章节扫描分析师。你的任务是对给定的**单章正文**进行结构化扫描，提取该章的核心信息。

当前章节序号：{chapter_index}
当前章节标题：{chapter_title}
当前章节正文：{chapter_text}

只输出 JSON，严格遵循以下 Schema：
{{
  "module": "章节摘要",
  "chapter": {chapter_index},
  "title": {chapter_title},
  "summary": "50字以内摘要",
  "key_events": ["事件1", "事件2"],
  "new_characters": [{{"name": "...", "role": "...", "traits": ["..."]}}],
  "character_updates": [{{"name": "...", "change": "..."}}],
  "emotional_tone": 0-10,
  "chapter_end_hook": "...或null",
  "foreshadowing_planted": ["..."],
  "foreshadowing_payoff": ["..."],
  "conflict_level": 0-10,
  "word_count": 数字
}}
"""


AGGREGATE_PROMPT_GENRE = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"题材与定位"模块。

输入摘要：{all_chapter_summaries}
书籍元信息：{book_meta}

输出 JSON：
{{"module":"题材与定位","genre":"...","target_audience":"...","market_position":"...","unique_selling_point":"..."}}
"""

AGGREGATE_PROMPT_TITLE = """
你是一位小说拆解聚合架构师。基于全书章节摘要和书籍元信息，生成"书名与简介"模块。

输入摘要：{all_chapter_summaries}
书籍元信息：{book_meta}

输出 JSON：
{{"module":"书名与简介","title_analysis":{{"memorability":0-10,"relevance":0-10,"comment":"..."}},"blurb_hooks":["..."],"blurb_effectiveness":"..."}}
"""

AGGREGATE_PROMPT_WORLD = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"世界观设定"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"世界观设定","core_rules":"...","factions":[{{"name":"...","desc":"...","chapters_involved":"第X-Y章"}}],"unique_elements":["..."],"logic_consistency":"...","world_immersion_score":0-10}}
"""

AGGREGATE_PROMPT_CHARACTER = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"人物图谱"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"人物图谱","protagonist":{{"name":"...","desire":"...","flaw":"...","belief":"...","arc":"从...到..."}},"supporting_chars":[{{"name":"...","role":"...","relation_evolution":"..."}}],"antagonist":{{"name":"...","pressure_model":"...","motivation":"...","final_confrontation_chapter":null或数字}},"relationship_map":"..."}}
"""

AGGREGATE_PROMPT_STRUCTURE = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"主线与结构"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"主线与结构","main_storyline":"...","structure":{{"acts":[{{"act":"起/承/转/合","chapters":"第X-Y章","summary":"..."}}]}},"subplots":[{{"name":"...","chapters":"...","resolution_chapter":"..."}}],"structure_score":0-10}}
"""

AGGREGATE_PROMPT_RHYTHM = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"节奏与爽点"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"节奏与爽点","climax_points":[{{"chapter":数字,"type":"...","intensity":0-10}}],"satisfaction_interval":"...","flat_periods":[{{"chapters":"第X-Y章","desc":"..."}}],"conflict_escalation":"...","emotion_curve":"...","rhythm_score":0-10}}
"""

AGGREGATE_PROMPT_GIMMICK = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"金手指/核心创意"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"金手指/核心创意","unique_setting":"...","innovation":"...","core_gimmick":"...","creativity_score":0-10,"execution_score":0-10}}
"""

AGGREGATE_PROMPT_OPENING = """
你是一位小说拆解聚合架构师。基于全书章节摘要（聚焦前3章），生成"开篇钩子"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"开篇钩子","first_500_words_hook":"...","protagonist_first_impression":"...","chapter_end_hooks":[{{"chapter":数字,"hook":"...","strength":0-10}}],"golden_three_effectiveness":"...","drop_off_risk":"..."}}
"""

AGGREGATE_PROMPT_FORESHADOW = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"伏笔与铺垫"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"伏笔与铺垫","foreshadowing_list":[{{"id":1,"plant_chapter":数字,"payoff_chapter":数字或null,"description":"...","function":"...","is_resolved":true/false}}],"information_asymmetry":"...","continuity":"...","foreshadowing_score":0-10}}
"""

AGGREGATE_PROMPT_PATTERN = """
你是一位小说拆解聚合架构师。基于全书章节摘要，生成"可迁移模式"模块。

输入摘要：{all_chapter_summaries}

输出 JSON：
{{"module":"可迁移模式","structural_patterns":["..."],"character_patterns":["..."],"rhythm_patterns":["..."],"writing_techniques":["..."],"imitation_advice":"..."}}
"""


AGGREGATE_PROMPT_MAP = {
    "题材与定位": AGGREGATE_PROMPT_GENRE,
    "书名与简介": AGGREGATE_PROMPT_TITLE,
    "世界观设定": AGGREGATE_PROMPT_WORLD,
    "人物图谱": AGGREGATE_PROMPT_CHARACTER,
    "主线与结构": AGGREGATE_PROMPT_STRUCTURE,
    "节奏与爽点": AGGREGATE_PROMPT_RHYTHM,
    "金手指/核心创意": AGGREGATE_PROMPT_GIMMICK,
    "开篇钩子": AGGREGATE_PROMPT_OPENING,
    "伏笔与铺垫": AGGREGATE_PROMPT_FORESHADOW,
    "可迁移模式": AGGREGATE_PROMPT_PATTERN,
}

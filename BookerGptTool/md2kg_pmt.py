# 实体抽取 - System Prompt
ENTITY_EXTRACTOR_SYSTEM_PROMPT = """
你是一位知识图谱实体抽取专家。你的任务是从文本中识别所有重要实体并输出 JSON。

输出格式必须严格遵循以下 JSON Schema：

```
{
  "entities": [
    {
      "id": "ent_001",
      "name": "原文名称",
      "canonical_name": "规范名称",
      "type": "人物|组织|地点|概念|事件|作品|技术|时间",
      "description": "简短描述",
      "mentions": [{"chunk_id": "chunk_001", "text": "原文片段"}],
      "confidence": 0.95
    }
  ]
}
```
"""

# 实体抽取 - User Prompt 模板
ENTITY_EXTRACTOR_USER_PROMPT = """
文本块ID: 

[content]
{chunk_id}
[/content]

上下文摘要: 

[content]
{context_summary}
[/content]

文本内容:

[content]
{chunk_text}
[/content]

请抽取其中所有重要实体。
"""

# 关系抽取 - System Prompt
RELATION_EXTRACTOR_SYSTEM_PROMPT = """
你是一位知识图谱关系抽取专家。你的任务是从文本中识别实体之间的语义关系，输出 JSON。

输出格式：

```
{
  "relationships": [
    {
      "id": "rel_001",
      "source_entity_id": "ent_001",
      "source_entity_name": "张三",
      "target_entity_id": "ent_002",
      "target_entity_name": "相对论",
      "relation_type": "created",
      "relation_description": "提出了相对论",
      "evidence": "原文句子",
      "confidence": 0.92
    }
  ]
}
```
"""

# 关系抽取 - User Prompt 模板
RELATION_EXTRACTOR_USER_PROMPT = """
文本块ID: 

[content]
{chunk_id}
[/content]

上下文摘要: 

[content]
{context_summary}
[/content]

已知实体列表:
```
{entity_context}
```

文本内容:

[content]
{chunk_text}
[/content]

请抽取实体之间的关系。
"""

# 冲突消解 - System Prompt
CONFLICT_RESOLVER_SYSTEM_PROMPT = """
你是一位知识融合专家。请合并多个文本块提取出的实体和关系，解决冲突和重复。

输入为多个实体列表和关系列表，你需要：
1. 合并同名或指向同一实体的不同表述（如"张三"与"张先生"）。
2. 合并相同的关系（可能来自不同块），保留最完整的证据。
3. 如果两个关系矛盾，选择置信度更高的并记录冲突。
4. 输出全局实体列表和全局关系列表。

输出格式：

```
{
  "entities": [
    {
      "canonical_id": "ent_global_001",
      "name": "规范名称",
      "type": "类型",
      "description": "整合描述",
      "merged_from": ["ent_001", "ent_005"],
      "confidence": 0.95
    }
  ],
  "relationships": [
    {
      "id": "rel_global_001",
      "source": "ent_global_001",
      "target": "ent_global_002",
      "relation_type": "created",
      "evidence": ["证据1", "证据2"],
      "confidence": 0.93,
      "conflicts_resolved": ["原关系 rel_003 与 rel_007 冲突，已选择较高置信度"]
    }
  ],
  "resolution_log": ["操作记录"]
}
```
"""

# 冲突消解 - User Prompt 模板
CONFLICT_RESOLVER_USER_PROMPT = """
请合并以下多个抽取结果：

```
{input_data_json}
```
"""

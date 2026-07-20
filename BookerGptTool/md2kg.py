import json
import logging
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from pydantic import BaseModel
from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_MODEL
from md2kg_models import (
    Entity, Relation, EntityList, RelationList,
    GlobalEntity, GlobalRelation, ResolvedGraph,
)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# 1. 基础智能体（Agent）抽象类
# ============================================================================
class BaseAgent:
    """所有智能体的基类，封装 OpenAI 调用与 JSON 校验"""
    def __init__(self, model: str = OPENAI_MODEL, temperature: float = 0.0):
        self.client = OpenAI(api_key=OPENAI_API_KEY)
        self.model = model
        self.temperature = temperature

    def _call_llm(self, system_prompt: str, user_prompt: str, response_model: BaseModel) -> BaseModel:
        """
        调用 OpenAI 的 ChatCompletion，强制返回 JSON，并用 Pydantic 解析。
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        # 要求模型返回 JSON 对象
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            response_format={"type": "json_object"}  # 仅当模型支持时可用
        )
        content = response.choices[0].message.content
        logger.info(f"LLM raw output: {content[:200]}...")  # 截断日志
        try:
            data = json.loads(content)
            # 使用 Pydantic 解析并校验
            return response_model.parse_obj(data)
        except Exception as e:
            logger.error(f"Failed to parse response: {e}\nRaw: {content}")
            raise


# ============================================================================
# 2. 具体智能体实现
# ============================================================================
class EntityExtractor(BaseAgent):
    """实体抽取智能体"""
    SYSTEM_PROMPT = """
你是一位知识图谱实体抽取专家。你的任务是从文本中识别所有重要实体并输出 JSON。

输出格式必须严格遵循以下 JSON Schema：
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
注意：只输出 JSON，不要有其他文字。
"""

    def run(self, chunk_text: str, chunk_id: str, context_summary: str = "") -> EntityList:
        user_prompt = f"""
文本块ID: {chunk_id}
上下文摘要: {context_summary}
文本内容:
{chunk_text}

请抽取其中所有重要实体。
"""
        return self._call_llm(self.SYSTEM_PROMPT, user_prompt, EntityList)


class RelationExtractor(BaseAgent):
    """关系抽取智能体"""
    SYSTEM_PROMPT = """
你是一位知识图谱关系抽取专家。你的任务是从文本中识别实体之间的语义关系，输出 JSON。

输出格式：
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
只输出 JSON。
"""

    def run(self, chunk_text: str, chunk_id: str, entity_list: EntityList, context_summary: str = "") -> RelationList:
        # 将已有实体列表传入，帮助关系抽取
        entity_context = "\n".join([f"{e.id}: {e.canonical_name} ({e.type})" for e in entity_list.entities])
        user_prompt = f"""
文本块ID: {chunk_id}
上下文摘要: {context_summary}
已知实体列表:
{entity_context}

文本内容:
{chunk_text}

请抽取实体之间的关系。
"""
        return self._call_llm(self.SYSTEM_PROMPT, user_prompt, RelationList)


class ConflictResolver(BaseAgent):
    """冲突消解与全局融合智能体"""
    SYSTEM_PROMPT = """
你是一位知识融合专家。请合并多个文本块提取出的实体和关系，解决冲突和重复。

输入为多个实体列表和关系列表，你需要：
1. 合并同名或指向同一实体的不同表述（如"张三"与"张先生"）。
2. 合并相同的关系（可能来自不同块），保留最完整的证据。
3. 如果两个关系矛盾，选择置信度更高的并记录冲突。
4. 输出全局实体列表和全局关系列表。

输出格式：
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
"""

    def run(self, all_entity_lists: List[EntityList], all_relation_lists: List[RelationList]) -> ResolvedGraph:
        # 将所有抽取结果转为 JSON 字符串
        input_data = {
            "entity_lists": [el.dict() for el in all_entity_lists],
            "relation_lists": [rl.dict() for rl in all_relation_lists]
        }
        user_prompt = f"请合并以下多个抽取结果：\n{json.dumps(input_data, indent=2, ensure_ascii=False)}"
        return self._call_llm(self.SYSTEM_PROMPT, user_prompt, ResolvedGraph)


# ============================================================================
# 3. 调度协调器（Orchestrator）
# ============================================================================
class KnowledgeGraphOrchestrator:
    """协调整个流程：分块 → 并行抽取 → 冲突消解 → 输出"""
    def __init__(self, max_workers: int = 5):
        self.max_workers = max_workers
        self.entity_extractor = EntityExtractor()
        self.relation_extractor = RelationExtractor()
        self.resolver = ConflictResolver()

    def build_graph(self, chunks: List[Dict[str, Any]]) -> ResolvedGraph:
        """
        chunks: 每个元素包含 'id', 'content', 'summary' (可选)
        """
        all_entity_lists = []
        all_relation_lists = []

        # ----- 阶段2：并行抽取实体和关系 -----
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for chunk in chunks:
                chunk_id = chunk['id']
                content = chunk['content']
                summary = chunk.get('summary', '')
                # 提交实体抽取任务
                future_entity = executor.submit(
                    self.entity_extractor.run, content, chunk_id, summary
                )
                futures.append(('entity', future_entity, chunk_id))
                # 提交关系抽取任务（需要先有实体？但我们可以先抽取实体，再抽取关系？）
                # 但是关系抽取需要实体列表，所以不能完全并行。我们采用两阶段：先抽所有实体，再抽所有关系。
                # 但我们可以让关系抽取也独立进行，不依赖本块的实体列表（让LLM自己识别）。
                # 为了更准确，我们先并行抽取实体，然后等全部完成后，再并行抽取关系（需实体列表）。
                # 但我们可以调整：关系抽取也直接基于文本，但带上已有实体列表（如果有）。
                # 为简化，我们这里先只提交实体抽取，关系抽取放在下一步。

            # 收集所有实体抽取结果
            for task_type, future, cid in futures:
                if task_type == 'entity':
                    try:
                        entity_list = future.result()
                        all_entity_lists.append(entity_list)
                    except Exception as e:
                        logger.error(f"实体抽取失败 (chunk {cid}): {e}")

        # 现在有了所有实体列表，我们可以并行抽取关系（每个块使用对应的实体列表）
        # 但为了演示，我们可以再开一轮并行。实际项目中，可以将实体列表与块绑定，但这里简化。
        # 我们将所有实体列表合并成一个大的列表传递给关系抽取？（不，关系抽取需要每个块对应的实体）
        # 更合理：我们在分块时就把块与实体列表关联起来。这里我们重新遍历chunks，提取对应的实体列表（按顺序）
        # 我们假设chunks顺序与all_entity_lists顺序一致（因为提交顺序一致）
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures_rel = []
            for idx, chunk in enumerate(chunks):
                chunk_id = chunk['id']
                content = chunk['content']
                summary = chunk.get('summary', '')
                # 获取该块对应的实体列表
                entity_list = all_entity_lists[idx] if idx < len(all_entity_lists) else EntityList(entities=[])
                future_rel = executor.submit(
                    self.relation_extractor.run, content, chunk_id, entity_list, summary
                )
                futures_rel.append((future_rel, chunk_id))

            for future, cid in futures_rel:
                try:
                    rel_list = future.result()
                    all_relation_lists.append(rel_list)
                except Exception as e:
                    logger.error(f"关系抽取失败 (chunk {cid}): {e}")

        # ----- 阶段3：冲突消解 -----
        resolved_graph = self.resolver.run(all_entity_lists, all_relation_lists)
        return resolved_graph


# ============================================================================
# 4. 使用示例
# ============================================================================
if __name__ == "__main__":
    # 模拟从书籍中切分出的文本块
    sample_chunks = [
        {
            "id": "chunk_001",
            "content": "张三是一位著名的物理学家，他在1905年提出了相对论。这一理论彻底改变了现代物理学。",
            "summary": "介绍张三和相对论的提出"
        },
        {
            "id": "chunk_002",
            "content": "相对论对后来的量子力学产生了深远影响。爱因斯坦也曾对此表示赞赏。",
            "summary": "相对论的影响"
        }
    ]

    orchestrator = KnowledgeGraphOrchestrator(max_workers=2)
    result = orchestrator.build_graph(sample_chunks)

    # 输出全局图谱
    print("===== 全局实体 =====")
    for ent in result.entities:
        print(f"{ent.canonical_id}: {ent.name} ({ent.type}) - {ent.description}")

    print("\n===== 全局关系 =====")
    for rel in result.relationships:
        print(f"{rel.source} --[{rel.relation_type}]--> {rel.target} : {rel.evidence}")

    print("\n===== 消解日志 =====")
    for log in result.resolution_log:
        print(log)

    # 可选：将结果导出为Neo4j Cypher（简单示例）
    print("\n===== Cypher 示例 =====")
    for ent in result.entities:
        print(f"CREATE (n:{ent.type} {{id: '{ent.canonical_id}', name: '{ent.name}', description: '{ent.description}'}});")
    for rel in result.relationships:
        print(f"MATCH (a {{id: '{rel.source}'}}), (b {{id: '{rel.target}'}}) "
              f"CREATE (a)-[:{rel.relation_type.upper()} {{evidence: '{rel.evidence[0]}'}}]->(b);")
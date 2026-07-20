import json
import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import call_llm_retry, ext_code_block, set_openai_props
from .base_agent import BaseAgent
from .md2kg_models import (
    Entity, Relation, EntityList, RelationList,
    GlobalEntity, GlobalRelation, ResolvedGraph,
)
from .md2kg_pmt import (
    ENTITY_EXTRACTOR_SYSTEM_PROMPT, ENTITY_EXTRACTOR_USER_PROMPT,
    RELATION_EXTRACTOR_SYSTEM_PROMPT, RELATION_EXTRACTOR_USER_PROMPT,
    CONFLICT_RESOLVER_SYSTEM_PROMPT, CONFLICT_RESOLVER_USER_PROMPT,
)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# 1. 具体智能体实现
# ============================================================================
class EntityExtractor(BaseAgent):
    """实体抽取智能体"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def run(self, chunk_text: str, chunk_id: str, context_summary: str = "") -> EntityList:
        user_prompt = ENTITY_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary, chunk_text=chunk_text
        )
        parse_output = lambda s: EntityList.model_validate_json(ext_code_block(s))
        return self._call(ENTITY_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)


class RelationExtractor(BaseAgent):
    """关系抽取智能体"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def run(self, chunk_text: str, chunk_id: str, entity_list: EntityList, context_summary: str = "") -> RelationList:
        entity_context = "\n".join([f"{e.id}: {e.canonical_name} ({e.type})" for e in entity_list.entities])
        user_prompt = RELATION_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary,
            entity_context=entity_context, chunk_text=chunk_text
        )
        parse_output = lambda s: RelationList.model_validate_json(ext_code_block(s))
        return self._call(RELATION_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)


class ConflictResolver(BaseAgent):
    """冲突消解与全局融合智能体"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def run(self, all_entity_lists: List[EntityList], all_relation_lists: List[RelationList]) -> ResolvedGraph:
        input_data = {
            "entity_lists": [el.model_dump() for el in all_entity_lists],
            "relation_lists": [rl.model_dump() for rl in all_relation_lists]
        }
        input_data_json = json.dumps(input_data, indent=2, ensure_ascii=False)
        user_prompt = CONFLICT_RESOLVER_USER_PROMPT.format(input_data_json=input_data_json)
        parse_output = lambda s: ResolvedGraph.model_validate_json(ext_code_block(s))
        return self._call(CONFLICT_RESOLVER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)


# ============================================================================
# 3. 调度协调器（Orchestrator）
# ============================================================================
class KnowledgeGraphOrchestrator:
    """协调整个流程：分块 → 并行抽取 → 冲突消解 → 输出"""
    def __init__(self, api_base: str, api_key: str, model: str,
                 max_workers: int = 5, retry: int = 3, stream: bool = False):
        self.max_workers = max_workers
        self.entity_extractor = EntityExtractor(api_base, api_key, model, retry=retry, stream=stream)
        self.relation_extractor = RelationExtractor(api_base, api_key, model, retry=retry, stream=stream)
        self.resolver = ConflictResolver(api_base, api_key, model, retry=retry, stream=stream)

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
# 4. 处理入口
# ============================================================================
def md2kg_handle(args):
    print(args)
    set_openai_props(args)

    # 读取输入文本
    if path.isfile(args.fname):
        fnames = [args.fname]
    elif path.isdir(args.fname):
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if f.endswith('.md')]
    if not fnames:
        print('请提供 MD 文件或目录')
        return

    # 确定输出文件路径
    ofname = (
        args.fname[:-3] + '.cyp'
        if path.isfile(args.fname)
        else path.join(args.fname, 'kg.cyp')
    )
    if path.isfile(ofname):
        print('MD 已处理过，跳过')
        return

    text = '\n\n'.join(
        open(f, encoding='utf8').read()
        for f in fnames
    )
    if not text.strip():
        print('输入内容为空')
        return

    # 按段落切分为文本块
    chunks = []
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    for i, para in enumerate(paragraphs):
        chunks.append({
            "id": f"chunk_{i+1:03d}",
            "content": para,
            "summary": para[:100],
        })

    orchestrator = KnowledgeGraphOrchestrator(
        api_base=args.host, api_key=args.key, model=args.model,
        max_workers=args.threads, retry=args.retry, stream=args.stream,
    )
    result = orchestrator.build_graph(chunks)

    # 输出结果
    lines = []
    lines.append("===== 全局实体 =====\n")
    for ent in result.entities:
        lines.append(f"{ent.canonical_id}: {ent.name} ({ent.type}) - {ent.description}")

    lines.append("\n===== 全局关系 =====\n")
    for rel in result.relationships:
        lines.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target} : {rel.evidence}")

    lines.append("\n===== 消解日志 =====\n")
    for log_entry in result.resolution_log:
        lines.append(log_entry)

    lines.append("\n===== Cypher 示例 =====\n")
    for ent in result.entities:
        lines.append(f"CREATE (n:{ent.type} {{id: '{ent.canonical_id}', name: '{ent.name}', description: '{ent.description}'}});")
    for rel in result.relationships:
        lines.append(
            f"MATCH (a {{id: '{rel.source}'}}), (b {{id: '{rel.target}'}}) "
            f"CREATE (a)-[:{rel.relation_type.upper()} {{evidence: '{rel.evidence[0]}'}}]->(b);"
        )

    output = '\n'.join(lines)
    print(output)
    open(ofname, 'w', encoding='utf8').write(output)
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
    AlignedEntity, AlignedRelation, SchemaAlignmentResult,
    EvaluatedTriplet, EvaluationResult,
)
from .md2kg_pmt import (
    ENTITY_EXTRACTOR_SYSTEM_PROMPT, ENTITY_EXTRACTOR_USER_PROMPT,
    RELATION_EXTRACTOR_SYSTEM_PROMPT, RELATION_EXTRACTOR_USER_PROMPT,
    CONFLICT_RESOLVER_SYSTEM_PROMPT, CONFLICT_RESOLVER_USER_PROMPT,
    SCHEMA_ALIGNER_SYSTEM_PROMPT, SCHEMA_ALIGNER_USER_PROMPT,
    EVALUATOR_SYSTEM_PROMPT, EVALUATOR_USER_PROMPT,
)

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# 1. 统一智能体
# ============================================================================
class Md2KgAgent(BaseAgent):
    """统一知识图谱智能体"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def extract_entities(self, chunk_text: str, chunk_id: str, context_summary: str = "") -> EntityList:
        user_prompt = ENTITY_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary, chunk_text=chunk_text
        )
        parse_output = lambda s: EntityList.model_validate_json(ext_code_block(s))
        return self._call(ENTITY_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)

    def extract_relations(self, chunk_text: str, chunk_id: str, entity_list: EntityList, context_summary: str = "") -> RelationList:
        entity_context = "\n".join([f"{e.id}: {e.canonical_name} ({e.type})" for e in entity_list.entities])
        user_prompt = RELATION_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary,
            entity_context=entity_context, chunk_text=chunk_text
        )
        parse_output = lambda s: RelationList.model_validate_json(ext_code_block(s))
        return self._call(RELATION_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)

    def resolve_conflicts(self, all_entity_lists: List[EntityList], all_relation_lists: List[RelationList]) -> ResolvedGraph:
        input_data = {
            "entity_lists": [el.model_dump() for el in all_entity_lists],
            "relation_lists": [rl.model_dump() for rl in all_relation_lists]
        }
        input_data_json = json.dumps(input_data, indent=2, ensure_ascii=False)
        user_prompt = CONFLICT_RESOLVER_USER_PROMPT.format(input_data_json=input_data_json)
        parse_output = lambda s: ResolvedGraph.model_validate_json(ext_code_block(s))
        return self._call(CONFLICT_RESOLVER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)

    def align_schema(self, resolved_graph: ResolvedGraph, target_schema: Dict[str, List[str]] = None) -> SchemaAlignmentResult:
        if target_schema is None:
            target_schema = {
                "entity_types": ["人物", "组织", "地点", "概念", "事件", "作品", "技术", "时间"],
                "relation_type": ["创建", "属于", "位于", "影响", "包含", "发表", "研究", "使用"]
            }

        entities_json = json.dumps(
            [{"canonical_id": e.canonical_id, "name": e.name, "type": e.type}
             for e in resolved_graph.entities],
            indent=2, ensure_ascii=False
        )
        relations_json = json.dumps(
            [{"id": r.id, "source": r.source, "target": r.target, "relation_type": r.relation_type}
             for r in resolved_graph.relationships],
            indent=2, ensure_ascii=False
        )

        user_prompt = SCHEMA_ALIGNER_USER_PROMPT.format(
            entity_types=", ".join(target_schema["entity_types"]),
            relation_types=", ".join(target_schema["relation_type"]),
            entities_json=entities_json,
            relations_json=relations_json
        )
        parse_output = lambda s: SchemaAlignmentResult.model_validate_json(ext_code_block(s))
        return self._call(SCHEMA_ALIGNER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)

    def evaluate(self, resolved_graph: ResolvedGraph, integration_threshold: float = 0.6) -> EvaluationResult:
        triplets = []
        for rel in resolved_graph.relationships:
            triplets.append({
                "id": rel.id,
                "source": rel.source,
                "target": rel.target,
                "relation_type": rel.relation_type,
                "evidence": rel.evidence,
                "confidence": rel.confidence
            })

        triplets_json = json.dumps(triplets, indent=2, ensure_ascii=False)
        user_prompt = EVALUATOR_USER_PROMPT.format(triplets_json=triplets_json)
        parse_output = lambda s: EvaluationResult.model_validate_json(ext_code_block(s))
        return self._call(EVALUATOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)


# ============================================================================
# 3. 调度协调器（Orchestrator）
# ============================================================================
class KnowledgeGraphOrchestrator:
    """协调整个流程：分块 → 并行抽取 → 冲突消解 → Schema对齐 → 评估 → 输出"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 max_workers: int = 5, retry: int = 3, stream: bool = False,
                 integration_threshold: float = 0.6):
        """
        Args:
            integration_threshold: 评估集成阈值，低于此值的三元组将被拒绝
        """
        self.max_workers = max_workers
        self.integration_threshold = integration_threshold

        # 初始化智能体
        self.agent = Md2KgAgent(api_base, api_key, model, retry=retry, stream=stream)

    def build_graph(self, chunks: List[Dict[str, Any]], target_schema: Dict[str, List[str]] = None) -> Dict[str, Any]:
        """
        构建知识图谱

        Args:
            chunks: 每个元素包含 'id', 'content', 'summary' (可选)
            target_schema: 目标Schema定义（可选）

        Returns:
            包含完整处理结果的字典
        """
        all_entity_lists = []
        all_relation_lists = []

        # ----- 阶段1：并行抽取实体 -----
        logger.info("阶段1：并行抽取实体...")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = []
            for chunk in chunks:
                chunk_id = chunk['id']
                content = chunk['content']
                summary = chunk.get('summary', '')
                future_entity = executor.submit(
                    self.agent.extract_entities, content, chunk_id, summary
                )
                futures.append((future_entity, chunk_id))

            for future, cid in futures:
                try:
                    entity_list = future.result()
                    all_entity_lists.append(entity_list)
                except Exception as e:
                    logger.error(f"实体抽取失败 (chunk {cid}): {e}")

        # ----- 阶段2：并行抽取关系 -----
        logger.info("阶段2：并行抽取关系...")
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures_rel = []
            for idx, chunk in enumerate(chunks):
                chunk_id = chunk['id']
                content = chunk['content']
                summary = chunk.get('summary', '')
                entity_list = all_entity_lists[idx] if idx < len(all_entity_lists) else EntityList(entities=[])
                future_rel = executor.submit(
                    self.agent.extract_relations, content, chunk_id, entity_list, summary
                )
                futures_rel.append((future_rel, chunk_id))

            for future, cid in futures_rel:
                try:
                    rel_list = future.result()
                    all_relation_lists.append(rel_list)
                except Exception as e:
                    logger.error(f"关系抽取失败 (chunk {cid}): {e}")

        # ----- 阶段3：冲突消解 -----
        logger.info("阶段3：冲突消解...")
        resolved_graph = self.agent.resolve_conflicts(all_entity_lists, all_relation_lists)

        # ----- 阶段4：Schema对齐 -----
        logger.info("阶段4：Schema对齐...")
        schema_alignment_result = self.agent.align_schema(resolved_graph, target_schema)

        # ----- 阶段5：质量评估 -----
        logger.info("阶段5：质量评估...")
        evaluation_result = self.agent.evaluate(resolved_graph, self.integration_threshold)

        # ----- 组装最终结果 -----
        result = {
            "resolved_graph": resolved_graph,
            "schema_alignment": schema_alignment_result,
            "evaluation": evaluation_result,
        }

        return result


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
        integration_threshold=getattr(args, 'threshold', 0.6),
    )
    result = orchestrator.build_graph(chunks)

    # 提取结果
    resolved_graph = result["resolved_graph"]
    schema_alignment = result["schema_alignment"]
    evaluation = result["evaluation"]

    # 输出结果
    lines = []
    lines.append("===== 全局实体 =====\n")
    for ent in resolved_graph.entities:
        lines.append(f"{ent.canonical_id}: {ent.name} ({ent.type}) - {ent.description}")

    lines.append("\n===== 全局关系 =====\n")
    for rel in resolved_graph.relationships:
        lines.append(f"{rel.source} --[{rel.relation_type}]--> {rel.target} : {rel.evidence}")

    lines.append("\n===== 消解日志 =====\n")
    for log_entry in resolved_graph.resolution_log:
        lines.append(log_entry)

    # Schema对齐结果
    lines.append("\n===== Schema对齐结果 =====\n")
    lines.append(f"对齐实体数: {len(schema_alignment.aligned_entities)}")
    lines.append(f"对齐关系数: {len(schema_alignment.aligned_relations)}")
    lines.append(f"未对齐数: {schema_alignment.unaligned_count}")
    for log_entry in schema_alignment.alignment_log:
        lines.append(log_entry)

    # 评估结果
    lines.append("\n===== 质量评估结果 =====\n")
    lines.append(f"接受三元组数: {evaluation.accepted_count}")
    lines.append(f"拒绝三元组数: {evaluation.rejected_count}")
    lines.append(f"平均分数: {evaluation.average_score:.2f}")
    for log_entry in evaluation.evaluation_log:
        lines.append(log_entry)

    # 只输出通过评估的三元组
    accepted_ids = {t.id for t in evaluation.triplets if t.should_integrate}
    final_relations = [r for r in resolved_graph.relationships if r.id in accepted_ids]

    lines.append("\n===== Cypher 示例 =====\n")
    for ent in resolved_graph.entities:
        lines.append(f"CREATE (n:{ent.type} {{id: '{ent.canonical_id}', name: '{ent.name}', description: '{ent.description}'}});")
    for rel in final_relations:
        lines.append(
            f"MATCH (a {{id: '{rel.source}'}}), (b {{id: '{rel.target}'}}) "
            f"CREATE (a)-[:{rel.relation_type.upper()} {{evidence: '{rel.evidence[0]}'}}]->(b);"
        )

    output = '\n'.join(lines)
    print(output)
    open(ofname, 'w', encoding='utf8').write(output)
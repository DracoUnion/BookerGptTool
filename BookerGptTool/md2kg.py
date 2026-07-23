import json
import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import call_llm_retry, ext_code_block, set_openai_props
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
class Md2KgAgent:
    """统一知识图谱智能体"""

    def __init__(self, args):
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.temperature = getattr(args, 'temp', 0.0)
        self.max_tokens = getattr(args, 'max_tokens', 2000)
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)

    def _call(self, system_prompt: str, user_prompt: str,
              max_tokens: Optional[int] = None, parse_output: Callable = None) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return call_llm_retry(
            messages, self.model,
            retry=self.retry,
            temp=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            parse_output=parse_output,
        )

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

    def __init__(self, args):
        """根据命令行参数初始化编排器。"""
        self.args = args
        self.max_workers = getattr(args, 'threads', 5)
        self.integration_threshold = getattr(args, 'threshold', 0.6)

        # 初始化智能体
        self.agent = Md2KgAgent(args)

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

    def run(self) -> Dict[str, Any]:
        """执行输入读取、知识图谱构建和结果输出的完整流程。"""
        logger.info(self.args)
        fnames = self._get_input_files()
        if not fnames:
            logger.info('请提供 MD 文件或目录')
            return {}

        ofname = self._get_output_fname()
        if path.isfile(ofname):
            logger.info('MD 已处理过，跳过')
            return {}

        text = '\n\n'.join(
            open(fname, encoding='utf8').read()
            for fname in fnames
        )
        if not text.strip():
            logger.info('输入内容为空')
            return {}

        chunks = self._build_chunks(text)
        result = self.build_graph(chunks)
        output = self._render_output(result)
        logger.info(output)
        open(ofname, 'w', encoding='utf8').write(output)
        return result

    def _get_input_files(self) -> List[str]:
        """获取待处理的 Markdown 文件。"""
        if path.isfile(self.args.fname):
            fnames = [self.args.fname]
        elif path.isdir(self.args.fname):
            fnames = [
                path.join(self.args.fname, fname)
                for fname in os.listdir(self.args.fname)
            ]
        else:
            fnames = []
        return [fname for fname in fnames if fname.endswith('.md')]

    def _get_output_fname(self) -> str:
        """根据输入路径确定知识图谱输出路径。"""
        return (
            self.args.fname[:-3] + '.cyp'
            if path.isfile(self.args.fname)
            else path.join(self.args.fname, 'kg.cyp')
        )

    @staticmethod
    def _build_chunks(text: str) -> List[Dict[str, str]]:
        """按段落切分文本块。"""
        paragraphs = [
            paragraph.strip()
            for paragraph in text.split('\n\n')
            if paragraph.strip()
        ]
        return [
            {
                "id": f"chunk_{index + 1:03d}",
                "content": paragraph,
                "summary": paragraph[:100],
            }
            for index, paragraph in enumerate(paragraphs)
        ]

    @staticmethod
    def _render_output(result: Dict[str, Any]) -> str:
        """将知识图谱结果渲染为报告和 Cypher 示例。"""
        resolved_graph = result["resolved_graph"]
        schema_alignment = result["schema_alignment"]
        evaluation = result["evaluation"]

        lines = ["===== 全局实体 =====\n"]
        for entity in resolved_graph.entities:
            lines.append(
                f"{entity.canonical_id}: {entity.name} "
                f"({entity.type}) - {entity.description}"
            )

        lines.append("\n===== 全局关系 =====\n")
        for relation in resolved_graph.relationships:
            lines.append(
                f"{relation.source} --[{relation.relation_type}]--> "
                f"{relation.target} : {relation.evidence}"
            )

        lines.append("\n===== 消解日志 =====\n")
        lines.extend(resolved_graph.resolution_log)

        lines.append("\n===== Schema对齐结果 =====\n")
        lines.append(f"对齐实体数: {len(schema_alignment.aligned_entities)}")
        lines.append(f"对齐关系数: {len(schema_alignment.aligned_relations)}")
        lines.append(f"未对齐数: {schema_alignment.unaligned_count}")
        lines.extend(schema_alignment.alignment_log)

        lines.append("\n===== 质量评估结果 =====\n")
        lines.append(f"接受三元组数: {evaluation.accepted_count}")
        lines.append(f"拒绝三元组数: {evaluation.rejected_count}")
        lines.append(f"平均分数: {evaluation.average_score:.2f}")
        lines.extend(evaluation.evaluation_log)

        accepted_ids = {
            triplet.id
            for triplet in evaluation.triplets
            if triplet.should_integrate
        }
        final_relations = [
            relation
            for relation in resolved_graph.relationships
            if relation.id in accepted_ids
        ]

        lines.append("\n===== Cypher 示例 =====\n")
        for entity in resolved_graph.entities:
            lines.append(
                f"CREATE (n:{entity.type} {{id: '{entity.canonical_id}', "
                f"name: '{entity.name}', description: '{entity.description}'}});"
            )
        for relation in final_relations:
            lines.append(
                f"MATCH (a {{id: '{relation.source}'}}), "
                f"(b {{id: '{relation.target}'}}) "
                f"CREATE (a)-[:{relation.relation_type.upper()} "
                f"{{evidence: '{relation.evidence[0]}'}}]->(b);"
            )

        return '\n'.join(lines)


# ============================================================================
# 4. 处理入口
# ============================================================================
def md2kg_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return KnowledgeGraphOrchestrator(args).run()

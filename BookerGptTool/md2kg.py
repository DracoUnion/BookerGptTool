import json
import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block
from .openai import call_llm_retry, set_openai_props
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

# 添加Pydantic导入用于自定义模型
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# Schema归纳提示词（用于无目标Schema时自动归纳）
# ============================================================================
SCHEMA_INDUCER_SYSTEM_PROMPT = """
你是一位知识图谱Schema归纳专家。你的任务是从已抽取的实体和关系中归纳出一个适合的知识图谱Schema（即实体类型和关系类型的集合）。

输出格式必须严格遵循以下 JSON Schema：

```
{
  "entity_types": ["类型1", "类型2", ...],
  "relation_types": ["关系类型1", "关系类型2", ...],
  "induction_log": ["操作记录"]
}
```

归纳规则：
1. 实体类型应基于实体的语义特征进行泛化（如将"张三"、"李四"归纳为"人物"）。
2. 关系类型应基于关系的语义特征进行泛化（如"created"、"founded"归纳为"创建"）。
3. 尽量使用通用的、领域中立的类型，但如果领域明显，可以使用领域特定的类型。
4. 去重和合并类义的类型。
5. 记录归纳过程和决策。
"""

SCHEMA_INDUCER_USER_PROMPT = """
待归纳的实体列表:
```
{entities_json}
```
待归纳的关系列表:
```
{relations_json}
```

请根据上述实体和关系，归纳出一个适合的知识图谱Schema。
"""


# Schema归纳结果模型
class SchemaInductionResult(BaseModel):
    entity_types: List[str] = Field(..., description="归纳出的实体类型列表")
    relation_types: List[str] = Field(..., description="归纳出的关系类型列表")
    induction_log: List[str] = Field(default_factory=list, description="归纳过程日志")


# ============================================================================
# 1. 统一智能体
# ============================================================================
from .md2kg_agent import Md2KgAgent


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

    def _induce_schema(self, resolved_graph: ResolvedGraph) -> Dict[str, List[str]]:
        """
        从解析后的图谱中归纳出Schema（当用户未提供目标Schema时使用）。

        Args:
            resolved_graph: 冲突消解后的全局图谱

        Returns:
            包含entity_types和relation_types的字典
        """
        logger.info("开始Schema归纳...")
        # 准备实体和关系的JSON表示
        entities_json = json.dumps(
            [{"canonical_id": e.canonical_id, "name": e.name, "type": e.type, "description": e.description}
             for e in resolved_graph.entities],
            indent=2, ensure_ascii=False
        )
        relations_json = json.dumps(
            [{"id": r.id, "source": r.source, "target": r.target, "relation_type": r.relation_type,
              "evidence": r.evidence, "confidence": r.confidence}
             for r in resolved_graph.relationships],
            indent=2, ensure_ascii=False
        )

        user_prompt = SCHEMA_INDUCER_USER_PROMPT.format(
            entities_json=entities_json,
            relations_json=relations_json
        )
        parse_output = lambda s: SchemaInductionResult.model_validate_json(ext_code_block(s))
        try:
            induction_result = self._call_with_agent(
                SCHEMA_INDUCER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output
            )
            logger.info(f"Schema归纳完成: {len(induction_result.entity_types)} 种实体类型, "
                        f"{len(induction_result.relation_types)} 种关系类型")
            logger.debug(f"归纳日志: {induction_result.induction_log}")
            return {
                "entity_types": induction_result.entity_types,
                "relation_types": induction_result.relation_types
            }
        except Exception as e:
            logger.error(f"Schema归纳失败: {e}")
            # 归纳失败时回退到默认Schema
            logger.info("回退到默认Schema")
            return {
                "entity_types": ["人物", "组织", "地点", "概念", "事件", "作品", "技术", "时间"],
                "relation_types": ["创建", "属于", "位于", "影响", "包含", "发表", "研究", "使用"]
            }

    def _call_with_agent(self, system_prompt: str, user_prompt: str,
                         max_tokens: Optional[int] = None, parse_output: Callable = None) -> Any:
        """
        使用智能体的底层调用方法（封装以避免直接访问私有方法）。

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            max_tokens: 最大token数
            parse_output: 输出解析函数

        Returns:
            解析后的结果
        """
        return self.agent._call(system_prompt, user_prompt, max_tokens=max_tokens, parse_output=parse_output)

    def build_graph(self, chunks: List[Dict[str, Any]], target_schema: Dict[str, List[str]] = None) -> Dict[str, Any]:
        """
        构建知识图谱

        Args:
            chunks: 每个元素包含 'id', 'content', 'summary' (可选)
            target_schema: 目标Schema定义（可选）。如果未提供，将自动归纳Schema。

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
        if target_schema is None:
            # 自动归纳Schema
            target_schema = self._induce_schema(resolved_graph)
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
            logger.fatal('请提供 MD 文件或目录')
            return {}

        ofname = self._get_output_fname()
        if path.isfile(ofname):
            logger.warn('MD 已处理过，跳过')
            return {}

        text = '\n\n'.join(
            open(fname, encoding='utf8').read()
            for fname in fnames
        )
        if not text.strip():
            logger.fatal('输入内容为空')
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


def reg_subparser(subparsers):
    md2kg_parser = subparsers.add_parser("md2kg", help="md2kg")
    md2kg_parser.add_argument("fname", help="MD file name")
    md2kg_parser.add_argument("-t", "--threads", type=int, default=8, help="num threads")
    md2kg_parser.add_argument("-th", "--threshold", type=float, default=0.6,
                             help="integration threshold for evaluation (default: 0.6)")
    md2kg_parser.set_defaults(func=md2kg_handle)
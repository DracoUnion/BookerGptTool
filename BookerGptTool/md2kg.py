import json
import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block
from .openai import call_llm_retry, set_openai_props
from .md2kg_models import *
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



# ============================================================================
# 1. 统一智能体
# ============================================================================
from .md2kg_tools import Md2KgTools


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
        self.agent = Md2KgTools(args)



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
                    self.agent.tool_extract_entities, content, chunk_id, summary
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
                    self.agent.tool_extract_relations, content, chunk_id, entity_list, summary
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
        resolved_graph = self.agent.tool_resolve_conflicts(all_entity_lists, all_relation_lists)

        # ----- 阶段4：Schema对齐 -----
        logger.info("阶段4：Schema对齐...")
        if target_schema is None:
            # 自动归纳Schema
            target_schema = self._induce_schema(resolved_graph)
        schema_alignment_result = self.agent.tool_align_schema(resolved_graph, target_schema)

        # ----- 阶段5：质量评估 -----
        logger.info("阶段5：质量评估...")
        evaluation_result = self.agent.tool_evaluate(resolved_graph, self.integration_threshold)

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


    def _get_output_fname(self) -> str:
        """根据输入路径确定知识图谱输出路径。"""
        return (
            self.args.fname[:-3] + '.cyp'
            if path.isfile(self.args.fname)
            else path.join(self.args.fname, 'kg.cyp')
        )






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
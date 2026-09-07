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

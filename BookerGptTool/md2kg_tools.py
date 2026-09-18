import json

import logging

import os

from os import path

from typing import List, Optional, Dict, Any, Callable

from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block, gen_objs_md5, read_yaml_model, write_yaml_model, read_text, write_text

from .openai import *

from .md2kg_models import *

from .md2kg_pmt import *



class Md2KgTools(ToolsMixin):
    """统一知识图谱智能体"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
        super(ToolsMixin, self).__init__()
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.temperature = getattr(args, 'temp', 0.0)
        self.max_tokens = getattr(args, 'max_tokens', 2000)
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)
        self.pj_dir = (
            path.dirname(args.fname) + '_md2kg'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_md2kg'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        
    def _call(self, system_prompt: str, user_prompt: str,
              max_tokens: Optional[int] = None, parse_output: Callable = None) -> str:
        """调用 LLM（system+user 消息），按 parse_output 解析结果并带重试。"""
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

    def tool_extract_entities(self, chunk_text: str, chunk_id: str, context_summary: str = "") -> EntityList:
        """从单个文本块中抽取实体（EntityList）。"""
        cache_fname = path.join(
            self.pj_dir,
            'entities_' + gen_objs_md5(chunk_text, chunk_id, context_summary) + '.yaml'
        )
        r = read_yaml_model(cache_fname, EntityList)
        if r: return r
        user_prompt = ENTITY_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary, chunk_text=chunk_text
        )
        parse_output = lambda s: EntityList.model_validate_json(ext_code_block(s))
        r = self._call(ENTITY_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_extract_relations(self, chunk_text: str, chunk_id: str, entity_list: EntityList, context_summary: str = "") -> RelationList:
        """结合已知实体，从单个文本块中抽取关系（RelationList）。"""
        entity_context = "\n".join([f"{e.id}: {e.canonical_name} ({e.type})" for e in entity_list.entities])
        cache_fname = path.join(
            self.pj_dir,
            'relations_' + gen_objs_md5(chunk_text, chunk_id, entity_list, context_summary) + '.yaml'
        )
        r = read_yaml_model(cache_fname, RelationList)
        if r: return r
        user_prompt = RELATION_EXTRACTOR_USER_PROMPT.format(
            chunk_id=chunk_id, context_summary=context_summary,
            entity_context=entity_context, chunk_text=chunk_text
        )
        parse_output = lambda s: RelationList.model_validate_json(ext_code_block(s))
        r = self._call(RELATION_EXTRACTOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_resolve_conflicts(self, all_entity_lists: List[EntityList], all_relation_lists: List[RelationList]) -> ResolvedGraph:
        """合并多个文本块的实体与关系，解决重复和矛盾，得到全局图谱（ResolvedGraph）。"""
        cache_fname = path.join(
            self.pj_dir,
            'resolve_' + gen_objs_md5(all_entity_lists, all_relation_lists) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ResolvedGraph)
        if r: return r
        input_data = {
            "entity_lists": [el.model_dump() for el in all_entity_lists],
            "relation_lists": [rl.model_dump() for rl in all_relation_lists]
        }
        input_data_json = json.dumps(input_data, indent=2, ensure_ascii=False)
        user_prompt = CONFLICT_RESOLVER_USER_PROMPT.format(input_data_json=input_data_json)
        parse_output = lambda s: ResolvedGraph.model_validate_json(ext_code_block(s))
        r = self._call(CONFLICT_RESOLVER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_align_schema(self, resolved_graph: ResolvedGraph, target_schema: Dict[str, List[str]] = None) -> SchemaAlignmentResult:
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

        cache_fname = path.join(
            self.pj_dir,
            'align_' + gen_objs_md5(resolved_graph, target_schema) + '.yaml'
        )
        r = read_yaml_model(cache_fname, SchemaAlignmentResult)
        if r: return r

        user_prompt = SCHEMA_ALIGNER_USER_PROMPT.format(
            entity_types=", ".join(target_schema["entity_types"]),
            relation_types=", ".join(target_schema["relation_type"]),
            entities_json=entities_json,
            relations_json=relations_json
        )
        parse_output = lambda s: SchemaAlignmentResult.model_validate_json(ext_code_block(s))
        r = self._call(SCHEMA_ALIGNER_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_evaluate(self, resolved_graph: ResolvedGraph, integration_threshold: float = 0.6) -> EvaluationResult:
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
        cache_fname = path.join(
            self.pj_dir,
            'eval_' + gen_objs_md5(resolved_graph, integration_threshold) + '.yaml'
        )
        r = read_yaml_model(cache_fname, EvaluationResult)
        if r: return r

        user_prompt = EVALUATOR_USER_PROMPT.format(triplets_json=triplets_json)
        parse_output = lambda s: EvaluationResult.model_validate_json(ext_code_block(s))
        r = self._call(EVALUATOR_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_read_input_file(self, fname) -> str:
        """读取待处理的 Markdown 文件。"""
        return read_text(fname)

    def tool_list_input_files(self) -> List[str]:
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

    def tool_induce_schema(self, resolved_graph: ResolvedGraph) -> Dict[str, List[str]]:
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

        cache_fname = path.join(
            self.pj_dir,
            'induce_' + gen_objs_md5(resolved_graph) + '.yaml'
        )
        r = read_yaml_model(cache_fname, None)
        if r: return r

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
            result = {
                "entity_types": induction_result.entity_types,
                "relation_types": induction_result.relation_types
            }
            write_yaml_model(cache_fname, result)
            return result
        except Exception as e:
            logger.error(f"Schema归纳失败: {e}")
            # 归纳失败时回退到默认Schema
            logger.info("回退到默认Schema")
            return {
                "entity_types": ["人物", "组织", "地点", "概念", "事件", "作品", "技术", "时间"],
                "relation_types": ["创建", "属于", "位于", "影响", "包含", "发表", "研究", "使用"]
            }

    @staticmethod
    def tool_build_chunks(text: str) -> List[Chunk]:
        """按段落切分文本块。"""
        paragraphs = [
            paragraph.strip()
            for paragraph in text.split('\n\n')
            if paragraph.strip()
        ]
        return [
            Chunk(
                id=f"chunk_{index + 1:03d}", 
                content=paragraph, 
                summary=paragraph[:100]
            )
            for index, paragraph in enumerate(paragraphs)
        ]

    @staticmethod
    def tool_render_output(result: Result) -> str:
        """将知识图谱结果渲染为报告和 Cypher 示例。"""
        resolved_graph = result.resolved_graph
        schema_alignment = result.schema_alignment
        evaluation = result.evaluation

        lines = ["// ===== 全局实体 =====\n"]
        for entity in resolved_graph.entities:
            lines.append(
                f"// {entity.canonical_id}: {entity.name} "
                f"// ({entity.type}) - {entity.description}"
            )

        lines.append("\n===== 全局关系 =====\n")
        for relation in resolved_graph.relationships:
            lines.append(
                f"// {relation.source} --[{relation.relation_type}]--> "
                f"// {relation.target} : {relation.evidence}"
            )

        lines.append("\n// ===== 消解日志 =====\n")
        lines.append("\n/*\n")
        lines.extend(resolved_graph.resolution_log)
        lines.append('\n */\n')

        lines.append("\n// ===== Schema对齐结果 =====\n")
        lines.append(f"// 对齐实体数: {len(schema_alignment.aligned_entities)}")
        lines.append(f"// 对齐关系数: {len(schema_alignment.aligned_relations)}")
        lines.append(f"// 未对齐数: {schema_alignment.unaligned_count}")
        lines.append("\n/*\n")
        lines.extend(schema_alignment.alignment_log)
        lines.append('\n */\n')

        lines.append("\n// ===== 质量评估结果 =====\n")
        lines.append(f"// 接受三元组数: {evaluation.accepted_count}")
        lines.append(f"// 拒绝三元组数: {evaluation.rejected_count}")
        lines.append(f"// 平均分数: {evaluation.average_score:.2f}")
        lines.append("\n/*\n")
        lines.extend(evaluation.evaluation_log)
        lines.append('\n */\n')

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

    # ============================================================
    # 十二、AutoSchemaKG 兼容工作流
    # ============================================================
    def tool_atlas_extract_triples(self, text: str) -> AtlasTripleList:
        """从单个文本块抽取实体/事件三元组。"""
        cache_fname = path.join(
            self.pj_dir,
            'atlas_triples_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, AtlasTripleList)
        if r: return r
        prompt = render_prompt(ATLAS_TRIPLE_EXTRACT_PMT, text=text)
        parse_output = lambda s: AtlasTripleList.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_atlas_generate_concepts(self, triples: AtlasTripleList) -> AtlasConceptList:
        """基于三元组归纳概念 Schema。"""
        triples_json = json_dump_model(triples)
        cache_fname = path.join(
            self.pj_dir,
            'atlas_concepts_' + gen_objs_md5(triples) + '.yaml'
        )
        r = read_yaml_model(cache_fname, AtlasConceptList)
        if r: return r
        prompt = render_prompt(ATLAS_CONCEPT_GENERATE_PMT, triples_json=triples_json)
        parse_output = lambda s: AtlasConceptList.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_atlas_run_pipeline(self, chunks: List[Chunk]) -> AtlasPipelineResult:
        """完整管道：多块抽取三元组 -> 合并 -> 归纳概念 -> 生成 CSV/GraphML。"""
        import json as _json
        all_triples = []
        for chunk in chunks:
            triples = self.tool_atlas_extract_triples(chunk.content)
            all_triples.extend(triples.triples)
        merged = AtlasTripleList(triples=all_triples)
        triples_json = json_dump_model(merged)
        # 归纳概念
        concepts = self.tool_atlas_generate_concepts(merged)
        # 写入工作区文件
        out_dir = self.pj_dir
        triples_json_path = path.join(out_dir, 'triples.json')
        triples_csv_path = path.join(out_dir, 'triples.csv')
        concepts_csv_path = path.join(out_dir, 'concepts.csv')
        graphml_path = path.join(out_dir, 'kg.graphml')
        # JSON
        write_text(triples_json_path, triples_json)
        # CSV (简化版)
        import csv
        with open(triples_csv_path, 'w', encoding='utf8', newline='') as f:
            w = csv.writer(f)
            w.writerow(['head','head_type','relation','tail','tail_type','sentence','confidence'])
            for t in all_triples:
                w.writerow([t.head, t.head_type, t.relation, t.tail, t.tail_type, t.sentence, t.confidence])
        with open(concepts_csv_path, 'w', encoding='utf8', newline='') as f:
            w = csv.writer(f)
            w.writerow(['name','description','parent','children','entities'])
            for c in concepts.concepts:
                w.writerow([c.name, c.description, c.parent, ';'.join(c.children), ';'.join(c.entities)])
        # GraphML (最小版)
        with open(graphml_path, 'w', encoding='utf8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n<graph id="G" edgedefault="directed">\n')
            seen_nodes = set()
            for t in all_triples:
                for n, typ in [(t.head, t.head_type), (t.tail, t.tail_type)]:
                    if n not in seen_nodes:
                        f.write(f'  <node id="{n}"><data key="type">{typ}</data></node>\n')
                        seen_nodes.add(n)
                f.write(f'  <edge source="{t.head}" target="{t.tail}"><data key="relation">{t.relation}</data></edge>\n')
            f.write('</graph>\n</graphml>')
        return AtlasPipelineResult(
            triples_json=triples_json_path,
            triples_csv=triples_csv_path,
            concepts_csv=concepts_csv_path,
            graphml_path=graphml_path,
        )

    # ============================================================
    # 十三、BookGraph 兼容工作流
    # ============================================================
    def tool_bookgraph_ingest(self, source_desc: str) -> BookGraphIngestionSource:
        """多模态摄入：根据源描述生成标准化摄入记录。"""
        cache_fname = path.join(
            self.pj_dir,
            'bg_ingest_' + gen_objs_md5(source_desc) + '.yaml'
        )
        r = read_yaml_model(cache_fname, BookGraphIngestionSource)
        if r: return r
        prompt = render_prompt(BOOKGRAPH_INGEST_PMT, source_desc=source_desc)
        parse_output = lambda s: BookGraphIngestionSource.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_bookgraph_enrich(
        self, source: BookGraphIngestionSource,
        existing_concepts: List[BookGraphNode],
    ) -> BookGraphEnrichment:
        """LLM 富化：抽取核心概念、字段、完善书目、推断关系。"""
        metadata_json = json_dump_model(source)
        existing_json = json_dump_model(existing_concepts)
        cache_fname = path.join(
            self.pj_dir,
            'bg_enrich_' + gen_objs_md5(source, existing_concepts) + '.yaml'
        )
        r = read_yaml_model(cache_fname, BookGraphEnrichment)
        if r: return r
        prompt = render_prompt(
            BOOKGRAPH_ENRICH_PMT,
            metadata_json=metadata_json,
            existing_concepts_json=existing_json,
        )
        parse_output = lambda s: BookGraphEnrichment.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_bookgraph_build_graph(
        self,
        ingestions: List[BookGraphIngestionSource],
        enrichments: List[BookGraphEnrichment],
    ) -> BookGraphResult:
        """合并所有摄入/富化结果，构建统一图谱并输出发现洞察。"""
        ingestions_json = json_dump_model(ingestions)
        enrichments_json = json_dump_model(enrichments)
        cache_fname = path.join(
            self.pj_dir,
            'bg_graph_' + gen_objs_md5(ingestions, enrichments) + '.yaml'
        )
        r = read_yaml_model(cache_fname, BookGraphResult)
        if r: return r
        prompt = render_prompt(
            BOOKGRAPH_BUILD_PMT,
            ingestions_json=ingestions_json,
            enrichments_json=enrichments_json,
        )
        parse_output = lambda s: BookGraphResult.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    # ============================================================
    # 十四、DeepRead 兼容工作流
    # ============================================================
    def tool_deepread_parse_book(self, text: str) -> DeepReadBook:
        """完整解析书籍：实体/关系/章节摘要/统计。"""
        cache_fname = path.join(
            self.pj_dir,
            'dr_parse_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, DeepReadBook)
        if r: return r
        prompt = render_prompt(DEEPREAD_PARSE_PMT, text=text)
        parse_output = lambda s: DeepReadBook.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_deepread_generate_wiki(self, book: DeepReadBook) -> DeepReadWikiIndex:
        """生成 Wiki 页面与首页索引。"""
        book_json = json_dump_model(book)
        cache_fname = path.join(
            self.pj_dir,
            'dr_wiki_' + gen_objs_md5(book) + '.yaml'
        )
        r = read_yaml_model(cache_fname, DeepReadWikiIndex)
        if r: return r
        prompt = render_prompt(DEEPREAD_WIKI_GENERATE_PMT, book_json=book_json)
        parse_output = lambda s: DeepReadWikiIndex.model_validate_json(ext_code_block(s))
        r = self._call('', prompt, parse_output=parse_output)
        write_yaml_model(cache_fname, r)
        return r

    def tool_deepread_run_pipeline(self, text: str) -> DeepReadWikiIndex:
        """完整管道：解析全书 -> 生成 Wiki -> 写入 wiki/ 目录。"""
        book = self.tool_deepread_parse_book(text)
        wiki = self.tool_deepread_generate_wiki(book)
        # 写入 wiki/ 目录
        wiki_dir = path.join(self.pj_dir, 'wiki')
        os.makedirs(wiki_dir, exist_ok=True)
        write_text(path.join(wiki_dir, 'index.md'), wiki.homepage)
        entities_dir = path.join(wiki_dir, 'entities')
        os.makedirs(entities_dir, exist_ok=True)
        for page in wiki.pages:
            safe_title = page.title.replace('/', '_').replace('\\', '_')
            write_text(path.join(entities_dir, f'{safe_title}.md'), page.content)
        return wiki

    # 工具名 -> OpenAI parameters 结构（type/properties/required）。
    # name 与 description 不再硬编码，由 get_tool_defs 从函数 __name__ / __doc__ 取得。
    # pydantic 模型参数用 Model.schema() 展开，不写死 {"type":"object"}。
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # ── 知识图谱工具 ──────────────────────────────────────
        "tool_extract_entities": params_schema(
            required=['chunk_text', 'chunk_id'],
            chunk_text=base_schema('string', '待抽取实体的文本块内容'),
            chunk_id=base_schema('string', '文本块ID'),
            context_summary=base_schema('string', '上下文摘要，默认空'),
        ),
        "tool_extract_relations": params_schema(
            required=['chunk_text', 'chunk_id', 'entity_list'],
            chunk_text=base_schema('string', '待抽取关系的文本块内容'),
            chunk_id=base_schema('string', '文本块ID'),
            entity_list=model_schema(EntityList, '已抽取的实体列表（EntityList）'),
            context_summary=base_schema('string', '上下文摘要，默认空'),
        ),
        "tool_resolve_conflicts": params_schema(
            required=['all_entity_lists', 'all_relation_lists'],
            all_entity_lists=model_list_schema(EntityList, '多个实体列表（EntityList 列表）'),
            all_relation_lists=model_list_schema(RelationList, '多个关系列表（RelationList 列表）'),
        ),
        "tool_align_schema": params_schema(
            required=['resolved_graph'],
            resolved_graph=model_schema(ResolvedGraph, '冲突消解后的全局图谱（ResolvedGraph）'),
            target_schema=base_schema('object', '目标Schema，含 entity_types 与 relation_type 两个列表；缺省用默认Schema'),
        ),
        "tool_evaluate": params_schema(
            required=['resolved_graph'],
            resolved_graph=model_schema(ResolvedGraph, '冲突消解后的全局图谱（ResolvedGraph）'),
            integration_threshold=base_schema('number', '集成阈值，默认 0.6'),
        ),
        "tool_list_input_files": params_schema(),
        "tool_read_input_file": params_schema(
            required=["fname"],
            fname=base_schema("string", "要读取的文件"),
        ),
        "tool_induce_schema": params_schema(
            required=['resolved_graph'],
            resolved_graph=model_schema(ResolvedGraph, '冲突消解后的全局图谱（ResolvedGraph）'),
        ),
        "tool_build_chunks": params_schema(
            required=['text'],
            text=base_schema('string', '待按段落切分的文本'),
        ),
        "tool_render_output": params_schema(
            required=['result'],
            result=model_schema(Result, '知识图谱结果（Result）'),
        ),

        # ── 十二、AutoSchemaKG 兼容工作流 ──────────────────────────
        "tool_atlas_extract_triples": params_schema(
            required=['text'],
            text=base_schema('string', '待抽取的文本块'),
        ),
        "tool_atlas_generate_concepts": params_schema(
            required=['triples'],
            triples=model_schema(AtlasTripleList, '已抽取的三元组（AtlasTripleList）'),
        ),
        "tool_atlas_run_pipeline": params_schema(
            required=['chunks'],
            chunks=model_list_schema(Chunk, '文本块列表（Chunk）'),
        ),

        # ── 十三、BookGraph 兼容工作流 ────────────────────────────
        "tool_bookgraph_ingest": params_schema(
            required=['source_desc'],
            source_desc=base_schema('string', '摄入源描述（类型+标识符）'),
        ),
        "tool_bookgraph_enrich": params_schema(
            required=['source', 'existing_concepts'],
            source=model_schema(BookGraphIngestionSource, '摄入记录（BookGraphIngestionSource）'),
            existing_concepts=model_list_schema(BookGraphNode, '图中已有概念/字段节点（BookGraphNode）'),
        ),
        "tool_bookgraph_build_graph": params_schema(
            required=['ingestions', 'enrichments'],
            ingestions=model_list_schema(BookGraphIngestionSource, '所有摄入记录（BookGraphIngestionSource）'),
            enrichments=model_list_schema(BookGraphEnrichment, '所有富化结果（BookGraphEnrichment）'),
        ),

        # ── 十四、DeepRead 兼容工作流 ────────────────────────────
        "tool_deepread_parse_book": params_schema(
            required=['text'],
            text=base_schema('string', '书籍全文'),
        ),
        "tool_deepread_generate_wiki": params_schema(
            required=['book'],
            book=model_schema(DeepReadBook, '书籍结构化数据（DeepReadBook）'),
        ),
        "tool_deepread_run_pipeline": params_schema(
            required=['text'],
            text=base_schema('string', '书籍全文'),
        ),
    }


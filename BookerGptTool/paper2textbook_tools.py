# -*- coding: utf-8 -*-
"""
paper2textbook_agent.py —— 封装 paper2textbook 的独立 LLM 调用。
每个方法对应 paper2textbook_pmt.py 中的一个提示词，返回结构化结果或文本。
"""

import yaml
import json
from os import path
import os
from functools import cache
from typing import *

from .openai import *
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import *
from pydantic import parse_obj_as


SUPPORTED_PAPER_EXTS = {'md', 'markdown', 'tex', 'txt', 'pdf'}
SUPPORTED_SURVEY_EXTS = {'md', 'markdown', 'tex', 'txt'}
FORMAT_LABELS = {'md': 'Markdown', 'tex': 'LaTeX'}


class Paper2TextbookTools(ToolsMixin):
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
        super(ToolsMixin, self).__init__()
        self.args = args
        self.model = args.model
        set_openai_props(args)
        self.pj_dir = (
            path.dirname(args.dir) + '_paper2textbook'
            if path.isfile(args.dir) else
            path.abspath(args.dir) + '_paper2textbook'
        )
        os.makedirs(self.pj_dir, exist_ok=True)

    # ── 工具 ──────────────────────────────────────────────
    # ── IO ──────────────────────────────────────────────
    
    # @cache
    def _list_papers(self, source: str) -> List[str]:
        """列出受支持格式（MD/TEX/TXT/PDF）的论文文件路径。

        若 source 是文件则返回其自身；若是目录则递归收集其中文件。
        """
        result = (
            [source.replace('\\', '/')]
            if path.isfile(source) else
            [
                path.join(root, fname).replace('\\', '/')
                for root, _, files in os.walk(source)
                for fname in sorted(files)
            ]
        )
        result = [f for f in result if extname(f).lower() in SUPPORTED_PAPER_EXTS]
        if not result:
            raise ValueError(f'请提供 MD/TEX/TXT/PDF 文件或所在目录')
        return result

    def tool_list_papers(self):
        """列出 args.dir 下的所有论文文件路径。"""
        return self._list_papers(self.args.dir)

    # @cache
    def tool_read_paper(self, fname: str) -> str:
        """读取论文全文：文本格式直接读，PDF 通过 PyMuPDF 抽取文本。"""
        ext = extname(fname).lower()
        if ext in {'md', 'markdown', 'tex', 'txt'}:
            return self._read_text(fname)
        if ext == 'pdf':
            try:
                import fitz
            except ImportError as ex:
                raise ValueError('读取 PDF 需要安装 PyMuPDF') from ex
            with fitz.open(fname) as doc:
                return '\n\n'.join(page.get_text() for page in doc)
        raise ValueError(f'不支持的论文格式：{fname}')

    # @cache
    def tool_paper_brief(self, paper_fnames: List[str], limit=500) -> Dict[str, str]:
        """为每篇论文生成前 limit 字符的简报（换行转空格）。"""
        return {
            f: self.tool_read_paper(f)[:limit].replace('\n', ' ')
            for f in paper_fnames
        }

    @staticmethod
    def _json(
        schema: Type[BaseModel], 
        prompt: str, 
        model: str, 
        args
    ) -> BaseModel:
        """调用 LLM 并把 ```json 代码块解析为 pydantic 对象。"""
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=lambda s: parse_obj_as(schema, json.loads(ext_code_block(s))),
        )

    @staticmethod
    def _text(prompt, model, args):
        """调用 LLM 并提取 [content]...[/content] 中的正文。"""
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=ext_cont_block,
        )

    # ============================================================
    # 一、概念卡片：单篇论文拆解
    # ============================================================

    def tool_ext_concepts(self, paper_name: str, paper: str) -> PaperConcepts:
        """从单篇论文中抽取核心概念/方法/定理/发现，形成概念卡片。"""
        cache_fname = path.join(
            self.pj_dir,
            'ccpt_' + gen_objs_md5(paper) + '.yaml'
        )
        r = read_yaml_model(cache_fname, PaperConcepts)
        if r: return r
        prompt = render_prompt(
            CONCEPT_EXT_PMT,
            paper=paper, pname=paper_name,
        )
        r: PaperConcepts = self._json(
            PaperConcepts,
            prompt, self.model, self.args,
        )
        r.paper = paper_name
        write_yaml_model(cache_fname, r)
        return r

    # ============================================================
    # 二、论文聚类
    # ============================================================

    def tool_cluster_papers(self, paper_briefs: Dict[str, str]) -> List[PartClus]:
        """根据论文简报将论文聚类为若干分部（PartClus）。"""
        cache_fname = path.join(
            self.pj_dir,
            'part_' + gen_objs_md5(paper_briefs) + '.yaml'
        )
        r = read_yaml_model(cache_fname, List[PartClus])
        if r: return r
        prompt = render_prompt(
            PAPER_CLUSTER_PMT,
            paper_briefs=json_dump_model(paper_briefs)
        )
        r = self._json(
            List[PartClus], prompt, self.model, self.args
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_fix_cluster(
        self,
        paper_briefs: Dict[str, str],
        parts: List[PartClus],
        problem: str
    ) -> List[PartClus]:
        """根据问题描述（problem）修正已生成的论文聚类结果。"""
        cache_fname = path.join(
            self.pj_dir,
            'part_fix_' + gen_objs_md5(parts, paper_briefs, problem) + '.yaml'
        )
        r = read_yaml_model(cache_fname, List[PartClus])
        if r: return r
        prompt = render_prompt(
            PAPER_CLUSTER_FIX_PMT,
            paper_briefs=json_dump_model(paper_briefs),
            parts=json_dump_model(parts),
            problem=problem,
        )
        return self._json(List[PartClus], prompt, self.model, self.args)

    # ============================================================
    # 三、全书大纲
    # ============================================================

    def tool_gen_outline(
        self,
        struct: List[str],
        concept_cards: List[PaperConcepts],
    ) -> List[OutlineChapter]:
        """根据书籍结构（struct）与概念卡片生成全书章级大纲。"""
        cache_fname = 'outline_' + gen_objs_md5(concept_cards) + '.yaml'
        r = read_yaml_model(cache_fname, List[OutlineChapter])
        if r: return r
        prompt = render_prompt(
            OUTLINE_PMT,
            struct=json_dump_model(struct),
            concept_cards=json_dump_model(concept_cards),
        )
        r = self._json(
            List[OutlineChapter],
            prompt, self.model, self.args
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_fix_outline(
        self,
        outline: List[OutlineChapter],
        struct: List[str],
        concept_cards: List[PaperConcepts],
        problem: str,
    ) -> List[OutlineChapter]:
        """根据问题描述（problem）修正已生成的全书大纲。"""
        cache_fname = 'outline_fix_' + gen_objs_md5(outline, concept_cards, problem) + '.yaml'
        r = read_yaml_model(cache_fname, List[OutlineChapter])
        if r: return r
        prompt = render_prompt(
            OUTLINE_FIX_PMT,
            outline=json_dump_model(outline),
            struct=json_dump_model(struct),
            concept_cards=json_dump_model(concept_cards),
            problem=problem,
        )
        r =  self._json(
            List[OutlineChapter],
            prompt, self.model, self.args
        )
        write_yaml_model(cache_fname, r)
        return r

    # ============================================================
    # 四、章节细纲
    # ============================================================

    def tool_gen_concept_anls_detail(
        self,
        i: int,
        outline: List[OutlineChapter],
        paper_desc: List[PaperConcepts],
    ) -> ConceptAnlsResult:
        """针对第 i 章做概念分析，产出知识单元（ConceptUnit）列表。"""
        cache_fname = 'detail_ccpt_' + gen_objs_md5(outline, paper_desc, i) + '.yaml'
        r = read_yaml_model(cache_fname, ConceptAnlsResult)
        if r: return r
        prompt = render_prompt(
            CONCEPT_ANLS_DETAIL_PMT,
            i=str(i),
            outline=json_dump_model(outline),
            paper_desc=json_dump_model(paper_desc),
        )
        r = self._json(ConceptAnlsResult, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_gen_rest_detail(
        self,
        i: int,
        outline: List[OutlineChapter],
        detail: ConceptAnlsResult,
        paper_desc: List[PaperConcepts],
    ) -> RestDetailResult:
        """基于概念分析结果生成第 i 章其余内容（目标/概念图/类比/小结/习题）。"""
        cache_fname = 'detail_rest_' + gen_objs_md5(outline, detail, paper_desc, i) + '.yaml'
        r = read_yaml_model(cache_fname, RestDetailResult)
        if r: return r
        prompt = render_prompt(
            REST_DETAIL_PMT,
            i=str(i),
            outline=json_dump_model(outline),
            detail=json_dump_model(detail),
            paper_desc=json_dump_model(paper_desc),
        )
        r = self._json(RestDetailResult, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_fix_detail(
        self,
        i: int,
        detail: ChapterDetail,
        outline: List[OutlineChapter],
        paper_desc: List[PaperConcepts],
        problem: str,
    ) -> ChapterDetail:
        """根据问题描述（problem）修正第 i 章的章节细纲。"""
        cache_fname = 'detail_fix_' + gen_objs_md5(detail, outline, paper_desc, problem) + '.yaml'
        r = read_yaml_model(cache_fname, ChapterDetail)
        if r: return r
        prompt = render_prompt(
            DETAIL_FIX_PMT,
            i=str(i),
            detail=json_dump_model(detail),
            outline=json_dump_model(outline),
            paper_desc=json_dump_model(paper_desc),
            problem=problem,
        )
        r = self._json(ChapterDetail, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    # ============================================================
    # 五、章节正文
    # ============================================================

    def tool_gen_body(
        self, i: int,
        outline: List[OutlineChapter],
        detail: ChapterDetail,
        paper_desc: List[PaperConcepts],
    ) -> str:
        """基于章节细纲生成第 i 章的章节正文。"""
        cache_fname = 'body_' + gen_objs_md5(outline, detail, paper_desc, i) + '.md'
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            r = read_text(cache_fname)
            return r
        prompt = render_prompt(
            BODY_PMT,
            i=str(i),
            outline=json_dump_model(outline),
            detail=json_dump_model(detail),
            paper_desc=json_dump_model(paper_desc),
        )
        r = self._text(prompt, self.model, self.args)
        write_text(cache_fname, r)
        return r

    def tool_check_body(self, body: str, detail: ChapterDetail) -> str:
        """检查章节正文是否与细纲一致，并返回问题反馈。"""
        cache_fname = 'body_check_' + gen_objs_md5(body, detail) + '.md'
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            r = read_text(cache_fname)
            return r
        prompt = render_prompt(BODY_CHK_PMT, body=body, detail=json_dump_model(detail))
        r = self._text(prompt, self.model, self.args)
        write_text(cache_fname, r)
        return r

    def tool_fix_body(self, body: str, comment: str, paper_desc: List[PaperConcepts]) -> str:
        """根据检查反馈（comment）修正章节正文。"""
        cache_fname = 'body_fix_' + gen_objs_md5(body, comment, paper_desc) + '.md'
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            r = read_text(cache_fname)
            return r
        prompt = render_prompt(
            BODY_FIX_PMT,
            body=body,
            comment=comment,
            paper_desc=json_dump_model(paper_desc),
        )
        r = self._text(prompt, self.model, self.args)
        write_text(cache_fname, r)
        return r

    # ============================================================
    # 六、辅助检查（术语对照 / 跨章一致性 / 引用审计）
    # ============================================================

    def tool_gen_glossary(self, paper: str) -> List[GlossaryEntry]:
        """根据论文内容生成术语对照表（术语/别名/首次出现位置）。"""
        cache_fname = 'glossary_' + gen_objs_md5(paper) + '.yaml'
        r = read_yaml_model(cache_fname, List[GlossaryEntry])
        if r: return r
        prompt = render_prompt(TERM_GLOSSARY_PMT, paper=paper)
        r = self._json(List[GlossaryEntry], prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_check_consistency(self, previous_chapter: str, current_chapter: str) -> str:
        """检查当前章与上一章之间的术语/口径一致性，并返回问题反馈。"""
        cache_fname = 'consist_check_' + gen_objs_md5(previous_chapter, current_chapter) + '.md'
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            r = read_text(cache_fname)
            return r
        prompt = render_prompt(
            CONSISTENCY_CHK_PMT,
            previous_chapters=previous_chapter, current_chapter=current_chapter,
        )
        r = self._text(prompt, self.model, self.args)
        write_text(cache_fname, r)
        return r

    def tool_audit_citations(self, chapter: str, paper: str) -> CitationAudit:
        """审计章节中的引用情况，返回引用统计、无支撑观点与缺失概念。"""
        cache_fname = 'audit_' + gen_objs_md5(chapter, paper) + '.yaml'
        r = read_yaml_model(cache_fname, CitationAudit)
        if r: return r
        prompt = render_prompt(CITATION_AUDIT_PMT, book=chapter, paper=paper)
        r = self._json(CitationAudit, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r



    @staticmethod
    def tool_parts_coverage_problem(paper_fnames: List[str], parts: List[PartClus]) -> str:
        """检查论文是否都被某分部覆盖；返回缺失/多余论文的问题描述。"""
        paper_ids = set(paper_fnames)
        clustered = {p for part in parts for p in part.papers}
        missing = sorted(paper_ids - clustered)
        unknown = sorted(clustered - paper_ids)
        prob = ''
        if missing:
            prob += '以下论文未出现在任何部分中：\n' + '\n'.join(missing) + '\n'
        if unknown:
            prob += '以下论文不存在：\n' + '\n'.join(unknown) + '\n'
        return prob


    @staticmethod
    def tool_outline_coverage_problem(
        cards: List[PaperConcepts],
        outline: OutlineChapter,
    ) -> str:
        """检查概念卡片是否都被大纲节点引用；返回未被纳入的论文列表问题描述。"""
        # 大纲节点的 src 里列出的是支撑该知识点的论文 ID。
        used_papers = {
            src.paper for  n in outline.nodes for src in n.src
        }
        missing = sorted(
            card.paper for card in cards if card.paper not in used_papers
        )
        if missing:
            return '以下论文/概念卡片未纳入大纲：\n' + '\n'.join(missing)
        return ''

    @staticmethod
    def tool_detail_coverage_problem(chapter: OutlineChapter, detail: ChapterDetail) -> str:
        """检查细纲引用的论文是否覆盖章节所需论文；返回缺失/多余论文问题描述。"""
        required = {
            src.paper for n in chapter.nodes for src in n.src
        }
        used = {s.paper for u in detail.units for s in u.sources}
        missing = sorted(required - used)
        unknown = used - required
        prob = ''
        if missing:
            prob += '以下论文未在细纲中引用：\n' + '\n'.join(missing) + '\n'
        if unknown:
            prob += '以下论文不存在：\n' + '\n'.join(unknown) + '\n'
        return prob



    # 工具名 -> OpenAI parameters 结构（type/properties/required）。
    # name 与 description 不再硬编码，由 get_tool_defs 从函数 __name__ / __doc__ 取得。
    # pydantic 模型参数用 Model.schema() 展开，不写死 {"type":"object"}。
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        # ── IO：论文文件与工作区读写 ──────────────────────────
        **ToolsMixin._TOOL_PARAMS,
        "tool_list_papers": params_schema(),
        "tool_read_paper": params_schema(
            required=['fname'],
            fname=base_schema('string', '论文文件路径'),
        ),
        "tool_paper_brief": params_schema(
            required=['paper_fnames'],
            paper_fnames=str_list_schema('论文文件路径列表'),
            limit=base_schema('integer', '每个简报的最大字符数，默认 500'),
        ),

        # ── 一、概念卡片：单篇论文拆解 ──────────────────────────
        "tool_ext_concepts": params_schema(
            required=['paper_name', 'paper'],
            paper_name=base_schema('string', '论文名称/标识'),
            paper=base_schema('string', '论文全文文本'),
        ),

        # ── 二、论文聚类 ──────────────────────────────────────
        "tool_cluster_papers": params_schema(
            required=['paper_briefs'],
            paper_briefs=str_str_map_schema('论文路径到简报的映射'),
        ),
        "tool_fix_cluster": params_schema(
            required=['paper_briefs', 'parts', 'problem'],
            paper_briefs=str_str_map_schema('论文路径到简报的映射'),
            parts=model_list_schema(PartClus, '当前聚类结果（PartClus 列表）'),
            problem=base_schema('string', '需要修正的问题描述'),
        ),

        # ── 三、全书大纲 ──────────────────────────────────────
        "tool_gen_outline": params_schema(
            required=['struct', 'concept_cards'],
            struct=str_list_schema('书籍结构（章节划分）'),
            concept_cards=model_list_schema(PaperConcepts, '概念卡片列表（PaperConcepts）'),
        ),
        "tool_fix_outline": params_schema(
            required=['outline', 'struct', 'concept_cards', 'problem'],
            outline=model_list_schema(OutlineChapter, '当前大纲（OutlineChapter 列表）'),
            struct=str_list_schema('书籍结构（章节划分）'),
            concept_cards=model_list_schema(PaperConcepts, '概念卡片列表（PaperConcepts）'),
            problem=base_schema('string', '需要修正的问题描述'),
        ),

        # ── 四、章节细纲 ──────────────────────────────────────
        "tool_gen_concept_anls_detail": params_schema(
            required=['i', 'outline', 'paper_desc'],
            i=base_schema('integer', '章节序号'),
            outline=model_list_schema(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
            paper_desc=model_list_schema(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
        ),
        "tool_gen_rest_detail": params_schema(
            required=['i', 'outline', 'detail', 'paper_desc'],
            i=base_schema('integer', '章节序号'),
            outline=model_list_schema(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
            detail=model_schema(ConceptAnlsResult, '概念分析结果（ConceptAnlsResult）'),
            paper_desc=model_list_schema(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
        ),
        "tool_fix_detail": params_schema(
            required=['i', 'detail', 'outline', 'paper_desc', 'problem'],
            i=base_schema('integer', '章节序号'),
            detail=model_schema(ChapterDetail, '当前章节细纲（ChapterDetail）'),
            outline=model_list_schema(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
            paper_desc=model_list_schema(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
            problem=base_schema('string', '需要修正的问题描述'),
        ),

        # ── 五、章节正文 ──────────────────────────────────────
        "tool_gen_body": params_schema(
            required=['i', 'outline', 'detail', 'paper_desc'],
            i=base_schema('integer', '章节序号'),
            outline=model_list_schema(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
            detail=model_schema(ChapterDetail, '章节细纲（ChapterDetail）'),
            paper_desc=model_list_schema(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
        ),
        "tool_check_body": params_schema(
            required=['body', 'detail'],
            body=base_schema('string', '章节正文'),
            detail=model_schema(ChapterDetail, '章节细纲（ChapterDetail）'),
        ),
        "tool_fix_body": params_schema(
            required=['body', 'comment', 'paper_desc'],
            body=base_schema('string', '章节正文'),
            comment=base_schema('string', '检查反馈内容'),
            paper_desc=model_list_schema(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
        ),

        # ── 六、辅助检查 ──────────────────────────────────────
        "tool_gen_glossary": params_schema(
            required=['paper'],
            paper=base_schema('string', '论文内容文本'),
        ),
        "tool_check_consistency": params_schema(
            required=['previous_chapter', 'current_chapter'],
            previous_chapter=base_schema('string', '上一章正文'),
            current_chapter=base_schema('string', '当前章正文'),
        ),
        "tool_audit_citations": params_schema(
            required=['chapter', 'paper'],
            chapter=base_schema('string', '章节文本'),
            paper=base_schema('string', '论文内容'),
        ),

        # ── 覆盖率校验（静态）────────────────────────────────
        "tool_parts_coverage_problem": params_schema(
            required=['paper_fnames', 'parts'],
            paper_fnames=str_list_schema('论文文件路径列表'),
            parts=model_list_schema(PartClus, '聚类结果（PartClus 列表）'),
        ),
        "tool_outline_coverage_problem": params_schema(
            required=['cards', 'outline'],
            cards=model_list_schema(PaperConcepts, '概念卡片列表（PaperConcepts）'),
            outline=model_schema(OutlineChapter, '大纲章（OutlineChapter）'),
        ),
        "tool_detail_coverage_problem": params_schema(
            required=['chapter', 'detail'],
            chapter=model_schema(OutlineChapter, '大纲章（OutlineChapter）'),
            detail=model_schema(ChapterDetail, '章节细纲（ChapterDetail）'),
        ),
    }


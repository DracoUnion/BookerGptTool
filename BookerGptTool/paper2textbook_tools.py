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

from .openai import ask_chatgpt_retry, set_openai_props
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import ext_code_block, ext_cont_block, render_prompt, extname
from pydantic import parse_obj_as


SUPPORTED_PAPER_EXTS = {'md', 'markdown', 'tex', 'txt', 'pdf'}
SUPPORTED_SURVEY_EXTS = {'md', 'markdown', 'tex', 'txt'}
FORMAT_LABELS = {'md': 'Markdown', 'tex': 'LaTeX'}


# ── 工具参数 schema 辅助 ──────────────────────────────────────────
# 为 _TOOL_PARAMS 生成 OpenAI 参数结构。
# pydantic 模型参数用 Model.schema() 展开其结构，而非仅写 {"type":"object"}。


def _sp(typ: str, desc: str, **extra) -> Dict[str, Any]:
    """基础标量参数：{"type": typ, "description": desc, **extra}。"""
    return {'type': typ, 'description': desc, **extra}


def _p_model(model, desc: str) -> Dict[str, Any]:
    """pydantic 模型参数：以 model.schema() 展开字段结构。"""
    return {**model.schema(), 'description': desc}


def _p_list(model, desc: str) -> Dict[str, Any]:
    """元素为 pydantic 模型的数组参数：items 用 model.schema()。"""
    return _sp('array', desc, items=model.schema())


def _p_str_list(desc: str) -> Dict[str, Any]:
    """元素为字符串的数组参数。"""
    return _sp('array', desc, items={'type': 'string'})


def _p_str_str_map(desc: str) -> Dict[str, Any]:
    """string->string 字典参数。"""
    return _sp('object', desc, additionalProperties={'type': 'string'})


class Paper2TextbookTools:
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
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
    
    @cache
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

    def _write_text(self, fname: str, text: str) -> None:
        """将 text 以 UTF-8 写入 fname（自动创建父目录）。"""
        os.makedirs(path.dirname(fname), exist_ok=True)
        open(fname, 'w', encoding='utf8').write(text)

    def _read_text(self, fname: str) -> str:
        """以 UTF-8 读取 fname 的文本内容。"""
        return open(fname, encoding='utf8').read()

    @cache
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

    @cache
    def tool_paper_brief(self, paper_fnames: List[str], limit=500) -> Dict[str, str]:
        """为每篇论文生成前 limit 字符的简报（换行转空格）。"""
        return {
            f: self.tool_read_paper(f)[:limit].replace('\n', ' ')
            for f in paper_fnames
        }


    def _write_yaml(self, fname: str, obj) -> None:
        """将对象（含 pydantic 模型/列表）以 YAML 形式写入 fname。"""
        if isinstance(obj, BaseModel):
            obj = obj.dict()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        os.makedirs(path.dirname(fname), exist_ok=True)
        with open(fname, 'w', encoding='utf8') as f:
            yaml.safe_dump(obj, f, allow_unicode=True, sort_keys=False)

    def _read_yaml(self, fname: str, model):
        """从 fname 读取 YAML 并解析为指定 pydantic 模型；文件缺失或损坏时返回 None。"""
        if not path.isfile(fname) or not path.getsize(fname):
            return None
        try:
            data = yaml.safe_load(open(fname, encoding='utf8').read())
        except yaml.error.YAMLError:
            return None
        return parse_obj_as(model, data)

    def tool_read_workspace_text(self, fname: str):
        """读取项目目录下的文本文件（fname 为项目内相对路径）。"""
        return self._read_text(path.join(self.pj_dir, fname))

    def tool_write_workspace_text(self, fname: str, text: str):
        """向项目目录写入文本文件（fname 为项目内相对路径）。"""
        return self._write_text(path.join(self.pj_dir, fname), text)

    def tool_read_workspace_json(self, fname: str):
        """读取项目目录下的 JSON 文件并解析为对象。"""
        return json.loads(self.tool_read_workspace_text(fname))

    def tool_write_workspace_json(self, fname: str, obj: Any):
        """将对象以 JSON 形式写入项目目录（fname 为项目内相对路径）。"""
        return self.tool_write_workspace_text(
            fname, json.dumps(obj, ensure_ascii=False))

    def tool_read_workspace_yaml(self, fname: str):
        """读取项目目录下的 YAML 文件并解析为对象。"""
        return yaml.safe_load(self.tool_read_workspace_text(fname))

    def tool_write_workspace_yaml(self, fname: str, obj: Any):
        """将对象以 YAML 形式写入项目目录（fname 为项目内相对路径）。"""
        return self.tool_write_workspace_text(
            fname, yaml.safe_dump(obj, allow_unicode=True))

    def _json_dump(self, obj) -> str:
        """将对象（含 pydantic 模型/列表）序列化为 JSON 字符串。"""
        if isinstance(obj, BaseModel):
            obj = obj.model_dump()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _json_load(self, text: str, model):
        """将 JSON 文本解析为指定 pydantic 模型。"""
        return parse_obj_as(model, json.loads(text))

    @staticmethod
    def _json(schema, prompt, model, args):
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
        prompt = render_prompt(
            CONCEPT_EXT_PMT,
            paper=paper, pname=paper_name,
        )
        r: PaperConcepts = self._json(
            PaperConcepts,
            prompt, self.model, self.args,
        )
        r.paper = paper_name
        return r

    # ============================================================
    # 二、论文聚类
    # ============================================================

    def tool_cluster_papers(self, paper_briefs: Dict[str, str]) -> List[PartClus]:
        """根据论文简报将论文聚类为若干分部（PartClus）。"""
        prompt = render_prompt(
            PAPER_CLUSTER_PMT,
            paper_briefs=self._json_dump(paper_briefs)
        )
        return self._json(
            List[PartClus], prompt, self.model, self.args
        )

    def tool_fix_cluster(
        self,
        paper_briefs: Dict[str, str],
        parts: List[PartClus],
        problem: str
    ) -> List[PartClus]:
        """根据问题描述（problem）修正已生成的论文聚类结果。"""
        prompt = render_prompt(
            PAPER_CLUSTER_FIX_PMT,
            paper_briefs=self._json_dump(paper_briefs),
            parts=self._json_dump(parts),
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
        prompt = render_prompt(
            OUTLINE_PMT,
            struct=self._json_dump(struct),
            concept_cards=self._json_dump(concept_cards),
        )
        return self._json(
            List[OutlineChapter],
            prompt, self.model, self.args
        )

    def tool_fix_outline(
        self,
        outline: List[OutlineChapter],
        struct: List[str],
        concept_cards: List[PaperConcepts],
        problem: str,
    ) -> List[OutlineChapter]:
        """根据问题描述（problem）修正已生成的全书大纲。"""
        prompt = render_prompt(
            OUTLINE_FIX_PMT,
            outline=self._json_dump(outline),
            struct=self._json_dump(struct),
            concept_cards=self._json_dump(concept_cards),
            problem=problem,
        )
        return self._json(
            List[OutlineChapter],
            prompt, self.model, self.args
        )

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
        prompt = render_prompt(
            CONCEPT_ANLS_DETAIL_PMT,
            i=str(i),
            outline=self._json_dump(outline),
            paper_desc=self._json_dump(paper_desc),
        )
        return self._json(ConceptAnlsResult, prompt, self.model, self.args)

    def tool_gen_rest_detail(
        self,
        i: int,
        outline: List[OutlineChapter],
        detail: ConceptAnlsResult,
        paper_desc: List[PaperConcepts],
    ) -> RestDetailResult:
        """基于概念分析结果生成第 i 章其余内容（目标/概念图/类比/小结/习题）。"""
        prompt = render_prompt(
            REST_DETAIL_PMT,
            i=str(i),
            outline=self._json_dump(outline),
            detail=self._json_dump(detail),
            paper_desc=self._json_dump(paper_desc),
        )
        return self._json(RestDetailResult, prompt, self.model, self.args)

    def tool_fix_detail(
        self,
        i: int,
        detail: ChapterDetail,
        outline: List[OutlineChapter],
        paper_desc: List[PaperConcepts],
        problem: str,
    ) -> ChapterDetail:
        """根据问题描述（problem）修正第 i 章的章节细纲。"""
        prompt = render_prompt(
            DETAIL_FIX_PMT,
            i=str(i),
            detail=self._json_dump(detail),
            outline=self._json_dump(outline),
            paper_desc=self._json_dump(paper_desc),
            problem=problem,
        )
        return self._json(ChapterDetail, prompt, self.model, self.args)

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
        prompt = render_prompt(
            BODY_PMT,
            i=str(i),
            outline=self._json_dump(outline),
            detail=self._json_dump(detail),
            paper_desc=self._json_dump(paper_desc),
        )
        return self._text(prompt, self.model, self.args)

    def tool_check_body(self, body: str, detail: ChapterDetail) -> str:
        """检查章节正文是否与细纲一致，并返回问题反馈。"""
        prompt = render_prompt(BODY_CHK_PMT, body=body, detail=self._json_dump(detail))
        return self._text(prompt, self.model, self.args)

    def tool_fix_body(self, body: str, comment: str, paper_desc: List[PaperConcepts]) -> str:
        """根据检查反馈（comment）修正章节正文。"""
        prompt = render_prompt(
            BODY_FIX_PMT,
            body=body,
            comment=comment,
            paper_desc=self._json_dump(paper_desc),
        )
        return self._text(prompt, self.model, self.args)

    # ============================================================
    # 六、辅助检查（术语对照 / 跨章一致性 / 引用审计）
    # ============================================================

    def tool_gen_glossary(self, paper: str) -> List[GlossaryEntry]:
        """根据论文内容生成术语对照表（术语/别名/首次出现位置）。"""
        prompt = render_prompt(TERM_GLOSSARY_PMT, paper=paper)
        return self._json(List[GlossaryEntry], prompt, self.model, self.args)

    def tool_check_consistency(self, previous_chapter: str, current_chapter: str) -> str:
        """检查当前章与上一章之间的术语/口径一致性，并返回问题反馈。"""
        prompt = render_prompt(
            CONSISTENCY_CHK_PMT,
            previous_chapters=previous_chapter, current_chapter=current_chapter,
        )
        return self._text(prompt, self.model, self.args)

    def tool_audit_citations(self, chapter: str, paper: str) -> CitationAudit:
        """审计章节中的引用情况，返回引用统计、无支撑观点与缺失概念。"""
        prompt = render_prompt(CITATION_AUDIT_PMT, book=chapter, paper=paper)
        return self._json(CitationAudit, prompt, self.model, self.args)



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

    def list_tools(self) -> Dict[str, Callable]:
        """返回以 tool_ 开头、可调用的成员方法字典（工具名→方法）。"""
        return {
            name:val
            for name, val in self.__dict__.items()
            if callable(val) and name.startswith('tool_')
        }


    # 工具名 -> OpenAI parameters 结构（type/properties/required）。
    # name 与 description 不再硬编码，由 get_tool_defs 从函数 __name__ / __doc__ 取得。
    # pydantic 模型参数用 Model.schema() 展开，不写死 {"type":"object"}。
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        # ── IO：论文文件与工作区读写 ──────────────────────────
        "tool_list_papers": {
            "type": "object", "properties": {}, "required": [],
        },
        "tool_read_paper": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '论文文件路径'),
            },
            "required": ["fname"],
        },
        "tool_paper_brief": {
            "type": "object",
            "properties": {
                "paper_fnames": _p_str_list('论文文件路径列表'),
                "limit": _sp('integer', '每个简报的最大字符数，默认 500'),
            },
            "required": ["paper_fnames"],
        },
        "tool_read_workspace_text": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
            },
            "required": ["fname"],
        },
        "tool_write_workspace_text": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
                "text": _sp('string', '要写入的文本内容'),
            },
            "required": ["fname", "text"],
        },
        "tool_read_workspace_json": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
            },
            "required": ["fname"],
        },
        "tool_write_workspace_json": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
                "obj": _sp('object', '要序列化为 JSON 的对象'),
            },
            "required": ["fname", "obj"],
        },
        "tool_read_workspace_yaml": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
            },
            "required": ["fname"],
        },
        "tool_write_workspace_yaml": {
            "type": "object",
            "properties": {
                "fname": _sp('string', '项目内相对路径'),
                "obj": _sp('object', '要写入的对象'),
            },
            "required": ["fname", "obj"],
        },

        # ── 一、概念卡片：单篇论文拆解 ──────────────────────────
        "tool_ext_concepts": {
            "type": "object",
            "properties": {
                "paper_name": _sp('string', '论文名称/标识'),
                "paper": _sp('string', '论文全文文本'),
            },
            "required": ["paper_name", "paper"],
        },

        # ── 二、论文聚类 ──────────────────────────────────────
        "tool_cluster_papers": {
            "type": "object",
            "properties": {
                "paper_briefs": _p_str_str_map('论文路径到简报的映射'),
            },
            "required": ["paper_briefs"],
        },
        "tool_fix_cluster": {
            "type": "object",
            "properties": {
                "paper_briefs": _p_str_str_map('论文路径到简报的映射'),
                "parts": _p_list(PartClus, '当前聚类结果（PartClus 列表）'),
                "problem": _sp('string', '需要修正的问题描述'),
            },
            "required": ["paper_briefs", "parts", "problem"],
        },

        # ── 三、全书大纲 ──────────────────────────────────────
        "tool_gen_outline": {
            "type": "object",
            "properties": {
                "struct": _p_str_list('书籍结构（章节划分）'),
                "concept_cards": _p_list(PaperConcepts, '概念卡片列表（PaperConcepts）'),
            },
            "required": ["struct", "concept_cards"],
        },
        "tool_fix_outline": {
            "type": "object",
            "properties": {
                "outline": _p_list(OutlineChapter, '当前大纲（OutlineChapter 列表）'),
                "struct": _p_str_list('书籍结构（章节划分）'),
                "concept_cards": _p_list(PaperConcepts, '概念卡片列表（PaperConcepts）'),
                "problem": _sp('string', '需要修正的问题描述'),
            },
            "required": ["outline", "struct", "concept_cards", "problem"],
        },

        # ── 四、章节细纲 ──────────────────────────────────────
        "tool_gen_concept_anls_detail": {
            "type": "object",
            "properties": {
                "i": _sp('integer', '章节序号'),
                "outline": _p_list(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
                "paper_desc": _p_list(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
            },
            "required": ["i", "outline", "paper_desc"],
        },
        "tool_gen_rest_detail": {
            "type": "object",
            "properties": {
                "i": _sp('integer', '章节序号'),
                "outline": _p_list(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
                "detail": _p_model(ConceptAnlsResult, '概念分析结果（ConceptAnlsResult）'),
                "paper_desc": _p_list(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
            },
            "required": ["i", "outline", "detail", "paper_desc"],
        },
        "tool_fix_detail": {
            "type": "object",
            "properties": {
                "i": _sp('integer', '章节序号'),
                "detail": _p_model(ChapterDetail, '当前章节细纲（ChapterDetail）'),
                "outline": _p_list(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
                "paper_desc": _p_list(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
                "problem": _sp('string', '需要修正的问题描述'),
            },
            "required": ["i", "detail", "outline", "paper_desc", "problem"],
        },

        # ── 五、章节正文 ──────────────────────────────────────
        "tool_gen_body": {
            "type": "object",
            "properties": {
                "i": _sp('integer', '章节序号'),
                "outline": _p_list(OutlineChapter, '全书大纲（OutlineChapter 列表）'),
                "detail": _p_model(ChapterDetail, '章节细纲（ChapterDetail）'),
                "paper_desc": _p_list(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
            },
            "required": ["i", "outline", "detail", "paper_desc"],
        },
        "tool_check_body": {
            "type": "object",
            "properties": {
                "body": _sp('string', '章节正文'),
                "detail": _p_model(ChapterDetail, '章节细纲（ChapterDetail）'),
            },
            "required": ["body", "detail"],
        },
        "tool_fix_body": {
            "type": "object",
            "properties": {
                "body": _sp('string', '章节正文'),
                "comment": _sp('string', '检查反馈内容'),
                "paper_desc": _p_list(PaperConcepts, '论文概念卡片列表（PaperConcepts）'),
            },
            "required": ["body", "comment", "paper_desc"],
        },

        # ── 六、辅助检查 ──────────────────────────────────────
        "tool_gen_glossary": {
            "type": "object",
            "properties": {
                "paper": _sp('string', '论文内容文本'),
            },
            "required": ["paper"],
        },
        "tool_check_consistency": {
            "type": "object",
            "properties": {
                "previous_chapter": _sp('string', '上一章正文'),
                "current_chapter": _sp('string', '当前章正文'),
            },
            "required": ["previous_chapter", "current_chapter"],
        },
        "tool_audit_citations": {
            "type": "object",
            "properties": {
                "chapter": _sp('string', '章节文本'),
                "paper": _sp('string', '论文内容'),
            },
            "required": ["chapter", "paper"],
        },

        # ── 覆盖率校验（静态）────────────────────────────────
        "tool_parts_coverage_problem": {
            "type": "object",
            "properties": {
                "paper_fnames": _p_str_list('论文文件路径列表'),
                "parts": _p_list(PartClus, '聚类结果（PartClus 列表）'),
            },
            "required": ["paper_fnames", "parts"],
        },
        "tool_outline_coverage_problem": {
            "type": "object",
            "properties": {
                "cards": _p_list(PaperConcepts, '概念卡片列表（PaperConcepts）'),
                "outline": _p_model(OutlineChapter, '大纲章（OutlineChapter）'),
            },
            "required": ["cards", "outline"],
        },
        "tool_detail_coverage_problem": {
            "type": "object",
            "properties": {
                "chapter": _p_model(OutlineChapter, '大纲章（OutlineChapter）'),
                "detail": _p_model(ChapterDetail, '章节细纲（ChapterDetail）'),
            },
            "required": ["chapter", "detail"],
        },
    }

    @staticmethod
    def _clean_doc(doc: Optional[str]) -> str:
        """将函数 docstring 压缩为单行描述。"""
        if not doc:
            return ''
        return ' '.join(line.strip() for line in doc.splitlines() if line.strip())

    def get_tool_defs(self) -> List:
        """返回所有 tool_* 方法的 OpenAI 函数工具定义（Chat Completions tools 格式）。

        name 取自函数对象的 __name__，description 取自函数对象的 __doc__；
        parameters 结构由 _TOOL_PARAMS 提供。可直接传给 openai 的 tools 参数。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": getattr(self, name).__name__,
                    "description": self._clean_doc(getattr(self, name).__doc__),
                    "parameters": params,
                },
            }
            for name, params in self._TOOL_PARAMS.items()
        ]

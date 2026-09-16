# -*- coding: utf-8 -*-
"""
paper2textbook_agent.py —— 封装 paper2textbook 的独立 LLM 调用。
每个方法对应 paper2textbook_pmt.py 中的一个提示词，返回结构化结果或文本。
"""

import yaml
import json
from os import path
import os

from .openai import ask_chatgpt_retry, set_openai_props
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import ext_code_block, ext_cont_block, render_prompt, extname
from pydantic import parse_obj_as


class Paper2TextbookTools:
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, proj_dir, args):
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
    
    def _list_papers(self, source: str) -> List[str]:
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
        return self._list_papers(self.args.dir)

    def _write_text(self, fname: str, text: str) -> None:
        os.makedirs(path.dirname(fname), exist_ok=True)
        open(fname, 'w', encoding='utf8').write(text)

    def _read_text(self, fname: str) -> str:
        return open(fname, encoding='utf8').read()

    def _read_paper(self, fname: str) -> str:
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

    def tool_read_paper(self, fname: str):
        return self._read_paper(path.join(self.args.dir, fname))

    def tool_read_workspace_text(self, fname: str):
        return self._read_text(path.join(self.pj_dir, fname))

    def tool_write_workspace_text(self, fname: str, text: str):
        return self._write_text(path.join(self.pj_dir, fname), text)

    def tool_read_workspace_json(self, fname: str):
        return json.loads(self.tool_read_workspace_text(fname))

    def tool_write_workspace_json(self, fname: str, obj: Any):
        return self.tool_write_workspace_text(
            fname, json.dumps(obj, ensure_ascii=False))

    def tool_read_workspace_yaml(self, fname: str):
        return yaml.safe_load(self.tool_read_workspace_text(fname))

    def tool_write_workspace_yaml(self, fname: str, obj: Any):
        return self.tool_write_workspace_text(
            fname, yaml.safe_dump(obj, allow_unicode=True))

    def _json_dump(self, obj) -> str:
        if isinstance(obj, BaseModel):
            obj = obj.model_dump()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _json_load(self, text: str, model):
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
        detail: str, 
        outline: str, 
        paper_desc: str, 
        problem: str,
    ) -> ChapterDetail:
        prompt = render_prompt(
            DETAIL_FIX_PMT,
            i=i, detail=detail, 
            outline=outline, 
            paper_desc=paper_desc,
            problem=problem,
        )
        return self._json(ChapterDetail, prompt, self.model, self.args)

    # ============================================================
    # 五、章节正文
    # ============================================================

    def gen_body(
        self, i: str, outline: str, detail: str, paper_desc: str,
    ) -> str:
        prompt = render_prompt(
            BODY_PMT,
            i=i, outline=outline, detail=detail, paper_desc=paper_desc,
        )
        return self._text(prompt, self.model, self.args)

    def check_body(self, body: str, detail: str) -> str:
        prompt = render_prompt(BODY_CHK_PMT, body=body, detail=detail)
        return self._text(prompt, self.model, self.args)

    def fix_body(self, body: str, comment: str, paper_desc: str) -> str:
        prompt = render_prompt(
            BODY_FIX_PMT,
            body=body, comment=comment, paper_desc=paper_desc,
        )
        return self._text(prompt, self.model, self.args)

    # ============================================================
    # 六、辅助检查（术语对照 / 跨章一致性 / 引用审计）
    # ============================================================

    def gen_glossary(self, paper: str) -> List[GlossaryEntry]:
        prompt = render_prompt(TERM_GLOSSARY_PMT, paper=paper)
        return self._json(List[GlossaryEntry], prompt, self.model, self.args)

    def check_consistency(self, previous_chapters: str, current_chapter: str) -> str:
        prompt = render_prompt(
            CONSISTENCY_CHK_PMT,
            previous_chapters=previous_chapters, current_chapter=current_chapter,
        )
        return self._text(prompt, self.model, self.args)

    def audit_citations(self, book: str, paper: str) -> CitationAudit:
        prompt = render_prompt(CITATION_AUDIT_PMT, book=book, paper=paper)
        return self._json(CitationAudit, prompt, self.model, self.args)
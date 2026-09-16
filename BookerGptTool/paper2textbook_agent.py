# -*- coding: utf-8 -*-
"""
paper2textbook_agent.py —— 封装 paper2textbook 的独立 LLM 调用。
每个方法对应 paper2textbook_pmt.py 中的一个提示词，返回结构化结果或文本。
"""

import json

from .openai import ask_chatgpt_retry, set_openai_props
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import ext_code_block, ext_cont_block, render_prompt
from pydantic import parse_obj_as


class Paper2TextbookAgent:
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(args)

    # ── 工具 ──────────────────────────────────────────────

    @staticmethod
    def _json(parser, prompt, model, args):
        """调用 LLM 并把 ```json 代码块解析为 pydantic 对象。"""
        return ask_chatgpt_retry(
            prompt, model, args,
            parse_output=lambda s: parser(json.loads(ext_code_block(s))),
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

    def ext_concepts(self, paper: str, pname: str, start: str) -> PaperConcepts:
        """从单篇论文中抽取核心概念/方法/定理/发现，形成概念卡片。"""
        prompt = render_prompt(
            CONCEPT_EXT_PMT,
            paper=paper, pname=pname, start=start,
        )
        return self._json(
            lambda d: PaperConcepts(paper=pname, **d),
            prompt, self.model, self.args,
        )

    # ============================================================
    # 二、论文聚类
    # ============================================================

    def cluster_papers(self, papers: str) -> PaperClusResult:
        prompt = render_prompt(PAPER_CLUSTER_PMT, papers=papers)
        return self._json(lambda d: PaperClusResult(**d), prompt, self.model, self.args)

    def fix_cluster(self, papers: str, parts: str, problem: str) -> PaperClusResult:
        prompt = render_prompt(
            PAPER_CLUSTER_FIX_PMT,
            papers=papers, parts=parts, problem=problem,
        )
        return self._json(lambda d: PaperClusResult(**d), prompt, self.model, self.args)

    # ============================================================
    # 三、全书大纲
    # ============================================================

    def gen_outline(
        self, struct: str, concept_cards: str,
    ) -> OutlineResult:
        prompt = render_prompt(
            OUTLINE_PMT,
            struct=struct, concept_cards=concept_cards, 
        )
        return self._json(
            lambda d: parse_obj_as(List[OutlineChapter], d), 
            prompt, self.model, self.args
        )

    def fix_outline(
        self, outline: str, struct: str, concept_cards: str,
        problem: str,
    ) -> OutlineResult:
        prompt = render_prompt(
            OUTLINE_FIX_PMT,
            outline=outline, struct=struct, concept_cards=concept_cards,
            problem=problem,
        )
        return self._json(
            lambda d: parse_obj_as(
                List[OutlineChapter], d), 
                prompt, self.model, self.args
            )

    # ============================================================
    # 四、章节细纲
    # ============================================================

    def gen_concept_anls_detail(
        self, i: str, outline: str, paper_desc: str,
    ) -> ConceptAnlsResult:
        prompt = render_prompt(
            CONCEPT_ANLS_DETAIL_PMT,
            i=i, outline=outline, paper_desc=paper_desc,
        )
        return self._json(lambda d: ConceptAnlsResult(**d), prompt, self.model, self.args)

    def gen_rest_detail(
        self, i: str, outline: str, detail: str, paper_desc: str,
    ) -> RestDetailResult:
        prompt = render_prompt(
            REST_DETAIL_PMT,
            i=i, outline=outline, detail=detail, paper_desc=paper_desc,
        )
        return self._json(lambda d: RestDetailResult(**d), prompt, self.model, self.args)

    def fix_detail(
        self, i: str, detail: str, outline: str, paper_desc: str, problem: str,
    ) -> ChapterDetail:
        prompt = render_prompt(
            DETAIL_FIX_PMT,
            i=i, detail=detail, outline=outline, paper_desc=paper_desc,
            problem=problem,
        )
        return self._json(lambda d: ChapterDetail(**d), prompt, self.model, self.args)

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

    def gen_glossary(self, papers: str) -> List[GlossaryEntry]:
        prompt = render_prompt(TERM_GLOSSARY_PMT, papers=papers)
        return self._json(lambda d: [GlossaryEntry(**e) for e in d], prompt, self.model, self.args)

    def check_consistency(self, previous_chapters: str, current_chapter: str) -> str:
        prompt = render_prompt(
            CONSISTENCY_CHK_PMT,
            previous_chapters=previous_chapters, current_chapter=current_chapter,
        )
        return self._text(prompt, self.model, self.args)

    def audit_citations(self, book: str, papers: str) -> CitationAudit:
        prompt = render_prompt(CITATION_AUDIT_PMT, book=book, papers=papers)
        return self._json(lambda d: CitationAudit(**d), prompt, self.model, self.args)
# -*- coding: utf-8 -*-
"""
paper2textbook_open_agent.py —— 封装 paper2textbook 的独立 LLM 调用。
每个方法对应 paper2textbook_open_pmt.py 中的一个提示词，返回结构化结果或文本。
"""

import yaml
import json
import shutil
from os import path
import os
from typing import *

from .openai import *
from .paper2textbook_open_models import *
from .paper2textbook_open_pmt import *
from .util import *
from pydantic import parse_obj_as
from datetime import datetime, timezone


SUPPORTED_PAPER_EXTS = {'md', 'markdown', 'tex', 'txt', 'pdf'}
SUPPORTED_SURVEY_EXTS = {'md', 'markdown', 'tex', 'txt'}
FORMAT_LABELS = {'md': 'Markdown', 'tex': 'LaTeX'}


class Paper2TextbookOpenTools(ToolsMixin):
    """封装 paper2textbook 的独立 LLM 调用。"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
        super(ToolsMixin, self).__init__()
        self.args = args
        self.model = args.model
        set_openai_props(args)
        self.pj_dir = (
            path.dirname(args.dir) + '_paper2textbook-open'
            if path.isfile(args.dir) else
            path.abspath(args.dir) + '_paper2textbook-open'
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
            return read_text(fname)
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
        cache_fname = path.join(
            self.pj_dir,
            'outline_' + gen_objs_md5(concept_cards) + '.yaml',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'outline_fix_' + gen_objs_md5(outline, concept_cards, problem) + '.yaml',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'detail_ccpt_' + gen_objs_md5(outline, paper_desc, i) + '.yaml',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'detail_rest_' + gen_objs_md5(outline, detail, paper_desc, i) + '.yaml',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'detail_fix_' + gen_objs_md5(detail, outline, paper_desc, problem) + '.yaml',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'body_' + gen_objs_md5(outline, detail, paper_desc, i) + '.md',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'body_check_' + gen_objs_md5(body, detail) + '.md',
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            r = read_text(cache_fname)
            return r
        prompt = render_prompt(BODY_CHK_PMT, body=body, detail=json_dump_model(detail))
        r = self._text(prompt, self.model, self.args)
        write_text(cache_fname, r)
        return r

    def tool_fix_body(self, body: str, comment: str, paper_desc: List[PaperConcepts]) -> str:
        """根据检查反馈（comment）修正章节正文。"""
        cache_fname = path.join(
            self.pj_dir,
            'body_fix_' + gen_objs_md5(body, comment, paper_desc) + '.md',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'glossary_' + gen_objs_md5(paper) + '.yaml',
        )
        r = read_yaml_model(cache_fname, List[GlossaryEntry])
        if r: return r
        prompt = render_prompt(TERM_GLOSSARY_PMT, paper=paper)
        r = self._json(List[GlossaryEntry], prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_check_consistency(self, previous_chapter: str, current_chapter: str) -> str:
        """检查当前章与上一章之间的术语/口径一致性，并返回问题反馈。"""
        cache_fname = path.join(
            self.pj_dir,
            'consist_check_' + gen_objs_md5(previous_chapter, current_chapter) + '.md',
        )
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
        cache_fname = path.join(
            self.pj_dir,
            'audit_' + gen_objs_md5(chapter, paper) + '.yaml',
        )
        r = read_yaml_model(cache_fname, CitationAudit)
        if r: return r
        prompt = render_prompt(CITATION_AUDIT_PMT, book=chapter, paper=paper)
        r = self._json(CitationAudit, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    # ============================================================
    # 七、Textbook Anything 兼容工作流
    # ============================================================

    def tool_ta_interview(self, subject: str, answers: List[str]) -> TeachingBrief:
        """根据访谈答案生成 TeachingBrief（教学简报）。"""
        cache_fname = path.join(
            self.pj_dir,
            'ta_brief_' + gen_objs_md5(subject, answers) + '.yaml',
        )
        r = read_yaml_model(cache_fname, TeachingBrief)
        if r: return r
        prompt = render_prompt(
            BRIEF_FILL_SYSTEM,
            brief=TeachingBrief(id=f"ta-{gen_objs_md5(subject)[:12]}", subject=subject, scope=subject).model_dump_json(indent=2),
            answers='\n'.join(f"Q{i+1}: {a}" for i, a in enumerate(answers)),
        )
        r = self._json(TeachingBrief, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_ta_research(self, brief: TeachingBrief) -> ResearchPlan:
        """根据 TeachingBrief 追踪依赖并生成研究计划。"""
        cache_fname = path.join(
            self.pj_dir,
            'ta_research_' + gen_objs_md5(brief) + '.yaml',
        )
        r = read_yaml_model(cache_fname, ResearchPlan)
        if r: return r
        prompt = render_prompt(RESEARCH_SYSTEM, brief=brief.model_dump_json(indent=2))
        r = self._json(ResearchPlan, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_ta_design(self, brief: TeachingBrief, research: ResearchPlan) -> TutorialDesign:
        """根据 TeachingBrief 和 ResearchPlan 生成教程设计。"""
        cache_fname = path.join(
            self.pj_dir,
            'ta_design_' + gen_objs_md5(brief, research) + '.yaml',
        )
        r = read_yaml_model(cache_fname, TutorialDesign)
        if r: return r
        prompt = render_prompt(
            DESIGN_SYSTEM,
            brief=brief.model_dump_json(indent=2),
            research=research.model_dump_json(indent=2),
        )
        r = self._json(TutorialDesign, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_ta_round(self, brief: TeachingBrief, design: TutorialDesign, round_num: int, total_rounds: int,
                      previous_rounds: List[RoundRecord] = None) -> RoundRecord:
        """执行一轮审查，生成 RoundRecord。"""
        tier_label = brief.tier.label_zh
        focus = ROUND_FOCUS_MAP.get(round_num, f"第 {round_num} 轮审查")
        cache_fname = path.join(
            self.pj_dir,
            f'ta_round_{round_num:02d}_' + gen_objs_md5(brief, design, round_num, previous_rounds or []) + '.yaml',
        )
        r = read_yaml_model(cache_fname, RoundRecord)
        if r: return r
        prompt = render_prompt(
            ROUND_SYSTEM,
            round_num=str(round_num),
            total_rounds=str(total_rounds),
            tier=tier_label,
            focus=focus,
            brief=brief.model_dump_json(indent=2),
            design=design.model_dump_json(indent=2),
            previous_rounds=json.dumps([rr.model_dump() for rr in (previous_rounds or [])], ensure_ascii=False, indent=2),
        )
        r = self._json(RoundRecord, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_ta_deliver(self, brief: TeachingBrief, design: TutorialDesign, rounds: List[RoundRecord],
                        fmt: str = "both") -> TutorialDocument:
        """根据所有产物生成最终交付文档。"""
        cache_fname = path.join(
            self.pj_dir,
            'ta_deliver_' + gen_objs_md5(brief, design, rounds, fmt) + '.yaml',
        )
        r = read_yaml_model(cache_fname, TutorialDocument)
        if r: return r
        # TutorialDocument 生成逻辑：组装所有产物
        doc = TutorialDocument(
            title=brief.subject,
            subtitle=f"{brief.tier.label_zh}教程",
            language=brief.language,
            brief=brief,
            research=ResearchPlan(dependency_map=[], sources=[]),
            design=design,
            sections=[],
            review_records=rounds,
            delivery_notes=[f"格式: {fmt}", f"生成时间: {datetime.now(timezone.utc).isoformat()}"],
        )
        write_yaml_model(cache_fname, doc)
        return doc



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



    # ============================================================
    # 八、article2book 兼容工作流：内容资产重组
    # ============================================================

    # @cache
    def tool_build_article_inventory(self, source: str) -> ArticleInventory:
        """扫描素材目录，建立素材清单与预处理状态索引（脚本辅助）。"""
        cache_fname = path.join(
            self.pj_dir,
            'inv_' + gen_objs_md5(source) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ArticleInventory)
        if r: return r
        if path.isfile(source):
            files = [source.replace('\\', '/')]
        else:
            files = [
                path.join(root, f).replace('\\', '/')
                for root, _, fnames in os.walk(source)
                for f in sorted(fnames)
            ]
        prompt = render_prompt(
            ARTICLE_INVENTORY_PMT,
            file_list='\n'.join(files),
        )
        r: ArticleInventory = self._json(
            ArticleInventory, prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_read_article(self, fname: str) -> str:
        """读取素材全文：文本格式直接读，PDF 通过 PyMuPDF 抽取文本。"""
        ext = extname(fname).lower()
        if ext in {'md', 'markdown', 'mdx', 'tex', 'txt', 'srt', 'vtt'}:
            return read_text(fname)
        if ext == 'pdf':
            try:
                import fitz
            except ImportError as ex:
                raise ValueError('读取 PDF 需要安装 PyMuPDF') from ex
            with fitz.open(fname) as doc:
                return '\n\n'.join(page.get_text() for page in doc)
        if ext == 'docx':
            try:
                import docx
            except ImportError as ex:
                raise ValueError('读取 DOCX 需要安装 python-docx') from ex
            doc = docx.Document(fname)
            return '\n\n'.join(p.text for p in doc.paragraphs)
        raise ValueError(f'不支持的素材格式：{fname}')

    def tool_read_articles_batch(self, file_paths: List[str], batch_no: int = 1) -> List[ArticleReadingNote]:
        """分批通读一批素材并形成结构化通读笔记（Agent 通读协议）。"""
        cache_fname = path.join(
            self.pj_dir,
            f'read_batch_{batch_no:02d}_' + gen_objs_md5(file_paths, batch_no) + '.yaml'
        )
        r = read_yaml_model(cache_fname, List[ArticleReadingNote])
        if r: return r
        articles = '\n\n'.join(
            f"=== {f} ===\n{self.tool_read_article(f)}"
            for f in file_paths
        )
        prompt = render_prompt(
            ARTICLE_READING_NOTE_PMT,
            articles=articles,
        )
        r: List[ArticleReadingNote] = self._json(
            List[ArticleReadingNote], prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_screen_articles(self, reading_notes: List[ArticleReadingNote]) -> ContentScreeningResult:
        """基于通读笔记做"保留 / 降权 / 排除"三分类筛选。"""
        cache_fname = path.join(
            self.pj_dir,
            'screen_' + gen_objs_md5(reading_notes) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ContentScreeningResult)
        if r: return r
        prompt = render_prompt(
            CONTENT_SCREENING_PMT,
            reading_notes=json_dump_model(reading_notes),
        )
        r: ContentScreeningResult = self._json(
            ContentScreeningResult, prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_judge_content_shape(self, screening: ContentScreeningResult) -> ContentShapeJudgment:
        """判断这批素材最适合转化为何种内容产品（书/小册子/课程/系列/手册/知识库）。"""
        cache_fname = path.join(
            self.pj_dir,
            'shape_' + gen_objs_md5(screening) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ContentShapeJudgment)
        if r: return r
        prompt = render_prompt(
            CONTENT_SHAPE_JUDGMENT_PMT,
            screening=json_dump_model(screening),
        )
        r: ContentShapeJudgment = self._json(
            ContentShapeJudgment, prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_assess_book_viability(self, screening: ContentScreeningResult) -> BookViabilityAssessment:
        """按 7 个维度评估成书可行性，并给出替代形态建议。"""
        cache_fname = path.join(
            self.pj_dir,
            'viability_' + gen_objs_md5(screening) + '.yaml'
        )
        r = read_yaml_model(cache_fname, BookViabilityAssessment)
        if r: return r
        prompt = render_prompt(
            BOOK_VIABILITY_PMT,
            screening=json_dump_model(screening),
        )
        r: BookViabilityAssessment = self._json(
            BookViabilityAssessment, prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_gen_planning_opinion(
        self,
        screening: ContentScreeningResult,
        shape: ContentShapeJudgment,
        viability: BookViabilityAssessment,
    ) -> PlanningOpinion:
        """综合形态判断与可行性评估，生成集中的书稿策划意见。"""
        cache_fname = path.join(
            self.pj_dir,
            'opinion_' + gen_objs_md5(screening, shape, viability) + '.yaml'
        )
        r = read_yaml_model(cache_fname, PlanningOpinion)
        if r: return r
        prompt = render_prompt(
            PLANNING_OPINION_PMT,
            screening=json_dump_model(screening),
            shape_judgment=json_dump_model(shape),
            viability=json_dump_model(viability),
        )
        r: PlanningOpinion = self._json(
            PlanningOpinion, prompt, self.model, self.args,
        )
        write_yaml_model(cache_fname, r)
        return r

    def tool_write_planning_opinion_md(self, opinion: PlanningOpinion) -> str:
        """把 PlanningOpinion 渲染为 `书稿策划意见.md` 的 Markdown 文本。"""
        cache_fname = path.join(self.pj_dir, '书稿策划意见.md')
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        md = _render_planning_opinion_md(opinion)
        write_text(cache_fname, md)
        return md

    # ============================================================
    # 九、paper-to-course 兼容工作流：论文 → HTML 课程 + Markdown + PPTX
    # ============================================================
    def tool_course_verify_paper(self, paper: str) -> CoursePaperInfo:
        """校验论文主题：提取标题/作者/摘要/关键词/领域（Step 0）。"""
        cache_fname = path.join(
            self.pj_dir,
            'course_verify_' + gen_objs_md5(paper) + '.yaml'
        )
        r = read_yaml_model(cache_fname, CoursePaperInfo)
        if r: return r
        prompt = render_prompt(COURSE_VERIFY_PMT, paper=paper)
        r = self._json(CoursePaperInfo, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_course_plan_structure(self, paper: str, info: CoursePaperInfo) -> CoursePlan:
        """规划 6 模块的课程目录结构（Step 2）。"""
        cache_fname = path.join(
            self.pj_dir,
            'course_plan_' + gen_objs_md5(paper) + '.yaml'
        )
        r = read_yaml_model(cache_fname, CoursePlan)
        if r: return r
        prompt = render_prompt(
            COURSE_PLAN_PMT,
            paper_details=json_dump_model(info),
        )
        r = self._json(CoursePlan, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_course_gen_module(self, module: CourseModuleSpec, paper: str) -> CourseModule:
        """生成单个 HTML 课程模块的内容（Step 3）。"""
        cache_fname = path.join(
            self.pj_dir,
            'course_mod_' + gen_objs_md5(module, paper) + '.yaml'
        )
        r = read_yaml_model(cache_fname, CourseModule)
        if r: return r
        prompt = render_prompt(
            COURSE_MODULE_PMT,
            module_json=json_dump_model(module),
            paper=paper,
        )
        r = self._json(CourseModule, prompt, self.model, self.args)
        # 以规划为准回填，防止模型串改 id/slug/title
        r.id, r.slug, r.title = module.id, module.slug, module.title
        write_yaml_model(cache_fname, r)
        return r

    def tool_course_gen_slides(self, course: CoursePlan, paper: str) -> SlidesConfig:
        """生成约 16 页 PPTX 幻灯片配置（Step 4）。"""
        cache_fname = path.join(
            self.pj_dir,
            'course_slides_' + gen_objs_md5(course, paper) + '.yaml'
        )
        r = read_yaml_model(cache_fname, SlidesConfig)
        if r: return r
        prompt = render_prompt(
            COURSE_SLIDES_PMT,
            course_desc=json_dump_model(course),
            paper_key_points=paper[:4000],
        )
        r = self._json(SlidesConfig, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_course_render_bundle(self, course: CoursePlan, modules: List[CourseModule], slides: SlidesConfig) -> CourseBundle:
        """渲染课程交付包：index.html + README.md + slides-config.json + build.sh，并写入工作区（Step 5）。"""
        bundle = _render_course_bundle(course, modules, slides)
        # 写入工作区 course_name/ 子目录
        out = path.join(self.pj_dir, course.course_name)
        os.makedirs(out, exist_ok=True)
        write_text(path.join(out, 'index.html'), bundle.index_html)
        write_text(path.join(out, 'README.md'), bundle.readme_md)
        write_text(path.join(out, 'slides-config.json'), bundle.slides_config_json)
        write_text(path.join(out, 'build.sh'), bundle.build_sh)
        return bundle

    # ============================================================
    # 十、report-to-lecture 兼容工作流：文章/研报/论文/白皮书 → 高保真讲义
    # ============================================================
    def tool_lecture_split_structure(self, text: str) -> LectureDocStructure:
        """拆解文档结构：章节、图表、关键结论段（Step 1）。"""
        cache_fname = path.join(
            self.pj_dir,
            'lect_structure_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, LectureDocStructure)
        if r: return r
        prompt = render_prompt(LECTURE_SPLIT_PMT, text=text)
        r = self._json(LectureDocStructure, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_lecture_build_ledger(self, text: str) -> CoverageLedger:
        """建立信息点覆盖率账本（Coverage Ledger，Step 2）。"""
        cache_fname = path.join(
            self.pj_dir,
            'lect_ledger_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, CoverageLedger)
        if r: return r
        prompt = render_prompt(LECTURE_LEDGER_PMT, text=text)
        r = self._json(CoverageLedger, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_lecture_build_claim_map(self, text: str) -> ClaimEvidenceMap:
        """建立 Claim-Evidence 映射账本（Step 2）。"""
        cache_fname = path.join(
            self.pj_dir,
            'lect_claims_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ClaimEvidenceMap)
        if r: return r
        prompt = render_prompt(LECTURE_CLAIM_MAP_PMT, text=text)
        r = self._json(ClaimEvidenceMap, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_lecture_gen_lecture(
        self, text: str, structure: LectureDocStructure,
        ledger: CoverageLedger, claim_map: ClaimEvidenceMap = None,
    ) -> str:
        """按教学顺序生成高保真讲义主体（Step 3-4，返回 Markdown 文本）。"""
        cache_fname = path.join(
            self.pj_dir, 'lect_body_' + gen_objs_md5(text) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        prompt = render_prompt(
            LECTURE_GEN_PMT,
            ledger_json=json_dump_model(ledger),
            structure_json=json_dump_model(structure),
            text=text,
            LECTURE_GUARDRAILS=LECTURE_GUARDRAILS,
            LECTURE_OUTLINE_TEMPLATE=LECTURE_OUTLINE_TEMPLATE,
        )
        body = self._text(prompt, self.model, self.args)
        write_text(cache_fname, body)
        return body

    def tool_lecture_check_coverage(
        self, text: str, ledger: CoverageLedger, lecture: str,
    ) -> LectureCoverageReport:
        """检查讲义长度比例与信息点覆盖率，判定是否通过护栏（Step 5）。"""
        cache_fname = path.join(
            self.pj_dir,
            'lect_check_' + gen_objs_md5(text, ledger, lecture) + '.yaml'
        )
        r = read_yaml_model(cache_fname, LectureCoverageReport)
        if r: return r
        prompt = render_prompt(
            LECTURE_CHECK_PMT,
            text=text,
            ledger_json=json_dump_model(ledger),
            lecture=lecture,
            min_length_ratio=0.8,
            min_coverage_ratio=0.8,
        )
        r = self._json(LectureCoverageReport, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_lecture_patch_lecture(self, lecture: str, report: LectureCoverageReport) -> str:
        """根据覆盖率报告补全讲义缺失点，返回修订后的完整讲义（Step 5）。"""
        missing_detail = '\n'.join(
            f"- {p}" for p in (report.missing_points or report.suggestions)
        ) or '（请按检查报告建议，优先补全：关键结论 > 关键证据 > 风险与边界 > 方法或行动建议）'
        cache_fname = path.join(
            self.pj_dir,
            'lect_patch_' + gen_objs_md5(lecture, missing_detail) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        prompt = render_prompt(
            LECTURE_PATCH_PMT,
            missing_detail=missing_detail,
            lecture=lecture,
        )
        body = self._text(prompt, self.model, self.args)
        write_text(cache_fname, body)
        return body

    # ============================================================
    # 十一、teach-from-paper 兼容工作流：论文 → 教学包
    # ============================================================
    def tool_teach_audience(self, text: str) -> TeachingAudience:
        """读取论文并输出 Pre-Flight 报告：标题/论点/受众级别/课时/前置知识（Phase 0）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_aud_' + gen_objs_md5(text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, TeachingAudience)
        if r: return r
        prompt = render_prompt(TEACH_AUDIENCE_PMT, text=text)
        r = self._json(TeachingAudience, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_teach_extract_results(self, text: str, audience: TeachingAudience) -> TeachingResults:
        """提取值得讲授的 3-5 个结果：陈述/直觉/失效模式/方法-结论辨析（Phase 1）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_results_' + gen_objs_md5(text, audience) + '.yaml'
        )
        r = read_yaml_model(cache_fname, TeachingResults)
        if r: return r
        prompt = render_prompt(
            TEACH_RESULTS_PMT,
            audience_json=json_dump_model(audience),
            text=text,
        )
        r = self._json(TeachingResults, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_teach_build_outline(self, audience: TeachingAudience, results: TeachingResults) -> TeachingOutline:
        """生成讲义主线（动机→设定→核心结果→方法→结论）与幻灯片骨架（Phase 2）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_outline_' + gen_objs_md5(audience, results) + '.yaml'
        )
        r = read_yaml_model(cache_fname, TeachingOutline)
        if r: return r
        prompt = render_prompt(
            TEACH_OUTLINE_PMT,
            audience_json=json_dump_model(audience),
            results_json=json_dump_model(results),
        )
        r = self._json(TeachingOutline, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_teach_discussion_questions(self, audience: TeachingAudience, results: TeachingResults) -> TeachingQuestions:
        """写 4-6 道分级讨论题（comprehension→application→critique）（Phase 3a）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_questions_' + gen_objs_md5(audience, results) + '.yaml'
        )
        r = read_yaml_model(cache_fname, TeachingQuestions)
        if r: return r
        prompt = render_prompt(
            TEACH_QUESTIONS_PMT,
            audience_json=json_dump_model(audience),
            results_json=json_dump_model(results),
        )
        r = self._json(TeachingQuestions, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_teach_exercise_brief(self, audience: TeachingAudience, results: TeachingResults) -> TeachingExercises:
        """写 2-4 个习题简介（题干/技能/答案形态，非完整解答）（Phase 3b）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_exercises_' + gen_objs_md5(audience, results) + '.yaml'
        )
        r = read_yaml_model(cache_fname, TeachingExercises)
        if r: return r
        prompt = render_prompt(
            TEACH_EXERCISES_PMT,
            audience_json=json_dump_model(audience),
            results_json=json_dump_model(results),
        )
        r = self._json(TeachingExercises, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_teach_render_package(
        self, audience: TeachingAudience, results: TeachingResults,
        outline: TeachingOutline, questions: TeachingQuestions,
        exercises: TeachingExercises,
    ) -> str:
        """把教学包渲染为 Markdown 报告（确定性渲染，写入工作区并返回文本）。"""
        cache_fname = path.join(
            self.pj_dir,
            'teach_package_' + gen_objs_md5(audience, results, outline, questions, exercises) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        md = _render_teaching_package(
            audience, results, outline, questions, exercises,
        )
        write_text(cache_fname, md)
        return md

    # ============================================================
    # 十二、kougi-forge 兼容工作流：需求分析 → 蓝图 → 样章 → 逐章 → 组装
    # ============================================================
    def tool_kougi_parse_input(self, input_text: str) -> KougiRequirements:
        """解析教材需求，识别主题/受众/课时/格式，必要时追问澄清（Phase 1）。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_reqs_' + gen_objs_md5(input_text) + '.yaml'
        )
        r = read_yaml_model(cache_fname, KougiRequirements)
        if r: return r
        prompt = render_prompt(KOUGI_PARSE_INPUT_PMT, input=input_text)
        r = self._json(KougiRequirements, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_gen_blueprint(self, project_def: str) -> KougiBlueprints:
        """生成 2-3 个蓝图方案并合并为优选方案（Phase 2）。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_bp_' + gen_objs_md5(project_def) + '.yaml'
        )
        r = read_yaml_model(cache_fname, KougiBlueprints)
        if r: return r
        prompt = render_prompt(KOUGI_BLUEPRINT_PMT, project_def=project_def)
        r = self._json(KougiBlueprints, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_write_sample(self, project_def: str, blueprint: KougiBlueprint,
                                sample_title: str) -> KougiSampleChapter:
        """编写样章：多个草稿变体综合为样章候选人（Phase 3）。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_sample_' + gen_objs_md5(project_def, blueprint, sample_title) + '.yaml'
        )
        r = read_yaml_model(cache_fname, KougiSampleChapter)
        if r: return r
        prompt = render_prompt(
            KOUGI_SAMPLE_PMT,
            project_def=project_def,
            blueprint_json=json_dump_model(blueprint),
            sample_title=sample_title,
        )
        r = self._json(KougiSampleChapter, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_write_chapter(self, project_def: str, blueprint: KougiBlueprint,
                                 sample: KougiSampleChapter, chapter_title: str,
                                 index: int) -> KougiChapter:
        """按样章风格生成单章（含概念解析/学习目标/类比/正文/小结/习题）（Phase 4）。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_ch_' + gen_objs_md5(project_def, blueprint, sample, chapter_title, index) + '.yaml'
        )
        r = read_yaml_model(cache_fname, KougiChapter)
        if r: return r
        prompt = render_prompt(
            KOUGI_CHAPTER_PMT,
            project_def=project_def,
            blueprint_json=json_dump_model(blueprint),
            sample_json=json_dump_model(sample),
            chapter_title=chapter_title,
        )
        r = self._json(KougiChapter, prompt, self.model, self.args)
        r.index = index
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_generate_exercises(self, chapter: KougiChapter) -> Dict[str, List[str]]:
        """为章节生成练习题与参考答案。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_ex_' + gen_objs_md5(chapter) + '.yaml'
        )
        r = read_yaml_model(cache_fname, dict)
        if r: return r
        prompt = render_prompt(KOUGI_EXERCISES_PMT, chapter_content=chapter.content)
        r = self._json(Dict[str, List[str]], prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        if r.get('exercises'):
            chapter.exercises = r['exercises']
        return r

    def tool_kougi_quality_gate(self, project_def: str, chapter: KougiChapter) -> Dict[str, Any]:
        """对章节做审校质量门禁，输出评分与是否通过。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_q_' + gen_objs_md5(project_def, chapter) + '.yaml'
        )
        r = read_yaml_model(cache_fname, dict)
        if r: return r
        prompt = render_prompt(
            KOUGI_QUALITY_PMT,
            project_def=project_def,
            chapter_draft=chapter.content,
        )
        r = self._json(Dict[str, Any], prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_assemble_book(self, project_def: str, blueprint: KougiBlueprint,
                                 chapters: List[KougiChapter]) -> KougiBookAssembly:
        """组装全书 Markdown + 术语表 + 练习题汇总，执行一致性检查（Phase 5）。"""
        cache_fname = path.join(
            self.pj_dir,
            'kougi_book_' + gen_objs_md5(project_def, blueprint, chapters) + '.yaml'
        )
        r = read_yaml_model(cache_fname, KougiBookAssembly)
        if r: return r
        prompt = render_prompt(
            KOUGI_ASSEMBLY_PMT,
            project_def=project_def,
            blueprint_json=json_dump_model(blueprint),
            chapters_json=json_dump_model(chapters),
        )
        r = self._json(KougiBookAssembly, prompt, self.model, self.args)
        write_yaml_model(cache_fname, r)
        return r

    def tool_kougi_render_book(self, assembly: KougiBookAssembly) -> str:
        """把全书组装结果写入工作区文件，返回 Markdown（确定性渲染）。"""
        cache_fname = path.join(self.pj_dir, '教材.md')
        write_text(cache_fname, assembly.full_markdown)
        if assembly.glossary:
            write_text(path.join(self.pj_dir, 'glossary.md'), assembly.glossary)
        if assembly.exercises_collection:
            write_text(path.join(self.pj_dir, 'exercises.md'), assembly.exercises_collection)
        return assembly.full_markdown

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

        # ── 七、Textbook Anything 兼容工作流 ──────────────────
        "tool_ta_interview": params_schema(
            required=['subject', 'answers'],
            subject=base_schema('string', '教学主题/范围'),
            answers=str_list_schema('访谈答案列表'),
        ),
        "tool_ta_research": params_schema(
            required=['brief'],
            brief=model_schema(TeachingBrief, '教学简报（TeachingBrief）'),
        ),
        "tool_ta_design": params_schema(
            required=['brief', 'research'],
            brief=model_schema(TeachingBrief, '教学简报（TeachingBrief）'),
            research=model_schema(ResearchPlan, '研究计划（ResearchPlan）'),
        ),
        "tool_ta_round": params_schema(
            required=['brief', 'design', 'round_num', 'total_rounds'],
            brief=model_schema(TeachingBrief, '教学简报（TeachingBrief）'),
            design=model_schema(TutorialDesign, '教程设计（TutorialDesign）'),
            round_num=base_schema('integer', '审查轮次序号'),
            total_rounds=base_schema('integer', '总审查轮数'),
            previous_rounds=model_list_schema(RoundRecord, '前轮审查记录列表'),
        ),
        "tool_ta_deliver": params_schema(
            required=['brief', 'design', 'rounds'],
            brief=model_schema(TeachingBrief, '教学简报（TeachingBrief）'),
            design=model_schema(TutorialDesign, '教程设计（TutorialDesign）'),
            rounds=model_list_schema(RoundRecord, '审查轮次记录列表'),
            fmt=base_schema('string', '输出格式（html/pdf/both）'),
        ),

        # ── 八、article2book 兼容工作流 ──────────────────────
        "tool_build_article_inventory": params_schema(
            required=['source'],
            source=base_schema('string', '素材目录或文件路径'),
        ),
        "tool_read_article": params_schema(
            required=['fname'],
            fname=base_schema('string', '素材文件路径'),
        ),
        "tool_read_articles_batch": params_schema(
            required=['file_paths', 'batch_no'],
            file_paths=str_list_schema('素材文件路径列表'),
            batch_no=base_schema('integer', '批次序号'),
        ),
        "tool_screen_articles": params_schema(
            required=['reading_notes'],
            reading_notes=model_list_schema(ArticleReadingNote, '通读笔记列表（ArticleReadingNote）'),
        ),
        "tool_judge_content_shape": params_schema(
            required=['screening'],
            screening=model_schema(ContentScreeningResult, '内容筛选结果（ContentScreeningResult）'),
        ),
        "tool_assess_book_viability": params_schema(
            required=['screening'],
            screening=model_schema(ContentScreeningResult, '内容筛选结果（ContentScreeningResult）'),
        ),
        "tool_gen_planning_opinion": params_schema(
            required=['screening', 'shape', 'viability'],
            screening=model_schema(ContentScreeningResult, '内容筛选结果（ContentScreeningResult）'),
            shape=model_schema(ContentShapeJudgment, '内容形态判断（ContentShapeJudgment）'),
            viability=model_schema(BookViabilityAssessment, '成书可行性评估（BookViabilityAssessment）'),
        ),
        "tool_write_planning_opinion_md": params_schema(
            required=['opinion'],
            opinion=model_schema(PlanningOpinion, '书稿策划意见（PlanningOpinion）'),
        ),

        # ── 九、paper-to-course 兼容工作流 ──────────────────────
        "tool_course_verify_paper": params_schema(
            required=['paper'],
            paper=base_schema('string', '论文全文文本'),
        ),
        "tool_course_plan_structure": params_schema(
            required=['paper', 'info'],
            paper=base_schema('string', '论文全文文本'),
            info=model_schema(CoursePaperInfo, '论文主题验证结果（CoursePaperInfo）'),
        ),
        "tool_course_gen_module": params_schema(
            required=['module', 'paper'],
            module=model_schema(CourseModuleSpec, '模块规划（CourseModuleSpec）'),
            paper=base_schema('string', '论文全文文本'),
        ),
        "tool_course_gen_slides": params_schema(
            required=['course', 'paper'],
            course=model_schema(CoursePlan, '课程结构规划（CoursePlan）'),
            paper=base_schema('string', '论文全文文本'),
        ),
        "tool_course_render_bundle": params_schema(
            required=['course', 'modules', 'slides'],
            course=model_schema(CoursePlan, '课程结构规划（CoursePlan）'),
            modules=model_list_schema(CourseModule, '已生成的 6 个 HTML 模块（CourseModule）'),
            slides=model_schema(SlidesConfig, 'PPTX 幻灯片配置（SlidesConfig）'),
        ),

        # ── 十、report-to-lecture 兼容工作流 ──────────────────────
        "tool_lecture_split_structure": params_schema(
            required=['text'],
            text=base_schema('string', '原文全文文本'),
        ),
        "tool_lecture_build_ledger": params_schema(
            required=['text'],
            text=base_schema('string', '原文全文文本'),
        ),
        "tool_lecture_build_claim_map": params_schema(
            required=['text'],
            text=base_schema('string', '原文全文文本'),
        ),
        "tool_lecture_gen_lecture": params_schema(
            required=['text', 'structure', 'ledger'],
            text=base_schema('string', '原文全文文本'),
            structure=model_schema(LectureDocStructure, '文档结构（LectureDocStructure）'),
            ledger=model_schema(CoverageLedger, '信息点覆盖率账本（CoverageLedger）'),
            claim_map=model_schema(ClaimEvidenceMap, 'Claim-Evidence 映射（ClaimEvidenceMap），可选'),
        ),
        "tool_lecture_check_coverage": params_schema(
            required=['text', 'ledger', 'lecture'],
            text=base_schema('string', '原文全文文本'),
            ledger=model_schema(CoverageLedger, '信息点覆盖率账本（CoverageLedger）'),
            lecture=base_schema('string', '已生成的讲义主体（Markdown）'),
        ),
        "tool_lecture_patch_lecture": params_schema(
            required=['lecture', 'report'],
            lecture=base_schema('string', '原讲义主体（Markdown）'),
            report=model_schema(LectureCoverageReport, '覆盖率检查报告（LectureCoverageReport）'),
        ),

        # ── 十一、teach-from-paper 兼容工作流 ──────────────────────
        "tool_teach_audience": params_schema(
            required=['text'],
            text=base_schema('string', '论文全文文本'),
        ),
        "tool_teach_extract_results": params_schema(
            required=['text', 'audience'],
            text=base_schema('string', '论文全文文本'),
            audience=model_schema(TeachingAudience, '受众设定（TeachingAudience）'),
        ),
        "tool_teach_build_outline": params_schema(
            required=['audience', 'results'],
            audience=model_schema(TeachingAudience, '受众设定（TeachingAudience）'),
            results=model_schema(TeachingResults, '值得讲授的结果（TeachingResults）'),
        ),
        "tool_teach_discussion_questions": params_schema(
            required=['audience', 'results'],
            audience=model_schema(TeachingAudience, '受众设定（TeachingAudience）'),
            results=model_schema(TeachingResults, '值得讲授的结果（TeachingResults）'),
        ),
        "tool_teach_exercise_brief": params_schema(
            required=['audience', 'results'],
            audience=model_schema(TeachingAudience, '受众设定（TeachingAudience）'),
            results=model_schema(TeachingResults, '值得讲授的结果（TeachingResults）'),
        ),
        "tool_teach_render_package": params_schema(
            required=['audience', 'results', 'outline', 'questions'],
            audience=model_schema(TeachingAudience, '受众设定（TeachingAudience）'),
            results=model_schema(TeachingResults, '值得讲授的结果（TeachingResults）'),
            outline=model_schema(TeachingOutline, '讲义主线与幻灯片骨架（TeachingOutline）'),
            questions=model_schema(TeachingQuestions, '讨论题（TeachingQuestions）'),
            exercises=model_schema(TeachingExercises, '习题简介（TeachingExercises），可选'),
        ),

        # ── 十二、kougi-forge 兼容工作流 ──────────────────────
        "tool_kougi_parse_input": params_schema(
            required=['input_text'],
            input_text=base_schema('string', '用户输入的教材需求'),
        ),
        "tool_kougi_gen_blueprint": params_schema(
            required=['project_def'],
            project_def=base_schema('string', '项目定义（主题/受众/课时/格式）'),
        ),
        "tool_kougi_write_sample": params_schema(
            required=['project_def', 'blueprint', 'sample_title'],
            project_def=base_schema('string', '项目定义'),
            blueprint=model_schema(KougiBlueprint, '全套蓝图（KougiBlueprint）'),
            sample_title=base_schema('string', '样章章节标题'),
        ),
        "tool_kougi_write_chapter": params_schema(
            required=['project_def', 'blueprint', 'sample', 'chapter_title', 'index'],
            project_def=base_schema('string', '项目定义'),
            blueprint=model_schema(KougiBlueprint, '全套蓝图（KougiBlueprint）'),
            sample=model_schema(KougiSampleChapter, '样章参考（KougiSampleChapter）'),
            chapter_title=base_schema('string', '本章章节标题'),
            index=base_schema('integer', '章节序号'),
        ),
        "tool_kougi_generate_exercises": params_schema(
            required=['chapter'],
            chapter=model_schema(KougiChapter, '已完成章节（KougiChapter）'),
        ),
        "tool_kougi_quality_gate": params_schema(
            required=['project_def', 'chapter'],
            project_def=base_schema('string', '项目定义'),
            chapter=model_schema(KougiChapter, '章节草稿（KougiChapter）'),
        ),
        "tool_kougi_assemble_book": params_schema(
            required=['project_def', 'blueprint', 'chapters'],
            project_def=base_schema('string', '项目定义'),
            blueprint=model_schema(KougiBlueprint, '全套蓝图（KougiBlueprint）'),
            chapters=model_list_schema(KougiChapter, '已完成全部章节（KougiChapter）'),
        ),
        "tool_kougi_render_book": params_schema(
            required=['assembly'],
            assembly=model_schema(KougiBookAssembly, '全书组装结果（KougiBookAssembly）'),
        ),
    }


# ============================================================
# article2book 兼容：PlanningOpinion -> Markdown 渲染
# ============================================================

_SHAPE_LABELS = {
    "book": "成书",
    "booklet": "小册子",
    "course": "课程",
    "series": "系列文章",
    "handbook": "实务手册",
    "kb": "知识库",
    "pool": "暂不建议产品化",
}

_WORTHI_LABELS = {
    "worth": "值得",
    "potential": "有潜力但需收束",
    "not_recommended": "暂不建议",
}

_CONCLUSION_LABELS = {
    "ready": "可以直接推进",
    "needs_rewrite": "可以成书但需重写",
    "not_recommended": "暂不建议成书",
}


def _render_planning_opinion_md(opinion: PlanningOpinion) -> str:
    """把 PlanningOpinion 结构化对象渲染为 `书稿策划意见.md` 的 Markdown 文本。"""
    o = opinion
    best_shape = _SHAPE_LABELS.get(o.best_shape, o.best_shape)
    book_worth = _WORTHI_LABELS.get(o.book_worthiness, o.book_worthiness)
    conclusion = _CONCLUSION_LABELS.get(o.conclusion_type, o.conclusion_type)

    def _list(items: List[str]) -> str:
        return '\n'.join(f"- {i}" for i in items) if items else "- 未提及"

    def _blank(items: List[str]) -> str:
        return '\n'.join(items) if items else "- 未提及"

    md = f"""# 书稿策划意见

## 一、结论

- **最佳内容形态**：{best_shape}
- **是否值得成书**：{book_worth}
- **结论类型**：{conclusion}
- **一句话总判断**：{o.one_line_judgment}

## 二、这批素材真正适合做成什么

- **推荐主形态**：{o.recommended_shape}
- **推荐理由**：{o.shape_reason}
- **不建议走的形态**：{o.not_recommended_shapes or ['未提及']}
- **不建议理由**：
{_list(o.not_recommended_reasons)}
- **如果一定要成书，需要先补足什么**：{o.if_force_book_need or '未提及'}

## 三、主命题、目标读者与定位

- **推荐主命题**：{o.core_proposition}
- **目标读者**：{o.target_reader}
- **读者最想解决的问题**：{o.reader_problem}
- **这份内容产品与常见同类内容的差异**：{o.differentiation}

## 四、推荐标题或产品名方向

- **推荐名称**：{o.recommended_title}
- **副标题**：{o.subtitle or '未提及'}
- **备选 1**：{o.alt_title_1 or '未提及'}
- **备选 2**：{o.alt_title_2 or '未提及'}

## 五、推荐结构草案

### 形态说明
- **推荐产物**：{o.shape_description}
- **结构逻辑**：{o.structure_logic}

### 目录 / 单元 / 栏目草案
{_list(o.toc_draft)}

## 六、最重要的删改动作

- **建议保留**：
{_list(o.to_retain)}
- **建议删除**：
{_list(o.to_delete)}
- **建议合并重写**：
{_list(o.to_merge_rewrite)}
- **建议补写**：
{_list(o.to_supplement)}
- **保留 / 合并 / 排除原则**：{o.retain_principles}

## 七、转化路径

- **第一步**：{o.steps[0] if o.steps else '未提及'}
- **第二步**：{o.steps[1] if len(o.steps) > 1 else '未提及'}
- **第三步**：{o.steps[2] if len(o.steps) > 2 else '未提及'}
- **风险点**：
{_list(o.risks)}

## 八、如果确认推进，第二阶段将怎么写

- **下一步产物**：{o.next_product}
- **默认输出文件**：{o.default_output_file}
- **写作方式**：{o.writing_approach}
- **是否拆分**：{'是' if o.will_split else '否'}
- **预计先从哪几章 / 单元 / 条目起草**：
{_list(o.start_from)}
"""
    return md



# ============================================================
# paper-to-course 兼容：课程交付包渲染（确定性、脚本级）
# ============================================================

# 内联设计系统样式（暖色调"开发者笔记本"美学）
_COURSE_DESIGN_CSS = """
    :root{
      --paper:#FAF7F2; --ink:#2E2B27; --accent:#D94F30;
      --muted:#8A8378; --card:#FFFFFF; --line:#E8E2D6;
    }
    *{box-sizing:border-box}
    body{margin:0;background:var(--paper);color:var(--ink);
      font-family:'DM Sans',-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
      line-height:1.7}
    header.course{background:var(--ink);color:#fff;padding:3rem 2rem}
    header.course h1{margin:0;font-size:2rem;color:#fff}
    header.course .sub{color:#c9c2b8;margin-top:.5rem}
    main{max-width:960px;margin:0 auto;padding:2rem 1.5rem 4rem}
    nav.course{position:sticky;top:0;background:var(--paper);border-bottom:1px solid var(--line);
      display:flex;gap:1rem;flex-wrap:wrap;padding:.6rem 1.5rem}
    nav.course a{color:var(--accent);text-decoration:none;font-weight:600}
    section.module{padding:2.5rem 0;border-bottom:1px solid var(--line)}
    section.module h2{color:var(--accent);font-size:1.5rem;margin-top:0}
    .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.2rem;margin:1rem 0}
    .tag{display:inline-block;background:var(--accent);color:#fff;border-radius:4px;padding:.1rem .5rem;font-size:.75rem}
    .term{border-bottom:1px dashed var(--accent);cursor:help}
    table.comparison-table,.comparison-table{border-collapse:collapse;width:100%;background:var(--card)}
    .comparison-table th,.comparison-table td{border:1px solid var(--line);padding:.5rem .8rem;text-align:left}
    .comparison-table th{background:var(--ink);color:#fff}
    .formula-block{background:var(--ink);color:#eef;border-radius:8px;padding:1rem;font-family:'JetBrains Mono',monospace;overflow-x:auto}
    .timeline-container{border-left:2px solid var(--accent);padding-left:1rem}
    .timeline-container .tl-item{margin:.6rem 0}
    .chat-window,.group-chat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1rem}
    .chat-window .msg,.group-chat .msg{margin:.4rem 0;padding:.4rem .8rem;border-radius:6px;background:#F3EEE5}
    .ablation-container{display:flex;gap:1rem;flex-wrap:wrap}
    .quiz-container .q{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:1rem;margin:.8rem 0}
    .grid-2x2{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
    @media(max-width:720px){.grid-2x2{grid-template-columns:1fr}}
    footer.course{background:var(--ink);color:#c9c2b8;text-align:center;padding:2rem;font-size:.85rem}
"""


def _render_course_bundle(course: CoursePlan, modules: List[CourseModule], slides: SlidesConfig) -> CourseBundle:
    """把课程结构 + 各模块 HTML + 幻灯片配置渲染为课程交付包。"""
    nav = '\n'.join(
        f'<a href="#{m.id}">{m.title}</a>'
        for m in modules
    )
    sections = '\n'.join(
        f'<section class="module" id="{m.id}">\n'
        f'  <span class="tag">{m.slug}</span>\n'
        f'  <h2>{m.title}</h2>\n{m.html}\n</section>'
        for m in modules
    )
    index_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{course.course_title}</title>
<style>{_COURSE_DESIGN_CSS}</style>
</head>
<body>
<header class="course">
  <h1>{course.course_title}</h1>
  <div class="sub">{course.subtitle}</div>
</header>
<nav class="course">{nav}</nav>
<main>
{nav}
{sections}
</main>
<footer class="course">由 paper-to-course 工作流生成 · {course.course_title}</footer>
<script>
document.querySelectorAll('.term').forEach(t=>t.title=t.textContent);
</script>
</body>
</html>"""
    readme_md = _render_course_readme(course, modules)
    slides_json = json.dumps(slides.model_dump(), ensure_ascii=False, indent=2)
    build_sh = _render_course_build_sh(course)
    return CourseBundle(
        course_name=course.course_name,
        index_html=index_html,
        readme_md=readme_md,
        slides_config_json=slides_json,
        build_sh=build_sh,
    )


def _render_course_readme(course: CoursePlan, modules: List[CourseModule]) -> str:
    """渲染课程 README.md（Markdown 版课程文档）。"""
    lines = [
        f'# {course.course_title}',
        '',
        f'> {course.subtitle}',
        '',
        '## 课程目录',
        '',
    ]
    for i, m in enumerate(modules, 1):
        lines.append(f'- **{i}. {m.title}**（{m.slug}）')
    lines += ['', '## 模块内容', '']
    for m in modules:
        # 简单去 HTML 标签，保留纯文本要点
        text = re.sub(r'<[^>]+>', '\n', m.html)
        text = re.sub(r'\n{2,}', '\n', text).strip()
        lines.append(f'### {m.title}')
        lines.append('')
        lines.append(text[:1200])
        lines.append('')
    return '\n'.join(lines)


def _render_course_build_sh(course: CoursePlan) -> str:
    """渲染 build.sh 打包脚本。"""
    return f'''#!/usr/bin/env bash
# {course.course_title} 打包脚本
set -euo pipefail
cd "$(dirname "$0")"
# 本包为自包含静态课程（index.html 已内联样式与模块内容），无需额外构建。
# 如需分离 HTML/MD/PPTX 资产，可在此调用 build-all.js。
# node scripts/build-all.js .
echo "课程已生成：index.html / README.md / slides-config.json"
'''


# ============================================================
# teach-from-paper 兼容：教学包渲染（确定性）
# ============================================================

def _render_teaching_package(
    audience: TeachingAudience,
    results: TeachingResults,
    outline: TeachingOutline,
    questions: TeachingQuestions,
    exercises: TeachingExercises,
) -> str:
    """把教学包结构化对象渲染为 `teach_from_paper_[标题].md` 的 Markdown 文本。"""
    date = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f'# Teaching Package: {audience.paper_title}',
        '',
        f'**Audience:** {audience.audience_level} · **Budget:** {audience.time_minutes} min · '
        f'**Date:** {date}',
        '',
        '## 1. Lecture Outline',
        '',
        f'- Motivation -> {outline.arc_motivation or "（待定）"}',
        f'- Setup -> {outline.arc_setup or "（待定）"}',
        f'- Key Result -> {outline.arc_key_result or "（待定）"}',
        f'- Method -> {outline.arc_method or "（待定）"}',
        f'- Takeaways -> {outline.arc_takeaways or "（待定）"}',
        '',
        '## 2. Results Worth Presenting',
        '',
    ]
    for r in results.results:
        lines += [
            f'### {r.id} — {r.name}',
            '',
            f'- **Statement:** {r.statement}',
            f'- **Intuition:** {r.intuition}',
            f'- **Breaks when:** {r.failure_mode}',
        ]
        if r.method_vs_takeaway:
            lines += [f'- **Method vs Takeaway:** {r.method_vs_takeaway}']
        lines += ['']
    if results.notation_notes:
        lines += ['**Notation notes:**']
        lines += [f'- {n}' for n in results.notation_notes]
        lines += ['']

    lines += ['## 3. Slide Skeleton', '', '| # | Title | Content note | Figure/diagram |', '| --- | --- | --- | --- |']
    for s in outline.slides:
        lines.append(f'| {s.num} | {s.title} | {s.content_note} | {s.figure or "—"} |')
    lines += ['']

    lines += ['## 4. Discussion Questions', '']
    for i, q in enumerate(questions.questions, 1):
        lines.append(f'{i}. [{q.depth}] {q.text}')
    lines += ['']

    if exercises.exercises:
        lines += ['## 5. Exercise Brief', '']
        for e in exercises.exercises:
            lines += [
                f'- **{e.id}:** {e.prompt} — drills {e.drills} — answer shape: {e.answer_shape}',
            ]
    return '\n'.join(lines)

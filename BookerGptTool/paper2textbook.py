# -*- coding: utf-8 -*-
"""
paper2textbook.py —— 多篇论文到可溯源教科书的完整编排器。

流程：
1. 读取/抽取多篇论文，形成带页码与原文摘录的概念卡片；
2. 按知识主题聚类论文，并校验论文覆盖；
3. 依据论文聚类、概念卡片和领域综述生成章—知识点大纲；
4. 逐章生成概念解析、学习目标、概念地图、练习等细纲并校验覆盖；
5. 逐章生成正文，执行格式检查、跨章一致性检查和修订；
6. 执行引用审计，合并章节并导出 Markdown/LaTeX/PDF。
"""

import html
import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from os import path
from typing import Dict, List, Tuple

import yaml
from pydantic import BaseModel, parse_obj_as

from .openai import logger as oai_logger
from .paper2textbook_agent import Paper2TextbookAgent
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import extname

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


SUPPORTED_PAPER_EXTS = {'md', 'markdown', 'tex', 'txt', 'pdf'}
SUPPORTED_SURVEY_EXTS = {'md', 'markdown', 'tex', 'txt'}
FORMAT_LABELS = {'md': 'Markdown', 'tex': 'LaTeX'}


class Paper2TextbookOrchestrator:
    """编排论文拆解、教学设计、章节写作和教材交付。"""

    def __init__(self, args):
        self.args = args
        self.agent = Paper2TextbookAgent(args)
        self.pool = ThreadPoolExecutor(max_workers=args.threads)
        self.pj_dir = (
            path.dirname(args.dir) + '_paper2textbook'
            if path.isfile(args.dir) else
            path.abspath(args.dir) + '_paper2textbook'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        self._paper_cache: Dict[str, str] = {}

    # ── 通用 I/O 与缓存 ─────────────────────────────────

    def _read_text(self, fname: str) -> str:
        return open(fname, encoding='utf8').read()

    def _write_text(self, fname: str, text: str) -> None:
        os.makedirs(path.dirname(fname), exist_ok=True)
        open(fname, 'w', encoding='utf8').write(text)

    def _write_yaml(self, fname: str, obj) -> None:
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
        if not path.isfile(fname) or not path.getsize(fname):
            return None
        try:
            data = yaml.safe_load(open(fname, encoding='utf8').read())
        except yaml.error.YAMLError:
            return None
        return parse_obj_as(model, data)

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

    def _cache_file(self, stage: str, name: str, ext: str) -> str:
        return path.join(self.pj_dir, stage, name + ext)

    # ── 论文读取 ────────────────────────────────────────

    def _discover_papers(self, source: str) -> List[str]:
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

    def _paper_brief(self, paper_fnames: List[str], limit=500) -> List[Tuple[str, str, str]]:
        return {
            f: self._read_paper(f)[:500].replace('\n', ' ')
            for f in paper_fnames
        }

    def _paper_list_text(self, papers: List[Tuple[str, str, str]]) -> str:
        rows = []
        for pid, fname, text in papers:
            title = self._first_heading(text) or pid
            abstract = self._abstract_text(text)
            rows.append(
                f'### {pid}\n\n- 文件：`{fname}`\n- 标题：{title}\n\n'
                f'[content]\n{abstract}\n[/content]'
            )
        return '\n\n'.join(rows)

    @staticmethod
    def _first_heading(text: str) -> str:
        m = re.search(r'^\s*#\s+(.+?)\s*$', text, re.M)
        return m.group(1).strip() if m else ''

    @staticmethod
    def _abstract_text(text: str) -> str:
        m = re.search(
            r'(?is)(?:\\begin\{abstract\}|(?:^|\n)##?\s+Abstract\b)'
            r'(.*?)(?:\\end\{abstract\}|(?=\n##?\s))',
            text,
        )
        if m:
            return m.group(1).strip()
        return text[:4000]

    # ── 概念卡片 ────────────────────────────────────────

    def _tr_extract_concepts(
        self, fname: str,
    ) -> PaperConcepts:
        paper_id = path.basename(fname)
        ccpt_fname = self._cache_file('concept_cards', paper_id, '.yaml')
        concept = self._read_yaml(ccpt_fname, PaperConcepts)
        if concept:
            return concept
        # PDF 文本按页保留页码标记；其它格式从第 1 页开始。
        text = self._read_paper(fname)
        start = '1'
        if extname(fname).lower() == 'pdf':
            start = '1'
        result = self.agent.ext_concepts(text, paper_id, start)
        self._write_yaml(ccpt_fname, result)
        return result

    def step_extract_concepts(
        self, paper_fnames: List[str],
    ) -> List[PaperConcepts]:
        logger.info('[1] 拆解论文并生成概念卡片')
        cards = []
        futures = [
            self.pool.submit(self._tr_extract_concepts, f) for f in paper_fnames
        ]
        for future in as_completed(futures):
            cards.append(future.result())
        cards.sort(key=lambda c: self._paper_id(c.paper))
        card_json = self._json_dump(cards)
        self._write_text(path.join(self.pj_dir, 'concept_cards.json'), card_json)
        return cards

    @staticmethod
    def _paper_id(pid: str) -> str:
        m = re.search(r'(\d+)', str(pid))
        return m.group(1) if m else str(pid)

    # ── 聚类 ────────────────────────────────────────────

    def _parts_fname(self) -> str:
        return path.join(self.pj_dir, 'parts.yaml')

    def step_cluster_papers(
        self, 
        paper_briefs: Dict[str, str], 
    ) -> List[PartClus]:
        logger.info('[2] 按知识主题聚类论文')
        parts_fname = self._parts_fname()
        parts = self._read_yaml(parts_fname, List[PartClus])
        if parts:
            return parts
        parts = self.agent.cluster_papers(paper_briefs)
        paper_fnames = list(paper_briefs.keys())
        for _ in range(self.args.check):
            problem = self._parts_coverage_problem(paper_fnames, parts)
            if not problem:
                logger.info('[2] 论文覆盖校验通过')
                break
            logger.warning('[2] 论文覆盖校验失败：\n%s', problem)
            parts = self.agent.fix_cluster(
                paper_briefs, self._json_dump(parts), problem,
            )
        self._write_yaml(parts_fname, parts)
        return parts

    @staticmethod
    def _parts_coverage_problem(paper_fnames: List[str], parts: List[PartClus]) -> str:
        paper_ids = set(paper_fnames)
        clustered = {p for part in parts for p in part.papers}
        missing = sorted(paper_ids - clustered)
        unknown = sorted(clustered - paper_ids)
        if not missing and not unknown:
            return ''
        lines = []
        if missing:
            lines.append('以下论文未出现在任何部分中：\n' + '\n'.join(missing))
        if unknown:
            lines.append('以下论文不存在：\n' + '\n'.join(unknown))
        return '\n'.join(lines)

    # ── 大纲 ────────────────────────────────────────────

    def _outline_fname(self) -> str:
        return path.join(self.pj_dir, 'outline.yaml')

    def step_gen_outline(
        self, parts: List[PartClus], cards: List[PaperConcepts],
    ) -> List[OutlineChapter]:
        logger.info('[3] 生成章—知识点大纲')
        outline_fname = self._outline_fname()
        outline = self._read_yaml(outline_fname, List[OutlineChapter])
        if outline is not None:
            return outline
        result = self.agent.gen_outline(
            self._json_dump(parts), 
            self._json_dump(cards),
        )
        for _ in range(self.args.check):
            problem = self._outline_coverage_problem(cards, result)
            if not problem:
                logger.info('[3] 概念卡片覆盖校验通过')
                break
            logger.warning('[3] 大纲覆盖校验失败：\n%s', problem)
            result = self.agent.fix_outline(
                self._json_dump(result), 
                self._json_dump(parts), 
                self._json_dump(cards),
                problem,
            )
        self._write_yaml(outline_fname, result)
        return result

    def _load_survey(self) -> str:
        survey = self.args.survey
        if not survey:
            return ''
        if path.isfile(survey):
            return self._read_text(survey)
        if path.isdir(survey):
            texts = []
            for root, _, files in os.walk(survey):
                for fname in sorted(files):
                    if extname(fname).lower() in SUPPORTED_SURVEY_EXTS:
                        texts.append(self._read_text(path.join(root, fname)))
            return '\n\n'.join(texts)
        raise ValueError(f'领域综述路径不存在：{survey}')

    @staticmethod
    def _outline_coverage_problem(cards, outline) -> str:
        # 大纲节点的 src 里列出的是支撑该知识点的论文 ID。
        used_papers = {
            src.paper for ch in outline.chapters for n in ch.nodes for src in n.src
        }
        missing = sorted(
            card.paper for card in cards if card.paper not in used_papers
        )
        if missing:
            return '以下论文/概念卡片未纳入大纲：\n' + '\n'.join(missing)
        return ''

    # ── 细纲 ────────────────────────────────────────────

    def _chapter_sources(self, chapter, cards) -> List[str]:
        ids = {src.paper for n in chapter.nodes for src in n.src}
        return [c.paper for c in cards if c.paper in ids]

    def _tr_gen_detail(self, outline: List[OutlineChapter], cards, idx: int) -> ChapterDetail:
        logger.info(f'[4] 编写第 {idx + 1} 章细纲')
        width = max(2, len(str(len(cards))))
        detail_fname = path.join(
            self.pj_dir, 'details', f'detail_{idx + 1:0{width}d}.yaml'
        )
        detail = self._read_yaml(detail_fname, ChapterDetail)
        if detail:
            return detail
        paper_desc_ch = self._paper_desc_ch(outline, cards)
        concept_part = self.agent.gen_concept_anls_detail(
            str(idx + 1), 
            self._json_dump(outline), 
            self._json_dump(paper_desc_ch),
        )
        rest_part = self.agent.gen_rest_detail(
            str(idx + 1), 
            self._json_dump(outline), 
            self._json_dump(concept_part), 
            self._json_dump(paper_desc_ch),
        )
        detail = ChapterDetail(
            no=idx + 1,
            **concept_part.model_dump(),
            **rest_part.model_dump(),
        )
        for _ in range(self.args.check):
            problem = self._detail_coverage_problem(outline, cards, detail)
            if not problem:
                logger.info(f'[4] 第 {idx + 1} 章细纲覆盖校验通过')
                break
            logger.warning('[4] 第 %d 章细纲覆盖校验失败：\n%s', idx + 1, problem)
            detail = self.agent.fix_detail(
                str(idx + 1), self._json_dump(detail), outline_json,
                paper_desc_ch, problem,
            )
        self._write_yaml(detail_fname, detail)
        return detail

    def _paper_desc_ch(self, chapter, cards: List[PaperConcepts]) -> List[PaperConcepts]:
        ids = {src.paper for n in chapter.nodes for src in n.src}
        cards = [c for c in cards if c.paper in ids]
        return cards

    @staticmethod
    def _detail_coverage_problem(chapter, cards, detail) -> str:
        required = {
            src.paper for n in chapter.nodes for src in n.src
        }
        used = {s.paper for u in detail.units for s in u.sources}
        missing = sorted(required - used)
        return '以下论文未在细纲中引用：\n' + '\n'.join(missing) if missing else ''

    def step_gen_details(
        self, outline: List[OutlineChapter], cards: List[PaperConcepts],
    ) -> List[ChapterDetail]:
        logger.info('[4] 生成章节细纲')
        details = []
        futures = [
            self.pool.submit(self._tr_gen_detail, ch, cards, i)
            for i, ch in enumerate(outline.chapters)
        ]
        for future in as_completed(futures):
            details.append(future.result())
        details.sort(key=lambda d: d.no)
        return details

    # ── 正文 ────────────────────────────────────────────

    def _gen_body_one(self, chapter, detail, cards, idx: int) -> str:
        logger.info(f'[5] 编写第 {idx + 1} 章正文')
        width = max(2, len(str(len(chapter.nodes))))
        body_fname = path.join(
            self.pj_dir, 'chapters', f'chapter_{idx + 1:0{width}d}.md'
        )
        if path.isfile(body_fname) and path.getsize(body_fname):
            return self._read_text(body_fname)
        paper_desc_ch = self._paper_desc_ch(chapter, cards)
        body = self.agent.gen_body(
            str(idx + 1), 
            self._json_dump(chapter), 
            self._json_dump(detail), 
            self._json_dump(paper_desc_ch),
        )
        for _ in range(self.args.check):
            comment = self.agent.check_body(body, self._json_dump(detail))
            if '[PERFECT/]' in comment:
                logger.info(f'[5] 第 {idx + 1} 章正文检查通过')
                break
            logger.info('[5] 第 %d 章正文检查意见：\n%s', idx + 1, comment)
            body = self.agent.fix_body(body, comment, paper_desc_ch)
        self._write_text(body_fname, body)
        return body

    def step_gen_bodies(
        self, outline: List[OutlineChapter], details: List[ChapterDetail],
        cards: List[PaperConcepts],
    ) -> List[str]:
        logger.info('[5] 生成章节正文')
        bodies = []
        futures = [
            self.pool.submit(self._gen_body_one, ch, detail, cards, i)
            for i, (ch, detail) in enumerate(zip(outline.chapters, details))
        ]
        for future in as_completed(futures):
            bodies.append(future.result())
        order = {ch.no: i for i, ch in enumerate(outline.chapters)}
        bodies = [b for _, b in sorted(
            zip([order[ch.no] for ch in outline.chapters], bodies), key=lambda x: x[0]
        )]
        return bodies

    # ── 辅助增强 ────────────────────────────────────────

    def _gen_glossary(self, papers: List[Tuple[str, str, str]]) -> List[GlossaryEntry]:
        cached = path.join(self.pj_dir, 'glossary.yaml')
        saved = self._read_yaml(cached, List[GlossaryEntry])
        if saved is not None:
            return saved
        result = self.agent.gen_glossary(self._paper_list_text(papers))
        self._write_yaml(cached, result)
        return result

    def _consistency_check(self, bodies: List[str]) -> List[str]:
        comments = []
        previous = ''
        for i, body in enumerate(bodies, 1):
            comment = self.agent.check_consistency(previous, body)
            if '[PERFECT/]' not in comment:
                comments.append(f'第 {i} 章：{comment}')
            previous += '\n\n' + body
        return comments

    def _citation_audit(self, book: str, papers: List[Tuple[str, str, str]]) -> CitationAudit:
        cached = path.join(self.pj_dir, 'citation_audit.yaml')
        saved = self._read_yaml(cached, CitationAudit)
        if saved:
            return saved
        result = self.agent.audit_citations(book, self._paper_list_text(papers))
        self._write_yaml(cached, result)
        return result

    # ── 组装与导出 ──────────────────────────────────────

    def _assemble_markdown(
        self, title: str, outline: List[OutlineChapter], bodies: List[str],
        glossary: List[GlossaryEntry], audit: CitationAudit,
    ) -> str:
        lines = [f'# {title}', '']
        lines += ['## 术语对照表', '']
        if glossary:
            lines += ['| 标准术语 | 同义词 | 首次出现 |', '|---|---|---|']
            for item in glossary:
                lines.append(
                    f'| {item.canonical} | {", ".join(item.aliases)} | {item.first_seen} |'
                )
        else:
            lines.append('（未生成术语对照表）')
        lines += ['', '## 正文', '']
        for i, body in enumerate(bodies, 1):
            lines += [f'# 第 {i} 章', '', body, '']
        lines += ['## 引用审计', '', '```json', self._json_dump(audit), '```']
        text = '\n'.join(lines)
        self._write_text(path.join(self.pj_dir, 'book.md'), text)
        return text

    def _assemble_tex(self, title: str, bodies: List[str]) -> str:
        lines = [
            '\\documentclass[UTF8,11pt]{ctexart}',
            '\\usepackage{amsmath,amssymb,booktabs,hyperref}',
        ]
        escaped_title = title.replace('&', '\\&')
        lines.append(f'\\title{{{escaped_title}}}')
        lines += ['\\begin{document}', '\\maketitle', '']
        for i, body in enumerate(bodies, 1):
            lines += [f'\\section{{第 {i} 章}}', '', body, '']
        lines += ['\\end{document}', '']
        text = '\n'.join(lines)
        self._write_text(path.join(self.pj_dir, 'main.tex'), text)
        return text

    def _assemble_pdf(self, title: str, bodies: List[str]) -> str:
        md = path.join(self.pj_dir, 'book.md')
        pdf_path = path.join(self.pj_dir, 'book.pdf')
        try:
            import subprocess as subp
            subp.run(
                ['pandoc', md, '-o', pdf_path, '--pdf-engine=xelatex'],
                check=True, capture_output=True,
            )
            return pdf_path
        except Exception as ex:
            logger.warning('[6] 无法通过 pandoc 生成 PDF（%s），改用 HTML 兜底', ex)
            return self._assemble_html(title, bodies)

    def _assemble_html(self, title: str, bodies: List[str]) -> str:
        nav = ' '.join(
            f'<a href="#chapter-{i}">第 {i} 章</a>' for i in range(1, len(bodies) + 1)
        )
        body = []
        for i, content in enumerate(bodies, 1):
            body.append(
                f'<section id="chapter-{i}"><h2>第 {i} 章</h2>'
                f'{content}</section>'
            )
        template = (
            '<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title>'
            '<style>body{{max-width:860px;margin:0 auto;padding:2rem;'
            'font:16px/1.7 Georgia,serif;color:#202a30}}'
            'nav{{background:#f5f7f7;padding:1rem;position:sticky;top:0}}'
            'nav a{{margin-right:1rem;color:#315a66}}'
            'h1,h2,h3{{font-family:Arial,sans-serif;color:#315a66}}'
            'section{{margin:3rem 0}} code,pre{{font-family:monospace}}</style>'
            '</head><body><h1>' + html.escape(title) + '</h1>'
            '<nav>' + nav + '</nav>' + '\n'.join(body) + '</body></html>'
        )
        out = path.join(self.pj_dir, 'book.html')
        self._write_text(out, template)
        return out

    def step_assemble(
        self, title: str, outline: List[OutlineChapter], bodies: List[str],
        papers: List[Tuple[str, str, str]], glossary: List[GlossaryEntry],
    ) -> None:
        logger.info('[6] 组装教材并执行引用审计')
        audit = CitationAudit()
        md = self._assemble_markdown(title, outline, bodies, glossary, audit)
        audit = self._citation_audit(md, papers)
        self._write_yaml(path.join(self.pj_dir, 'citation_audit.yaml'), audit)
        self._assemble_markdown(title, outline, bodies, glossary, audit)
        if self.args.format == 'tex':
            self._assemble_tex(title, bodies)
        elif self.args.format == 'pdf':
            self._assemble_pdf(title, bodies)
        elif self.args.format == 'html':
            self._assemble_html(title, bodies)
        self._write_text(
            path.join(self.pj_dir, 'README.md'),
            '# paper2textbook 输出\n\n'
            f'- 教材：`book.{self.args.format}`\n'
            '- 中间结果保存在各阶段目录，可重复运行并断点续作。\n',
        )

    # ── 主流程 ──────────────────────────────────────────

    def run(self):
        if not path.exists(self.args.dir):
            raise ValueError('请提供论文文件、论文目录或 ARXIV ID')
        os.makedirs(self.pj_dir, exist_ok=True)
        logger.info(self.args)
        paper_fnames = self._discover_papers()
        paper_briefs = self._paper_brief(paper_fnames)
        cards = self.step_extract_concepts(paper_fnames)
        parts = self.step_cluster_papers(paper_briefs)
        outline = self.step_gen_outline(parts, cards)
        details = self.step_gen_details(outline, cards)
        bodies = self.step_gen_bodies(outline, details, cards)
        glossary = self._gen_glossary(paper_briefs) if self.args.glossary else []
        if self.args.consistency:
            comments = self._consistency_check(bodies)
            if comments:
                logger.warning('[6] 跨章一致性检查发现问题：\n%s', '\n'.join(comments))
        self.step_assemble(
            self.args.title or path.basename(path.abspath(self.args.dir)),
            outline, bodies, paper_briefs, glossary,
        )
        logger.info('[DONE] 教材已写入 %s', self.pj_dir)


def paper2textbook(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    Paper2TextbookOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'paper2textbook',
        help='多篇论文到可溯源教科书',
    )
    parser.add_argument('dir', help='论文文件、论文目录或 ARXIV ID（暂以本地文件/目录为主）')
    parser.add_argument('-f', '--format', choices=('md', 'tex', 'pdf'), default='md', help='输出格式')
    parser.add_argument('-T', '--threads', type=int, default=4, help='并行线程数')
    parser.add_argument('-c', '--check', type=int, default=3, help='覆盖/格式检查次数')
    parser.add_argument('--title', help='教材标题')
    parser.add_argument('--glossary', action='store_true', help='生成术语对照表')
    parser.add_argument('--consistency', action='store_true', help='执行跨章一致性检查')
    parser.add_argument('-D', '--debug', action='store_true', help='调试模式')
    parser.set_defaults(func=paper2textbook)

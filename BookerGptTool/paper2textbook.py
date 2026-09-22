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
from concurrent.futures import ThreadPoolExecutor, Future
from os import path
from typing import Dict, List, Tuple, Optional

import yaml
from pydantic import BaseModel, parse_obj_as

from .openai import logger as oai_logger
from .paper2textbook_agent import Paper2TextbookAgent
from .paper2textbook_models import *
from .paper2textbook_pmt import *
from .util import (
    extname,
    ext_code_block,
    ext_cont_block,
    render_prompt,
    read_yaml_model,
    write_yaml_model,
    read_text,
    write_text,
    gen_objs_md5,
)
from .openai import ask_chatgpt_retry, set_openai_props

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)



class Paper2TextbookMixin:
    """编排器通用校验与持久化辅助。"""

    @staticmethod
    def _parts_check_problem(parts: List[PartClus], paper_fnames: List[str]) -> str:
        paper_ids = set(paper_fnames)
        clustered = {p for pt in parts for p in pt.papers}
        missing = sorted(paper_ids - clustered)
        unknown = sorted(clustered - paper_ids)
        prob = ''
        if missing:
            prob += '以下论文未出现在任何分部中：\n' + '\n'.join(missing) + '\n'
        if unknown:
            prob += '以下论文不存在：\n' + '\n'.join(unknown) + '\n'
        return prob

    @staticmethod
    def _outline_check_problem(outline: List[OutlineChapter], concept_cards: List[PaperConcepts]) -> str:
        used_papers = {
            src.paper for ch in outline for n in ch.nodes for src in n.src
        }
        all_papers = {c.paper for c in concept_cards}
        missing = sorted(all_papers - used_papers)
        prob = ''
        if missing:
            prob += '以下论文/概念卡片未纳入大纲：\n' + '\n'.join(missing) + '\n'
        return prob

    @staticmethod
    def _detail_check_problem(chapter: OutlineChapter, detail: ChapterDetail) -> str:
        required = {src.paper for n in chapter.nodes for src in n.src}
        used = {s.paper for u in detail.units for s in u.sources}
        missing = sorted(required - used)
        unknown = used - required
        prob = ''
        if missing:
            prob += '以下论文未在细纲中引用：\n' + '\n'.join(missing) + '\n'
        if unknown:
            prob += '以下论文不存在：\n' + '\n'.join(unknown) + '\n'
        return prob

    def _collect_hdls(self, res_callback=None, write_callback=None) -> None:
        save_step = max(min(len(self.hdls) // 5, 100), 1)
        for i, h in enumerate(self.hdls):
            r = h.result()
            if res_callback: res_callback(r)
            if write_callback and (i % save_step == 0 or i == len(self.hdls) - 1):
                write_callback()
        self.hdls = []


class Paper2TextbookOrchestrator(Paper2TextbookMixin):
    """编排器：按固定顺序驱动论文→教科书的完整生成流程。

    步骤：
      1. step_read_papers     读取论文列表与全文
      2. step_ext_concepts    概念卡片（并行 + 缓存）
      3. step_cluster_papers  论文聚类 + 校验
      4. step_gen_outline     全书大纲 + 校验
      5. step_gen_details     逐章细纲（并行 + 校验）
      6. step_gen_bodies      逐章正文（并行 + 校验）
      7. step_write_book      汇总导出
    """

    def __init__(self, args):
        self.args = args
        self.agent = Paper2TextbookAgent(args)
        self.pj_dir = self.agent.pj_dir
        self.pool = ThreadPoolExecutor(getattr(args, 'threads', 8))
        self.check = getattr(args, 'check', 3)
        self.hdls: List[Future] = []


    # ── 1. 读取论文 ──────────────────────────────────────────

    def step_read_papers(self) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
        logger.info('[1] 读取论文列表与全文')
        paper_fnames = self.agent.tool_list_papers()
        papers_text = {f: self.agent.tool_read_paper(f) for f in paper_fnames}
        paper_briefs = self.agent.tool_paper_brief(paper_fnames)
        return paper_fnames, papers_text, paper_briefs

    # ── 2. 概念卡片 ──────────────────────────────────────────

    def step_ext_concepts(self, papers_text: Dict[str, str]) -> List[PaperConcepts]:
        logger.info('[2] 生成概念卡片')
        concepts = []
        for fname, text in papers_text.items():
            cache_fname = path.join(self.pj_dir, 'ccpt_' + gen_objs_md5(text) + '.yaml')
            r = read_yaml_model(cache_fname, PaperConcepts)
            if r is None:
                r = self.agent.tool_ext_concepts(fname, text)
                write_yaml_model(cache_fname, r)
            concepts.append(r)
        write_yaml_model(path.join(self.pj_dir, 'concepts.yaml'), concepts)
        return concepts

    # ── 3. 论文聚类 ──────────────────────────────────────────

    def step_cluster_papers(self, paper_briefs: Dict[str, str], paper_fnames: List[str]) -> List[PartClus]:
        logger.info('[3] 论文聚类')
        cache_fname = path.join(self.pj_dir, 'parts.yaml')
        parts = read_yaml_model(cache_fname, List[PartClus])
        if parts:
            return parts
        parts = self.agent.tool_cluster_papers(paper_briefs)
        for _ in range(self.check):
            prob = self._parts_check_problem(parts, paper_fnames)
            if not prob:
                logger.info('[3] 聚类校验通过')
                break
            logger.warn(f'[3] 聚类校验失败：\n{prob}')
            parts = self.agent.tool_fix_cluster(paper_briefs, parts, prob)
        write_yaml_model(cache_fname, parts)
        return parts

    # ── 4. 全书大纲 ──────────────────────────────────────────

    def step_gen_outline(self, parts: List[PartClus], concept_cards: List[PaperConcepts]) -> List[OutlineChapter]:
        logger.info('[4] 生成全书大纲')
        cache_fname = path.join(self.pj_dir, 'outline.yaml')
        outline = read_yaml_model(cache_fname, List[OutlineChapter])
        if outline:
            return outline
        struct = [pt.title for pt in parts]
        outline = self.agent.tool_gen_outline(struct, concept_cards)
        for _ in range(self.check):
            prob = self._outline_check_problem(outline, concept_cards)
            if not prob:
                logger.info('[4] 大纲校验通过')
                break
            logger.warn(f'[4] 大纲校验失败：\n{prob}')
            outline = self.agent.tool_fix_outline(outline, struct, concept_cards, prob)
        write_yaml_model(cache_fname, outline)
        return outline

    # ── 5. 逐章细纲（并行）───────────────────────────────────

    def step_gen_details(self, outline: List[OutlineChapter], concept_cards: List[PaperConcepts]) -> List[ChapterDetail]:
        logger.info('[5] 生成逐章细纲')
        details = [None] * len(outline)

        def res_callback(tpl):
            idx, detail = tpl
            details[idx] = detail

        for i, ch in enumerate(outline):
            h = self.pool.submit(self._tr_gen_detail, i, outline, concept_cards)
            self.hdls.append(h)
            if len(self.hdls) > self.pool._max_workers:
                self._collect_hdls(res_callback)
        self._collect_hdls(res_callback)

        write_yaml_model(path.join(self.pj_dir, 'details.yaml'), details)
        return details

    def _tr_gen_detail(self, i: int, outline: List[OutlineChapter], concept_cards: List[PaperConcepts]) -> Tuple[int, ChapterDetail]:
        logger.info(f'[5] 编写第{i+1}章细纲')
        cache_fname = path.join(self.pj_dir, f'detail_{i+1:03d}.yaml')
        d = read_yaml_model(cache_fname, ChapterDetail)
        if d:
            return i, d

        anls = self.agent.tool_gen_concept_anls_detail(i, outline, concept_cards)
        rest = self.agent.tool_gen_rest_detail(i, outline, anls, concept_cards)
        detail = ChapterDetail(no=i+1, **anls.dict(), **rest.dict())

        for _ in range(self.check):
            prob = self._detail_check_problem(outline, detail)
            if not prob:
                logger.info(f'[5] 细纲 {i+1} 校验通过')
                break
            logger.warn(f'[5] 细纲 {i+1} 校验失败：\n{prob}')
            detail = self.agent.tool_fix_detail(i, detail, outline, concept_cards, prob)

        write_yaml_model(cache_fname, detail)
        return i, detail

    # ── 6. 逐章正文（并行）───────────────────────────────────

    def step_gen_bodies(self, outline: List[OutlineChapter], details: List[ChapterDetail], concept_cards: List[PaperConcepts]) -> List[str]:
        logger.info('[6] 生成逐章正文')
        bodies = [None] * len(outline)

        def res_callback(tpl):
            idx, body = tpl
            bodies[idx] = body

        for i,  detail in enumerate(details):
            h = self.pool.submit(self._tr_gen_body, i, outline, detail, concept_cards)
            self.hdls.append(h)
            if len(self.hdls) > self.pool._max_workers:
                self._collect_hdls(res_callback)
        self._collect_hdls(res_callback)

        # 跨章一致性：正文齐备后顺序校验（线程间依赖上一章正文）
        for i in range(1, len(bodies)):
            if not bodies[i] or not bodies[i-1]:
                continue
            cmt2 = self.agent.tool_check_consistency(bodies[i-1], bodies[i])
            if cmt2.strip():
                logger.info(f'[6] 跨章一致性提示（第{i+1}章）：\n{cmt2}')
                bodies[i] = self.agent.tool_fix_body(bodies[i], cmt2, concept_cards)
                write_text(path.join(self.pj_dir, f'chapter_{i+1:03d}.md'), bodies[i])
        return bodies

    def _tr_gen_body(self, i: int, outline: List[OutlineChapter], detail: ChapterDetail, concept_cards: List[PaperConcepts]) -> Tuple[int, str]:
        logger.info(f'[6] 编写第{i+1}章正文')
        cache_fname = path.join(self.pj_dir, f'chapter_{i+1:03d}.md')
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return i, read_text(cache_fname)

        body = self.agent.tool_gen_body(i, outline, detail, concept_cards)

        for _ in range(self.check):
            cmt = self.agent.tool_check_body(body, detail)
            if '[PERFECT/]' in cmt:
                logger.info(f'[6] 正文 {i+1} 校验通过')
                break
            logger.warn(f'[6] 正文 {i+1} 校验未通过：\n{cmt}')
            body = self.agent.tool_fix_body(body, cmt, concept_cards)

        paper_text = '\n\n'.join(self.agent.tool_read_paper(c.paper) for c in concept_cards)
        audit = self.agent.tool_audit_citations(body, paper_text)
        if audit.unsupported_claims:
            logger.info(f'[6] 引用审计提示无支撑观点：{len(audit.unsupported_claims)} 条')

        write_text(cache_fname, body)
        return i, body

    # ── 7. 汇总导出 ──────────────────────────────────────────

    def step_write_book(self, outline: List[OutlineChapter], details: List[ChapterDetail], bodies: List[str]) -> str:
        logger.info('[7] 汇总导出教科书')
        lines = ['# 教科书\n']
        for i, (ch, body) in enumerate(zip(outline, bodies)):
            lines.append(f'## {ch.no}. {ch.name}\n')
            lines.append(f'{ch.desc}\n')
            lines.append(body)
            lines.append('')

        full_md = '\n'.join(lines)
        out_fname = path.join(self.pj_dir, '教科书.md')
        write_text(out_fname, full_md)
        logger.info(f'[*] 教科书已写入：{out_fname}')
        return out_fname

    # ── 主流程 ───────────────────────────────────────────────

    def run(self) -> None:
        if not path.exists(self.args.dir):
            raise ValueError('请提供论文文件、论文目录或 ARXIV ID')
        os.makedirs(self.pj_dir, exist_ok=True)
        logger.info(self.args)

        paper_fnames, papers_text, paper_briefs = self.step_read_papers()
        concept_cards = self.step_ext_concepts(papers_text)
        parts = self.step_cluster_papers(paper_briefs, paper_fnames)
        outline = self.step_gen_outline(parts, concept_cards)
        details = self.step_gen_details(outline, concept_cards)
        bodies = self.step_gen_bodies(outline, details, concept_cards)
        self.step_write_book(outline, details, bodies)

        logger.info(f'[*] 已完成，目标文件已写入 {self.pj_dir}')



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
    parser.add_argument('-t', '--threads', type=int, default=8, help='线程数')
    parser.add_argument('-c', '--check', type=int, default=3, help='校验次数')
    parser.add_argument('-D', '--debug', action='store_true', help='调试模式')
    parser.set_defaults(func=paper2textbook)

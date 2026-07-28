import argparse
import logging
import traceback
import copy
import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
import re
import os
import hashlib
import shutil
import yaml
import fitz
import functools
import cv2
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterator, List, Optional, Tuple
import json
import json_repair
import tqdm
from imgyaso.quant import pngquant
from pydantic import BaseModel
from .clean_heading import clean_md_llm
from .pdf_ocr_pmt import *
from .pdf_ocr_models import *
from .util import (
    call_vlm_retry,
    ask_chatgpt_retry,
    set_openai_props,
    extname,
    to_kebab,
    ext_code_block,
    ext_cont_block,
)

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ── Agent 类 ─────────────────────────────────────


class PdfOcrAgent:
    """封装所有 LLM 调用的智能体类。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        set_openai_props(args)

    def ocr(self, img: bytes) -> str:
        parse_output = lambda ans: OCRResult(
            **json_repair.loads(ext_code_block(ans))
        )
        res: OCRResult = call_vlm_retry(
            img, OCR_PMT,
            model_name=self.args.vmodel,
            args=self.args,
            parse_output=parse_output,
        )
        return self._res2md(res)

    def _res2md(self, r: OCRResult) -> str:
        mds = []
        for seg in r.contents:
            if seg.type == 'image':
                bbox = seg.bbox
                md = f'![](bbox={bbox})'
            elif seg.type == 'title':
                md = '# ' + seg.markdown
            elif seg.type == 'list':
                md = '+   ' + seg.markdown
            elif seg.type == 'code':
                md = '```\n' + seg.markdown + '\n```'
            elif seg.type == 'quote':
                md = '> ' + seg.markdown
            else:
                md = seg.markdown
            mds.append(md)
        return '\n\n'.join(mds).strip() \
            or '<!-- no content -->'

    def merge(self, prev_line: str, next_line: str) -> int:
        ques = MERGE_PMT.replace('{prev}', prev_line) \
            .replace('{next}', next_line)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        return int('[TRUE]' in ans)

    def post_proc(self, text: str) -> str:
        ques = POSTPROC_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def translate(self, text: str) -> str:
        ques = TRANS_BODY_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_toc(self, toc_text: str) -> List[List[str]]:
        ques = TOC_PMT.replace('{text}', toc_text)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        return re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)

    def trans_title(self, title: str) -> str:
        ques = TRANS_TITLE_PMT.replace('{text}', title)
        return ask_chatgpt_retry(ques, self.args.model, self.args)


# ── 编排器 ───────────────────────────────────────


class PDFOcrOrchestrator:
    """编排整个 PDF OCR 流水线。

    流水线步骤之间通过参数和返回值传递数据，
    实例属性仅存放 agents 和线程池基础设施，
    所有路径由 run() 计算并通过参数传递。
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

        # ── Agent ──
        self.agent: PdfOcrAgent = PdfOcrAgent(args)

        # ── 线程池基础设施 ──
        self.pool: Optional[ThreadPoolExecutor] = \
            ThreadPoolExecutor(self.args.page_threads)
        self._hdls: List[Future] = []

    @staticmethod
    def _resolve_paths(args: argparse.Namespace) -> dict:
        """根据 args 计算所有输出路径，返回字典。"""
        name = path.basename(args.fname)[:-4]
        slug = to_kebab(name)
        d = path.dirname(args.fname)
        pj_dir = path.join(d, slug) if args.mkdir else d
        return {
            'name': name,
            'slug': slug,
            'pj_dir': pj_dir,
            'md_fname': (
                path.join(pj_dir, f'{slug}.md')
                if args.mkdir else args.fname[:-4] + '.md'
            ),
            'yaml_fname': (
                path.join(pj_dir, 'meta.yaml')
                if args.mkdir else args.fname[:-4] + '.yaml'
            ),
            'img_dir': (
                path.join(pj_dir, 'img')
                if args.mkdir else args.fname[:-4] + '_imgs'
            ),
        }

    # ── 线程池工具 ────────────────────────────────

    def _submit(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """提交线程池任务。"""
        h = self.pool.submit(fn, *args, **kwargs)
        self._hdls.append(h)

    def _drain(self, on_done: Optional[Callable] = None) -> None:
        """等待所有已提交任务完成并清空。
        on_done: 每个子线程完成后在主线程中调用的回调。
        """
        with tqdm.tqdm(total=len(self._hdls)) as pbar:
            for h in as_completed(self._hdls):
                h.result()
                if on_done:
                    on_done()
                pbar.update(1)
        self._hdls = []

    # ── 主线程写入 ──────────────────────────────────

    def _write_yaml(self, obj: Meta, yaml_fname: str) -> None:
        """在主线程中将 meta 写回 yaml 文件。"""
        if isinstance(obj, BaseModel):
            obj = obj.dict()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        with open(yaml_fname, 'w', encoding='utf8') as f:
            f.write(yaml.safe_dump(obj, allow_unicode=True))

    # ── 线程池任务 ────────────────────────────────

    def _corp_img(self, img: bytes, bbox: List[float]) -> bytes:
        xmin, ymin, xmax, ymax = bbox
        fmt_bytes = isinstance(img, bytes)
        if fmt_bytes:
            img = cv2.imdecode(
                np.frombuffer(img, np.uint8),
                cv2.IMREAD_UNCHANGED
            )
        h, w = img.shape[0], img.shape[1]
        xmin = int(w * xmin)
        xmax = int(w * xmax)
        ymin = int(h * ymin)
        ymax = int(h * ymax)
        img_pt = img[ymin:ymax + 1, xmin: xmax + 1]
        if 0 in img_pt.shape:
            img_pt = np.full([1, 1, 3], 255, np.uint8)
        if fmt_bytes:
            img_pt = bytes(cv2.imencode(
                '.png', img_pt,
                [cv2.IMWRITE_PNG_COMPRESSION, 9]
            )[1])
        return img_pt

    def _tr_ocr_page(self, img: bytes, page: Page) -> None:
        logger.debug(f'[3] 识别页码 {page.pgno + 1}')
        page.md = self.agent.ocr(img=img)

    def _tr_proc_img(
        self, img: bytes, page: Page, img_dir: str, pdf_hash: str
    ) -> None:
        logger.debug(f'[4] 处理图像 {page.pgno}')
        md = page.md
        pgno = page.pgno
        img_links = re.findall(r'!\[\]\(.+?\)', md)
        for j, link in enumerate(img_links):
            m = re.search(
                r'bbox=\[(\d+\.\d+),\x20(\d+\.\d+),'
                r'\x20(\d+\.\d+),\x20(\d+\.\d+)\]',
                link,
            )
            if not m:
                continue
            bbox = [
                float(m.group(1)), float(m.group(2)),
                float(m.group(3)), float(m.group(4)),
            ]
            img_pt = self._corp_img(img, bbox)
            img_pt = pngquant(img_pt)
            img_fname = f'{pdf_hash}_{pgno}_{j}.png'
            img_ffname = path.join(img_dir, img_fname)
            logger.debug(f'[5] {img_ffname}')
            open(img_ffname, 'wb').write(img_pt)
            md = md.replace(link, f'![](img/{img_fname})')
            page.md = md
        page.img_proc = True

    def _tr_group_page(self, group: Group) -> None:
        logger.debug(f'[5] 处理页面合并')
        text = '\n\n'.join(group.raw)
        group.md = self.agent.post_proc(text=text)
        if self.args.trans:
            group.mdcn = self.agent.translate(text=group.md)
        else:
            group.mdcn = group.md

    def _tr_merge_group(self, prev_group: Group, group: Group) -> None:
        logger.debug(f'[6] 处理分组合并')
        prev_line = prev_group.mdcn.strip()
        next_line = group.mdcn.strip()
        prev = re.search(r'^.+?\Z', prev_line, flags=re.M).group()
        next = re.search(r'\A.+?$', next_line, flags=re.M).group()
        group.merge = self.agent.merge(
            prev_line=prev, next_line=next,
        )

    # ── 流水线各步骤 ──────────────────────────────
    #
    # 数据流：
    #   load_pdf  → (doc, pdf_hash)
    #   init_meta(doc) → res
    #   ocr_pages(doc, res)        — 原地修改 pages.md
    #   process_images(doc, res, pdf_hash) — 原地修改 pages.md / img_proc
    #   group_pages(res)           — 填充 res.groups
    #   merge_groups(res)          — 原地修改 groups.merge
    #   build_full_text(res) → (full_text, name_cn)
    #   fix_toc(full_text, res) → full_text
    #   write_output(full_text, name_cn) → None

    def load_pdf(self) -> Tuple[fitz.Document, str]:
        """[1] 加载 PDF 文件。返回 (doc, pdf_hash)。"""
        logger.info(f'[1] 加载 {self.args.fname}')
        pdf = open(self.args.fname, 'rb').read()
        pdf_hash = hashlib.md5(pdf).hexdigest()
        doc = fitz.open('pdf', BytesIO(pdf))
        return doc, pdf_hash

    def init_meta(self, doc: fitz.Document, yaml_fname: str) -> Meta:
        """[2] 加载或初始化 meta.yaml。返回 Meta。"""
        logger.info(f'[2] 初始化 {yaml_fname}')
        if path.isfile(yaml_fname) and \
           path.getsize(yaml_fname) != 0:
            res = yaml.safe_load(
                open(yaml_fname, encoding='utf8').read()
            )
            return Meta(**res)
        pages = [Page(pgno=i) for i in range(len(doc))]
        res = Meta(pages=pages)
        self._write_yaml(res, yaml_fname)
        return res

    def ocr_pages(
        self, doc: fitz.Document, res: Meta, yaml_fname: str
    ) -> None:
        """[3] VLM 识别每页图像。原地填充 pages.md。"""
        logger.info('[3] 识别图像')
        for i, pg in tqdm.tqdm(enumerate(res.pages)):
            if pg.md:
                continue
            pgno = pg.pgno
            img = doc[pgno] \
                .get_pixmap(dpi=self.args.dpi) \
                .pil_tobytes('png')
            self._submit(
                self._tr_ocr_page,
                img, pg,
            )
        self._drain(lambda: self._write_yaml(res, yaml_fname))

    def process_images(
        self, doc: fitz.Document, res: Meta, pdf_hash: str,
        img_dir: str, yaml_fname: str,
    ) -> None:
        """[4] 裁切并保存页面中的插图。原地填充 pages.md/img_proc。"""
        logger.info('[4] 处理图片')
        os.makedirs(img_dir, exist_ok=True)
        for i, pg in tqdm.tqdm(enumerate(res.pages)):
            if pg.img_proc:
                continue
            pgno = pg.pgno
            img = doc[pgno] \
                .get_pixmap(dpi=self.args.dpi) \
                .pil_tobytes('png')
            self._submit(
                self._tr_proc_img,
                img, pg,
                img_dir, pdf_hash,
            )
        self._drain(lambda: self._write_yaml(res, yaml_fname))

    def group_pages(self, res: Meta, yaml_fname: str) -> None:
        """[5] 按长度分组，后处理 + 翻译。填充 res.groups。"""
        logger.info('[5] 处理页间合并')
        if not res.groups:
            res.groups = mkgroups(res.pages, self.args)
        for i, g in tqdm.tqdm(enumerate(res.groups)):
            if g.md and g.mdcn:
                continue
            self._submit(
                self._tr_group_page,
                g,
            )
        self._drain(lambda: self._write_yaml(res, yaml_fname))

    def merge_groups(self, res: Meta, yaml_fname: str) -> None:
        """[6] 判断组间是否需要合并。过滤并原地填充 groups.merge。"""
        logger.info('[6] 处理组间合并')
        res.groups = [g for g in res.groups if g.mdcn]
        for i, g in tqdm.tqdm(enumerate(res.groups)):
            if i == 0:
                continue
            if g.merge != -1:
                continue
            self._submit(
                self._tr_merge_group,
                res.groups[i - 1], g,
            )
        self._drain(lambda: self._write_yaml(res, yaml_fname))

    def build_full_text(self, res: Meta, name: str) -> Tuple[str, str]:
        """[6+] 拼接全文，可选清理与标题翻译。返回 (full_text, name_cn)。"""
        full_text = ''
        for i, g in enumerate(res.groups):
            logger.debug(f'[6] 生成全文 {i}')
            if g.merge != 1:
                full_text += '\n\n'
            full_text += g.mdcn

        name_cn = ''
        if self.args.clean:
            full_text = clean_md_llm(full_text, self.args)
            name_cn = self.agent.trans_title(title=name)
            full_text = f'# {name_cn}\n\n{full_text}'

        return full_text, name_cn

    def fix_toc(self, full_text: str, res: Meta, yaml_fname: str) -> str:
        """[7] 修正目录层级。返回修正后的 full_text。"""
        logger.info('[7] 修正目录')
        if res.toc:
            toc = res.toc
        else:
            toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
            toc = self.agent.fix_toc(toc_text='\n'.join(toc))
            res.toc = toc
            self._write_yaml(res, yaml_fname)
        for lvl, title in toc:
            logger.debug(f'[7] {lvl} {title}')
            try:
                full_text = re.sub(
                    r'^#+\x20+' + re.escape(title) + '$',
                    f'{lvl} {title}',
                    full_text, flags=re.M,
                )
            except re.error:
                pass
        return full_text

    def write_output(
        self, full_text: str, name_cn: str,
        md_fname: str, pj_dir: str, slug: str,
        name: str,
    ) -> None:
        """[8] 写入 md / README / SUMMARY。"""
        logger.info(f'[8] 写入 {md_fname}')
        open(md_fname, 'w', encoding='utf8') \
            .write(full_text)

        if self.args.mkdir:
            if not name_cn:
                name_cn = self.agent.trans_title(title=name)

            logger.info('[8] 写入 README.md')
            readme = README_TMPL \
                .replace('{name}', name) \
                .replace('{name_cn}', name_cn)
            readme_fname = path.join(
                pj_dir, 'README.md'
            )
            open(readme_fname, 'w', encoding='utf8') \
                .write(readme)

            logger.info('[8] 写入 SUMMARY.md')
            toc = [
                f'+   [{name_cn}](README.md)',
                f'+   [{name_cn}]({slug}.md)',
            ]
            summary_fname = path.join(
                pj_dir, 'SUMMARY.md'
            )
            open(summary_fname, 'w', encoding='utf8') \
                .write('\n'.join(toc))

    # ── 主流程 ─────────────────────────────────────

    def run(self) -> None:
        if not self.args.fname.endswith('.pdf'):
            logger.fatal('请提供PDF文件')
            return

        paths = self._resolve_paths(self.args)
        name = paths['name']
        slug = paths['slug']
        pj_dir = paths['pj_dir']
        md_fname = paths['md_fname']
        yaml_fname = paths['yaml_fname']
        img_dir = paths['img_dir']

        os.makedirs(pj_dir, exist_ok=True)
        if path.isfile(md_fname):
            logger.warn('PDF 已处理')
            return

        # 1. 加载 PDF
        doc, pdf_hash = self.load_pdf()

        # 2. 初始化 meta
        res = self.init_meta(doc, yaml_fname)

        # 3. OCR 识别
        self.ocr_pages(doc, res, yaml_fname)

        # 4. 处理图片
        self.process_images(doc, res, pdf_hash, img_dir, yaml_fname)

        # 5. 分组 + 后处理 + 翻译
        self.group_pages(res, yaml_fname)

        # 6. 组间合并
        self.merge_groups(res, yaml_fname)

        # 7. 拼接全文
        full_text, name_cn = self.build_full_text(res, name)

        # 8. 修正目录
        full_text = self.fix_toc(full_text, res, yaml_fname)

        # 9. 写入文件
        self.write_output(full_text, name_cn, md_fname, pj_dir, slug, name)

        logger.info('[*] 处理完毕')


# ── 模块级工具函数 ────────────────────────────────


def mkgroups(pages: List[Page], args: argparse.Namespace) -> List[Group]:
    groups = [Group()]
    for p in pages:
        exi_len = sum(len(md) for md in groups[-1].raw)
        if exi_len > args.limit:
            groups.append(Group())
        groups[-1].raw.append(
            f"[PAGE {p.pgno}]\n\n{p.md}"
        )
    groups = [g for g in groups if g.raw]
    return groups


# ── 入口 ─────────────────────────────────────────


def pdf_ocr(args: argparse.Namespace) -> None:
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if f.endswith('.pdf')]
    if not fnames:
        logger.fatal('请提供 PDF 或目录')
        return

    pool = ThreadPoolExecutor(args.file_threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(pdf_ocr_file_safe, args)
        hdls.append(h)
    for h in hdls:
        h.result()


def pdf_ocr_file_safe(args: argparse.Namespace) -> None:
    try:
        PDFOcrOrchestrator(args).run()
    except:
        traceback.print_exc()

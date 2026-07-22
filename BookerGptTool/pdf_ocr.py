import argparse
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
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, List, Optional, Tuple
import json
import json_repair
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


# ── Agent 类 ─────────────────────────────────────


class Agent:
    """LLM Agent 基类，封装一次 LLM 调用。"""
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def run(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class OCRAgent(Agent):
    """VLM 图像识别 Agent，将图片转为结构化 OCR 结果。"""
    def run(self, img: bytes) -> str:
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
        """将 OCRResult 转为 Markdown 文本。"""
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


class MergeAgent(Agent):
    """判断两页的首尾是否属于同一段落。"""
    def run(self, prev_line: str, next_line: str) -> int:
        ques = MERGE_PMT.replace('{prev}', prev_line) \
            .replace('{next}', next_line)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        merge_str = ans.replace('```', '').strip()
        return int(merge_str == 'true')


class PostProcAgent(Agent):
    """OCR 后处理 Agent，纠正错误、合并跨页段落、识别标题。"""
    def run(self, text: str) -> str:
        ques = POSTPROC_PMT.replace('{text}', text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)


class TranslateAgent(Agent):
    """英译中 Agent，将 Markdown 正文翻译为中文。"""
    def run(self, text: str) -> str:
        ques = TRANS_BODY_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )


class TOCAgent(Agent):
    """目录修复 Agent，修正 OCR 目录的层级和错字。"""
    def run(self, toc_text: str) -> List[List[str]]:
        ques = TOC_PMT.replace('{text}', toc_text)
        ans = ask_chatgpt_retry(ques, self.args.model, self.args)
        return re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)


class TitleAgent(Agent):
    """标题翻译 Agent，将英文书名翻译为中文。"""
    def run(self, title: str) -> str:
        ques = TRANS_TITLE_PMT.replace('{text}', title)
        return ask_chatgpt_retry(ques, self.args.model, self.args)


# ── 编排器 ───────────────────────────────────────


class PDFOcrOrchestrator:
    """编排整个 PDF OCR 流水线。

    流水线步骤之间通过参数和返回值传递数据，
    实例属性仅存放配置（args / agents / 路径）和线程池基础设施。
    """

    def __init__(self, args: argparse.Namespace) -> None:
        # ── 配置 ──
        self.args = args
        self.name: str = path.basename(args.fname)[:-4]
        self.slug: str = to_kebab(self.name)
        self.dir: str = path.dirname(args.fname)
        self.pj_dir: str = (
            path.join(self.dir, self.slug)
            if args.mkdir else self.dir
        )
        self.md_fname: str = (
            path.join(self.pj_dir, f'{self.slug}.md')
            if args.mkdir
            else args.fname[:-4] + '.md'
        )
        self.yaml_fname: str = (
            path.join(self.pj_dir, 'meta.yaml')
            if args.mkdir
            else args.fname[:-4] + '.yaml'
        )
        self.img_dir: str = (
            path.join(self.pj_dir, 'img')
            if args.mkdir
            else args.fname[:-4] + '_imgs'
        )

        # ── Agents ──
        self.ocr_agent: OCRAgent = OCRAgent(args)
        self.merge_agent: MergeAgent = MergeAgent(args)
        self.post_proc_agent: PostProcAgent = PostProcAgent(args)
        self.translate_agent: Optional[TranslateAgent] = (
            TranslateAgent(args) if args.trans else None
        )
        self.toc_agent: TOCAgent = TOCAgent(args)
        self.title_agent: TitleAgent = TitleAgent(args)

        # ── 线程池基础设施 ──
        self.pool: Optional[ThreadPoolExecutor] = None
        self._hdls: List[Future] = []

    # ── 线程池工具 ────────────────────────────────

    def _submit(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """提交线程池任务。"""
        h = self.pool.submit(fn, *args, **kwargs)
        self._hdls.append(h)

    def _drain(self) -> None:
        """等待所有已提交任务完成并清空。"""
        for h in self._hdls:
            h.result()
        self._hdls = []

    # ── 主线程写入 ──────────────────────────────────

    def _write_meta(self, res: Meta) -> None:
        """在主线程中将 meta 写回 yaml 文件。"""
        with open(self.yaml_fname, 'w', encoding='utf8') as f:
            obj = (
                [r.dict() for r in res]
                if isinstance(res, list)
                else res.dict()
            )
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
        print(f'[3] 识别页码 {page.pgno + 1}')
        page.md = self.ocr_agent.run(img=img)

    def _tr_proc_img(
        self, img: bytes, page: Page, img_dir: str, pdf_hash: str
    ) -> None:
        print(f'[4] 处理图像 {page.pgno}')
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
            print(f'[5] {img_ffname}')
            open(img_ffname, 'wb').write(img_pt)
            md = md.replace(link, f'![](img/{img_fname})')
            page.md = md
        page.img_proc = True

    def _tr_group_page(self, group: Group) -> None:
        print(f'[5] 处理页面合并')
        text = '\n\n'.join(group.raw)
        group.md = self.post_proc_agent.run(text=text)
        if self.translate_agent:
            group.mdcn = self.translate_agent.run(
                text=group.md,
            )
        else:
            group.mdcn = group.md

    def _tr_merge_group(self, prev_group: Group, group: Group) -> None:
        print(f'[6] 处理分组合并')
        prev_line = prev_group.mdcn.strip()
        next_line = group.mdcn.strip()
        prev = re.search(r'^.+?\Z', prev_line, flags=re.M).group()
        next = re.search(r'\A.+?$', next_line, flags=re.M).group()
        group.merge = self.merge_agent.run(
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
        print(f'[1] 加载 {self.args.fname}')
        pdf = open(self.args.fname, 'rb').read()
        pdf_hash = hashlib.md5(pdf).hexdigest()
        doc = fitz.open('pdf', BytesIO(pdf))
        return doc, pdf_hash

    def init_meta(self, doc: fitz.Document) -> Meta:
        """[2] 加载或初始化 meta.yaml。返回 Meta。"""
        print(f'[2] 初始化 {self.yaml_fname}')
        if path.isfile(self.yaml_fname) and \
           path.getsize(self.yaml_fname) != 0:
            res = yaml.safe_load(
                open(self.yaml_fname, encoding='utf8').read()
            )
            return Meta(**res)
        pages = [Page(pgno=i) for i in range(len(doc))]
        res = Meta(pages=pages)
        open(self.yaml_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(
                res.dict(), allow_unicode=True
            ))
        return res

    def ocr_pages(self, doc: fitz.Document, res: Meta) -> None:
        """[3] VLM 识别每页图像。原地填充 pages.md。"""
        print('[3] 识别图像')
        for i, g in enumerate(res.pages):
            if g.md:
                continue
            pgno = g.pgno
            img = doc[pgno] \
                .get_pixmap(dpi=self.args.dpi) \
                .pil_tobytes('png')
            self._submit(
                self._tr_ocr_page,
                img, g,
            )
        self._drain()
        self._write_meta(res)

    def process_images(
        self, doc: fitz.Document, res: Meta, pdf_hash: str
    ) -> None:
        """[4] 裁切并保存页面中的插图。原地填充 pages.md/img_proc。"""
        print('[4] 处理图片')
        os.makedirs(self.img_dir, exist_ok=True)
        for i, g in enumerate(res.pages):
            if g.img_proc:
                continue
            pgno = g.pgno
            img = doc[pgno] \
                .get_pixmap(dpi=self.args.dpi) \
                .pil_tobytes('png')
            self._submit(
                self._tr_proc_img,
                img, g,
                self.img_dir, pdf_hash,
            )
        self._drain()
        self._write_meta(res)

    def group_pages(self, res: Meta) -> None:
        """[5] 按长度分组，后处理 + 翻译。填充 res.groups。"""
        print('[5] 处理页间合并')
        if not res.groups:
            res.groups = mkgroups(res.pages, self.args)
        for i, g in enumerate(res.groups):
            if g.md and g.mdcn:
                continue
            self._submit(
                self._tr_group_page,
                g,
            )
        self._drain()
        self._write_meta(res)

    def merge_groups(self, res: Meta) -> None:
        """[6] 判断组间是否需要合并。过滤并原地填充 groups.merge。"""
        print('[6] 处理组间合并')
        res.groups = [g for g in res.groups if g.mdcn]
        for i, g in enumerate(res.groups):
            if i == 0:
                continue
            if g.merge != -1:
                continue
            self._submit(
                self._tr_merge_group,
                res.groups[i - 1], g,
            )
        self._drain()
        self._write_meta(res)

    def build_full_text(self, res: Meta) -> Tuple[str, str]:
        """[6+] 拼接全文，可选清理与标题翻译。返回 (full_text, name_cn)。"""
        full_text = ''
        for i, g in enumerate(res.groups):
            print(f'[6] 生成全文 {i}')
            if g.merge != 1:
                full_text += '\n\n'
            full_text += g.mdcn

        name_cn = ''
        if self.args.clean:
            full_text = clean_md_llm(full_text, self.args)
            name_cn = self.title_agent.run(title=self.name)
            full_text = f'# {name_cn}\n\n{full_text}'

        return full_text, name_cn

    def fix_toc(self, full_text: str, res: Meta) -> str:
        """[7] 修正目录层级。返回修正后的 full_text。"""
        print('[7] 修正目录')
        if res.toc:
            toc = res.toc
        else:
            toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
            toc = self.toc_agent.run(toc_text='\n'.join(toc))
            res.toc = toc
            self._write_meta(res)
        for lvl, title in toc:
            print(f'[7] {lvl} {title}')
            try:
                full_text = re.sub(
                    r'^#+\x20+' + re.escape(title) + '$',
                    f'{lvl} {title}',
                    full_text, flags=re.M,
                )
            except re.error:
                pass
        return full_text

    def write_output(self, full_text: str, name_cn: str) -> None:
        """[8] 写入 md / README / SUMMARY。"""
        print(f'[8] 写入 {self.md_fname}')
        open(self.md_fname, 'w', encoding='utf8') \
            .write(full_text)

        if self.args.mkdir:
            if not name_cn:
                name_cn = self.title_agent.run(
                    title=self.name
                )

            print('[8] 写入 README.md')
            readme = README_TMPL \
                .replace('{name}', self.name) \
                .replace('{name_cn}', name_cn)
            readme_fname = path.join(
                self.pj_dir, 'README.md'
            )
            open(readme_fname, 'w', encoding='utf8') \
                .write(readme)

            print('[8] 写入 SUMMARY.md')
            toc = [
                f'+   [{name_cn}](README.md)',
                f'+   [{name_cn}]({self.slug}.md)',
            ]
            summary_fname = path.join(
                self.pj_dir, 'SUMMARY.md'
            )
            open(summary_fname, 'w', encoding='utf8') \
                .write('\n'.join(toc))

    # ── 主流程 ─────────────────────────────────────

    def run(self) -> None:
        set_openai_props(self.args)
        if not self.args.fname.endswith('.pdf'):
            print('请提供PDF文件')
            return

        os.makedirs(self.pj_dir, exist_ok=True)
        if path.isfile(self.md_fname):
            print('PDF 已处理')
            return

        self.pool = ThreadPoolExecutor(self.args.threads)
        try:
            # 1. 加载 PDF
            doc, pdf_hash = self.load_pdf()

            # 2. 初始化 meta
            res = self.init_meta(doc)

            # 3. OCR 识别
            self.ocr_pages(doc, res)

            # 4. 处理图片
            self.process_images(doc, res, pdf_hash)

            # 5. 分组 + 后处理 + 翻译
            self.group_pages(res)

            # 6. 组间合并
            self.merge_groups(res)

            # 7. 拼接全文
            full_text, name_cn = self.build_full_text(res)

            # 8. 修正目录
            full_text = self.fix_toc(full_text, res)

            # 9. 写入文件
            self.write_output(full_text, name_cn)

            print('[*] 处理完毕')
        finally:
            self.pool.shutdown(wait=False)


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
    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if f.endswith('.pdf')]
    if not fnames:
        print('请提供 PDF 或目录')
        return

    args.threads = max(
        int(args.threads ** 0.5),
        int(args.threads / len(fnames)),
    )
    pool = ThreadPoolExecutor(args.threads)
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

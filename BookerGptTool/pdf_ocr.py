import base64
import gc
import argparse
import logging
import traceback
import copy
import numpy as np
from io import BytesIO
from os import path
import re
import os
import hashlib
import shutil
import yaml
import pymupdf as pymu
import functools
import cv2
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from typing import Any, Callable, Iterator, List, Optional, Tuple
import json
import json_repair
import tqdm
from imgyaso.quant import pngquant
from pydantic import BaseModel, parse_obj_as
from .clean_heading import clean_md_llm
from .pdf_ocr_pmt import *
from .pdf_ocr_models import *
from .resplit_models import *
from .tomd import tomd
from .util import (
    extname,
    to_kebab,
    ext_code_block,
    ext_cont_block,
    render_prompt,
    malloc_trim_linux,
    read_yaml_model,
    write_yaml_model,
    get_md_title,
)
from .openai import (
    call_vlm_retry,
    ask_chatgpt_retry,
    set_openai_props,
)
from .openai import logger as oai_logger
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def _corp_img(img: bytes, bbox: List[float]) -> bytes:
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

# ── Agent 类 ─────────────────────────────────────


from .pdf_ocr_agent import PdfOcrAgent
from .pdf_ocr_agent import PdfOcrSplitAgent



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
        self.split_agent: PdfOcrSplitAgent = PdfOcrSplitAgent(args)

        # ── 线程池基础设施 ──
        self.pool: Optional[ThreadPoolExecutor] = \
            ThreadPoolExecutor(self.args.page_threads)
        self._hdls: List[Future] = []
        self.paths = self.agent.resolve_paths(self.args)



    # ── 线程池工具 ────────────────────────────────

    def _submit(self, fn: Callable, *args: Any, **kwargs: Any) -> None:
        """提交线程池任务。"""
        h = self.pool.submit(fn, *args, **kwargs)
        self._hdls.append(h)

    def _collect_hdls(self, res_callback: Optional[Callable] = None) -> None:
        """等待所有已提交任务完成并清空。
        on_done: 每个子线程完成后在主线程中调用的回调。
        """
        for h in self._hdls:
            r = h.result()
            if res_callback: res_callback(r)
        self._hdls = []

    # ── 主线程写入 ──────────────────────────────────

    # ── 线程池任务 ────────────────────────────────

    def _tr_ocr_page(self, pymu_page: pymu.Page, page: Page) -> None:
        logger.debug(f'[3] 识别页码 {page.pgno + 1}')
        # doc = pymu.open('pdf', BytesIO(pdf_data))
        # pymu_page = doc[page.pgno]
        if self.args.force_ocr or \
           is_scanned_page(pymu_page, self.args.text_thres, self.args.img_thres):
            img = pymu_page \
                .get_pixmap(dpi=self.args.dpi) \
                .pil_tobytes('png')
            img = pngquant(img)
            page.md = self.agent.ocr(img=img)
            del img
        else:
            page.md = tomd(pymu_page.get_text('html'))   
        gc.collect()
        malloc_trim_linux()
        logger.debug(f'[3] 识别页码 {page.pgno + 1} 完成')


    def _tr_proc_img(
        self, img: bytes, page: Page, img_dir: str, pdf_hash: str
    ) -> None:
        logger.debug(f'[4] 处理图像 {page.pgno}')
        md = page.md
        pgno = page.pgno
        img_links = re.findall(r'!\[.*?\]\([\s\S]+?\)', md)
        for j, link in enumerate(img_links):
            m1 = re.search(
                r'bbox=\[(\d+\.\d+),\x20(\d+\.\d+),'
                r'\x20(\d+\.\d+),\x20(\d+\.\d+)\]',
                link,
            )
            m2 = re.search(
                r'data:image/\w+;base64,([\w+=/\n]+)', link
            )
            if not m1 and not m2:
                continue
            if m1:
                bbox = [
                    float(m1.group(1)), float(m1.group(2)),
                    float(m1.group(3)), float(m1.group(4)),
                ]
                img_pt = _corp_img(img, bbox)
            else:
                img_pt = base64.b64decode(
                    m2.group(1).replace('\n', ''))
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
        m_prev = re.search(r'^.+?\Z', prev_line, flags=re.M)
        m_next = re.search(r'\A.+?$', next_line, flags=re.M)
        if m_prev and m_next:
            prev = m_prev.group()
            next = m_next.group()
            group.merge = self.agent.merge(
                prev_line=prev, next_line=next,
            )
        else:
            group.merge = 0

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

    def load_pdf(self) -> Tuple[pymu.Document, str]:
        """[1] 加载 PDF 文件。返回 (doc, pdf_hash)。"""
        logger.info(f'[1] 加载 {self.args.fname}')
        pdf = open(self.args.fname, 'rb').read()
        pdf_hash = hashlib.md5(pdf).hexdigest()
        doc = pymu.open('pdf', BytesIO(pdf))
        return doc, pdf, pdf_hash

    def init_page(self, doc: pymu.Document) -> List[Page]:
        """[2] 加载或初始化 meta.yaml。返回 Meta。"""
        page_fname = self.paths['page_fname']
        logger.info(f'[2] 初始化 {page_fname}')
        pages = read_yaml_model(page_fname, List[Page])
        if pages: return pages
        pages = [Page(pgno=i) for i in range(len(doc))]
        write_yaml_model(page_fname, pages)
        return pages

    def ocr_pages(
        self, doc: pymu.Document, pdf_data: bytes,
        pages: List[Page], 
    ) -> None:
        """[3] VLM 识别每页图像。原地填充 pages.md。"""
        logger.info('[3] 识别图像')
        page_fname = self.paths['page_fname']
        # doc = pymu.open('pdf', BytesIO(pdf_data))
        save_step = max(min(len(pages) // 5, 100), 1)
        for i, pg in enumerate(tqdm.tqdm(pages)):
            if not pg.md:
                pymu_page = doc[pg.pgno]
                self._submit(
                    self._tr_ocr_page, pymu_page, pg, 
                )
                if len(self._hdls) > self.args.page_threads:
                    self._collect_hdls()
            if i % save_step == 0:
                write_yaml_model(page_fname, pages)
        self._collect_hdls()
        write_yaml_model(page_fname, pages)

    def process_images(
        self, doc: pymu.Document, pages: List[Page], 
        pdf_hash: str,
    ) -> None:
        """[4] 裁切并保存页面中的插图。原地填充 pages.md/img_proc。"""
        logger.info('[4] 处理图片')
        page_fname = self.paths['page_fname']
        img_dir = self.paths['img_dir']
        os.makedirs(img_dir, exist_ok=True)
        save_step = max(min(len(pages) // 5, 100), 1)
        for i, pg in enumerate(tqdm.tqdm(pages)):
            if not pg.img_proc:
                img = doc[pg.pgno] \
                    .get_pixmap(dpi=self.args.dpi) \
                    .pil_tobytes('png')
                self._submit(
                    self._tr_proc_img, img, pg, img_dir, pdf_hash,
                )
                if len(self._hdls) > self.args.page_threads:
                   self._collect_hdls() 
            if i % save_step == 0:
                write_yaml_model(page_fname, pages)
        self._collect_hdls()
        write_yaml_model(page_fname, pages)

    def group_pages(self, pages: List[Page]) -> List[Group]:
        """[5] 按长度分组，后处理 + 翻译。填充 res.groups。"""
        logger.info('[5] 处理页间合并')
        group_fname = self.paths['group_fname']
        groups = read_yaml_model(group_fname, List[Group])
        if not groups:
            groups = mkgroups(pages, self.args)
            write_yaml_model(group_fname, groups)

        save_step = max(min(len(groups) // 5, 100), 1)
        for i, g in enumerate(tqdm.tqdm(groups)):
            if not (g.md and g.mdcn):
                self._submit(
                    self._tr_group_page, g,
                )
                if len(self._hdls) > self.args.page_threads:
                    self._collect_hdls()
            if i % save_step == 0:
                write_yaml_model(group_fname, groups)
        self._collect_hdls()
        write_yaml_model(group_fname, groups)
        return groups

    def merge_groups(self, groups: List[Group]) -> None:
        """[6] 判断组间是否需要合并。过滤并原地填充 groups.merge。"""
        logger.info('[6] 处理组间合并')
        group_fname = self.paths['group_fname']
        save_step = max(min(len(groups) // 5, 100), 1)
        for i, g in enumerate(tqdm.tqdm(groups)):
            if not (i == 0 or g.merge != -1):
                self._submit(
                    self._tr_merge_group, groups[i - 1], g,
                )
                if len(self._hdls) > self.args.page_threads:
                    self._collect_hdls()
            if i % save_step == 0:
                write_yaml_model(group_fname, groups)
        self._collect_hdls()
        write_yaml_model(group_fname, groups)

    def build_full_text(self, groups: List[Group]) -> Tuple[str, str]:
        """[6+] 拼接全文，可选清理与标题翻译。返回 (full_text, name_cn)。"""
        name = self.paths['name']
        full_text = ''
        for i, g in enumerate(groups):
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

    def fix_toc(self, full_text: str) -> str:
        """[7] 修正目录层级。返回修正后的 full_text。"""
        logger.info('[7] 修正目录')
        toc_fname = self.paths['toc_fname']
        toc = read_yaml_model(toc_fname, None)
        if not toc:
            toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
            toc = self.agent.fix_toc(toc_text='\n'.join(toc))
            write_yaml_model(toc_fname, toc)
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

    def write_output_split(
        self, chs: List[str], name_cn: str,
    ):
        assert self.args.split
        name = self.paths['name']
        slug = self.paths['slug']
        pj_dir = self.paths['pj_dir']
        summary_fname = self.paths['summary_fname']
        readme_fname = self.paths['readme_fname']

        for i, c in enumerate(chs):
            ch_fname = path.join(pj_dir, slug + '_' + str(i).zfill(l) + '.md')
            logger.debug(f'[8] {ch_fname}')
            open(ch_fname, 'w', encoding='utf8').write(c)

        if self.args.mkdir:
            if not name_cn:
                name_cn = self.agent.trans_title(title=name)
            logger.info('[8] 写入 README.md')
            readme = render_prompt(README_TMPL, name=name, name_cn=name_cn)
            open(readme_fname, 'w', encoding='utf8') \
                .write(readme)

            logger.info('[8] 写入 SUMMARY.md')
            toc = [f'+   [{name_cn}](README.md)']
            l = len(str(len(chs)))
            for i, ch in enumerate(chs):
                title, _ = get_md_title(ch)
                if not title: continue
                ch_fname = slug + '_' + str(i).zfill(l) + '.md'
                toc.append(f'+   [{title}]({ch_fname})')
            summary = '\n'.join(toc)
            open(summary_fname, 'w', encoding='utf8') \
                .write(summary)


    def write_output(
        self, chs: List[str], name_cn: str,
    ) -> None:
        """[8] 写入 md / README / SUMMARY。"""
        assert not self.args.split
        md_fname = self.paths['md_fname']
        logger.info(f'[8] 写入 {md_fname}')
        name = self.paths['name']
        slug = self.paths['slug']
        pj_dir = self.paths['pj_dir']
        summary_fname = self.paths['summary_fname']
        readme_fname = self.paths['readme_fname']

        open(md_fname, 'w', encoding='utf8') \
            .write(chs[0])

        if self.args.mkdir:
            if not name_cn:
                name_cn = self.agent.trans_title(title=name)

            logger.info('[8] 写入 README.md')
            readme = render_prompt(README_TMPL, name=name, name_cn=name_cn)
            open(readme_fname, 'w', encoding='utf8') \
                .write(readme)

            logger.info('[8] 写入 SUMMARY.md')
            toc = [
                f'+   [{name_cn}](README.md)',
                f'+   [{name_cn}]({slug}.md)',
            ]
            open(summary_fname, 'w', encoding='utf8') \
                .write('\n'.join(toc))

    def _tr_ch_split_llm(self, lines, args):
        starts = self.split_agent.split(lines)
        checks = self.split_agent.check_split(lines, starts)
        judges = self.split_agent.judge_split(lines, starts, checks)
        return judges

    def _split_chapters(self, md):
        logger.info('[6] 分章节')
        chs_fname = self.paths['chs_fname']
        chs = read_yaml_model(chs_fname, None)
        if chs: return chs
        if not self.args.split: return [md]
        
        lines = md.split('\n')
        lines = [
            {
                'no': i,
                'line': l[:50] + '...' if len(l) > 50 else l,
            }
            for i, l in enumerate(lines)
        ]
        res: List[JudgeSplitAccResult] = []
        def res_callback(res):
            res += h.result().chapter_starts
        for i in range(0, len(lines), self.args.split_limit - self.args.split_overlap):
            part = lines[i: i+self.args.split_limit]
            h = self.pool.submit(
                self._tr_ch_split_llm,
                part, self.args
            )
            self._hdls.append(h)
            if len(self._hdls) > self.args.page_threads:
                self._collect_hdls(res_callback)

        
        self._collect_hdls(res_callback)
        res.sort(key=lambda x: x.no)

        chs = [[]]
        split_lines = {r.no for r in res}
        for i, l in enumerate(lines):
            if i in split_lines:
                chs.append([])
            chs[-1].append(l)
        chs = ['\n'.join(ch) for ch in chs]

        write_yaml_model(chs_fname, chs)
        return chs

    # ── 主流程 ─────────────────────────────────────

    def run(self) -> None:
        if not self.args.fname.endswith('.pdf'):
            logger.fatal('请提供PDF文件')
            return

        pj_dir = self.paths['pj_dir']
        md_fname = self.paths['md_fname']
        meta_dir = self.paths['meta_dir']

        os.makedirs(pj_dir, exist_ok=True)
        os.makedirs(meta_dir, exist_ok=True)
        if path.isfile(md_fname):
            logger.warn('PDF 已处理')
            return

        # 1. 加载 PDF
        doc, pdf_data, pdf_hash = self.load_pdf()
        # 2. 初始化 meta
        pages = self.init_page(doc)
        # 3. OCR 识别
        self.ocr_pages(doc, pdf_data, pages)
        # 4. 处理图片
        self.process_images(doc, pages, pdf_hash)
        # 5. 分组 + 后处理 + 翻译
        groups = self.group_pages(pages)
        # 6. 组间合并
        self.merge_groups(groups)
        # 7. 拼接全文
        full_text, name_cn = self.build_full_text(groups)
        # 8. 修正目录
        full_text = self.fix_toc(full_text)
        chs = self._split_chapters(md=full_text)
        # 9. 写入文件
        self.write_output_split(chs, name_cn) \
            if self.args.split else \
            self.write_output(chs, name_cn)
        del doc, pdf_data, pages, groups
        gc.collect()
        malloc_trim_linux()
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
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
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
        args.func = None
        h = pool.submit(pdf_ocr_file_safe, args)
        hdls.append(h)
    for h in hdls:
        h.result()


def pdf_ocr_file_safe(args: argparse.Namespace) -> None:
    try:
        PDFOcrOrchestrator(args).run()
    except KeyboardInterrupt:
        raise
    except:
        logger.warn(traceback.format_exc())

def is_scanned_page(page: pymu.Page, text_threshold=20, image_coverage_threshold=0.8):
    """
    综合判断一个PDF页面是否为扫描件
    """
    # 1. 文本检查
    text = page.get_text().strip()
    if len(text) >= text_threshold:
        # 如果文本量足够，基本可以判定为非扫描件
        return False

    # 2. 图像覆盖检查
    page_rect = page.rect
    page_area = abs(page_rect)
    total_image_area = 0

    # 获取页面中所有图片的列表
    image_list = page.get_images(full=True)
    for img in image_list:
        # 获取该图片在页面上的显示区域
        bbox = page.get_image_bbox(img)
        # 计算当前图片与页面的交集面积
        intersection = bbox & page_rect
        total_image_area += abs(intersection)

    # 计算图像覆盖比例
    coverage_ratio = total_image_area / page_area if page_area > 0 else 0
    
    # 如果图像覆盖面积超过阈值，则判定为扫描件
    return coverage_ratio >= image_coverage_threshold


def reg_subparser(subparsers):
    pdf_ocr_parser = subparsers.add_parser("pdf-ocr", help="PDF OCR，生成可处理的文本/Markdown")
    pdf_ocr_parser.add_argument("fname", help="PDF 文件名")
    pdf_ocr_parser.add_argument("--dpi", type=int, default=150, help="DPI")
    pdf_ocr_parser.add_argument("--trans", action='store_true', help="是否翻译")
    pdf_ocr_parser.add_argument("--clean", action='store_true', help="是否清理标题")
    pdf_ocr_parser.add_argument("--split", action='store_true', help="是否划分章节")
    pdf_ocr_parser.add_argument("-sl", "--split-limit", type=int, default=3000, help="划分章节的分块大小上限")
    pdf_ocr_parser.add_argument("-so", "--split-overlap", type=int, default=50, help="划分章节的分块重叠大小")
    pdf_ocr_parser.add_argument("-md", "--mkdir", action='store_true', help="是否生成单个目录")
    pdf_ocr_parser.add_argument("-ft", "--file-threads", type=int, default=1, help="文件线程数")
    pdf_ocr_parser.add_argument("-pt", "--page-threads", type=int, default=8, help="页面线程数")
    pdf_ocr_parser.add_argument("-l", "--limit", type=int, default=8000, help="分组文本上限")
    pdf_ocr_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    pdf_ocr_parser.add_argument("-tt", "--text-thres", type=int, default=20, help="")
    pdf_ocr_parser.add_argument("-it", "--img-thres", type=float, default=0.8, help="")
    pdf_ocr_parser.add_argument("-fo", "--force-ocr", action='store_true', help="")
    pdf_ocr_parser.set_defaults(func=pdf_ocr)

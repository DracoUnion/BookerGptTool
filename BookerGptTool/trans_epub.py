import gc
import tqdm
import copy
import traceback
import os
import json
import logging
from os import path
import re
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed, ProcessPoolExecutor
from imgyaso.quant import pngquant
from .trans_epub_pmt import *
from .tomd import tomd
from .util import (
    to_kebab,
    read_zip,
    is_pic,
    get_md_title,
    epub2html_pandoc,
    group_chunks,
    split_md_lines,
    ext_cont_block,
    ext_code_block,
    render_prompt,
    malloc_trim_linux,
    read_yaml_model,
    write_yaml_model
)
from .openai import logger as oai_logger
from .openai import set_openai_props, ask_chatgpt_retry
from .fmt import fmt_zh, fmt_publisher
from .clean_heading import clean_md_llm
from .trans_epub_models import *
from .resplit_models import *

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def trunc_text(text, limit=50):
    return (
        text[:limit] + '...'
        if len(text) > limit
        else text
    )



from .trans_epub_agent import EpubTranslatorAgent
from .trans_epub_agent import EpubTranslatorSplitAgent



class TransEpubDispatcher:
    def __init__(self, args):
        self.args = args
        self.agent = EpubTranslatorAgent(args)
        self.split_agent = EpubTranslatorSplitAgent(args)

        self.pool = ThreadPoolExecutor(self.args.page_threads)
        self.hdls = []
        self.paths = self.agent.resolve_paths(args)

    def run(self):
        args = self.args
        logger.info(args)
        if not args.fname.endswith('.epub'):
            logger.fatal('请提供EPUB文件')
            return

        os.makedirs(self.paths['proj_dir'], exist_ok=True)
        md_fnames = [
            f for f in os.listdir(self.paths['proj_dir'])
            if f.endswith('.md') and
               f != 'README.md' and
               f != 'SUMMRY.md'
        ]
        if md_fnames:
            logger.warn('已处理')
            return

        meta = self._init_meta()
        if meta is None:
            return
        html = self._convert_html()
        md = self._convert_md(html=html)
        self._export_images()
        chunks = self._format_translate(md=md)
        md = self._fix_toc(chunks, meta)
        chs = self._split_chapters(md=md)
        self._write_chapters(chs=chs)
        self._gen_readme(meta=meta)
        self._gen_summary(chs=chs, meta=meta)
        del html, md, chunks, chs
        gc.collect()
        malloc_trim_linux()
        logger.info('[*] 完成')

    def _init_meta(self):
        logger.info('[1] 初始化元数据')
        name=self.paths['name']
        slug=self.paths['slug']
        meta_dir=self.paths['meta_dir']
        meta_fname=self.paths['meta_fname']
        os.makedirs(meta_dir, exist_ok=True)
        meta = read_yaml_model(meta_fname, Meta)
        if not meta:
            name_cn = self.agent.translate_title(name)
            meta = Meta(name=name, slug=slug, name_cn=name_cn)
            open(meta_fname, 'w', encoding='utf8').write(yaml.safe_dump(meta.dict()))
        return meta

    def _convert_html(self):
        logger.info('[2] 转换 html 和 md')
        html_fname = self.paths['html_fname']
        if path.isfile(html_fname) and \
           path.getsize(html_fname) != 0:
            return open(html_fname, encoding='utf8').read()
        epub = open(self.args.fname, 'rb').read()
        html = epub2html_pandoc(epub)
        html = fmt_publisher(html, self.args.fmt_mode)
        open(html_fname, 'w', encoding='utf8').write(html)
        return html

    def _convert_md(self, html):
        md_fname = self.paths['md_fname'] 
        if path.isfile(md_fname) and \
           path.getsize(md_fname) != 0:
            return open(md_fname, encoding='utf8').read()
        md = tomd(html)
        open(md_fname, 'w', encoding='utf8').write(md)
        return md

    def _export_images(self):
        logger.info('[3] 导出图像')
        img_dir = self.paths['img_dir']
        os.makedirs(img_dir, exist_ok=True)
        fdict = read_zip(self.args.fname)
        for iname, data in fdict.items():
            if not is_pic(iname):
                continue
            logger.debug(f'[3] {iname}')
            ifname = path.join(img_dir, path.basename(iname))
            if path.isfile(ifname):
                continue
            data = pngquant(data)
            open(ifname, 'wb').write(data)

    def _tr_fmt_trans(self, chunk: Chunk):
        logger.debug(f'[4] 处理分块')
        if not chunk.fmt:
            chunk.fmt = self.agent.format_text(chunk.raw)
        if not chunk.trans:
            chunk.trans = fmt_zh(self.agent.translate_body(chunk.fmt))


    def _collect_hdls(self, res_callback:Optional[Callable]=None):
        for h in self.hdls:
            r = h.result()
            if res_callback: res_callback(r)
        self.hdls = []

    def _format_translate(self, md):
        logger.info('[4] 排版和翻译')
        chunk_fname = self.paths['chunk_fname']
        chunks = read_yaml_model(chunk_fname, List[Chunk])
        if not chunks:
            groups = group_chunks(split_md_lines(md))
            chunks = [Chunk(raw=c) for c in groups]
            write_yaml_model(chunk_fname, chunks)

        save_step = max(min(len(chunks) // 5, 100), 1)
        for i, c in enumerate(tqdm.tqdm(chunks)):
            if not (c.fmt and c.trans):
                h = self.pool.submit(self._tr_fmt_trans, c)
                self.hdls.append(h)
                if len(self.hdls) > self.args.page_threads:
                    self._collect_hdls()
            if i % save_step == 0:
                write_yaml_model(chunk_fname, chunks)
        self._collect_hdls()
        write_yaml_model(chunk_fname, chunks)
        return chunks

    def _fix_toc(self, chunks, meta):
        logger.info('[5] 修正目录')
        meta_fname = self.paths['meta_fname']
        md = '\n\n'.join(c.trans for c in chunks)
        if self.args.clean:
            name_cn = meta.name_cn
            md = clean_md_llm(md, self.args)
            md = f'# {name_cn}\n\n{md}'
        if meta.toc:
            toc = meta.toc
        else:
            toc = re.findall(r'^#+\x20+.+?$', md, re.M)
            ans = self.agent.fix_toc('\n'.join(toc))
            toc = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
            meta.toc = toc
            write_yaml_model(meta_fname, meta)
        for lvl, title in toc:
            logger.debug(f'[7] {lvl} {title}')
            try:
                md = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', md, flags=re.M)
            except re.error:
                pass
        return md

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
        def res_callback(r):
            res += r.chapter_starts
        for i in range(0, len(lines), self.args.split_limit - self.args.split_overlap):
            part = lines[i: i+self.args.split_limit]
            h = self.pool.submit(
                self._tr_ch_split_llm,
                part, self.args
            )
            self.hdls.append(h)
            if len(self.hdls) > self.args.page_threads:
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

    def _write_chapters(self, chs):
        proj_dir = self.paths['proj_dir']
        slug = self.paths['slug']
        l = len(str(len(chs)))
        for i, c in enumerate(chs):
            ch_fname = path.join(proj_dir, slug + '_' + str(i).zfill(l) + '.md')
            logger.debug(f'[6] {ch_fname}')
            open(ch_fname, 'w', encoding='utf8').write(c)

    def _gen_readme(self, meta):
        logger.info('[7] 生成 readme')
        name = self.paths['name']
        readme_fname = self.paths['readme_fname']
        readme = render_prompt(README_TMPL, name=name, name_cn=meta.name_cn)
        open(readme_fname, 'w', encoding='utf8').write(readme)

    def _gen_summary(self, chs, meta):
        logger.info('[8] 生成 summary')
        slug = self.paths['slug']
        summary_fname = self.paths['summary_fname']
        l = len(str(len(chs)))
        toc = [f'+   [{meta.name_cn}](README.md)']
        for i, ch in enumerate(chs):
            title, _ = get_md_title(ch)
            if not title: continue
            ch_fname = slug + '_' + str(i).zfill(l) + '.md'
            toc.append(f'+   [{title}]({ch_fname})')
        summary = '\n'.join(toc)
        open(summary_fname, 'w', encoding='utf8').write(summary)


def trans_epub(args):
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
    fnames = [f for f in fnames if f.endswith('.epub')]
    if not fnames:
        logger.info('请提供 EPUB 或目录')
        return

    pool = ThreadPoolExecutor(args.file_threads) 
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        args.func = None
        h = pool.submit(trans_epub_file_safe, args)
        hdls.append(h)
    for h in as_completed(hdls):
        h.result()

def trans_epub_file_safe(args):
    try:
        TransEpubDispatcher(args).run()
    except KeyboardInterrupt:
        raise
    except:
        logger.warn(traceback.format_exc())


def reg_subparser(subparsers):
    trans_epub_parser = subparsers.add_parser("trans-epub", help="翻译 EPUB")
    trans_epub_parser.add_argument("fname", help="EPUB 文件名")
    trans_epub_parser.add_argument("-ft", "--file-threads", type=int, default=1, help="文件线程数")
    trans_epub_parser.add_argument("-pt", "--page-threads", type=int, default=8, help="页面线程数")
    trans_epub_parser.add_argument("-l", "--limit", type=int, default=8000, help="分块大小上限")
    trans_epub_parser.add_argument("-m", "--fmt-mode", default='none', help="格式化模式")
    trans_epub_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    trans_epub_parser.add_argument("--split", action='store_true', help="是否拆分中文")
    trans_epub_parser.add_argument("--clean", action='store_true', help="是否清理标题")
    trans_epub_parser.add_argument("-sl", "--split-limit", type=int, default=3000, help="划分章节的分块大小上限")
    trans_epub_parser.add_argument("-so", "--split-overlap", type=int, default=50, help="划分章节的分块重叠大小")
    trans_epub_parser.set_defaults(func=trans_epub)

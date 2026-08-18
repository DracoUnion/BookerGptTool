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
    set_openai_props, 
    to_kebab, 
    read_zip, 
    is_pic, 
    get_md_title, 
    epub2html_pandoc, 
    group_chunks, 
    split_md_lines, 
    ask_chatgpt_retry, 
    ext_cont_block, 
    ext_code_block,
    logger as util_logger,
    malloc_trim_linux,
)
from .fmt import fmt_zh, fmt_publisher
from .clean_heading import clean_md_llm
from .trans_epub_models import *

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



class EpubTranslatorAgent:
    def __init__(self, args):
        self.args = args
        set_openai_props(args)

    def translate_title(self, text: str) -> str:
        ques = TRANS_TITLE_PMT.replace('{text}', text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def format_text(self, text: str) -> str:
        ques = FMT_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def translate_body(self, text: str) -> str:
        ques = TRANS_BODY_PMT.replace('{text}', text)
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_toc(self, text: str) -> str:
        ques = TOC_PMT.replace('{text}', text)
        return ask_chatgpt_retry(ques, self.args.model, self.args)

    def extract_chapter_toc(self, titles: list) -> List[TocExtResult]:
        ques = TOC_EXT_PMT.replace('{titles}', json.dumps(titles, ensure_ascii=False))
        parse_output = lambda s: parse_obj_as(
            List[TocExtResult],
            json.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(
            ques, self.args.model, self.args,
            parse_output=parse_output,
        )


class TransEpubDispatcher:
    def __init__(self, args):
        self.args = args
        self.agent = EpubTranslatorAgent(args)

        self.pool = ProcessPoolExecutor(self.args.page_threads) \
            if self.args.multi_processes else \
            ThreadPoolExecutor(self.args.page_threads)
        self.hdls = []

    def _resolve_paths(self, args):
        name = path.basename(args.fname)[:-5]
        slug = to_kebab(name)
        proj_dir = path.join(path.dirname(args.fname), slug)
        meta_dir = path.join(proj_dir, 'asset')
        img_dir = path.join(proj_dir, 'img')
        return dict(
            name=name,
            slug=slug,
            proj_dir=proj_dir,
            meta_dir=meta_dir,
            img_dir=img_dir,
            meta_fname=path.join(meta_dir, 'meta.yaml'),
            html_fname=path.join(meta_dir, 'all.html'),
            md_fname=path.join(meta_dir, 'all.md'),
            chunk_fname=path.join(meta_dir, 'chunks.yaml'),
            chs_fname=path.join(meta_dir, 'chs.yaml'),
            readme_fname=path.join(proj_dir, 'README.md'),
            summary_fname=path.join(proj_dir, 'SUMMARY.md'),
        )

    def run(self):
        args = self.args
        logger.info(args)
        if not args.fname.endswith('.epub'):
            logger.fatal('请提供EPUB文件')
            return

        p = self._resolve_paths(args)
        os.makedirs(p['proj_dir'], exist_ok=True)
        md_fnames = [
            f for f in os.listdir(p['proj_dir'])
            if f.endswith('.md') and
               f != 'README.md' and
               f != 'SUMMRY.md'
        ]
        if md_fnames:
            logger.warn('已处理')
            return

        meta = self._init_meta(
            name=p['name'], slug=p['slug'],
            meta_dir=p['meta_dir'], meta_fname=p['meta_fname'],
        )
        if meta is None:
            return
        html = self._convert_html(html_fname=p['html_fname'])
        md = self._convert_md(md_fname=p['md_fname'], html=html)
        self._export_images(img_dir=p['img_dir'])
        chunks = self._format_translate(chunk_fname=p['chunk_fname'], md=md)
        md = self._fix_toc(chunks, meta, meta_fname=p['meta_fname'])
        chs = self._split_chapters(chs_fname=p['chs_fname'], md=md)
        self._write_chapters(proj_dir=p['proj_dir'], slug=p['slug'], chs=chs)
        self._gen_readme(name=p['name'], readme_fname=p['readme_fname'], meta=meta)
        self._gen_summary(slug=p['slug'], summary_fname=p['summary_fname'], chs=chs, meta=meta)
        del html, md, chunks, chs
        gc.collect()
        malloc_trim_linux()
        logger.info('[*] 完成')

    def _init_meta(self, name, slug, meta_dir, meta_fname):
        args = self.args
        logger.info('[1] 初始化元数据')
        os.makedirs(meta_dir, exist_ok=True)
        if path.isfile(meta_fname) and \
           path.getsize(meta_fname) != 0:
            meta = yaml.safe_load(open(meta_fname, encoding='utf8').read())
            meta = Meta(**meta)
        else:
            name_cn = self.agent.translate_title(name)
            meta = Meta(name=name, slug=slug, name_cn=name_cn)
            open(meta_fname, 'w', encoding='utf8').write(yaml.safe_dump(meta.dict()))
        return meta

    def _convert_html(self, html_fname):
        logger.info('[2] 转换 html 和 md')
        if path.isfile(html_fname) and \
           path.getsize(html_fname) != 0:
            return open(html_fname, encoding='utf8').read()
        epub = open(self.args.fname, 'rb').read()
        html = epub2html_pandoc(epub)
        html = fmt_publisher(html, self.args.fmt_mode)
        open(html_fname, 'w', encoding='utf8').write(html)
        return html

    def _convert_md(self, md_fname, html):
        if path.isfile(md_fname) and \
           path.getsize(md_fname) != 0:
            return open(md_fname, encoding='utf8').read()
        md = tomd(html)
        open(md_fname, 'w', encoding='utf8').write(md)
        return md

    def _export_images(self, img_dir):
        logger.info('[3] 导出图像')
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

    def _write_yaml(self, fname, obj):
        if isinstance(obj, BaseModel):
            obj = obj.dict()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        with open(fname, 'w', encoding='utf8') as f:
            f.write(yaml.safe_dump(obj, allow_unicode=True))
            f.flush()

    def _collect_hdls(self, write_callback:Optional[Callable]=None, res_callback:Optional[Callable]=None):
        save_step = max(min(len(self.hdls) // 5, 100), 1)
        with tqdm.tqdm(total=len(self.hdls)) as pbar:
            for i, h in enumerate(as_completed(self.hdls)):
                r = h.result()
                if res_callback: res_callback(r)
                if write_callback and \
                  (i % save_step == 0 or i == len(self.hdls) - 1):
                    write_callback()
            pbar.update(1)
        self.hdls = []

    def _format_translate(self, chunk_fname, md):
        logger.info('[4] 排版和翻译')
        if path.isfile(chunk_fname) and \
           path.getsize(chunk_fname) != 0:
            chunks = yaml.safe_load(open(chunk_fname, encoding='utf8').read())
            chunks = parse_obj_as(List[Chunk], chunks)
        else:
            groups = group_chunks(split_md_lines(md))
            chunks = [Chunk(raw=c) for c in groups]
            self._write_yaml(chunk_fname, chunks)

        for i, c in enumerate(tqdm.tqdm(chunks)):
            if c.fmt and c.trans:
                continue
            h = self.pool.submit(_mp_fmt_trans, self.args, i, c) \
                if self.args.multi_processes else \
                self.pool.submit(self._tr_fmt_trans, c)
            self.hdls.append(h)

        def res_callback(tpl): chunks[tpl[0]] = tpl[1] 
        self._collect_hdls(
            lambda: self._write_yaml(chunk_fname, chunks),
            res_callback if self.args.multi_processes else None
        )
        return chunks

    def _fix_toc(self, chunks, meta, meta_fname):
        logger.info('[5] 修正目录')
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
            self._write_yaml(meta_fname, meta)
        for lvl, title in toc:
            logger.debug(f'[7] {lvl} {title}')
            try:
                md = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', md, flags=re.M)
            except re.error:
                pass
        return md

    def _split_chs(self, md):
        lines = md.split('\n')
        titles = []
        in_code = False
        for i, l in enumerate(lines):
            if '```' in l:
                in_code = not in_code
            elif not in_code and re.search(r'^#+ ', l):
                titles.append({
                    'no': i,
                    'title': re.sub(r'^#+ ', '', l),
                    'before': [],
                    'after': [],
                })
        for it in titles:
            st = max(0, it['no'] - 10)
            ed = min(len(lines) - 1, it['no'] + 10)
            for i in range(st, it['no']):
                it['before'].append(trunc_text(lines[i]))
            for i in range(it['no'] + 1, ed + 1):
                it['after'].append(trunc_text(lines[i]))

        res: List[TocExtResult] = self.agent.extract_chapter_toc(titles)
        title_nos = set(it.no for it in res if it.no != 0)
        for i, l in enumerate(lines):
            if i in title_nos:
                lines[i] = '[split/]' + l
        return '\n'.join(lines).split('[split/]')

    def _split_chapters(self, chs_fname, md):
        logger.info('[6] 分章节')
        if path.isfile(chs_fname) and \
           path.getsize(chs_fname) != 0:
            chs = yaml.safe_load(open(chs_fname, encoding='utf8').read())
        else:
            chs = self._split_chs(md) if self.args.split else [md]
            self._write_yaml(chs_fname, chs)
        return chs

    def _write_chapters(self, proj_dir, slug, chs):
        l = len(str(len(chs)))
        for i, c in enumerate(chs):
            ch_fname = path.join(proj_dir, slug + '_' + str(i).zfill(l) + '.md')
            logger.debug(f'[5] {ch_fname}')
            open(ch_fname, 'w', encoding='utf8').write(c)

    def _gen_readme(self, name, readme_fname, meta):
        logger.info('[7] 生成 readme')
        readme = README_TMPL.replace('{name}', name).replace('{name_cn}', meta.name_cn)
        open(readme_fname, 'w', encoding='utf8').write(readme)

    def _gen_summary(self, slug, summary_fname, chs, meta):
        logger.info('[8] 生成 summary')
        l = len(str(len(chs)))
        toc = [f'+   [{meta.name_cn}](README.md)']
        for i, ch in enumerate(chs):
            title, _ = get_md_title(ch)
            if not title: continue
            ch_fname = slug + '_' + str(i).zfill(l) + '.md'
            toc.append(f'+   [{title}]({ch_fname})')
        summary = '\n'.join(toc)
        open(summary_fname, 'w', encoding='utf8').write(summary)

def _mp_fmt_trans(args, idx: int, chunk: Chunk) -> Tuple[int, Chunk]:
    logger.debug(f'[4] 处理分块')
    agent = EpubTranslatorAgent(args)
    if not chunk.fmt:
        chunk.fmt = agent.format_text(chunk.raw)
    if not chunk.trans:
        chunk.trans = fmt_zh(agent.translate_body(chunk.fmt))
    return idx, chunk


def trans_epub(args):
    if args.debug:
        logger.setLevel(logging.DEBUG)
        util_logger.setLevel(logging.DEBUG)
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

    pool = ProcessPoolExecutor(args.file_threads) \
        if args.multi_processes else \
        ThreadPoolExecutor(args.file_threads) 
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

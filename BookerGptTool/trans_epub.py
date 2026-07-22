import copy
import traceback
import os
from os import path
import re
import yaml
import functools
from concurrent.futures import ThreadPoolExecutor
from imgyaso.quant import pngquant
from .trans_epub_pmt import *
from .util import set_openai_props, to_kebab, read_zip, is_pic, tomd, get_md_title, epub2html_pandoc, group_chunks, split_md_lines
from .fmt import fmt_zh, fmt_publisher
from .clean_heading import clean_md_llm
from .trans_epub_models import *
from .trans_epub_agent import EpubTranslatorAgent


def fix_toc(full_text, meta: Meta, agent: EpubTranslatorAgent, write_callback):
    if meta.toc:
        toc = meta.toc
    else:
        toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
        ans = agent.fix_toc('\n'.join(toc))
        toc = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
        meta.toc = toc
        write_callback()
    for lvl, title in toc:
        print(f'[7] {lvl} {title}')
        try:
            full_text = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', full_text, flags=re.M)
        except re.error:
            pass
    return full_text

def trunc_text(text, limit=50):
    return (
        text[:limit] + '...'
        if len(text) > limit
        else text
    )

def split_chs(md, agent: EpubTranslatorAgent):
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

    res: List[TocExtResult] = agent.extract_chapter_toc(titles)
    title_nos = set(it.no for it in res if it.no != 0)
    for i, l in enumerate(lines):
        if i in title_nos:
            lines[i] = '[split/]' + l
    return '\n'.join(lines).split('[split/]')



class TransEpubDispatcher:
    def __init__(self, args):
        self.args = args
        self.agent = EpubTranslatorAgent(args)

    def run(self):
        args = self.args
        print(args)
        set_openai_props(args)
        if not args.fname.endswith('.epub'):
            print('请提供EPUB文件')
            return

        meta, meta_fname, name, slug, proj_dir, meta_dir = self._init_meta()
        if meta is None:
            return
        html = self._convert_html(meta_dir)
        md = self._convert_md(meta_dir, html)
        self._export_images(proj_dir)
        chunks = self._format_translate(meta_dir, md)
        md = self._fix_toc(chunks, meta, meta_fname)
        chs = self._split_chapters(meta_dir, md)
        self._write_chapters(proj_dir, chs, slug)
        self._gen_readme(proj_dir, name, meta)
        self._gen_summary(proj_dir, chs, slug, meta)
        print('[*] 完成')

    def _init_meta(self):
        args = self.args
        print('[1] 初始化元数据')
        name = path.basename(args.fname)[:-5]
        slug = to_kebab(name)
        proj_dir = path.join(path.dirname(args.fname), slug)
        os.makedirs(proj_dir, exist_ok=True)
        md_fnames = [
            f for f in os.listdir(proj_dir)
            if f.endswith('.md') and
               f != 'README.md' and
               f != 'SUMMRY.md'
        ]
        if md_fnames:
            print('已处理')
            return None, None, None, None, None, None

        meta_dir = path.join(proj_dir, 'asset')
        os.makedirs(meta_dir, exist_ok=True)
        meta_fname = path.join(meta_dir, 'meta.yaml')
        if path.isfile(meta_fname) and \
           path.getsize(meta_fname) != 0:
            meta = yaml.safe_load(open(meta_fname, encoding='utf8').read())
            meta = Meta(**meta)
        else:
            name_cn = self.agent.translate_title(name)
            meta = Meta(name=name, slug=slug, name_cn=name_cn)
            open(meta_fname, 'w', encoding='utf8').write(yaml.safe_dump(meta.dict()))
        return meta, meta_fname, name, slug, proj_dir, meta_dir

    def _convert_html(self, meta_dir):
        print('[2] 转换 html 和 md')
        html_fname = path.join(meta_dir, 'all.html')
        if path.isfile(html_fname) and \
           path.getsize(html_fname) != 0:
            return open(html_fname, encoding='utf8').read()
        epub = open(self.args.fname, 'rb').read()
        html = epub2html_pandoc(epub)
        html = fmt_publisher(html, self.args.fmt_mode)
        open(html_fname, 'w', encoding='utf8').write(html)
        return html

    def _convert_md(self, meta_dir, html):
        md_fname = path.join(meta_dir, 'all.md')
        if path.isfile(md_fname) and \
           path.getsize(md_fname) != 0:
            return open(md_fname, encoding='utf8').read()
        md = tomd(html)
        open(md_fname, 'w', encoding='utf8').write(md)
        return md

    def _export_images(self, proj_dir):
        print('[3] 导出图像')
        img_dir = path.join(proj_dir, 'img')
        os.makedirs(img_dir, exist_ok=True)
        fdict = read_zip(self.args.fname)
        for iname, data in fdict.items():
            if not is_pic(iname):
                continue
            print(f'[3] {iname}')
            ifname = path.join(img_dir, path.basename(iname))
            if path.isfile(ifname):
                continue
            data = pngquant(data)
            open(ifname, 'wb').write(data)

    def _tr_fmt_trans(self, chunk: Chunk):
        print(f'[4] 处理分块')
        if not chunk.fmt:
            chunk.fmt = self.agent.format_text(chunk.raw)
        if not chunk.trans:
            chunk.trans = fmt_zh(self.agent.translate_body(chunk.fmt))

    def _write_yaml(self, fname, res):
        with open(fname, 'w', encoding='utf8') as f:
            obj = (
                [r.dict() for r in res]
                if isinstance(res, list)
                else res.dict()
            )
            f.write(yaml.safe_dump(obj, allow_unicode=True))

    def _format_translate(self, meta_dir, md):
        print('[4] 排版和翻译')
        chunk_fname = path.join(meta_dir, 'chunks.yaml')
        if path.isfile(chunk_fname) and \
           path.getsize(chunk_fname) != 0:
            chunks = yaml.safe_load(open(chunk_fname, encoding='utf8').read())
            chunks = parse_obj_as(List[Chunk], chunks)
        else:
            groups = group_chunks(split_md_lines(md))
            chunks = [Chunk(raw=c) for c in groups]
            open(chunk_fname, 'w',  encoding='utf8') \
                .write(yaml.safe_dump([c.dict() for c in chunks], allow_unicode=True))

        pool = ThreadPoolExecutor(self.args.threads)
        hdls = []

        for c in chunks:
            if c.fmt and c.trans:
                continue
            h = pool.submit(
                    self._tr_fmt_trans,
                    c,
                )
            hdls.append(h)

        for h in hdls:
            h.result()
        self._write_yaml(chunk_fname, chunks)
        return chunks

    def _fix_toc(self, chunks, meta, meta_fname):
        print('[5] 修正目录')
        md = '\n\n'.join(c.trans for c in chunks)
        if self.args.clean:
            name_cn = meta.name_cn
            md = clean_md_llm(md, self.args)
            md = f'# {name_cn}\n\n{md}'
        md = fix_toc(
            md, meta, self.agent,
            functools.partial(self._write_yaml, meta_fname, meta),
        )
        return md

    def _split_chapters(self, meta_dir, md):
        print('[6] 分章节')
        chs_fname = path.join(meta_dir, 'chs.yaml')
        if path.isfile(chs_fname) and \
           path.getsize(chs_fname) != 0:
            chs = yaml.safe_load(open(chs_fname, encoding='utf8').read())
        else:
            chs = split_chs(md, self.agent) if self.args.split else [md]
            open(chs_fname, 'w', encoding='utf8').write(yaml.safe_dump(chs, allow_unicode=True))

        return chs

    def _write_chapters(self, proj_dir, chs, slug):
        l = len(str(len(chs)))
        for i, c in enumerate(chs):
            ch_fname = path.join(proj_dir, slug + '_' + str(i).zfill(l) + '.md')
            print(f'[5] {ch_fname}')
            open(ch_fname, 'w', encoding='utf8').write(c)

    def _gen_readme(self, proj_dir, name, meta):
        print('[7] 生成 readme')
        readme = README_TMPL.replace('{name}', name).replace('{name_cn}', meta.name_cn)
        readme_fname = path.join(proj_dir, 'README.md')
        open(readme_fname, 'w', encoding='utf8').write(readme)

    def _gen_summary(self, proj_dir, chs, slug, meta):
        print('[8] 生成 summary')
        l = len(str(len(chs)))
        toc = [f'+   [{meta.name_cn}](README.md)']
        for i, ch in enumerate(chs):
            title, _ = get_md_title(ch)
            if not title: continue
            ch_fname = slug + '_' + str(i).zfill(l) + '.md'
            toc.append(f'+   [{title}]({ch_fname})')
        summary = '\n'.join(toc)
        summary_fname = path.join(proj_dir, 'SUMMARY.md')
        open(summary_fname, 'w', encoding='utf8').write(summary)


def trans_epub(args):
    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if f.endswith('.epub')]
    if not fnames:
        print('请提供 EPUB 或目录')
        return

    args.threads = max(
        int(args.threads ** 0.5),
        int(args.threads / len(fnames))
    )
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(trans_epub_file_safe, args)
        hdls.append(h)
    for h in hdls:
        h.result()

def trans_epub_file_safe(args):
    try:
        TransEpubDispatcher(args).run()
    except:
        traceback.print_exc()

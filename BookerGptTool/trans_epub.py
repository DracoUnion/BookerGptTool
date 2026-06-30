import copy
import traceback
import os
from io import BytesIO
from os import path
import re
import yaml
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import json_repair as json
from imgyaso.quant import pngquant
from .trans_epub_pmt import *
from .util import ask_chatgpt_retry, set_openai_props, to_kebab, read_zip, is_pic, tomd, get_md_title, epub2html_pandoc, group_chunks, split_md_lines, ext_cont_block
from .fmt import fmt_zh, fmt_publisher
from .md2skill_chunker import chunk_markdown
from .clean_heading import clean_md_llm
from .trans_epub_models import *


def fix_toc(full_text, meta: Meta, args, write_callback):
    if meta.toc:
        toc = meta.toc
    else:
        toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
        ques = TOC_PMT.replace('{text}', '\n'.join(toc))
        ans =  ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
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

def split_chs(md):
    lines = md.split('\n')
    in_code = False
    for i, l in enumerate(lines):
        if '```' in l:
            in_code = not in_code
        elif not in_code and l.startswith('# ') and i != 0:
            lines[i] = '[split/]' + l
    return '\n'.join(lines).split('[split/]')

def tr_fmt_trans(chunks: List[Chunk], idx, args, write_callback):
    print(f'[4] 处理分块 {idx+1}')
    raw = chunks[idx].raw
    fmt = chunks[idx].fmt
    trans = chunks[idx].trans
    if not fmt:
        ques = FMT_PMT.replace('{text}', raw)
        fmt = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=ext_cont_block,
        )
        chunks[idx].fmt = fmt
        write_callback()
    if not trans:
        ques = TRANS_BODY_PMT.replace('{text}', fmt)
        trans = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=ext_cont_block,
        )
        chunks[idx].trans = fmt_zh(trans)
        write_callback()

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
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []
    for h in hdls: 
        h.result()

def trans_epub_file_safe(args):
    try:
        trans_epub_file(args)
    except:
        traceback.print_exc()

def trans_epub_file(args):
    print(args)
    set_openai_props(args)
    if not args.fname.endswith('.epub'):
        print('请提供EPUB文件')
        return
    
    print('[1] 初始化元数据')
    name = path.basename(args.fname)[:-5]
    slug = to_kebab(name)
    proj_dir = path.join(path.dirname(args.fname), slug)
    os.makedirs(proj_dir, exist_ok=True)
    meta_dir = path.join(proj_dir, 'asset')
    os.makedirs(meta_dir, exist_ok=True)
    meta_fname = path.join(meta_dir, 'meta.yaml')
    if path.isfile(meta_fname):
        meta = yaml.safe_load(open(meta_fname, encoding='utf8').read())
        meta = Meta(**meta)
    else:
        ques = TRANS_TITLE_PMT.replace('{text}', name)
        name_cn = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        meta = Meta(name=name, slug=slug, name_cn=name_cn)
        open(meta_fname, 'w', encoding='utf8').write(yaml.safe_dump(meta.dict()))

    print('[2] 转换 html 和 md')
    html_fname = path.join(meta_dir, 'all.html')
    if path.isfile(html_fname):
        html = open(html_fname, encoding='utf8').read()
    else:
        epub = open(args.fname, 'rb').read()
        html = epub2html_pandoc(epub)
        html = fmt_publisher(html, args.fmt_mode)
        open(html_fname, 'w', encoding='utf8').write(html)
    
    md_fname = path.join(meta_dir, 'all.md')
    if path.isfile(md_fname):
        md = open(md_fname, encoding='utf8').read()
    else:
        md = tomd(html)
        open(md_fname, 'w', encoding='utf8').write(md)

    print('[3] 导出图像')
    img_dir = path.join(proj_dir, 'img')
    os.makedirs(img_dir, exist_ok=True)
    fdict = read_zip(args.fname)
    for iname, data in fdict.items():
        if not is_pic(iname): 
            continue
        print(f'[3] {iname}')
        ifname = path.join(img_dir, path.basename(iname))
        if path.isfile(ifname): 
            continue
        data = pngquant(data)
        open(ifname, 'wb').write(data)
        
    print('[4] 排版和翻译')
    chunk_fname = path.join(meta_dir, 'chunks.yaml')
    if path.isfile(chunk_fname):
        chunks = yaml.safe_load(open(chunk_fname, encoding='utf8').read())
        chunks = parse_obj_as(List[Chunk], chunks)
    else:
        groups = group_chunks(split_md_lines(md))
        chunks = [Chunk(raw=c) for c in groups]
        open(chunk_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump([c.dict() for c in chunks], allow_unicode=True))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback_mdl(fname, res):
        with lock:
            with open(fname, 'w', encoding='utf8') as f:
                obj = (
                    [r.dict() for r in res]
                    if isinstance(res, list)
                    else res.dict()
                )
                f.write(yaml.safe_dump(obj, allow_unicode=True))
    
    for idx, c in enumerate(chunks):
        if c.fmt and c.trans:
            continue
        h = pool.submit(
                tr_fmt_trans, 
                chunks, idx, args,
                functools.partial(write_callback_mdl, chunk_fname, chunks),
            )
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: 
        h.result()
    hdls = []    

    print('[5] 修正目录')
    md = '\n\n'.join(c.trans for c in chunks)
    if args.clean:
        name_cn = meta.name_cn
        md = clean_md_llm(md, args)
        md = f'# {name_cn}\n\n{md}'
    md = fix_toc(
        md, meta, args, 
        functools.partial(write_callback_mdl, meta_fname, meta),
    )

    print('[6] 分章节')
    chs_fname = path.join(meta_dir, 'chs.yaml')
    if path.isfile(chs_fname):
        chs = yaml.safe_load(open(chs_fname, encoding='utf8').read())
    else:
        chs = split_chs(md) if args.split else [md]
        open(chs_fname, 'w', encoding='utf8').write(yaml.safe_dump(chs, allow_unicode=True))
    
    l = len(str(len(chs)))
    for i, c in enumerate(chs):
        ch_fname = path.join(proj_dir, slug + '_' + str(i).zfill(l) + '.md')
        print(f'[5] {ch_fname}')
        open(ch_fname, 'w', encoding='utf8').write(c)

    print('[7] 生成 readme')
    readme = README_TMPL.replace('{name}', name).replace('{name_cn}', meta.name_cn)
    readme_fname = path.join(proj_dir, 'README.md')
    open(readme_fname, 'w', encoding='utf8').write(readme)

    print('[8] 生成 summary')
    toc =[f'+   [{meta.name_cn}](README.md)']
    for i, ch in enumerate(chs):
        title, _ = get_md_title(ch)
        if not title: continue
        ch_fname = slug + '_' + str(i).zfill(l) + '.md'
        toc.append(f'+   [{title}]({ch_fname})')
    summary = '\n'.join(toc)   
    summary_fname = path.join(proj_dir, 'SUMMARY.md')
    open(summary_fname, 'w', encoding='utf8').write(summary)

    print('[*] 完成')


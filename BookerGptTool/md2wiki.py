import json_repair as json
import re
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import yaml
import os
from os import path
from .md2skill_chunker import chunk_markdown
from .util import call_chatgpt_retry, set_openai_props, ngram_coverage
from .md2wiki_pmt import *

def tr_make_draft(cand_items, idx, args, write_callback):
    print(f'[2] 编写词条初稿 {idx+1}')
    origin = '\n\n'.join(cand_items[idx]['chunks'])
    tp = cand_items[idx]['type']
    tmpl = ITEM_TMPL_MAP.get(tp, TERM_TMPL)
    ques = DRAFT_PMT.replace('{origin}', origin) \
        .replace('{name}', cand_items[idx]['name']) \
        .replace('{tmpl}', tmpl)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    draft = ans.replace('[content]', '') \
        .replace('[/content]', '').strip()
    # 检测幻觉
    ratio = ngram_coverage(origin, draft)
    if ratio > 0.6:
        cand_items[idx]['draft'] = draft
    cand_items[idx]['generated'] = True
    write_callback()

def get_cand_items(chunks):
    cand_items_map = {}
    for c in chunks:
        for it in c['items']:
            name = it.get('name')
            if not name: continue
            cand_items_map.setdefault(name, {
                'chunks': [],
                'draft': '',
                **it,
            })
            cand_items_map[name]['chunks'].append(c['chunk'])
    return list(cand_items_map.values())

def tr_gen_cand_item(res, idx, args, write_callback):
    print(f'[1] 提取候选词条 {idx+1}')
    ques = EXT_PMT.replace('{text}', res[idx]['chunk'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    lines = ans.replace('```', '').strip().split('\n')
    lines = [json.loads(l) for l in lines if l.strip()]
    lines = [l for l in lines if isinstance(l, dict)]
    res[idx]['items'] = lines
    res[idx]['generated'] = True
    write_callback()

def md2wiki(args):
    print(args)
    set_openai_props(args)
    if not args.fname.endswith('.md'):
        print('请提供 MD 文件')
        return

    pj_dir = path.join(args.fname[:-3])
    os.makedirs(pj_dir, exist_ok=True)
    print(f'[1] 提取候选词条')
    md = open(args.fname, encoding='utf8').read()
    chunk_fname = path.join(pj_dir, 'chunks.yaml')
    if path.isfile(chunk_fname):
        chunks = yaml.safe_load(
            open(chunk_fname, encoding='utf8').read())
    else:
        cres = chunk_markdown(md, path.basename(args.fname))
        chunks = [{
            'chunk': c.content,
            'title': c.heading_path,
            'items': [],
            'generated': False,
        } for c in cres.chunks]
        open(chunk_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(chunks, allow_unicode=True))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            with open(fname, 'w',  encoding='utf8') as f:
                f.write(yaml.safe_dump(res, allow_unicode=True))

    for i, it in enumerate(chunks):
        if it['generated']: continue
        h = pool.submit(
            tr_gen_cand_item,
            chunks, i, args, 
            functools.partial(write_callback, chunk_fname, chunks)
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print(f'[2] 编写词条初稿')
    cand_items_fname = path.join(pj_dir, 'cand_items.yaml')
    if path.isfile(cand_items_fname):
        cand_items = yaml.safe_load(
            open(cand_items_fname, encoding='utf8').read())
    else:
        cand_items = get_cand_items(chunks)
        open(cand_items_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(cand_items, allow_unicode=True))

    for i, it in enumerate(cand_items):
        if it.get('generated'): continue
        h = pool.submit(
            tr_make_draft,
            cand_items, i, args,
            functools.partial(write_callback, cand_items_fname, cand_items),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

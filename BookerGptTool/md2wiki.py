import json_repair as json
import re
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import yaml
import os
from os import path
from .md2skill_chunker import chunk_markdown
from .util import call_chatgpt_retry, set_openai_props

EXT_PMT = '''
你是一位知识工程专家，擅长从文本中提取可独立成百科词条的知识单元。分析以下文本片段，提取所有值得成为Wiki词条的实体。

## 输出格式

为每个候选词条输出一行JSON，包含词条名（`name`），类型（`type`），原文引述（`origin`）和章节标题（`title`），其中原文引述需要包含所有相关段落：

``
{"name": "...", "type": "person|event|concept|location|term", "origin": ["..."], "title": "X > Y > Z"}
```

## 文本内容

[content]
{text}
[/content]
'''

def tr_gen_cand_item(res, idx, args, write_callback):
    print(f'[1] 提取候选词条 {idx+1}')
    ques = EXT_PMT.replace('{text}', res[idx]['chunk'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    lines = ans.replace('```', '').strip().split('\n')
    lines = [json.loads(l) for l in lines]
    res[idx]['items'] = lines
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
    cand_item_fname = path.join(pj_dir, 'cand_items.yaml')
    if path.isfile(cand_item_fname):
        cand_items = yaml.safe_load(
            open(cand_item_fname, encoding='utf8').read())
    else:
        cres = chunk_markdown(md, path.basename(args.fname))
        cand_items = [{
            'chunk': c.content,
            'title': c.heading_path,
            'items': [],
            'generated': False,
        } for c in cres.chunks]
        open(cand_item_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(cand_items, allow_unicode=True))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            with open(fname, 'w',  encoding='utf8') as f:
                f.write(yaml.safe_dump(res, allow_unicode=True))

    for i, it in enumerate(cand_items):
        if it['generated']: continue
        h = pool.submit(
            tr_gen_cand_item,
            cand_items, i, args, 
            functools.partial(write_callback, cand_item_fname, cand_items)
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []


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

DRAFT_PMT = '''
你是一位专业的百科编辑，负责为「{name}」撰写标准 Wiki 词条。

## 核心规则（必须遵守）

1. **唯一事实来源**：只使用下方「原文素材」中提供的信息。不要添加任何外部知识（包括你从其他书籍、网络学到的内容）。
2. **禁止捏造**：如果原文缺少某方面信息（例如原因、时间、人物、数据），请明确写「原文未记载」，而不是编造。
3. **逐句溯源**：每句话末尾用括号标注来源段落编号，例如 `（§1）` 表示来自第一段原文。
4. **关联词条**：只能从原文中出现的关键名词里选择关联词条，并用 `[[双括号]]` 标记。

## 原文素材（唯一依据）

[content]
{origin}
[/content]

## 输出格式（严格 Markdown）

[content]
# {词条名}

## 类型

[[概念|事件|人物|地点]]  

## 定义

一句话定义（必须来自原文）

## 详细描述

3-5 段，每段后标注来源  

## 关键特征

（可选）用无序列表呈现，每项后标注来源  

## 关联词条

+   相关词条1
+   相关词条2

（仅限原文出现过的名词）  
[/content]
'''

######################################################

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

def tr_make_draft(cand_items, idx, args, write_callback):
    print(f'[2] 编写词条初稿 {idx+1}')
    origin = '\n\n'.join(cand_items[idx]['chunks'])
    ques = EXT_PMT.replace('{origin}', origin) \
        .replace('{name}', cand_items[idx]['name'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    draft = ans.replace('[content]', '') \
        .replace('[/content]', '').strip()
    cand_items[idx]['draft'] = draft
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
            cand_items_map[name]['chunks'].append(c)
    return list(cand_items_map.items())

def tr_gen_cand_item(res, idx, args, write_callback):
    print(f'[1] 提取候选词条 {idx+1}')
    ques = EXT_PMT.replace('{text}', res[idx]['chunk'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    lines = ans.replace('```', '').strip().split('\n')
    lines = [json.loads(l) for l in lines if l.strip()]
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
        if it.get('draft'): continue
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

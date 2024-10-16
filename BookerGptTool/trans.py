import openai
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import json
import random
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import functools
from .util import *

DFT_TRANS_PROMPT = '''
假设你是一个高级文档工程师和翻译员，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文文本翻译成中文。

## 注意事项

-   粗体（**bold**）和斜体（*itatic*）需要翻译翻译内容并保留符号。
-   内联代码（`code`）不需要翻译。
-   链接（[link](https://example.org)）需要翻译其内容，但保留网址。
-   原文可能有多行，不要漏掉任何一行，并且注意一定不要重复输出原文！！！
-   原文每一行都会带一个前缀（-   ），译文的每一行一定要带上相同前缀，不然我没办法解析。

## 示例

原文：
-   [Feynman's learning method](https://wiki.example.org/feynmans_learning_method) is inspired by **Richard Feynman**, the Nobel Prize winner in physics. 
-   With Feynman's skills, you can understand the knowledge points in depth in just `20 min`, and it is memorable and *hard to forget*. 
译文：
-   [费曼学习法](https://wiki.example.org/feynmans_learning_method)的灵感源于诺贝尔物理奖获得者**理查德·费曼**。
-   运用费曼技巧，你只需花上`20 min`就能深入理解知识点，而且记忆深刻，*难以遗忘*。

## 以下是需要翻译的文本

原文：
{en}
译文：
'''

def shuffle_group(g):
    count = len(g['ids'])
    idcs = list(range(count))
    random.shuffle(idcs)
    g['ids'] = [g['ids'][i] for i in idcs]
    g['ens'] = [g['ens'][i] for i in idcs]

def openai_trans(en, prompt, model_name, temp=0, retry=10):
    ques = prompt.replace('{en}', en)
    ans = call_chatgpt_retry(ques, model_name, temp, retry)
    ans = fix_lists(ans)
    return ans
    
def group_totrans(totrans, limit):
    groups = [] # [{ids: [str], ens: [str]}]
    for it in totrans:
        if not it.get('en') or it.get('zh'):
            continue
        if it.get('type') in ['TYPE_PRE']:
            continue
        if len(groups) == 0:
            groups.append({
                'ids': [it['id']],
                'ens': [it['en']]
            })
        else:
            total = len('\n'.join(groups[-1]['ens']))
            if total + len(it['en']) <= limit:
               groups[-1]['ids'].append(it['id']) 
               groups[-1]['ens'].append(it['en']) 
            else:
                groups.append({
                    'ids': [it['id']],
                    'ens': [it['en']]
                })
    return groups

def is_mathml_block(text: str):
    text = text.strip()
    pref, suff = '<math ', '</math>'
    return text.startswith(pref) and \
           text.endswith(suff) and \
           text[len(pref):].find(pref) == -1 and \
           text[:-len(suff)].find(suff) == -1

def is_svg_block(text: str):
    text = text.strip()
    pref, suff = '<svg ', '</svg>'
    return text.startswith(pref) and \
           text.endswith(suff) and \
           text[len(pref):].find(pref) == -1 and \
           text[:-len(suff)].find(suff) == -1

def preproc_totrans(totrans):
    for i, it in enumerate(totrans):
        if not it.get('id'):
            it['id'] = f'totrans-{i}'
        if not it.get('type'):
            it['type'] = 'TYPE_NORMAL'
        if not it.get('prefs'):
            it['prefs'] = []
        if it.get('en'):
            it['en'] = it['en'].replace('\n', '')
            if is_mathml_block(it['en']) or \
               is_svg_block(it['en']):
                it['zh'] = it['en']
        if it['type'] == 'TYPE_PRE':
            it['zh'] = it.get('en', '')

def tr_trans(g, args, totrans_id_map, write_callback=None):
    for i in range(args.retry):
        shuffle_group(g)    
        en = '\n'.join('-   ' + en for en in g['ens'])
        ans = openai_trans(en, args.prompt, args.model, args.temp, args.retry)
        zhs = re.findall(r'^\-\x20{3}(.+?)$', ans, flags=re.M)
        if len(g['ids']) == len(zhs):
            break
        print(f'en-zh match retry {i+1}')
        if i == args.retry - 1: 
            raise AssertionError('en-zh no match')
    for id, zh in zip(g['ids'], zhs):
        totrans_id_map.get(id, {})['zh'] = zh
    # 及时保存已翻译文本
    if write_callback: write_callback()

def tr_trans_safe(*args, **kw):
    try:
        tr_trans(*args, **kw)
    except:
        traceback.print_exc()

def trans_one(totrans, args, pool, write_callback=None):
    # totrans: [{id?: str, en?: str, zh?: str, type: str, ...}]
    preproc_totrans(totrans)
    groups = group_totrans(totrans, args.limit)
    totrans_id_map = {it['id']:it for it in totrans}
    
    hdls = []
    for g in groups:
        h = pool.submit(
            tr_trans_safe, 
            g, args, totrans_id_map, 
            write_callback,
        )
        hdls.append(h)
    return hdls


file_lock = Lock()

def write_callback(fname, totrans):
    with file_lock:
        open(fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(totrans, allow_unicode=True))

def trans_yaml_handle(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    fnames = [args.fname] if path.isfile(args.fname) \
             else [path.join(args.fname, f) for f in os.listdir(args.fname)]
    fnames = [f for f in fnames if extname(f) == 'yaml']
    if not fnames:
        print('请提供 YAML 文件')
        return
        
    
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        print(f)
        totrans = yaml.safe_load(open(f, encoding='utf8').read())
        hdls += trans_one(
            totrans, args, pool,
            functools.partial(write_callback, f, totrans),
        )
        if len(hdls) >= args.threads:
            for h in hdls: h.result()
    for h in hdls: h.result()
        
    
def trans_handle(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    ans = openai_trans(args.en, args.prompt, args.temp, args.model)
    

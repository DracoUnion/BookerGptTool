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

DFT_STYLE_PROMPT = '''
假设你是一个高级文档工程师，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文或中文文本排版。

## 注意事项

-   所有单独出现的变量名（`varName`），函数名（`funcName()`），类名（`ClassName`），路径名（`/path.to.xxx`），命令名（`cmdname`）以及它们的语句或表达式（`ClassName.funcName(var1 + var2, "cmd arg0 arg1"`）都需要添加反引号。
-   如果整句是一行代码，整体添加反引号
-   如果上述东西被粗体（**）或者斜体（*）包围，去掉星号再加反引号
-   原文可能有多行，不要漏掉任何一行，并且注意一定不要重复输出原文！！！
-   译文的每一句一定要带上前缀（-   ），不然我没办法解析。

## 示例

原文：
-   进入 /path/to/xxx 目录，找到 xxx.json。
-   在表达式 cvar = avar + bvar 中，加法运算符（+）将 avar 与 bvar 相加，得到它们的和 cvar
-   在 List.of(arg0, arg1, arg2) 中，List 接口的工厂方法 of() 接受一系列的元素，返回包含它们的只读列表。
-   之后我们这样调用 cmd 命令：cmd arg0 arg1 arg2。
-   if (condVar > someVal) {console.log("xxx")}
排版后：
-   进入`/path/to/xxx`目录，找到`xxx.json`。
-   在表达式`cvar = avar + bvar`中，加法运算符（`+`）将`avar`与`bvar`相加，得到它们的和`cvar`
-   在`List.of(arg0, arg1, arg2)`中，`List`接口的工厂方法`of()`接受一系列的元素，返回包含它们的只读列表。
-   之后我们这样调用`cmd`命令：`cmd arg0 arg1 arg2`。
-   `if (condVar > someVal) {console.log("xxx")}`

## 以下是需要排版的文本

原文：
{text}
排版后：
'''

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
            # if is_mathml_block(it['en']):
            #     it['zh'] = it['en']
        if it['type'] == 'TYPE_PRE':
            it['zh'] = it.get('en', '')

def shuffle_group(g):
    count = len(g['ids'])
    idcs = list(range(count))
    random.shuffle(idcs)
    g['ids'] = [g['ids'][i] for i in idcs]
    g['ens'] = [g['ens'][i] for i in idcs]

def group_tostylish(totrans, limit):
    groups = [] # [{ids: [str], ens: [str]}]
    for it in totrans:
        if not it.get('en') or it.get('stylish'):
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

def tr_stylish(g, args, totrans_id_map, write_callback=None):
    for i in range(args.retry):
        shuffle_group(g)    
        en = '\n'.join('-   ' + en for en in g['ens'])
        ques = args.prompt.replace('{text}', en)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
        ans = fix_lists(ans)
        stys = re.findall(r'^\-\x20{3}(.+?)$', ans, flags=re.M)
        if len(g['ids']) == len(stys):
            break
        print(f'en-zh match retry {i+1}')
        if i == args.retry - 1: 
            raise AssertionError('en-zh no match')
    for id, sty in zip(g['ids'], stys):
        it = totrans_id_map.get(id, {})
        it['en'] = sty
        it['stylish'] = True
    # 及时保存已翻译文本
    if write_callback: write_callback()

def tr_stylish_safe(*args, **kw):
    try:
        tr_stylish(*args, **kw)
    except:
        traceback.print_exc()

def stylish_one(totrans, args, pool, write_callback=None):
    # totrans: [{id?: str, en?: str, zh?: str, type: str, ...}]
    preproc_totrans(totrans)
    groups = group_tostylish(totrans, args.limit)
    totrans_id_map = {it['id']:it for it in totrans}
    
    hdls = []
    for g in groups:
        h = pool.submit(
            tr_stylish_safe, 
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

def stylish_yaml_handle(args):
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
        hdls += stylish_one(
            totrans, args, pool, 
            functools.partial(write_callback, f, totrans),
        )
        if len(hdls) >= args.threads:
            for h in hdls: h.result()
    for h in hdls: h.result()
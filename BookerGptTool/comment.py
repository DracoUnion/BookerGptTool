import openai
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import json
import random
import copy
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .util import *

DFT_EXT_VAR_PROMPT = '''
假设你是一位资深的程序员，提取代码里的所有全局变量、常量、类字段（格式为`{类名}.{字段名}`），输出它们的名字。


注意只需要输出名称，不需要输出其它任何东西。

====
格式
====

## 全局变量

-   `{全局变量1}`
-   `{全局变量2}`
-   ...

## 常量

-   `{常量1}`
-   `{常量2}`
-   ...


## 类字段

-   `{类字段1}`
-   `{类字段2}`
-   ...


====
代码
====

```
{code}
```
'''

DFT_EXY_FUNC_PMT = '''
假设你是一位资深的程序员，提取代码里的所有全局函数、类方法（格式为`{类名}.{方法名}`），输出它们的名字。


注意只需要输出名称，不需要输出其它任何东西。

====
格式
====

## 全局函数

-   `{全局函数1}`
-   `{全局函数2}`
-   ...

## 类方法

-   `{类方法1}`
-   `{类方法2}`
-   ...


====
代码
====

```
{code}
```

'''

DFT_COMM_VAR_PROMPT = '''
假设你是一位资深的程序员，请解析以下代码中的指定全局变量，常量，函数，类方法，生成技术文档。

####
格式
####

## `{名称}`

类型：`{类型}`

默认值：`{默认值}`

功能描述：{功能描述}

###########
要分析的代码
###########

```
{code}
```

#############
要分析的变量等
#############

{vars}
'''

DFT_COMM_FUNC_PROMPT = '''
假设你是一位资深的程序员，请解析以下代码中的全局函数或方法{func}，生成技术文档。

####
注意
####

-   流程图使用 mermaid 格式
-   对于每个变量或者函数，只需要输出格式内规定的项目，不要输出任何其他东西
-   源码每行都需要加上注释，采用中文

====
格式
====

## `{名称}`

参数：

-   `{参数1}`
-   `{参数2}`
-   ...

返回值：`{返回值}`

功能描述：{功能描述}

流程图：

```
{流程图}
```

带注释的源码：

```
{源码}
```

###########
要分析的代码
###########

```
{code}
```
'''

def get_ind_len(text):
    return len(re.search(r'\A\x20*', text).group())

def openai_comment(code, prompt, model_name, temp=0, retry=10):
    ques = prompt.replace('{code}', code)
    ans = call_chatgpt_retry(ques, model_name, temp, retry)
    # ans = re.sub(r'^```\w*$', '', ans, flags=re.M)
    # ans = re.sub(r'\A\n+|\n+\Z', '', ans)
    # 如果原始代码有缩进，但结果无缩进，则添加缩进
    # ind = get_ind_len(code)
    # if ind and not get_ind_len(ans):
    #     ans = re.sub(r'^', '\x20' * ind, ans, flags=re.M)
    return ans

def chunk_code(lines, limit=2000):
    if isinstance(lines, str):
        # lines = lines.split('\n')
        lines = lines.replace('\t', '\x20' * 4)
        lines = re.split(r'^(?=\S|\x20{4}\S)', lines, flags=re.M)
        
    lines = [l.replace('\t', '\x20' * 4) for l in lines]
    lines = [l for l in lines if len(l) <= limit]
    blocks = ['']
    for l in lines:
        if get_ind_len(l) < get_ind_len(blocks[-1]):
            # 如果当前块缩进更少，则不合并
            blocks.append(l)
        elif len(blocks[-1]) + len(l) > limit:
            # 超出限制则不合并
            blocks.append(l)
        else:
            # 否则合并
            blocks[-1] += l
    
    return blocks

def process_dir(args):
    dir = args.fname
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for base, _, fnames in os.walk(dir):
        for f in fnames:
            args = copy.deepcopy(args)
            args.fname = path.join(base, f)
            h = pool.submit(process_file_safe, args)
            hdls.append(h)
    for h in hdls: h.result()

def process_file_safe(args):
    try:
        process_file(args)
    except:
        traceback.print_exc()

def process_file(args):
    fname = args.fname
    ext = extname(fname)
    if ext not in [
        'c', 'h', 'cpp', 'cxx', 'hpp',
        'java', 'cs', 'php', 'go', 
        'js', 'ts', 'jsx', 'tsx', 'vue',
        'py', 'pyx', 'pyi', 'pxd',
    ]:
        print(f'{fname} 代码类型不支持')
        return
    ofname = fname + '.md'
    if path.isfile(ofname):
        print(f'{fname} 已存在')
        return
    print(fname)
    code = open(fname, encoding='utf8').read()
    '''
    blocks = chunk_code(code, args.limit)
    parts = []
    for b in blocks:
        part = openai_comment(b, args.prompt, args.model, args.temp, args.retry)
        parts.append(part)
    comment = '```\n' + '\n'.join(parts) + '\n```'
    '''
    lst = openai_comment(code, DFT_EXT_VAR_PROMPT, args.model, args.temp, args.retry)
    lst = re.sub(r'^\-\x20{3}\(?无\)?', '', lst, flags=re.M)
    ms = re.finditer(r'^\-\x20{3}.+?$', lst, re.M)
    vars = '\n'.join([m.group() for m in ms])
    print(f'变量：\n{vars}')
    ques = DFT_COMM_VAR_PROMPT.replace('{code}', code) \
        .replace('{vars}', vars)
    doc_vars = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
    doc = doc_vars
    lst = openai_comment(code, DFT_EXY_FUNC_PMT, args.model, args.temp, args.retry)
    lst = re.sub(r'^\-\x20{3}\(?无\)?', '', lst, flags=re.M)
    ms = re.finditer(r'^\-\x20{3}.+?$', lst, re.M)
    funcs = '\n'.join([m.group() for m in ms])
    print(f'函数：\n{funcs}')
    ms = re.finditer(r'^\-\x20{3}(.+?)$', funcs, re.M)
    for m in ms:
        func = m.group(1)
        ques = DFT_COMM_FUNC_PROMPT.replace('{code}', code) \
            .replace('{func}', func)
        doc_func = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
        doc += '\n\n' + doc_func
    res = f'# `{fname}`\n\n{doc}'
    open(ofname, 'w', encoding='utf8').write(res)
    

def extname(name):
    m = re.search(r'\.(\w+)$', name)
    return m.group(1) if m else ''

    
def comment_handle(args):
    set_openai_props(args.key, args.proxy, args.host)
 
    if path.isdir(args.fname):
        process_dir(args)
    else:
        process_file(args)

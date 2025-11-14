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

DFT_COMM_PROMPT = '''
假设你是一位资深的程序员，请解析以下代码中的全局变量，常量，函数，类字段，方法等，生成技术文档。

## 注意

-   以 Markdown 格式输出
-   对于每个变量或者函数，只需要输出格式内规定的项目，不要输出任何其他东西
-   任何流程图或者源码需要包含在三个反引号（```）中

## 格式（仅限于函数和方法）

名称：{名称}

参数：{参数}

返回值：{返回值}

功能描述：{功能描述}

流程图：（使用mermad）

```
{流程图}
```

带注释的源码：（包含在三个反引号中）

```
{源码}
```

## 格式（仅限于变量常量和字段）

名称：{名称}

类型：{类型}

默认值：{默认值}

功能描述：{功能描述}

## 要分析的代码

```
{code}
```
'''

def get_ind_len(text):
    return len(re.search(r'\A\x20*', text).group())

def openai_comment(code, prompt, model_name, temp=0, retry=10):
    ques = prompt.replace('{code}', code)
    ans = call_chatgpt_retry(ques, model_name, temp, retry)
    ans = re.sub(r'^```\w*$', '', ans, flags=re.M)
    ans = re.sub(r'\A\n+|\n+\Z', '', ans)
    # 如果原始代码有缩进，但结果无缩进，则添加缩进
    ind = get_ind_len(code)
    if ind and not get_ind_len(ans):
        ans = re.sub(r'^', '\x20' * ind, ans, flags=re.M)
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
    doc = openai_comment(code, args.prompt, args.model, args.temp, args.retry)
    print(doc)
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

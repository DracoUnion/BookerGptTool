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
假设你是一位资深的程序员，请你参照示例并遵循注意事项，为给定代码的每个语句添加注释，解释它们的作用。

## 注意事项

-   注释后的代码需要包含在代码块中，前后用三个反引号包围
-   不要改变代码的任何缩进
-   不要省略代码任何部分，每一行代码都需要注释
-   不要总结代码的整个含义，也不要将注释写到代码块之外
-   只输出代码块，不要输出其它东西

## 示例

代码：

```
def read_zip(fname):
    bio = BytesIO(open(fname, 'rb').read())
    zip = zipfile.ZipFile(bio, 'r')
    fdict = {n:zip.read(n) for n in zip.namelist()}
    zip.close()
    return fdict
```

注释：

```
# 根据 ZIP 文件名读取内容，返回其中文件名到数据的字典
def read_zip(fname):
    # 根据 ZIP 文件名读取其二进制，封装成字节流
    bio = BytesIO(open(fname, 'rb').read())
    使用字节流里面内容创建 ZIP 对象
    zip = zipfile.ZipFile(bio, 'r')
    遍历 ZIP 对象所包含文件的文件名，读取文件数据，组成文件名到数据的字典
    fdict = {n:zip.read(n) for n in zip.namelist()}
    # 关闭 ZIP 对象
    zip.close()
    # 返回结果字典
    return fdict
```

## 以下是需要注释的代码

代码：

```
{code}
```

注释：
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
    if ext not in ['c', 'h', 'cpp', 'cxx', 'java', 'cs', 'php', 'go', 'js', 'ts', 'py']:
        print(f'{fname} 代码类型不支持')
        return
    ofname = fname + '.md'
    if path.isfile(ofname):
        print(f'{fname} 已存在')
        return
    print(fname)
    code = open(fname, encoding='utf8').read()
    blocks = chunk_code(code, args.limit)
    parts = []
    for b in blocks:
        part = openai_comment(b, args.prompt, args.model, args.temp, args.retry)
        parts.append(part)
    comment = '```\n' + '\n'.join(parts) + '\n```'
    print(f'==={fname}===\n{comment}')
    res = f'# `{fname}`\n\n{comment}'
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

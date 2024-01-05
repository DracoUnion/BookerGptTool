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

DFT_COMM_PROMPT = '''
假设你是一位资深的程序员，请你参照示例为给定代码的每个语句添加注释，解释它们的作用。

示例：

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

以下是需要注释的代码。

代码：

```
{code}
```

注释：
'''

def call_openai_retry(code, prompt, model_name, retry=10):
    for i in range(retry):
        try:
            ques = prompt.replace('{code}', code)
            print(f'ques: {json.dumps(ques, ensure_ascii=False)}')
            client = openai.OpenAI(
                base_url=openai.host,
                api_key=openai.api_key,
                http_client=httpx.Client(
                    proxies=openai.proxy,
                    transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                )
            )
            ans = client.chat.completions.create(
                messages=[{
                    "role": "user",
                    "content": ques,
                }],
                model=model_name,
                temperature=0,
            ).choices[0].message.content
            ans = re.sub(r'\A```\w*\n', '', ans)
            ans = re.sub(r'\n```\Z', '', ans)
            print(f'ans: {json.dumps(ans, ensure_ascii=False)}')
            return ans
        except Exception as ex:
            print(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex

def chunk_code(lines, limit=20):
    if isinstance(lines, str):
        lines = lines.split('\n')
        
    lines = [l for l in lines if len(l) <= 200]
    blocks = []
    for i in range(0, len(lines), limit):
        blocks.append(lines[i:i+limit])
    
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
        code = '\n'.join(b)
        part = call_openai_retry(code, args.prompt, args.model, args.retry)
        parts.append(part)
    comment = '```\n' + '\n'.join(parts) + '\n```'
    print(f'==={fname}===\n{comment}')
    res = f'# `{fname}`\n\n{comment}'
    open(ofname, 'w', encoding='utf8').write(res)
    

def extname(name):
    m = re.search(r'\.(\w+)$', name)
    return m.group(1) if m else ''

    
def comment_handle(args):
    openai.api_key = args.key
    openai.proxy = args.proxy
    openai.host = args.host
 
    if path.isdir(args.fname):
        process_dir(args)
    else:
        process_file(args)

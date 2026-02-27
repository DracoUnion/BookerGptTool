import openai
import base64
import httpx
import requests
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
from typing import *

def request_retry(method, url, retry=10, check_status=False, **kw):
    kw.setdefault('timeout', 10)
    for i in range(retry):
        try:
            r = requests.request(method, url, **kw)
            if check_status: r.raise_for_status()
            return r
        except KeyboardInterrupt as e:
            raise e
        except Exception as e:
            print(f'{url} retry {i}')
            if i == retry - 1: raise e

def reform_paras_mdcn(text, size=1500):
    text = re.sub(r'```[\s\S]+?```', '', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    lines = sum([
        re.split(r'(?<=[。，：！？；])', l) for l in lines
    ], [])
    lines = [l for l in lines if l]
    paras = ['']
    for l in lines:
        if len(paras[-1]) + len(l) > size:
            paras.append(l)
        else:
            paras[-1] += l
    return paras
    
def fix_lists(ans):
    # 调整列表格式
    ans = re.sub(r'^(\x20*)[\+\-\*]\x20+', r'\1-   ', ans, flags=re.M)
    ans = re.sub(r'^(\x20*)(\d+\.)\x20+', r'\1\2  ', ans, flags=re.M)
    return ans

def call_chatgpt_retry(ques, model_name, temp=0, retry=10, max_tokens=None):
    # 改变指令符号的形式，避免模型出错
    ques = re.sub(r'<\|([\w\-\.]+)\|>', r'</\1/>', ques)
    for i in range(retry):
        try:
            print(f'ques: {json.dumps(ques, ensure_ascii=False)}')
            client = openai.OpenAI(
                base_url=openai.base_url,
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
                temperature=temp,
                max_tokens=max_tokens,
            ).choices[0].message.content.strip()
            # 还原指令格式
            ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
            ans = re.sub(r'<think>[\s\S]+?</think>', '', ans)
            print(f'ans: {json.dumps(ans, ensure_ascii=False)}')
            return ans
        except Exception as ex:
            print(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex

def set_openai_props(key=None, proxy=None, host=None):
    openai.api_key = key
    openai.proxy = proxy
    openai.base_url = host

RE_TITLE = r'\A\s*^#+\x20+(.+?)$'

def get_md_title(text):
    m = re.search(RE_TITLE, text, flags=re.M)
    if not m:
        return None, (None, None)
    return m.group(1).strip(), m.span(1)
    
def extname(fname):
    m = re.search(r'\.(\w+)$', fname)
    return m.group(1) if m else ''

def load_train_data_batch(fname, bs, exts=None):
    ds = load_train_data(fname, exts)
    iter_ = iter(ds)
    while True:
        try:
            yield [next(iter_) for _ in range(bs)]
        except StopIteration:
            break
    

def load_train_data(fname, exts=None):
    exts = exts or ['yaml', 'json', 'jsonl']
    if path.isfile(fname):
        fnames = [fname]
    elif path.isdir(fname):
        fnames = [
            path.join(fname, f) 
            for f in os.listdir(fname) 
        ]
    else:
        raise Exception('请提供 YAML 文件或其目录')
    fnames = [
        f for f in fnames
        if extname(f) in exts
    ]
    for f in fnames:
        ds = read_ds_file(f)
        for dit in ds:
            yield dit

def write_ds_file(fname, ds):
    ext = extname(fname).lower()
    if ext == 'yaml':
        data = yaml.safe_dump(ds, allow_unicode=True)
    elif ext == 'json':
        data = json.dumps(ds, ensure_ascii=False)
    elif ext == 'jsonl':
        data = '\n'.join(
            json.dumps(it, ensure_ascii=False) 
            for it in ds
        )
    else:
        raise Exception('文件必须是 JSON、JSONL、YAML')
    open(fname, 'w', encoding='utf8').write(data)


def read_ds_file(fname):
    ext = extname(fname).lower()
    data = open(fname, encoding='utf8').read()
    if ext == 'yaml':
        ds = yaml.safe_load(data)
    elif ext == 'json':
        ds = json.loads(data)
    elif ext == 'jsonl':
        lines = data.split('\n')
        ds = [
            json.loads(l)
            for l in lines if l.strip()
        ]
    else:
        raise Exception('文件必须是 JSON、JSONL、YAML')

    # random.shuffle(ds)
    return ds

def combine_prompt_args(prompt: str, args: Dict[str, Any]):
    return re.sub(r"{(\w+)}", lambda g: args.get(g.group(1), g.group(0)), prompt)

def norm_l2(arr, axis=-1):
    l2 = (arr**2).sum(axis, keepdims=True) ** 0.5
    return arr / l2


def call_glmocr_retry(img, retry=10):
    img_base64 = base64.b64encode(img).decode('ascii')
    url = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
    payload = {
        "model": "glm-ocr",
        "file": f'data:image/png;base64,{img_base64}',
    }
    headers = {
        "Authorization": f"Bearer {openai.api_key}",
        "Content-Type": "application/json"
    }
    for i in range(retry):
        try:
            res = requests.post(
                url, 
                json=payload, 
                headers=headers, 
                proxies=openai.proxy,
            )
            res.raise_for_status()
        except Exception as ex:
            print(f'GLM retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex
    print(res.text)
    return json.loads(res.text)['md_results']
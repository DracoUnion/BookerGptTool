import ctypes
import sys
import pyturndown
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import logging
import json
import random
import copy
import re
import zipfile
import subprocess as subp
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import tempfile
import uuid
from typing import *

def d(name):
    DIR = path.dirname(path.abspath(__file__))
    return path.join(DIR, name)

def get_md_title(text):
    RE_TITLE = r'^#+\x20+(.+?)$'
    m = re.search(RE_TITLE, text, flags=re.M)
    if not m:
        return None, (None, None)
    return m.group(1).strip(), m.span(1)

def epub2html_pandoc(epub):
    fname = path.join(tempfile.gettempdir(), uuid.uuid4().hex + '.epub')
    ofname = fname[:-5] + '.html'
    open(fname, 'wb').write(epub)
    subp.Popen(['pandoc', fname, '-o', ofname]).communicate()
    html = open(ofname, encoding='utf8').read()
    os.remove(fname)
    os.remove(ofname)
    return html

def is_pic(fname):
    ext = [
        'jpg', 'jpeg', 'jfif', 'png', 
        'gif', 'tiff', 'webp'
    ]
    m = re.search(r'\.(\w+)$', fname.lower())
    return bool(m and m.group(1) in ext)

def read_zip(fname: str) -> Dict[str, bytes]:
    bio = BytesIO(open(fname, 'rb').read())
    zip = zipfile.ZipFile(bio, 'r')
    fdict = {n:zip.read(n) for n in zip.namelist()}
    zip.close()
    return fdict

def to_kebab(name: str) -> str:
    """将技能名转为 kebab-case slug"""
    s = re.sub(r"[^\w\s\u4e00-\u9fff\-]", "", name)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "unnamed"

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
    with open(fname, 'w', encoding='utf8') as f:
        f.write(data)


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

def group_chunks(chunks, limit=8000):
    groups = ['']
    for c in chunks:
        if len(groups[-1]) +  len(c) + 2 > limit:
            groups.append(c)
        else:
            groups[-1] += '\n\n' + c
    groups = [g for g in groups if g]
    return groups

def split_md_lines(md):
    lines = md.split('\n')
    res = []
    in_code = False
    code = ''
    for l in lines:
        if '```' in l:
            if in_code: 
                # 结尾
                code += l
                res.append(code)
                code = ''
            else:
                # 开头
                code += l + '\n'
            in_code = not in_code
        elif not in_code:
            if l.strip(): res.append(l)
        elif in_code:
            code += l + '\n'
    # 处理结尾缺失情况：
    if code:
        res.append(code + '```')
    return res

tok_en_zh = lambda text: \
    re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]+', text)
get_ngram_set = lambda toks, n: \
    set(tuple(toks[i:i+n]) for i in range(len(toks)-n+1))

def ngram_jaccard(text1: str, text2: str, n: int = 3) -> float:
    set1 = get_ngram_set(tok_en_zh(text1.lower()), n)
    set2 = get_ngram_set(tok_en_zh(text2.lower()), n)
    inter = len(set1 & set2)
    union = len(set1 | set2)
    return inter / union if union else 0.0

def ngram_coverage(src: str, gen: str, n: int = 3) -> float:
    src_set = get_ngram_set(tok_en_zh(src.lower()), n)
    gen_set = get_ngram_set(tok_en_zh(gen.lower()), n)
    inter = len(src_set & gen_set)
    all_ = len(gen_set)
    return inter / all_ if all_ else 0.0

ext_code_block = lambda s: re.search(r'```\w*([\s\S]+)```', s).group(1)
ext_cont_block = lambda s: re.search(r'\[content\]([\s\S]+)\[/content\]', s).group(1)


def malloc_trim_linux():
    if sys.platform == 'linux':
        libc = ctypes.CDLL("libc.so.6")
        libc.malloc_trim(0)
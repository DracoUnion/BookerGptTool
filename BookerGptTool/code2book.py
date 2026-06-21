import openai
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import json_repair
import json
import random
import copy
import re
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .util import call_chatgpt_retry, set_openai_props, extname
from .code2book_pmt import *

def tr_gen_code_desc(res, idx, args, write_callback):
    fname = res[idx]['file']
    print(f'[2] 生成描述 {fname}')
    code = open(res[idx]['full_path'], encoding='utf8').read()
    ques = CLS_FUNC_EXT_PMT.replace('{fname}', fname) \
        .replace('{code}', code)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    descs_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    descs = json_repair.loads(descs_str)
    res[idx].update(descs)
    write_callback()

def code2book(args):
    print(args)
    set_openai_props(args)
    if not path.isdir(args.dir):
        print('请提供项目目录！')
        return
    pj_dir = path.abspath(args.dir) + '_code2book'
    os.makedirs(pj_dir, exist_ok=True)

    print('[1] 探索项目结构')
    ext_li = [
        'c', 'h', 'cpp', 'cxx', 'hpp',
        'java', 'cs', 'php', 'go', 
        'js', 'ts', 'jsx', 'tsx', 'vue',
        'py', 'pyx', 'pyi', 'pxd',
    ]
    fnames = [
        path.join(rt, f)
        for rt, _, fnames in os.walk(args.dir)
        for f in fnames
        if extname(f) in ext_li
    ]
    fnames_li = '\n'.join(fnames)
    print(fnames_li)

    print('[2] 生成源码文件描述')
    code_desc_fname = path.join(pj_dir, 'code_desc.yaml')
    if path.isfile(code_desc_fname):
        code_desc = yaml.safe_load(
            open(code_desc_fname, encoding='utf8').read())
    else:
        code_desc = [
            {
                "file": f,
                "full_path": path.join(args.dir, f),
            }
            for f in fnames
        ]
        open(code_desc_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(code_desc))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            with open(fname, 'w', encoding='utf8') as f:
                f.write(yaml.safe_dump(res, allow_unicode=True))

    for i, it in enumerate(code_desc):
        if it.get('desc'): continue
        h = pool.submit(
            tr_gen_code_desc,
            code_desc, i, 
            args,
            functools.partial(write_callback, code_desc_fname, code_desc)
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print('[3] 生成大纲')
    outline_fname = path.join(pj_dir, 'outline.yaml')
    if path.isfile(outline_fname):
        outline = yaml.safe_load(
            open(outline_fname, encoding='utf8').read())
    else:
        ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', json.dumps(code_desc, ensure_ascii=False))
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        outline_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        outline = json_repair.loads(outline_str)
        open(outline_fname, 'w', encoding='utf8').write(yaml.safe_dump(outline))

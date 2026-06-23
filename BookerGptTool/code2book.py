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

def tr_gen_body(details, idx, bodies, fname, args):
    print(f'[5] 编写第{idx+1}章正文')
    code_fnames = [
        c['file'] 
        for u in details[idx]['units']
        for c in u['code']
    ]
    code_dict = {
        f:open(path.join(args.dir, f), encoding='utf8').read()
        for f in code_fnames
    }
    code_str = '\n\n'.join([
        f'`{f}`\n\n```\n{code}\n```'
        for f, code in code_dict.items()
    ])
    detail_str = json.dumps(details[idx], ensure_ascii=False)
    ques = BODY_PMT.replace('{detail}', detail_str) \
        .replace('{code}', code_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    body = ans.replace('[content]', '').replace('[/content]', '')
    bodies[idx] = body
    open(fname, 'w', encoding='utf8').write(body)

def tr_gen_detail(outline_chs, idx, details, args, write_callback):
    print(f'[4] 编写第{idx+1}章细纲')
    code_fnames = [
        f for pt in outline_chs[idx]['nodes']
          for f in pt['src']
    ]
    code_dict = {
        f:open(path.join(args.dir, f), encoding='utf8').read()
        for f in code_fnames
    }
    code_str = '\n\n'.join([
        f'`{f}`\n\n```\n{code}\n```'
        for f, code in code_dict.items()
    ])
    outline_str = json.dumps(outline_chs[idx], ensure_ascii=False)
    ques = SRC_ANLS_DETAIL_PMT.replace('{i}', str(idx + 1)) \
        .replace('{outline}', outline_str) \
        .replace('{code}', code_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    detail_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    detail = json_repair.loads(detail_str)
    details[idx].update(detail)
    write_callback()
    detail_str = json.dumps(details[idx], ensure_ascii=False)
    ques = SPEC_DETAIL_PMT.replace('{detail}', detail_str) \
        .replace('{code}', code_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    spec_detail_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    spec_detail = json_repair.loads(spec_detail_str)
    details[idx].update(spec_detail)
    write_callback()


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
                "file": path.relpath(f, args.dir),
                "full_path": f,
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
        readme = open(path.join(args.dir, 'README.md'), encoding='utf8').read()
        ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', json.dumps(code_desc, ensure_ascii=False)) \
            .replace('{readme}', readme)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        outline_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        outline = json_repair.loads(outline_str)
        open(outline_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(outline, allow_unicode=True))

    print('[4] 生成细纲')
    outline_chs = sum([
        pt['chapters']for pt in outline['parts']
    ], [])
    details = []
    for i, ch in enumerate(outline_chs):
        detail_fname = path.join(pj_dir, f'detail_{i+1}.yaml')
        if path.isfile(detail_fname):
            detail = yaml.safe_load(
                open(detail_fname, encoding='utf8').read())
            details.append(detail)
            continue
        details.append({})
        h = pool.submit(
            tr_gen_detail,
            outline_chs, i, details, args,
            functools.partial(write_callback, detail_fname, details[-1])
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print(f'[5] 生成正文')
    bodies = []
    for i, detail in enumerate(details):
        body_fname = path.join(pj_dir, f'body_{i+1}.md')
        if path.isfile(body_fname):
            body = open(detail_fname, encoding='utf8').read()
            bodies.append(body)
            continue
        bodies.append('')
        h = pool.submit(
            tr_gen_body,
            details, i, bodies, body_fname, args,
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []
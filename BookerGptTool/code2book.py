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
import sys
from .util import ask_chatgpt_retry, set_openai_props, extname
from .code2book_pmt import *

def check_details(details, code_desc, fnames, pj_dir, args):
    fixed = all(d.get('fixed', False) for d in details)
    if fixed: 
        print(f'[4] 细纲校验通过')
        return details
    for i, d in enumerate(details):
        d['no'] = i + 1
    fnames_li = '\n'.join(fnames)
    code_desc_str = json.dumps(code_desc, ensure_ascii=False)
    readme = open(path.join(args.dir, 'README.md'), encoding='utf8').read()
    total_funcs = [
        cd['file'] + ':' + fn['name']
        for cd in code_desc
        for fn in cd.get('funcs', [])
        if 'file' in cd and 'name' in fn
    ]
    total_funcs += [
        cd['file'] + ':' + cls_['name'] + '.' + m['name']
        for cd in code_desc
        for cls_ in cd.get('classes', [])
        for m in cls_.get('methods', [])
        if 'file' in cd and 'name' in cls_ and 'name' in m
    ]
    total_funcs = [
        it.replace('\\', '/').replace('()', '')
        for it in total_funcs
    ]
    for _ in range(args.check):
        exi_funcs = [
            cd['file'] + ':' + cd['class_or_func']
            for d in details
            for u in d.get('units', [])
            for cd in u.get('code', [])
            if 'file' in cd and 'class_or_func' in cd
        ]
        exi_funcs = [
            it.replace('\\', '/').replace('()', '')
            for it in exi_funcs
        ]
        rest_funcs = list(set(total_funcs) - set(exi_funcs))
        if len(rest_funcs) == 0:
            print(f'[4] 细纲校验通过')
            break
        print(f'[4] 细纲校验未通过')
        print('\n'.join(rest_funcs))
        details_str = json.dumps(details, ensure_ascii=False)
        ques = DETAIL_FIX_PMT \
            .replace('{details}', details_str) \
            .replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{rest_funcs}', '\n'.join(rest_funcs))
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        details_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        details = json_repair.loads(details_str)
        # sorted(details, key=lambda it: it['no'])
        for i, d in enumerate(details): 
            d['fixed'] = True
            detail_fname = path.join(pj_dir, f'detail_{i+1}.yaml')
            open(detail_fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(d, allow_unicode=True))
    return details


def tr_gen_body(outline_chs, details, idx, bodies, fname, args):
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
    outline_str = json.dumps(outline_chs, ensure_ascii=False)
    detail_str = json.dumps(details[idx], ensure_ascii=False)
    ques = BODY_PMT.replace('{detail}', detail_str) \
        .replace('{outline}', outline_str) \
        .replace('{code}', code_str) \
        .replace('{i}', str(idx + 1))
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    body = ans.replace('[content]', '').replace('[/content]', '')
    bodies[idx] = body
    open(fname, 'w', encoding='utf8').write(body)

    print(f'[5] 校验正文 {idx + 1}')
    for _ in range(args.check):
        ques = BODY_CHK_PMT.replace('{body}', body)
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        if "[PERFECT/]" in ans:
            print(f'[5] 正文 {idx + 1} 校验完成')
            break
        cmt = ans.replace('[content]', '').replace('[/content]', '')
        print(f'[5] 正文 {idx + 1} 校验未通过')
        print(cmt)
        ques = BODY_FIX_PMT.replace('{detail}', detail_str) \
            .replace('{body}', body) \
            .replace('{comment}', cmt) \
            .replace('{code}', code_str)
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
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
    outline_str = json.dumps(outline_chs, ensure_ascii=False)
    ques = SRC_ANLS_DETAIL_PMT.replace('{i}', str(idx + 1)) \
        .replace('{outline}', outline_str) \
        .replace('{code}', code_str)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    detail_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    detail = json_repair.loads(detail_str)
    details[idx].update(detail)
    write_callback()
    detail_str = json.dumps(details[idx], ensure_ascii=False)
    ques = REST_DETAIL_PMT.replace('{detail}', detail_str) \
        .replace('{outline}', outline_str) \
        .replace('{i}', str(idx + 1)) \
        .replace('{code}', code_str)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    spec_detail_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    spec_detail = json_repair.loads(spec_detail_str)
    details[idx].update(spec_detail)
    write_callback()

def gen_outline(fnames, code_desc, args):
    fnames_li = '\n'.join(fnames)
    code_desc_str = json.dumps(code_desc, ensure_ascii=False)
    readme = open(path.join(args.dir, 'README.md'), encoding='utf8').read()
    ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
        .replace('{code_desc}', code_desc_str) \
        .replace('{readme}', readme)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    outline_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    outline = json_repair.loads(outline_str)
    
    print('[3] 校验源码文件完整覆盖')
    for _ in range(args.check):
        outline_fnames = [
            f.replace('\\', '/')
            for pt in outline['parts']
            for ch in pt['chapters']
            for n in ch['nodes']
            for f in n['src']
        ]
        rest_fnames = list(set(fnames) - set(outline_fnames))
        if len(rest_fnames) == 0:
            print('[3] 校验通过')
            break
        print('[3] 校验未通过')
        print('\n'.join(rest_fnames))
        ques = OUTLINE_FIX_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{rest_fnames}', '\n'.join(rest_fnames))
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        outline_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        outline = json_repair.loads(outline_str)

    return outline

def tr_gen_code_desc(res, idx, args, write_callback):
    fname = res[idx]['file']
    print(f'[2] 生成描述 {fname}')
    full_path = path.join(args.dir, fname)
    code = open(full_path, encoding='utf8').read()
    ques = CLS_FUNC_EXT_PMT.replace('{fname}', fname) \
        .replace('{code}', code)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
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
        path.join(path.relpath(rt, args.dir), f) \
            .replace('\\', '/')
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
        outline = gen_outline(fnames, code_desc, args)
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

    print(f'[4] 校验细纲')
    details = check_details(details, code_desc, fnames, pj_dir, args)

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
            outline_chs,
            details, i, bodies, 
            body_fname, args,
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print('[*] 已完成')
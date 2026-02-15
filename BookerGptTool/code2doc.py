import openai
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import json_repair as json
import random
import copy
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .util import call_chatgpt_retry, set_openai_props
from .code2doc_pmt import *


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

def build_vars_flds_md(jvars):
    tmpl = '''
### `{name}`
    
{desc}

类型：`{type}`
    '''
    vars_md  = '\n\n'.join(
        tmpl.replace('{name}', v['name'])
            .replace('{desc}', v['desc'])
            .replace('{type}', v['type'])
        for v in jvars['vars']
    )

    flds_md = '\n\n'.join(
        tmpl.replace('{name}', v['class'] + '.' + v['name'])
            .replace('{desc}', v['desc'])
            .replace('{type}', v['type'])
        for v in jvars['fields']
    )

    return  vars_md + '\n\n' + flds_md

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

    print('[1] 处理大纲')
    ques = OVVW_PMT.replace('{code}', code)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    ovvw_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    jovvw = json.loads(ovvw_str)
    desc = jovvw['desc']
    process = '\n'.join(jovvw['process'])
    sturcture = '\n'.join(jovvw['structure'])

    print('[2] 分析全局变量和类字段')
    flds = [
        f'{c}.{f}'
        for c in jovvw['classes']
        for f in c['fields']
    ]
    vars = jovvw['vars']
    vars_txt = '\n'.join(vars + flds)
    ques = VAR_FLD_EXT_PMT.replace('{code}', code) \
        .replace('{vars}', vars_txt)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    vars_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    jvars = json.loads(vars_str)
    vars_flds_md = build_vars_flds_md(jvars)

    print(f'[3] 分析全局函数和类方法')
    mtds = [
        f'{c}.{f}'
        for c in jovvw['classes']
        for f in c['methods']
    ]
    funcs = jovvw['funcs']
    func_md_dict = {}
    for func_name in funcs + mtds:
        print(f'[3] 分析 {func_name}')
        ques = FUNC_MTD_EXT_PMT.replace('{code}', code) \
            .replace('{func}', func_name)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        ans = ans.replace('[content]', '') \
            .replace('[/content]', '')
        func_md_dict[func_name] = ans
    
    funcs_mtds_md = '\n\n'.join(
        f'### `{k}`\n\n{v}' 
        for k, v in func_md_dict.items()
    )

    print(f'[4] 分析关键组件')
    ques = KEY_CMPN_PMT.replace('{code}', code)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    key_comp = ans.replace('[content]', '') \
            .replace('[/content]', '')
    
    print(f'[5] 分析改机建议')
    ques = ADVC_PMT.replace('{code}', code)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    advc = ans.replace('[content]', '') \
            .replace('[/content]', '')

    doc = f'''
# `{fname}` 详细设计文档

{desc}

## 整体流程

{process}

## 类结构

{sturcture}

## 全局变量及字段

{vars_flds_md}
    

## 全局函数及方法

{funcs_mtds_md}

## 关键组件

{key_comp}

## 问题及建议

{advc}
    '''
    open(ofname, 'w', encoding='utf8').write(doc)
    

def extname(name):
    m = re.search(r'\.(\w+)$', name)
    return m.group(1) if m else ''

    
def code2doc_handle(args):
    set_openai_props(args.key, args.proxy, args.host)
 
    if path.isdir(args.fname):
        process_dir(args)
    else:
        process_file(args)

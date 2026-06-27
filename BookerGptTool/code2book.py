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
from .util import ask_chatgpt_retry, set_openai_props, extname, ext_code_block, ext_cont_block
from .code2book_pmt import *
from .code2book_models import *

def check_details(details: List[Detail], code_desc: List[CodeDescItemResult], fnames, pj_dir, args):
    fixed = all(d.get('fixed', False) for d in details)
    if fixed: 
        print(f'[4] 细纲校验通过')
        return details
    for i, d in enumerate(details):
        d.no = i + 1
    fnames_li = '\n'.join(fnames)
    code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
    readme = open(path.join(args.dir, 'README.md'), encoding='utf8').read()
    total_funcs = [
        cd.file + ':' + fn.name
        for cd in code_desc
        for fn in cd.funcs
    ]
    total_funcs += [
        cd.file + ':' + cls_.name + '.' + m.name
        for cd in code_desc
        for cls_ in cd.classes
        for m in cls_.methods
    ]
    total_funcs = [
        it.replace('\\', '/').replace('()', '')
        for it in total_funcs
    ]
    for _ in range(args.check):
        exi_funcs = [
            cd.file + ':' + cd.class_or_func
            for d in details
            for u in d.units
            for cd in u.code
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
        details_str = json.dumps(
            [d.dict() for d in details], ensure_ascii=False)
        ques = DETAIL_FIX_PMT \
            .replace('{details}', details_str) \
            .replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{rest_funcs}', '\n'.join(rest_funcs))
        parse_output = lambda s: parse_obj_as(
            List[Detail], json_repair.loads(ext_code_block(s))
        )
        details: List[Detail] = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=parse_output,
        )
        sorted(details, key=lambda it: it.no)
        for i, d in enumerate(details): 
            d.fixed = True
            detail_fname = path.join(pj_dir, f'detail_{i+1}.yaml')
            open(detail_fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(d.dict(), allow_unicode=True))
    return details


def tr_gen_body(outline_chs, details, idx, bodies, fname, args):
    print(f'[5] 编写第{idx+1}章正文')
    details = parse_obj_as(List[Detail], details)
    code_fnames = [
        c.file
        for u in details[idx].units
        for c in u.code
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
    detail_str = json.dumps(details[idx].dict(), ensure_ascii=False)
    ques = BODY_PMT.replace('{detail}', detail_str) \
        .replace('{outline}', outline_str) \
        .replace('{code}', code_str) \
        .replace('{i}', str(idx + 1))
    body = ask_chatgpt_retry(
        ques, args.model, args.temp, 
        args.retry, args.max_tokens,
        parse_output=ext_cont_block,
    )
    bodies[idx] = body
    open(fname, 'w', encoding='utf8').write(body)

    print(f'[5] 校验正文 {idx + 1}')
    for _ in range(args.check):
        ques = BODY_CHK_PMT.replace('{body}', body)
        cmt = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=ext_cont_block,
        )
        if "[PERFECT/]" in cmt:
            print(f'[5] 正文 {idx + 1} 校验完成')
            break
        print(f'[5] 正文 {idx + 1} 校验未通过')
        print(cmt)
        ques = BODY_FIX_PMT.replace('{detail}', detail_str) \
            .replace('{body}', body) \
            .replace('{comment}', cmt) \
            .replace('{code}', code_str)
        body = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=ext_cont_block,
        )
        bodies[idx] = body
        open(fname, 'w', encoding='utf8').write(body)


def tr_gen_detail(outline_chs: List[OutlineChapterResult], idx, details: List[Detail], args, write_callback):
    print(f'[4] 编写第{idx+1}章细纲')
    code_fnames = [
        f for pt in outline_chs[idx].nodes
          for f in pt.src
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
    parse_output = lambda s: SrcAnlsDetailResult(
        **json_repair.loads(ext_code_block(s))
    )
    detail: SrcAnlsDetailResult = ask_chatgpt_retry(
        ques, args.model, args.temp, 
        args.retry, args.max_tokens,
        parse_output=parse_output,
    )
    details[idx] = Detail(**details[idx], **detail.dict())
    write_callback()
    detail_str = json.dumps(details[idx], ensure_ascii=False)
    ques = REST_DETAIL_PMT.replace('{detail}', detail_str) \
        .replace('{outline}', outline_str) \
        .replace('{i}', str(idx + 1)) \
        .replace('{code}', code_str)
    parse_output = lambda s: RestDetailResult(
        **json_repair.loads(ext_code_block(s))
    )
    spec_detail: RestDetailResult = ask_chatgpt_retry(
        ques, args.model, args.temp, 
        args.retry, args.max_tokens,
        parse_output=parse_output,
    )
    details[idx] = Detail(**details[idx], **spec_detail.dict())
    write_callback()

def gen_outline(fnames, code_desc: List[CodeDescItemResult], args) -> OutlineResult:
    fnames_li = '\n'.join(fnames)
    code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
    readme = open(path.join(args.dir, 'README.md'), encoding='utf8').read()
    ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
        .replace('{code_desc}', code_desc_str) \
        .replace('{readme}', readme)
    parse_output = lambda s: OutlineResult(
        **json_repair.loads(ext_code_block(s))
    )
    outline: OutlineResult = ask_chatgpt_retry(
        ques, args.model, args.temp, 
        args.retry, args.max_tokens,
        parse_output=parse_output,
    )
    
    print('[3] 校验源码文件完整覆盖')
    for _ in range(args.check):
        outline_fnames = [
            f.replace('\\', '/')
            for pt in outline.parts
            for ch in pt.chapters
            for n in ch.nodes
            for f in n.src
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
        outline = ask_chatgpt_retry(
            ques, args.model, args.temp, 
            args.retry, args.max_tokens,
            parse_output=parse_output,
        )

    return outline

def tr_gen_code_desc(res: List[CodeDescItemResult], idx, args, write_callback):
    fname = res[idx].file
    print(f'[2] 生成描述 {fname}')
    full_path = path.join(args.dir, fname)
    code = open(full_path, encoding='utf8').read()
    ques = CLS_FUNC_EXT_PMT.replace('{fname}', fname) \
        .replace('{code}', code)
    parse_output = lambda s: ClsFuncExtResult(
        **json_repair.loads(ext_code_block(s))
    )
    descs: ClsFuncExtResult = ask_chatgpt_retry(
        ques, args.model, args.temp, 
        args.retry, args.max_tokens,
        parse_output=parse_output,
    )
    res[idx] = CodeDescItemResult(file=fname, **descs.dict())
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
        code_desc = parse_obj_as(List[CodeDescItemResult], code_desc)
    else:
        code_desc = [
            CodeDescItemResult(
                file=f,
                desc='',
                process=[],
                structure=[],
                classes=[],
                funcs=[],
            )
            for f in fnames
        ]
        open(code_desc_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump([d.dict() for d in code_desc]))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback_mdl(fname, res):
        with lock:
            with open(fname, 'w', encoding='utf8') as f:
                obj = (
                    [r.dict() for r in res]
                    if isinstance(res, list)
                    else res.dict()
                )
                f.write(yaml.safe_dump(obj, allow_unicode=True))

    for i, it in enumerate(code_desc):
        if it.desc: continue
        h = pool.submit(
            tr_gen_code_desc,
            code_desc, i, 
            args,
            functools.partial(write_callback_mdl, code_desc_fname, code_desc)
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
        outline = OutlineResult(**outline)
    else:
        outline = gen_outline(fnames, code_desc, args)
        open(outline_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(outline.dict(), allow_unicode=True))


    print('[4] 生成细纲')
    outline_chs = sum([
        pt.chapters for pt in outline.parts
    ], [])
    details: List[Detail] = []
    for i, ch in enumerate(outline_chs):
        detail_fname = path.join(pj_dir, f'detail_{i+1}.yaml')
        if path.isfile(detail_fname):
            detail = yaml.safe_load(
                open(detail_fname, encoding='utf8').read())
            detail = Detail(**detail)
            details.append(detail)
            continue
        details.append(Detail())
        h = pool.submit(
            tr_gen_detail,
            outline_chs, i, details, args,
            functools.partial(write_callback_mdl, detail_fname, details[-1])
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
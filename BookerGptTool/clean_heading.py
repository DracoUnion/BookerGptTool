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
from pydantic import parse_obj_as
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .util import extname, ext_code_block, render_prompt
from .openai import ask_chatgpt_retry, set_openai_props
from .clean_heading_pmt import *
from .clean_heading_models import *

def clean_handle(args):
    print(args)
    set_openai_props(args)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f) 
            for f in os.listdir(args.fname)
        ]
    
    if not fnames:
        print('请提供 MD 文件')
        return

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(clean_file, args)
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: h.result()

def clean_md_llm(md, args, nlines=1000):
    lines = md.split('\n')
    if nlines < 1:
        ed = int(nlines * len(lines))
    else:
        ed = int(nlines)
    heading = [{
        'no': i,
        'line': l[:50] + '...' if len(l) > 50 else l,
    } for i, l in enumerate(lines[:ed])]
    heading_str = json.dumps({"lines": heading}, ensure_ascii=False)
    ques = render_prompt(CLEAN_HEAD_PMT, text=heading_str)
    parse_output = lambda s: parse_obj_as(
        List[CleanHeadingLineResult], 
        json_repair.loads(ext_code_block(s))
    )
    res: List[CleanHeadingLineResult] = ask_chatgpt_retry(
        ques, args.model, args, 
        parse_output=parse_output,
    )

    torm = set()
    for it in res:
        if it.role in ["info", "copyright", "toc"]:
            torm.add(it.no)
    
    lines = [l for i, l in enumerate(lines) if i not in torm]
    md = '\n'.join(lines)
    return md

def clean_file(args):
    md = open(args.fname, encoding='utf8').read()
    md = clean_md_llm(md, args, args.lines)
    open(args.fname, 'w', encoding='utf8').write(md)


def reg_subparser(subparsers):
    clean_parser = subparsers.add_parser("clean-heading", help="clean heading")
    clean_parser.add_argument("fname", help="MD for dir of them")
    clean_parser.add_argument("-l", "--lines", type=float, default=3000, help="ratio/lines of heading")
    clean_parser.add_argument("-t", "--threads", type=int, default=8, help="num of threads")
    clean_parser.set_defaults(func=clean_handle)

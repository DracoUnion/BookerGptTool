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
from .clean_heading_pmt import *

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
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: h.result()

def clean_file(args):
    lines = open(args.fname, encoding='utf8').read().split('\n')
    ed = int(args.ratio * len(lines))
    heading = lines[:ed]
    heading_str = json.dumps({"lines": heading})
    ques = CLEAN_HEAD_PMT.replace('{text}', heading_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    res_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    res = json_repair.loads(res_str)

    torm = set()
    for st, ed in res['info'] + res['copyright'] + res['toc']:
        for i in range(st, ed + 1):
            torm.add(i)
    
    lines = [l for i, l in enumerate(lines) if i not in torm]
    md = '\n'.join(lines)
    open(args.fname, 'w', encoding='utf8').write(md)

import openai
import httpx
import os
import traceback
import yaml
import argparse
import copy
from os import path
import json
import random
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import functools
from .util import *
from .erchuang_pmt import *

def erchuang_single(args):
    ofname = args.fname[:-3] + f'_{args.style}.md'
    if path.isfile(ofname):
        print(f'{args.fname} 已生成')
        return
    cont = open(args.fname, encoding='utf8').read()
    pmt = (
             XHS_PMT if args.style == 'xhs' 
        else GZH_PMT if args.style == 'gzh'
        else FMT_PMT if args.style == 'fmt'
        else SUM_PMT if args.style == 'sum'
        else QA_PMT if args.style == 'qa'
        else KOUBO_PMT
    )
    ques = pmt.replace('{text}', cont)
    ans = ask_chatgpt_retry(ques, args.model, args)
    ans = ans.replace('[content]', '') \
        .replace('[/content]', '')
    open(ofname, 'w', encoding='utf8').write(ans)
    print(ofname)

def gen_xhs_single_safe(args):
    try:
        erchuang_single(args)
    except KeyboardInterrupt:
        raise
    except:
        traceback.print_exc()

def erchuang_handle(args):
    print(args)
    set_openai_props(args)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f) 
            for f in os.listdir(args.fname)
        ]
    fnames = [
        f for f in fnames 
        if extname(f) == 'md'
    ]
    if not fnames:
        print('请提供 MD 文件')
        return

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(gen_xhs_single_safe, args)
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []
            
    for h in hdls: h.result()
    
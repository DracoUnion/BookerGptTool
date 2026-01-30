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

DFT_PMT = '''
把以下内容翻译成中文并整理成一篇教程，要注意（1）删除一切语气词（如啊/哦/嗯），（2）markdown格式输出。（3）保留原文**每一句**话含义。（4）标题配上合适的emoji（5）核心概念用**公式**描述。（6）保证行文流畅，每一小节前后带有自然的过渡（例如，上一节我们介绍了xxx，本节中我们来看看yyy），特别是每个列表前要有简短的一句话介绍（例如，以下是xxx）（7）内容尽可能简单直白，让初学者能够看懂（8）开头要有概述（例如，在本节课中我们将要学习xxx），结尾要有总结（本节课中我们一起学习了yyy）（9）标题要写明课程名称和编号。（10）标题，正文，列表，引用彼此之间隔两个换行符（\n\n）。（11）只需要输出教程，不需要什么其他东西（例如，根据您的要求...以下是为您生成的xxx）。

{text}
'''


def mknote_file_safe(args):
    try:
        mknote_file(args)
    except:
        traceback.print_exc()


def mknote_file(args):

    if not args.fname.endswith('.md'):
        print('请提供 MD 文件!')
        return
    if args.fname.endswith('_note.md'):
        print('文件已总结')
        return
    ofname = args.fname[:-3] + '_note.md'
    if path.isfile(ofname):
        print('文件已总结')
        return

    text = open(args.fname, encoding='utf8').read()
    total = len(text)
    ms = re.finditer(r'!\[.*?\]\(.+?\)', text)
    links = [
        (m.span()[0] / total, m.group())
        for m in ms
    ]
    ques = DFT_PMT.replace('{text}', text)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    lines = re.split(r'\n\n(?=\S)', ans)
    total = len(lines)
    for frac, link in links[::-1]:
        idx = int(frac * total)
        lines.insert(idx, link)
    res = '\n\n'.join(lines)
    open(ofname, 'w', encoding='utf8').write(res)

def mknote(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f) 
            for f in os.listdir(args.fname)
        ]


    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(mknote_file_safe, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()

    for h in hdls: h.result()
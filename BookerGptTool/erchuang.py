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

XHS_PMT = '''
假设你是一位小红书资深博主，请参考下面的素材，生成一篇吸引人的小红书笔记。

## 注意

1.  只需要输出笔记，不需要输出任何其它东西。
2.  二创要求：
    -   标题：保留原主题，但需用“二极管标题法”优化（正面/负面刺激+爆款词）；
    -   正文：基于原笔记核心观点，结尾留互动问题；
    -   标签：保留原笔记1-2个核心标签，新增2-3个相关长尾标签。
3.  标题创作规则
    -   不偏离原主题：如原标题是“正确 XXX 方法”，新标题需围绕“XXX”展开；
    -   优化表达：用“小白必看”“绝绝子”“我不允许”等爆款词替代原标题的平淡表述；
    -   加情绪刺激：正面或负面。
    -   emoji：标题前后需要添加含义相近的 emoji。
4.  正文创作规则
    -   风格：选择“轻松”“亲切”“热情”中的一种，贴合小红书主流调性；
    -   开篇：用“提出疑问”或“对比”方式；
    -   emoji：每行开头要配合含义相近的 emoji；
    -   尽可能保留素材的绝大部分观点，不要遗漏关键信息；
    -   差异化亮点：
        -   若原笔记信息单薄，补充专业细节；
        -   若原笔记是产品推荐，新增使用场景；
    -   互动引导：结尾用开放式问题。

## 素材

{text}
'''

GZH_PMT = '''
假设你是一个资深公众号作者，请参考下面的素材，生成一篇有深度的公众号文章。


## 注意

+   文章应当在五千到一万字，需要有数据支撑
+   只需要输出文章，不需要输出任何其它东西

## 素材

{text}
'''

def gen_xhs_single(args):
    suf = 'xhs' if args.style == 'xhs' else 'gzh'
    ofname = args.fname + f'_{suf}.txt'
    if path.isfile(ofname):
        print(f'{args.fname} 已生成')
        return
    cont = open(args.fname, encoding='utf8').read()
    pmt = XHS_PMT if args.style == 'xhs' else GZH_PMT
    ques = pmt.replace('{text}', cont)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
    open(ofname, 'w', encoding='utf8').write(ans)
    print(ofname)

def gen_xhs_single_safe(args):
    try:
        gen_xhs_single(args)
    except:
        traceback.print_exc()

def gen_xhs(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f) 
            for f in os.listdir(args.fname)
        ]
    fnames = [
        f for f in fnames 
        if extname(f) in ['txt', 'md']
    ]
    if not fnames:
        print('请提供 TXT 或 MD 文件')
        return

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(gen_xhs_single_safe, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()

    for h in hdls: h.result()
    
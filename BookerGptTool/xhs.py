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
2.   二创要求：
     -   标题：保留原主题，但需用“二极管标题法”优化（正面/负面刺激+爆款词）；
     -   正文：基于原笔记核心观点，新增生活化场景描述，结尾留互动问题；
     -   标签：保留原笔记1-2个核心标签，新增2-3个相关长尾标签。
3.   标题创作规则
     -   不偏离原主题：如原标题是“正确洗脸方法”，新标题需围绕“洗脸”展开；
     -   优化表达：用“小白必看”“绝绝子”“我不允许”等爆款词替代原标题的平淡表述；
     -   加情绪刺激：正面或负面。
4.   正文创作规则
     -   风格：选择“轻松”“亲切”“热情”中的一种，贴合小红书主流调性；
     -   开篇：用“提出疑问”或“对比”方式；
     -   差异化亮点：
         -   若原笔记信息单薄，补充专业细节（如原笔记说“多吃水果”，补充“苹果含果胶，帮肠道排毒”）；
         -   若原笔记是产品推荐，新增使用场景（如原笔记说“面霜好用”，补充“冬天涂完睡觉，早上脸不脱皮”）；
     -   互动引导：结尾用开放式问题（如“你平时洗脸用什么水温？评论区告诉我～”）。

## 素材

{text}
'''

def gen_xhs_single(args):
    ofname = args.fname[:-4] + '_xhs.txt'
    if path.isfile(ofname):
        print(f'{args.fname} 已生成')
        return
    cont = open(args.fname, encoding='utf8').read()
    ques = XHS_PMT.replace('{text}', cont)
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
    fnames = [f for f in fnames if extname(f) in 'txt']
    if not fnames:
        print('请提供 TXT 文件')
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
    
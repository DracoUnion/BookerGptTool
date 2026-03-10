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
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from .util import call_chatgpt_retry, set_openai_props
from .fiction_pmt import *


def write_fiction(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)

    if args.out_dir is None:
        args.out_dir = uuid.uuid4().hex
    os.makedirs(args.out_dir, exist_ok=True)

    print(f'[1] 生成世界观设定')
    setting_fname = path.join(args.out_dir, '世界观.md')
    if path.isfile(setting_fname):
        world_setting = open(setting_fname, encoding='utf8').read()
    else:
        ques = SETTING_PMT.replace('{idea}', args.idea)
        world_setting = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        open(setting_fname, 'w', encoding='utf8').write(world_setting)
    
    print(f'[2] 生成主要角色')
    role_fname = path.join(args.out_dir, '角色.md')
    if path.isfile(role_fname):
        roles =  open(role_fname, encoding='utf8').read()
    else:
        ques = ROLE_PMT.replace('{setting}', world_setting)
        roles = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        open(role_fname, 'w', encoding='utf8').write(roles)
    
    print(f'[3] 生成章节大纲')
    outline_fname =  path.join(args.out_dir, '大纲.md')
    if path.isfile(outline_fname):
        outline = open(outline_fname, encoding='utf8').read()
    else:
        ques = OUTLINE_PMT.replace('{setting}', world_setting) \
            .replace('{roles}', roles) \
            .replace('{nchapters}', str(args.chapters))
        outline = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        open(outline_fname, 'w', encoding='utf8').write(outline)
    
    print(f'[4] 生成细纲')
    details = []
    for i in range(1, args.chapters + 1):
        print(f'[4] 生成第{i}章细纲')
        detail_fname = path.join(args.out_dir, f'细纲{i}.md')
        if path.isfile(detail_fname):
            detail = open(detail_fname, encoding='utf8').read()
        else:
            ques = DETAIL_PMT.replace('{setting}', world_setting) \
                .replace('{roles}', roles) \
                .replace('{outline}', outline) \
                .replace('{i}', str(i)) 
            detail = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
            open(detail_fname, 'w', encoding='utf8').write(detail)
        details.append(detail)

    print('[5] 生成正文')
    bodies = []
    for i in range(1, args.chapters + 1):
        print(f'[5] 生成第{i}章正文')
        body_fname = path.join(args.out_dir, f'正文{i}.md')
        if path.isfile(body_fname):
            body = open(body_fname, encoding='utf8').read()
        else:
            ques = BODY_PMT.replace('{setting}', world_setting) \
                .replace('{roles}', roles) \
                .replace('{detail}', details[i - 1]) \
                .replace('{command}', args.write_command) \
                .replace('{i}', str(i)) \
                .replace('{nword}', str(args.words))
            body = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
            open(body_fname, 'w', encoding='utf8').write(body)
        bodies.append(body)

    print('[6] 润色正文')
    for i in range(1, args.chapters + 1):
        print(f'[6] 润色第{i}章正文')
        polish_fname = path.join(args.out_dir, f'润色正文{i}.md')
        if path.isfile(polish_fname):
            body = open(polish_fname, encoding='utf8').read()
        else:
            ques = POLISH_PMT.replace('{body}', bodies[i - 1]) \
                .replace('{command}', args.polish_command) \
                .replace('{style}', args.style_example)
            body = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
            open(polish_fname, 'w', encoding='utf8').write(body)
        bodies[i - 1] = body
        
    print('[*] 全部完成')
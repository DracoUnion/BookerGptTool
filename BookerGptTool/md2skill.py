import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
import re
import os
import shutil
import json_repair as json
from .util import call_chatgpt_retry, set_openai_props, extname
from .md2skill_pmt import *

def ext_toc_preface(md, preface_len=3000):
    toc = '\n'.join(re.findall(r'^#+\s+.+?$', md, re.M))
    preface = md[:preface_len]
    if len(md) > preface_len:
        preface +='\n\n[正文省略...]'
    return toc, preface

def md2skill(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    if not args.fname.endswith('.md'):
        print('请提供 MD 文件')
        return
    md = open(args.fname, encoding='utf8').read()
    print(f'[1] 生成 SCHEMA')
    toc, preface = ext_toc_preface(md)
    ques = SCHEMA_PMT.replace('{toc}', toc) \
        .replace('{preface}', preface)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    schema = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    schema = json.loads(schema)
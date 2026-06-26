import openai
import base64
import httpx
import requests
import os
import traceback
import yaml
import argparse
from os import path
import json
import random
import copy
import re
import zipfile
import subprocess as subp
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import tempfile
import uuid
from typing import *


def d(name):
    DIR = path.dirname(path.abspath(__file__))
    return path.join(DIR, name)

def get_md_title(text):
    RE_TITLE = r'^#+\x20+(.+?)$'
    m = re.search(RE_TITLE, text, flags=re.M)
    if not m:
        return None, (None, None)
    return m.group(1).strip(), m.span(1)

def epub2html_pandoc(epub):
    fname = path.join(tempfile.gettempdir(), uuid.uuid4().hex + '.epub')
    ofname = fname[:-5] + '.html'
    open(fname, 'wb').write(epub)
    subp.Popen(['pandoc', fname, '-o', ofname]).communicate()
    html = open(ofname, encoding='utf8').read()
    os.remove(fname)
    os.remove(ofname)
    return html

def tomd(html, lang=None):
    # 处理 IFRAME
    RE_IFRAME = r'<iframe[^>]*src="(.+?)"[^>]*>'
    RE_IFRAME_ALL = r'</?iframe[^>]*>'
    RE_IFRAME_REPL = r'<br/><br/><a href="\1">\1</a><br/><br/>'
    html = re.sub(RE_IFRAME, RE_IFRAME_REPL, html)
    html = re.sub(RE_IFRAME_ALL, '', html)
    js_fname = d('tomd.js')
    html_fname = path.join(tempfile.gettempdir(), uuid.uuid4().hex + '.html')
    open(html_fname, 'w', encoding='utf8').write(html)
    subp.Popen(
        ["node", js_fname, html_fname],
        shell=True,
    ).communicate()
    md_fname = re.sub(r'\.html$', '', html_fname) + '.md'
    md = open(md_fname, encoding='utf8').read()
    os.remove(html_fname)
    if lang:
        md = re.sub(r'```([\s\S]+?```)', '```' + lang + r'\1', md)
    return md

def is_pic(fname):
    ext = [
        'jpg', 'jpeg', 'jfif', 'png', 
        'gif', 'tiff', 'webp'
    ]
    m = re.search(r'\.(\w+)$', fname.lower())
    return bool(m and m.group(1) in ext)

def read_zip(fname: str) -> Dict[str, bytes]:
    bio = BytesIO(open(fname, 'rb').read())
    zip = zipfile.ZipFile(bio, 'r')
    fdict = {n:zip.read(n) for n in zip.namelist()}
    zip.close()
    return fdict

def to_kebab(name: str) -> str:
    """将技能名转为 kebab-case slug"""
    s = re.sub(r"[^\w\s\u4e00-\u9fff\-]", "", name)
    s = re.sub(r"[\s_]+", "-", s).strip("-").lower()
    return s[:60] or "unnamed"

def request_retry(method, url, retry=10, check_status=False, **kw):
    kw.setdefault('timeout', 10)
    for i in range(retry):
        try:
            r = requests.request(method, url, **kw)
            if check_status: r.raise_for_status()
            return r
        except KeyboardInterrupt as e:
            raise e
        except Exception as e:
            print(f'{url} retry {i}')
            if i == retry - 1: raise e

def reform_paras_mdcn(text, size=1500):
    text = re.sub(r'```[\s\S]+?```', '', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    lines = sum([
        re.split(r'(?<=[。，：！？；])', l) for l in lines
    ], [])
    lines = [l for l in lines if l]
    paras = ['']
    for l in lines:
        if len(paras[-1]) + len(l) > size:
            paras.append(l)
        else:
            paras[-1] += l
    return paras
    
def fix_lists(ans):
    # 调整列表格式
    ans = re.sub(r'^(\x20*)[\+\-\*]\x20+', r'\1-   ', ans, flags=re.M)
    ans = re.sub(r'^(\x20*)(\d+\.)\x20+', r'\1\2  ', ans, flags=re.M)
    return ans

def call_vlm_retry(img, ques, model_name, temp=0, retry=10, max_tokens=None, think=False):
    img_base64 = base64.b64encode(img).decode('ascii')
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": ques},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}"
                }
            },
        ]
    }]
    return call_llm_retry(msgs, model_name, temp, retry, max_tokens, think)


def repl_ins_token(msgs):
    repl_ins_token_re = lambda s: re.sub(r'<\|([\w\-\.]+)\|>', r'</\1/>', s)
    for m in msgs:
        cont = m.get('content')
        if isinstance(cont, str):
            m['content'] = repl_ins_token_re(m['content'])
        elif isinstance(cont, list):
            for it in m['content']:
                tp = it.get('type')
                if tp == 'text':
                    it['text'] = repl_ins_token_re(it['text'])
    return msgs

def ask_chatgpt_retry(ques, model_name, temp=0, retry=10, max_tokens=None, think=False):
    msgs = [{
        'role': 'user',
        'content': ques,
    }]
    return call_llm_retry(msgs, model_name, temp, retry, max_tokens, think)

def call_llm_retry(msgs, model_name, temp=0, retry=10, max_tokens=None, think=False):
    # 改变指令符号的形式，避免模型出错
    msgs = repl_ins_token(msgs)
    client = openai.OpenAI(
        base_url=openai.base_url,
        api_key=openai.api_key,
        default_headers={'User-Agent': openai.user_agent},
        http_client=httpx.Client(
            proxies=openai.proxy,
            transport=httpx.HTTPTransport(local_address="0.0.0.0"),
        ),
    )
    extra_body = {
        "chat_template_kwargs": {"enable_thinking": think},
        "enable_thinking": think,
        "think": think,
        'include_reasoning': think,
        'reasoning': {"effort": "medium" if think else "none"},
        'thinking': {"type": "enabled" if think else "disabled"},
    }
    if think:
        extra_body.update({
            "reasoning_effort": "medium" if think else "none",
        })
    for i in range(retry):
        try:
            res = client.chat.completions.create(
                messages=msgs,
                model=model_name,
                temperature=temp,
                max_tokens=max_tokens,
                extra_body=extra_body,
                stream=openai.stream,
            )
            if openai.stream:
                ans = collect_stream_content(res)
            else:
                ans = res.choices[0].message.content.strip()
            if not ans: raise ValueError(f'回复为空：{res}')
            break
        except Exception as ex:
            print(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex
    # 还原指令格式
    ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
    ans = re.sub(r'<think>[\s\S]+?</think>', '', ans)
    print(f'ans: {json.dumps(ans, ensure_ascii=False)}')
    return ans

def set_openai_props(args):
    openai.api_key = args.key
    openai.proxy = args.proxy
    openai.base_url = args.host
    openai.user_agent = args.user_agent
    openai.pren = args.pass_reasoning_effort_none
    openai.stream = args.stream

def collect_stream_content(resp):
    content = []
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            pt = chunk.choices[0].delta.content
            content.append(pt)
            print(f'stream: {json.dumps(pt, ensure_ascii=False)}')
    return ''.join(content)

def extname(fname):
    m = re.search(r'\.(\w+)$', fname)
    return m.group(1) if m else ''

def load_train_data_batch(fname, bs, exts=None):
    ds = load_train_data(fname, exts)
    iter_ = iter(ds)
    while True:
        try:
            yield [next(iter_) for _ in range(bs)]
        except StopIteration:
            break
    

def load_train_data(fname, exts=None):
    exts = exts or ['yaml', 'json', 'jsonl']
    if path.isfile(fname):
        fnames = [fname]
    elif path.isdir(fname):
        fnames = [
            path.join(fname, f) 
            for f in os.listdir(fname) 
        ]
    else:
        raise Exception('请提供 YAML 文件或其目录')
    fnames = [
        f for f in fnames
        if extname(f) in exts
    ]
    for f in fnames:
        ds = read_ds_file(f)
        for dit in ds:
            yield dit

def write_ds_file(fname, ds):
    ext = extname(fname).lower()
    if ext == 'yaml':
        data = yaml.safe_dump(ds, allow_unicode=True)
    elif ext == 'json':
        data = json.dumps(ds, ensure_ascii=False)
    elif ext == 'jsonl':
        data = '\n'.join(
            json.dumps(it, ensure_ascii=False) 
            for it in ds
        )
    else:
        raise Exception('文件必须是 JSON、JSONL、YAML')
    with open(fname, 'w', encoding='utf8') as f:
        f.write(data)


def read_ds_file(fname):
    ext = extname(fname).lower()
    data = open(fname, encoding='utf8').read()
    if ext == 'yaml':
        ds = yaml.safe_load(data)
    elif ext == 'json':
        ds = json.loads(data)
    elif ext == 'jsonl':
        lines = data.split('\n')
        ds = [
            json.loads(l)
            for l in lines if l.strip()
        ]
    else:
        raise Exception('文件必须是 JSON、JSONL、YAML')

    # random.shuffle(ds)
    return ds

def combine_prompt_args(prompt: str, args: Dict[str, Any]):
    return re.sub(r"{(\w+)}", lambda g: args.get(g.group(1), g.group(0)), prompt)

def norm_l2(arr, axis=-1):
    l2 = (arr**2).sum(axis, keepdims=True) ** 0.5
    return arr / l2


def call_glmocr_retry(img, retry=10):
    img_base64 = base64.b64encode(img).decode('ascii')
    url = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
    payload = {
        "model": "glm-ocr",
        "file": f'data:image/png;base64,{img_base64}',
    }
    headers = {
        "Authorization": f"Bearer {openai.api_key}",
        "Content-Type": "application/json"
    }
    for i in range(retry):
        try:
            res = requests.post(
                url, 
                json=payload, 
                headers=headers, 
                proxies=openai.proxy,
            )
            if res.status_code >= 400:
                raise requests.HTTPError(f'HTTP {res.status_code}: {res.text}')
            break
        except Exception as ex:
            print(f'GLM retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex
    print(res.text)
    return json.loads(res.text)['md_results']

def group_chunks(chunks, limit=8000):
    groups = ['']
    for c in chunks:
        if len(groups[-1]) +  len(c) + 2 > limit:
            groups.append(c)
        else:
            groups[-1] += '\n\n' + c
    groups = [g for g in groups if g]
    return groups

def split_md_lines(md):
    lines = md.split('\n')
    res = []
    in_code = False
    code = ''
    for l in lines:
        if '```' in l:
            if in_code: 
                # 结尾
                code += l
                res.append(code)
                code = ''
            else:
                # 开头
                code += l + '\n'
            in_code = not in_code
        elif not in_code:
            if l.strip(): res.append(l)
        elif in_code:
            code += l + '\n'
    # 处理结尾缺失情况：
    if code:
        res.append(code + '```')
    return res

tok_en_zh = lambda text: \
    re.findall(r'[\u4e00-\u9fff]|[a-zA-Z]+', text)
get_ngram_set = lambda toks, n: \
    set(tuple(toks[i:i+n]) for i in range(len(toks)-n+1))

def ngram_jaccard(text1: str, text2: str, n: int = 3) -> float:
    set1 = get_ngram_set(tok_en_zh(text1.lower()), n)
    set2 = get_ngram_set(tok_en_zh(text2.lower()), n)
    inter = len(set1 & set2)
    union = len(set1 | set2)
    return inter / union if union else 0.0

def ngram_coverage(src: str, gen: str, n: int = 3) -> float:
    src_set = get_ngram_set(tok_en_zh(src.lower()), n)
    gen_set = get_ngram_set(tok_en_zh(gen.lower()), n)
    inter = len(src_set & gen_set)
    all_ = len(gen_set)
    return inter / all_ if all_ else 0.0
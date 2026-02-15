from .util import *
import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
from .paper2code_pmt import *

dft_hdrs = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}

def arxiv_id2text(aid):
    url = f'https://arxiv.org/src/{aid}'
    data = requests.get(url, headers=dft_hdrs).content
    tar = tarfile.open(fileobj=BytesIO(data), mode='r:gz')
    tex_fnames = [
        n for n in tar.getnames()
        if n.endswith('.tex')
    ]
    if not tex_fnames:
        raise FileNotFoundError('找不到 TEX 文件')
    tex = '\n'.join([
        tar.extractfile(f).read().decode('utf8')
        for f in tex_fnames
    ])
    return tex

def ext_chapters(tex):
    title = re.findall(r'\\title\{(.+?)\}', tex)
    if not title: raise ValueError('找不到标题')
    abs_ = re.findall(r'\\begin\{abstract\}([\s\S]+?)\\end\{abstract\}', tex)
    if not abs_: raise ValueError('找不到摘要')
    chs = re.findall(r'\\section\{(.+?)\}([\s\S]+?)(?=\\section|\Z)', tex)
    # chs = {title:cont for title, cont in chs}
    return title[0], abs_[0], chs

def paper2code(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    print('[Downloading] download arxiv paper')
    tex = arxiv_id2text(args.arxiv)
    title, abs_, chs = ext_chapters(tex)
    print('[Planning] Overall plan')
    ques = PLAN_PMT.replace("{paper}", tex)
    plan = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    print('"[Planning] Architecture design')
    ques = FLIST_PMT.replace("{paper}", tex) \
        .replace('{plan}', plan)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    flist_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    print('"[Planning] Logic design')
    ques = TASKS_PMT.replace("{paper}", tex) \
        .replace('{plan}', plan) \
        .replace('{flist}', flist_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    tasks_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    print('[Planning] Configuration file generation')
    ques = CFG_PMT.replace("{paper}", tex) \
        .replace('{plan}', plan) \
        .replace('{flist}', flist_str) \
        .replace('{tasks}', tasks_str)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    cfg_str = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
    # print(plan, '\n', flist_str, '\n', tasks_str, '\n', cfg_str)
    jtask = json.loads(tasks_str)
    tasks = jtask['task_list']
    fdesc_dict = jtask['file_descs']
    logic_anls_dict = {}
    
    for fname in tasks:
        print(f"[ANALYSIS] {fname}")
        fdesc = fdesc_dict.get(fname, '“未指定”')
        ques = ANLS_PMT.replace("{paper}", tex) \
            .replace('{plan}', plan) \
            .replace('{flist}', flist_str) \
            .replace('{tasks}', tasks_str) \
            .replace('{config}', cfg_str) \
            .replace('{fname}', fname) \
            .replace('{fdesc}', fdesc)
        logic_anls = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        logic_anls_dict[fname] = logic_anls
    
    # print(logic_anls)
    code_dict = {}
    for idx, fname in enumerate(tasks):
        print(f"[CODING] {fname}")
        done_files = ','.join(code_dict.keys()) or 'none'
        logic_analysis = logic_anls_dict.get(fname, "“未指定”")
        ques = CODE_PMT.replace("{paper}", tex) \
            .replace('{plan}', plan) \
            .replace('{flist}', flist_str) \
            .replace('{tasks}', tasks_str) \
            .replace('{config}', cfg_str) \
            .replace('{done_file_list}', done_files) \
            .replace('{todo_file_name}', fname) \
            .replace('{logic_analysis}', logic_analysis)
        code = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        code_dict[fname] = code

    print(code_dict)


    


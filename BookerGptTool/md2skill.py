import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
import re
import os
import shutil
import yaml
import json_repair as json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import functools
from .util import call_chatgpt_retry, set_openai_props, extname, reform_paras_mdcn
from .md2skill_pmt import *
from .skill_validator import *
from .md_chunker import chunk_markdown

TYPE_PMT_MAP = {
    '技术手册': TECH_EXT_PMT,
    '叙事类': NARRATIVE_EXT_PMT,
    '方法论': METHOD_EXT_PMT,
    '学术教材': ACADEMIC_EXT_PMT,
    '保险合同': INSURANCE_EXT_PMT,
    '行业报告': REPORT_EXT_PMT,
    '医学法律': MED_LGL_EXT_PMT,
    '流程规范': PROC_EXT_PMT,
}

def get_pmt_by_type(tp):
    """根据 book_type 解析出 (prompt_name, user_template)"""
    # 精确匹配
    if tp in TYPE_PMT_MAP:
        return TYPE_PMT_MAP[tp]

    # 模糊匹配
    bt = tp.lower()
    if any(kw in bt for kw in ("叙事", "小说", "故事", "fiction", "narrative")):
        return TYPE_PMT_MAP["叙事类"]
    if any(kw in bt for kw in ("方法", "框架", "methodology", "framework")):
        return TYPE_PMT_MAP["方法论"]
    if any(kw in bt for kw in ("教材", "学术", "academic", "textbook")):
        return TYPE_PMT_MAP["学术教材"]
    if any(kw in bt for kw in ("保险", "保单", "保障", "理赔", "insurance")):
        return TYPE_PMT_MAP["保险合同"]
    if any(kw in bt for kw in ("报告", "研报", "白皮书", "report")):
        return TYPE_PMT_MAP["行业报告"]
    if any(kw in bt for kw in ("医学", "法律", "金融", "medical", "legal")):
        return TYPE_PMT_MAP["医学法律"]
    if any(kw in bt for kw in ("规范", "标准", "规程", "条例", "手册", "manual", "guide", "操作")):
        return TYPE_PMT_MAP["技术手册"]

    return DFT_EXT_PMT

def tr_gen_raw_skill(tp, paras, idx, args, write_callback):
    ques = get_pmt_by_type(tp) \
        .replace('{content}', paras[idx]['content']) \
        .replace('{context}', paras[idx]['context'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    paras[idx]['raw_skills'] = ans.replace('[content]', '') \
        .replace('[/content]', '')
    write_callback()


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

    yaml_fname = args.fname[:-3] + '.yaml'
    if path.isfile(yaml_fname):
        res = yaml.safe_load(
            open(yaml_fname, encoding='utf8').read())
    else:
        res = {}
        open(yaml_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(res, allow_unicode=True))

    print(f'[1] 生成 SCHEMA')
    if res.get('schema'):
        schema = res['schema']
    else:
        toc, preface = ext_toc_preface(md)
        ques = SCHEMA_PMT.replace('{toc}', toc) \
            .replace('{preface}', preface)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        schema = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        schema = json.loads(schema)
        res['schema'] = schema
        open(yaml_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(res, allow_unicode=True))
    
    print(f'[2] 生成原始技能')
    if res.get('paras'):
        paras = res['paras']
    else:
        paras = chunk_markdown(
            md, path.basename(args.fname)[:-3]).chunks
        paras = [{
            'content': p.content, 
            'context': p.context,
            'raw_skills': ''
        } for p in paras]
        res['paras'] = paras
        open(yaml_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(res, allow_unicode=True))
    
    pool = ThreadPoolExecutor(args.threads)
    hdls = []

    lock = Lock()
    def write_callback(fname, res):
        with lock:
            open(yaml_fname, 'w',  encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))

    for i, p in enumerate(paras):
        if p.get('raw_skills'): continue
        h = pool.submit(
            tr_gen_raw_skill, 
            schema['book_type'],
            paras, i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print(f'[3] 校验原始技能')
    if res.get('passed_skills'):
        passed_skills = res['passed_skills']
    else:
        skills = [
            p['raw_skills'].split('\n---\n') 
            for p in paras
        ]
        skills = functools.reduce(lambda x, y: x + y, skills, [])
        validator = SkillValidator()
        passed_skills, _ = validator.validate_batch([
            RawSkill(s) for s in skills
        ])
        passed_skills = [s.raw_text for s in passed_skills]
        res['passed_skills'] = passed_skills
        open(yaml_fname, 'w',  encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))
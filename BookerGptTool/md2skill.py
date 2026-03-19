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
from sentence_transformers import SentenceTransformer
from typing import Dict, Optional, List, Callable
from .util import call_chatgpt_retry, set_openai_props, extname, reform_paras_mdcn
from .md2skill_pmt import *
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

# 领域同义词库：值 → 标准化标签
DOMAIN_SYNONYMS: dict[str, str] = {
    # 保险领域
    "保险": "保险", "insurance": "保险", "保障": "保险",
    "理赔": "保险·理赔", "赔付": "保险·理赔", "claims": "保险·理赔",
    "核保": "保险·核保", "承保": "保险·核保", "underwriting": "保险·核保",
    # 法律领域
    "法律": "法律", "法规": "法律", "legal": "法律", "法务": "法律",
    "合同": "法律·合同", "contract": "法律·合同", "条款": "法律·合同",
    # 技术领域
    "技术": "技术", "technology": "技术", "tech": "技术",
    "开发": "技术·开发", "编程": "技术·开发", "programming": "技术·开发",
    "运维": "技术·运维", "devops": "技术·运维", "ops": "技术·运维",
    # 医学领域
    "医学": "医学", "medical": "医学", "临床": "医学·临床",
    "药学": "医学·药学", "pharmacy": "医学·药学",
    # 金融领域
    "金融": "金融", "finance": "金融", "财务": "金融",
    "投资": "金融·投资", "investment": "金融·投资",
}

def parse_raw_skill(raw_skill: str) -> Optional[Dict[str, str]]:
    RE_RAW_SKILL = r'```ya?ml\n([\s\S]+?)\n```\n([\s\S]+)'
    m = re.search(RE_RAW_SKILL, raw_skill)
    if not m: return None
    try:
        res = yaml.safe_load(m.group(1))
    except:
        return None
    res['body'] = m.group(2)
    res['raw_text'] = raw_skill
    # 补全缺失字段
    if 'name' not in res:
        first_line = res['body'].split("\n")[0].strip("# ").strip()
        res["name"] = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", first_line).strip("-").lower()[:50]
    if 'trigger' not in res:
        res['trigger'] = "通用知识查询"
    return res

def normalize_skills_tags(skills: List[Dict[str, str]]) -> dict[str, str]:
    """
    批量归一化所有 Skill 的 domain 标签。

    返回原始 → 归一化的映射表。
    """
    tag_map: dict[str, str] = {}
    for skill in skills:
        ori = skill.get('domain', '')
        norm = DOMAIN_SYNONYMS.get(ori.lower(), ori)
        if ori != norm:
            tag_map[ori] = norm
            skill['domain'] = norm
    return tag_map

def cluster_skills(
    skills: List[Dict[str, str]], 
    emb_model_name: str,
    threshold: float = 0.88
) -> List[List[Dict[str, str]]]:
    normalize_skills_tags(skills)

    # 构建每个 Skill 的文本表示（trigger + body 前 500 字）
    texts = [
        f"{s['trigger']} {s['body'][:500]}" 
        for s in skills
    ]

    st = SentenceTransformer(emb_model_name)
    vectors = st.encode(texts)
    sims = st.similarity(vectors)

    # 贪心聚类
    used = [False] * len(skills)
    clusters = []

    for i in range(len(skills)):
        if used[i]:  continue
        cluster = [skills[i]]
        used[i] = True

        for j in range(i + 1, len(skills)):
            if used[j]:  continue
            # 余弦相似度
            sim = sims[i, j]
            if sim >= threshold:
                cluster.append(skills[j])
                used[j] = True
        
        clusters.append(cluster)

    return clusters

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

def check_hallucination(
    body: str, source_text: str
) -> bool:
    """
    幻觉初筛：检查 body 中的关键术语是否在 source_text 中出现。

    策略：提取 body 中的非常见术语（> 2 字的中文词或英文词），
    检查其是否在原文中出现。超过 40% 的术语未出现则标记疑似幻觉。
    """

    # Skill 结构性停用词（R1 输出的格式标签，不属于幻觉）
    _CN_STOPWORDS = {
        "执行步骤", "输出格式", "格式要求", "输出格式要求", "前置条件",
        "触发条件", "判断条件", "操作步骤", "注意事项", "具体步骤",
        "排查步骤", "解决方案", "处理方法", "诊断步骤", "核心步骤",
        "检查项目", "原因分析", "结果输出", "结论建议", "适用场景",
        "原因为", "如适用", "事件详情", "配置项", "存在状态",
        "匹配情况", "检查上游", "列出调度", "资源使用", "解决办法",
    }
    _EN_STOPWORDS = {
        "this", "that", "with", "from", "your", "have", "will", "when",
        "null", "true", "false", "none", "else", "step", "then", "each",
        "following", "output", "input", "check", "verify", "ensure",
        "execute", "confirm", "should", "must", "below", "above",
        "format", "result", "trigger", "domain", "skill", "prerequisites",
    }

    # 提取 body 中的关键词（> 2 字中文词或英文单词）
    cn_terms = set(re.findall(r"[\u4e00-\u9fff]{3,}", body)) - _CN_STOPWORDS
    en_terms = set(
        w.lower()
        for w in re.findall(r"[A-Za-z]{4,}", body)
        if w.lower() not in _EN_STOPWORDS
    )

    all_terms = cn_terms | en_terms
    if len(all_terms) < 3:
        return True

    source_lower = source_text.lower()
    missing = {t for t in all_terms if t.lower() not in source_lower}

    miss_rate = len(missing) / len(all_terms)
    if miss_rate > 0.4:
        return False

    return True

def tr_gen_raw_skill(tp, paras, idx, args, write_callback):
    ques = get_pmt_by_type(tp) \
        .replace('{content}', paras[idx]['content']) \
        .replace('{context}', paras[idx]['context'])
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    raw_skills = ans.replace('[content]', '') \
        .replace('[/content]', '').split('---')
    raw_skills = [parse_raw_skill(rs) for rs in raw_skills]
    raw_skills = [
        rs for rs in raw_skills 
        if rs and check_hallucination(rs['body'], paras[idx]['content'])
    ]
    paras[idx]['raw_skills'] = raw_skills
    write_callback()

def tr_merge_cluster(
    cluster: List[Dict[str, str]],
    skills: List[str], idx: int, 
    args: object, write_callback: Callable,
):
    text = '\n---\n'.join([s['raw_text'] for s in cluster])
    ques = REDUCE_PMT.replace('{count}', str(len(cluster))) \
        .replace('{skills}', text)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    merged = ans.replace('[content]', '') \
        .replace('[/content]', '')
    skills[idx] = merged
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

    output_dir = args.fname[:-3] + '_md2skill'
    os.makedirs(output_dir, exist_ok=True)

    print(f'[1] 生成 SCHEMA')
    schema_fname = path.join(output_dir, 'schema.yaml')
    if path.isfile(schema_fname):
        schema = yaml.safe_load(open(schema_fname, encoding='utf8').read())
    else:
        toc, preface = ext_toc_preface(md)
        ques = SCHEMA_PMT.replace('{toc}', toc) \
            .replace('{preface}', preface)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        schema = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
        schema = json.loads(schema)
        open(schema_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(schema))
    
    print(f'[2] 生成原始技能')
    raw_skill_fname = path.join(output_dir, 'raw_skills.yaml')
    if path.isfile(raw_skill_fname):
        raw_skills = yaml.safe_load(
            open(raw_skill_fname, encoding='utf8').read())
    else:
        chunks = chunk_markdown(
            md, path.basename(args.fname)[:-3]).chunks
        raw_skills = [{
            'content': c.content, 
            'context': c.context,
            'raw_skills': ''
        } for c in chunks]
        open(raw_skill_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(raw_skills, allow_unicode=True))
    
    pool = ThreadPoolExecutor(args.threads)
    hdls = []

    lock = Lock()
    def write_callback(fname, res):
        with lock:
            open(fname, 'w',  encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))

    for i, p in enumerate(raw_skills):
        if p.get('raw_skills'): continue
        h = pool.submit(
            tr_gen_raw_skill, 
            schema['book_type'],
            raw_skills, i, args,
            functools.partial(write_callback, raw_skill_fname, raw_skills),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls:
        h.result()
    hdls = []

    print(f'[3] 原始技能聚类')
    clusters_fname = path.join(output_dir, 'clusters.yaml')
    if path.isfile(clusters_fname):
         clusters = yaml.safe_load(
            open(clusters_fname, encoding='utf8').read())
    else:
        skills = [rs['raw_skills'] for rs in raw_skills]
        skills = functools.reduce(lambda x, y: x + y, skills, [])
        clusters = cluster_skills(skills, args.emb)
        open(clusters_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(clusters, allow_unicode=True))


    skills_fname = path.join(output_dir, 'skills.yaml')
    if path.isfile(skills_fname):
        skills = yaml.safe_load(
            open(skills_fname, encoding='utf8').read())
    else:
        skills = ['' for _ in len(clusters)]
        for i, s in enumerate(clusters):
            h = pool.submit(
                tr_merge_cluster,
                s, skills, i, args,
                functools.partial(write_callback, skills_fname, skills)
            )
            hdls.append(h)
            if len(hdls) > args.threads:
                for h in hdls: h.result()
                hdls = []
        
        for h in hdls: h.result()
        hdls = []
            
        

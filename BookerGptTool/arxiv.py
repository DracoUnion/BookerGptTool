from .util import *
import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
from sentence_transformers import SentenceTransformer

dft_hdrs = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}


ARXIV_SUM_PMT = '''
假设你是一个高级科研人员和人工智能专家，遵循给定格式和注意事项，总结给定的段落。

## 注意事项

1.  每个段落需要总结成一个概要和至少三个子概要，均为20-50字
2.  确保原文中每一段都得到总结，也就是概要中的段落数和原文相等，不要漏掉任何一段！
3.  不要省略概要前的格式（-   ）或（1.  ），否则我无法解析。

## 格式

原文：
-   {段落1}
-   {段落2}
-   ...
概要：
-   {概要1}
    1.  {子概要1}
    2.  {子概要2}
    3.  ...
-   {概要2}
    1.  ...
-   ...

## 要总结的段落

原文：
{text}
概要：
'''

ARXIV_QA_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，遵循给定格式和注意事项，根据给定片段回答给定问题。

## 注意事项

1.  保证不要漏掉每个问题，也就是回答数量和问题数量要匹配。
2.  不要省略回答前的格式（-   ），否则我无法解析。

## 格式

概要：
{概要}
问题：
-   {问题1}
-   {问题2}
-   ...
回答：
-   {回答1}
-   {回答2}
-   ...

## 以下是需要处理的内容

概要：
{sum}
问题：
{ques}
回答：
'''

sum_queses = [
    '这篇文章的方法有什么意义，对现实世界有什么价值？',
    '这篇文章的方法相比现有方法有哪些优势，解决了什么现有方法解决不了的问题？'
    '该方法的主要步骤是什么？（该方法关键变量请使用latex展示！！！）',
    '如果该方法提出了新的网络结构，新结构的设计是什么样子，如果没有，该方法如何利用已有网络？',
    '这篇文章所使用的【数据集】、【任务类型】和【评价指标】是什么？',
    '该方法在每个【数据集】、【任务类型】和【评价指标】上，实现了什么性能，与现有方法对比如何？（请列出具体数值！！！）',
    '这篇文章还存在什么问题，其后续工作中有哪些改进路径？',
]

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

def tr_sum_text_safe(*args, **kw):
    try:
        tr_sum_text(*args, **kw)
    except:
        traceback.print_exc()

def tr_sum_text(it, args, write_func):
    RE_LIST = r'^(?:\x20{4})?(?:\-\x20{3}|\d\.\x20\x20).+?$'
    ques = ARXIV_SUM_PMT.replace('{text}', '-   ' + it['text'])
    ans = call_chatgpt_retry(ques, args.model, args.retry)
    sums = re.findall(RE_LIST, ans, flags=re.M)
    it['summary'] = '\n'.join(sums)
    write_func()

'''
def clsf_chs(chs, model_path):
    m3e = SentenceTransformer(model_path)
    title_embs = m3e.encode([title for title, _ in chs])
    cate_embs = m3e.encode(CATES)
    title_embs = norm_l2(title_embs)
    cate_embs = norm_l2(cate_embs)
    sim_mat = title_embs @ cate_embs.T
    sim_idcs = np.argsort(sim_mat, -1)[:, ::-1][:, :3]
    sim_scs = np.sort(sim_mat, -1)[:, ::-1][:, :3]

    cate_ch_map = { 
        c:'' for c in CATES
    }
    for title_idx, (title, cont) in enumerate(chs):
        for cate_idx, sc in zip(sim_idcs[title_idx], sim_scs[title_idx]):
            # if sc < 0.8: continue
            cate = CATES[cate_idx]
            print(f'title: {title}, cate: {cate}, sim: {sc}')
            cate_ch_map[cate] += '{' + title + '}' + cont
    return  cate_ch_map
'''
def reform_paras_texen(text, size=5000):
    text = text.replace('\n', ' ')
    lines = re.split(r'(?<=[\.,:!\?;]\x20)', text)
    lines = [l for l in lines if l]
    paras = ['']
    for l in lines:
        if len(paras[-1]) + len(l) > size:
            paras.append(l)
        else:
            paras[-1] += l
    return paras
    
    

def sum_arxiv(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    
    yaml_fname = args.arxiv + '.yaml'
    if path.isfile(yaml_fname):
        tosum = yaml.safe_load(open(yaml_fname, encoding='utf8'))
    else:
        # 从 Arxiv 下载 tex
        tex = arxiv_id2text(args.arxiv)
        # 提取摘要和段落
        title, abs_, chs = ext_chapters(tex)
        paras = reform_paras_texen(''.join(f'{title}: {body}' for title, body in chs))
        tosum = {
            'title': title,
            'abs': abs_,
            'paras': [{'text': p} for p in paras]
        }
    
    # 创建锁和写入回调
    lock = Lock()
    def write_callback():
        with lock:
            open(yaml_fname, 'w', encoding='utf8').write(yaml.safe_dump(tosum))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for it in tosum['paras']:
        if 'summary' in it: continue
        hdl = pool.submit(tr_sum_text_safe, it, args, write_callback)
        hdls.append(hdl)
    for h in hdls: h.result()

    if 'qas' not in tosum:
        summary = '\n'.join([p['summary'] for p in tosum['paras']])
        summary = f'-   标题：{title}\n-   摘要：{abs_}\n{summary}'
        ques = ARXIV_QA_PROMPT.replace('{sum}', summary) \
                .replace('{ques}', '\n'.join('-   ' + q for q in sum_queses))
        ans = call_chatgpt_retry(ques. args.model, args.retry)
        sum_anses = re.findall(r'^\-\x20{3}(.+?)$', ans, re.M)
        assert len(sum_queses) == len(sum_anses)
        tosum['qas'] = [{'question': q, 'answer': a} for q, a in zip(sum_queses, sum_anses)]
        write_callback()

    # 总结摘要
    res = f'# 【GPT总结】 {title}\n\n'
    res += f'> 原文：<https://ar5iv.labs.arxiv.org/html/{args.arxiv}>\n\n'
    res += '\n\n'.join([
        '## ' + qa['question'] + '\n\n' + qa['answer']  
        for qa in tosum['qas']
    ])

    ofname = args.arxiv + '.md'
    open(ofname, 'w', encoding='utf8').write(res)

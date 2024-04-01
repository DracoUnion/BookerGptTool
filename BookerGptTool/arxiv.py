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

CATES = ['背景', '相关工作', '方法', '实验', '总结']

ARXIV_CLS_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请将给定的论文段落分为【背景】、【相关工作】、【方法】、【实验】、【总结】之一。注意段落可能有多个，每个都需要分类，不要漏掉其中任何一个！

## 示例

段落：
-   In recent years, large pretrained language models, especially in Transformer-based architectures (e.g., GPT; Brown et al. 2020), have shown strong emergent in-context learning (ICL) ability (Wei et al., 2022; Dong et al., 2023). Different from finetuning which needs additional parameter updates, ICL just needs several demonstration examples prepended ∗Contribution during internship at Microsoft Research.
-   Related Work Recently, some pieces of work have attempted to understand the inference mechanism of in-context learning. Xie et al. (2022) explain in-context learning as implicit Bayesian inference. They state that in-context learning emerges when language models can infer the shared latent concept among the demonstration examples, which is learned during pretraining. On another aspect, Olsson et al. (2022) focus on specific modules in Transformers. They find some induction heads in Transformers that refer to abstract patterns in previous sequences to help predict the next token. 
-   Understanding In-Context Learning (ICL) as Implicit Finetuning We first qualitatively analyze the Transformer attention under a relaxed linear attention form to figure out a dual form between it and gradient descent. Then, we compare in-context learning with explicit finetuning to analyze connections between these two optimization forms. Based on these theoretical findings, we propose to understand in-context learning as implicit finetuning
-   Experiments  Experimental Settings We analyze two off-the-shelf pretrained GPT models with 1.3 billion and 2.7 billion model parameters, respectively, which are released by fairseq1 . In the rest of this paper, we call them GPT 1.3B and GPT 2.7B for short. All experiments are conducted on NVIDIA V100 GPUs with 32 GB memory. For each task, we use the same template to format examples for zero-shot learning (ZSL), finetuning (FT), and in-context learning (ICL). 
-   Conclusion In this paper, we aim to explain the working mechanism of GPT-based ICL. Theoretically, we figure out a dual form between Transformer attention and gradient descent, and propose to understand ICL as a process of meta-optimization. Further, we analyze connections between ICL and explicit finetuning and show the reasonability to regard ICL as implicit finetuning. Empirically, we comprehensively compare ICL and finetuning based on six real NLP tasks. 
类别：
-   背景
-   相关工作
-   方法
-   实验
-   总结

## 以下是需要处理的内容

段落：
{text}
类别：
'''

ABS_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本，概述这篇文章提出了什么方法，利用了什么技术或者模型，实现了什么效果。注意要使用中文！！！

## 论文

{text}
'''

BG_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本，完成以下要求。注意要使用中文！！！

+   概述这篇文章的方法有什么意义，对现实世界有什么价值。
+   描述这篇文章的方法相比现有方法有哪些优势，解决了什么现有方法解决不了的问题

## 论文

{text}
'''

RLT_WK_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本，列举出相关的已有方法。注意要使用中文！！！

## 论文

{text}
'''

MTD_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本，完成如下要求。注意要使用中文！！！

+   详细描述该方法的主要步骤。
+   如果该方法提出了新的网络结构，详细描述新结构的设计，
+   如果没有，详细描述该方法如何利用已有网络。
+   该方法关键变量请使用latex展示！！！

## 论文

{text}
'''

EXP_PROMPT  = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本完成如下要求，注意要使用中文！！！

+    总结这篇文章所使用的【数据集】、【任务类型】和【评价指标】。
+    之后，总结该方法在每个【数据集】、【任务类型】和【评价指标】上，实现了什么性能，与现有方法对比如何。
+    请列出具体数值！！！

## 论文

{text}
'''

FTR_WK_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定的论文文本，结这个方法还存在什么问题，尝试推测其后续工作中有哪些改进路径。注意要使用中文！！！

## 论文

{text}
'''

cate_prompts = {
    '摘要': ['概述', ABS_PROMPT],
    '背景': ['意义与创新点', BG_PROMPT],
    '相关工作': ['相关工作', RLT_WK_PROMPT],
    '方法': ['方法', MTD_PROMPT],
    '实验': ['实验', EXP_PROMPT],
    '总结': ['未来工作', FTR_WK_PROMPT],
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
    tex = tar.extractfile(tex_fnames[0]) \
             .read().decode('utf8')
    return tex
    
def ext_chapters(tex):
    title = re.findall(r'\\title\{(.+?)\}', tex)
    if not title: raise ValueError('找不到标题')
    abs_ = re.findall(r'\\begin\{abstract\}([\s\S]+?)\\end\{abstract\}', tex)
    if not abs_: raise ValueError('找不到摘要')
    chs = re.findall(r'\\section\{(.+?)\}([\s\S]+?)(?=\\section|\Z)', tex)
    # chs = {title:cont for title, cont in chs}
    return title[0], abs_[0], chs

def clsf_chs(chs, model_path):
    print(chs)
    m3e = SentenceTransformer(model_path)
    title_embs = m3e.encode([title for title, _ in chs])
    cate_embs = m3e.encode(CATES)
    title_embs = norm_l2(title_embs)
    cate_embs = norm_l2(cate_embs)
    sim_mat = title_embs @ cate_embs.T
    sim_idcs = np.argsort(sim_mat, -1)[:, :3]
    sim_scs = np.sort(sim_mat, -1)[:, :3]

    cate_ch_map = { 
        c:'' for c in CATES
    }
    for title_idx, (title, cont) in enumerate(chs):
        for cate_idx, sc in zip(sim_idcs[title_idx], sim_scs[title_idx]):
            if sc < 0.8: continue
            cate = CATES[cate_idx]
            cate_ch_map[cate] += '{' + title + '}' + cont
    return  cate_ch_map


    
    

def sum_arxiv(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    ofname = f'{args.arxiv}_sum.txt'
    if path.isfile(ofname):
        raise FileExistsError(f'{args.arxiv} 已总结')
    # 从 Arxiv 下载 tex
    tex = arxiv_id2text(args.arxiv)
    # 提取摘要和段落
    title, abs_, chs = ext_chapters(tex)
    # 调用嵌入向量分类
    cate_ch_map = clsf_chs(chs, args.emb)
    # 总结摘要
    res = f'# 【GPT总结】 {title}\n\n'
    res += f'> 原文：<https://ar5iv.labs.arxiv.org/html/{args.arxiv}>\n\n'
    ques = ABS_PROMPT.replace('{text}', abs_)
    ans = call_chatgpt_retry(ques, args.model, args.retry)
    res += f'## 概述\n\n{ans}\n\n'
    # 总结各个段落
    for c, ch in cate_ch_map.items():
        if not ch: continue
        pmt = cate_prompts[c][1]
        ques = pmt.replace('{text}', ch)
        ans = call_chatgpt_retry(ques, args.model, args.retry)
        subtitle = cate_prompts[c][0]
        res += f'## {subtitle}\n\n{ans}\n\n'

    open(ofname, 'w', encoding='utf8').write(res)

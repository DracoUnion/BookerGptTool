from .util import *
import requests
import tarfile
from io import BytesIO

dft_hdrs = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}

DFT_ARXIV_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定大纲总结给定论文。注意要使用中文！！！

## 大纲

（1）概述：请结合【abstract】章节的内容，先概述这篇文章提出了什么方法，利用了什么技术或者模型，实现了什么效果

（2）重要性：请结合【backgroud】章节的内容，概述这篇文章的方法有什么意义，对现实世界有什么价值

（3）相关工作：请结合【related work】章节的内容，列举出相关的已有方法

（4）创新点：请结合【related work】章节的内容，描述这篇文章的方法相比现有方法有哪些优势，解决了什么现有方法解决不了的问题

（5）细节：请结合【method】章节的内容，详细描述该方法的主要步骤。如果该方法提出了新的网络结构，详细描述新结构的设计，如果没有，详细描述该方法如何利用已有网络。如果该方法关键变量请使用latex展示！！！

（6）实验设置：请结合【experiments】章节，总结这篇文章所使用的【数据集】、【任务类型】和【评价指标】。

（7）实验结果：请结合【experiments】章节，总结该方法在每个【数据集】、【任务类型】和【评价指标】上，实现了什么性能，与现有方法对比如何。请列出具体数值！！！

（8）未来工作：请结合【conclusion】章节，总结这个方法还存在什么问题，尝试推测其后续工作中有哪些改进路径

## 论文

{text}
'''

def sum_arxiv(args):
    print(args)
    aid = args.arxiv
    set_openai_props(args.key, args.proxy, args.host)
    url = f'https://arxiv.org/src/{aid}'
    data = requests.get(url, headers=dft_hdrs).content
    tar = tarfile.open(fileobj=BytesIO(data), mode='r:gz')
    tex_fnames = [
        n for n in tar.getnames()
        if n.endswith('.tex')
    ]
    if not tex_fnames:
        print('找不到 TEX 文件')
        return
    tex = tar.extractfile(tex_fnames[0]) \
             .read().decode('utf8')[:args.limit]
    ques = args.prompt.replace('{text}', tex)
    ans = call_openai_retry(ques, args.model, args.retry)
    open(f'{aid}.txt', 'w', encoding='utf8').write(ans)

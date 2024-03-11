from .util import *
import requests
import tarfile
from io import BytesIO

dft_hdrs = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}

DFT_ARXIV_PROMPT = '''
假设你是一个高级科研人员和人工智能专家，请按照给定格式总结给定论文。注意要使用中文！！！

## 格式

1.  创新点
2.  重要性
3.  相关工作
    1.   {工作1}
    2.   {工作2}
    3.   ...
4.  所使用的模型
    1.  模型名称
    2.  模型结构简介
5.  实验设置
    1.  数据集
    2.  任务类型
    3.  评价指标
6.  实验结果
    1.  本模型在 {任务} 和 {数据集} 上的 {指标} 是 {xxx}，对比 {其它模型} 的 {yyy} 提升了 {zzz}。
7.  未来工作

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

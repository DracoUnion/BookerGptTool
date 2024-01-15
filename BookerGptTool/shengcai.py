from BookerEpubTool.util import *
from os import path
import yaml
from .util import *

DFT_SHENGCAI_PROMPT = '''
假设你是一个公司的技术总监、商业分析师和人工智能专家。市场部门的员工会定期向你发送商机，你需要将它们变成一个可盈利的项目。请参考格式要求，分析商机并从中提取盈利点、操作流程和所需软件。

注意事项：

-  公司只能分配一个两到三人的项目组处理这个商机，请保证流程尽可能自动化。
-  所需软件的预算是有限制的，尽可能使用开源软件。如果可能，列出它们的名称并且给出链接。
-  如果没有现有软件满足要求，可以要求项目组成员临时开发，请标注【需要开发】。
-  流程应当尽可能详细，例如，不要只输出“生成一篇xxx产品的软文”，应当输出“首先，使用xxx提示词生成yyy软文的标题；之后使用zzz提示词针对每个标题生成正文。”

格式：

商机：
盈利点：
操作步骤：
所需软件：

以下是需要分析的商机。

商机：{text}
'''

def get_content(html):
    rt = pq(rm_xml_header(html))
    return rt('body').text().strip().replace('\n', ' ')

def parse_shengcai(args):
    openai.api_key = args.key
    openai.proxy = args.proxy
    openai.host = args.host
    if not args.fname.endswith('.epub'):
        print('请提供 EPUB 文件')
        return
    yaml_fname = args.fname[:5] + '.yaml'
    if path.isfile(yaml_fname):
        todo = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
    else:
        fdict = read_zip(args.fname)
        opf, _ = read_opf_ncx(fdict)
        todo = [
            {
                'id': id_,
                'content': get_content(fdict[name].decode('utf8', 'ignore')),
                'result': '',
            }
            for id_, name in opf['items'].items()
        ]
        open(yaml_fname, 'w', encoding='utf8').write(
            yaml.safe_dump(todo, allow_unicode=True)
        )

    for it in todo:
        if  it.get('result') or \
            not it.get('content'): 
            continue
        ques = args.prompt.replace('{text}', it['content'][:args.limit])
        ans = call_openai_retry(ques, args.model, args.retry)
        it['result'] = ans
        open(yaml_fname, 'w', encoding='utf8').write(
            yaml.safe_dump(todo, allow_unicode=True)
        )


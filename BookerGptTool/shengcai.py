from BookerEpubTool.util import *
from os import path
import yaml
from .util import *

DFT_SHENGCAI_PROMPT = '''
假设你是一个公司的技术总监、商业分析师和人工智能专家。市场部门的员工会定期向你发送商机，你需要将它们变成一个可盈利的项目。请参考示例分析商机并从中提取盈利点、操作流程和所需软件。

注意事项：

-  公司只能分配一个两到三人的项目组处理这个商机，请保证流程尽可能自动化。
-  先考虑全自动化的传统软件，再考虑AI软件，最后再选择需要手动操作的软件。
-  所需软件的预算是有限制的，尽可能使用开源或者免费软件。如果可能，列出它们的名称并且给出链接。
-  如果没有现有软件满足要求，可以要求项目组成员临时开发，请标注【需要开发】。
-  流程应当尽可能详细，例如，不要只输出“生成一篇xxx产品的软文”，应当输出“首先，使用xxx提示词生成yyy软文的标题；之后使用zzz提示词针对每个标题生成正文。”

示例：

商机：闲鱼虚拟冲啊！！！闲鱼之前一直对虚拟资料管控严格，今天官方通知，闲鱼已放开无品牌类的网课和学习资料管控。消息是图书类目下有出版物亮照的闲鱼官方运营放出的消息，群是老瞿之前做闲鱼二手书入的官方运营群，确保是官方运营。消息没有全面公开，所以就存在一个信息差！ 利好：无版权的虚拟课程、资料 发布方向：国考省考公考资料、事业单位考试资料、考研上岸冲刺资料、大学期末考试资料
盈利点：利用闲鱼放开无品牌虚拟课程和学习资料管控的商机，可以发布无版权的虚拟课程和资料，针对国考省考公考资料、事业单位考试资料、考研上岸冲刺资料、大学期末考试资料等方向进行盈利。
操作步骤：
1.  百度搜索【xxx题库】，确定目标网站
2.  编写爬虫或者使用现有软件爬取全站内容
3.  判断内容是否侵权，如果侵权，则使用大语言模型改写内容，使之表述不一样但含义相同
4.  使用大语言模型生成内容介绍
5.  使用RPA工具在闲鱼上发布链接，引流到私域
6.  使用IM软件与用户进行互动，解答问题，提供售后服务。
所需软件：
1.  【需要开发】全站爬虫：可能需要使用Python开发
2.  大语言模型：ChatGPT 或者 ChatGLM，用于改写内容或生成介绍
3.  闲鱼APP：用于发布虚拟课程和学习资料。
4.  Appnium：用于自动化操作软件发布信息
5.  微信：用于私域营销。 

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
    print(args.fname)
    if not args.fname.endswith('.epub'):
        print('请提供 EPUB 文件')
        return
    yaml_fname = args.fname[:-5] + '.yaml'
    if path.isfile(yaml_fname):
        todo = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
    else:
        fdict = read_zip(args.fname)
        _, ncx = read_opf_ncx(fdict)
        todo = [
            {
                'id': it['id'],
                'content': get_content(fdict[it['src']].decode('utf8', 'ignore')),
                'result': '',
            }
            for it in ncx['nav']
        ][1:]
        open(yaml_fname, 'w', encoding='utf8').write(
            yaml.safe_dump(todo, allow_unicode=True)
        )

    for it in todo:
        if  it.get('result') or \
            not it.get('content'): 
            continue
        if len(it['content']) < args.min:
            continue
        ques = args.prompt.replace('{text}', it['content'][:args.limit])
        ans = call_openai_retry(ques, args.model, args.retry)
        it['result'] = ans
        open(yaml_fname, 'w', encoding='utf8').write(
            yaml.safe_dump(todo, allow_unicode=True)
        )


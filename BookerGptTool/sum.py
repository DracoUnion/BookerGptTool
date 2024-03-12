from .util import *
import re

DFT_SUM_PMT = '''
假设你是一个高级编辑，遵循给定格式和注意事项，总结给定的段落。

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

def reform_paras(text, size=1500):
    text = re.sub(r'```[\s\S]+?```', '', text)
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    lines = sum([
        re.split('(?<=。|，|：|！|？|；)', l) for l in lines
    ], [])
    lines = [l for l in lines if l]
    paras = ['']
    for l in lines:
        if len(paras[-1]) + len(l) > size:
            paras.append(l)
        else:
            paras[-1] += l
    return paras

def sum_text(args):
    set_openai_props(args.key, args.proxy, args.host)
    print(args)
    ext = extname(args.fname)
    if ext not in ['md', 'srt', 'txt']:
       print('请提供 MD 或者 SRT 或者 TXT 文件')
       return
    cont = open(args.fname, encoding='utf8').read()
    paras = reform_paras(cont, args.para_size)
    res = ''
    for p in paras:
        # text = '\n'.join(['-   ' + p for p in paras])
        ques = args.prompt.replace('{text}', '-   ' + p)
        ans = call_openai_retry(ques, args.model, args.retry)
        sums = re.findall(
            r'^(?:\x20{4})?(?:\-\x20{3}|\d\.\x20\x20).+?$', 
            ans, flags=re.M
        )
        sums = '\n'.join(sums)
        res += sums + '\n'
    ofname = args.fname[:-len(ext)-1] + '_sum.md'
    title ='【总结】' + path.basename(args.fname)
    if ext == 'md':
        md_title, _ = get_md_title(cont)
        title = md_title or title
    res = f'# {title}\n\n{res}'
    open(ofname, 'w', encoding='utf8').write(res)
from .util import *
import re

DFT_SUM_PMT = '''
假设你是一个高级编辑，请分段总结给定内容，遵循给定格式。

## 格式

1.  {第一个论点}
    1.  {第一个分论点}
    2.  {第二个分论点}
    3.  ...
2.  {第二个论点}
    1.  ...
3.  ...

{text}
'''

def sum_text(args):
    set_openai_props(args.key, args.proxy, args.host)
    print(args)
    ext = extname(args.fname)
    if ext not in ['md', 'srt']:
       print('请提供 MD 或者 SRT 文件')
       return
    cont = open(args.fname, encoding='utf8').read()[:args.limit]
    ques = args.prompt.replace('{text}', cont)
    ans = call_openai_retry(ques, args.model, args.retry)
    ofname = re.sub(r'\.\w+$', '', args.fname) + '_sum.md'
    if ext == 'md':
        title, _ = get_md_title(cont)
        if title: ans = f'# {title}\n\n{ans}'
    open(ofname, 'w', encoding='utf8').write(ans)
from .util import *
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import traceback

DFT_SUM_PMT = '''
假设你是一个高级编辑，遵循给定格式和注意事项，总结给定的段落。

## 注意事项

1.  每个段落需要总结成一个概要和至少三个子概要，均为20-50字
2.  确保原文中每一段都得到总结，也就是概要中的段落数和原文相等，不要漏掉任何一段！
3.  不要省略概要前的格式（-   ）或（1.  ），否则我无法解析。

## 格式

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


def tr_sum_text_safe(*args, **kw):
    try:
        tr_sum_text(*args, **kw)
    except:
        traceback.print_exc()

def tr_sum_text(it, args, write_func):
    RE_LIST = r'^(?:\x20{4})?(?:\-\x20{3}|\d\.\x20\x20).+?$'
    if 'summary' not in it:
        ques = DFT_SUM_PMT.replace('{text}',  it['text'])
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        ans = fix_lists(ans)
        sums = re.findall(RE_LIST, ans, flags=re.M)
        it['summary'] = '\n'.join(sums)
    write_func()

def sum_text(args):
    set_openai_props(args.key, args.proxy, args.host)
    print(args)
    if args.model == 'gpt-3.5-turbo':
        args.model += '-16k'
    ext = extname(args.fname)
    if ext not in ['md', 'srt', 'txt', 'yaml']:
       print('请提供 MD 或者 SRT 或者 TXT 文件')
       return
    if args.fname.endswith('_sum.md'):
        print('不能重复总结内容')
        return
    
    yaml_fname = args.fname[:-len(ext)-1] + '.yaml'
    if path.isfile(yaml_fname):
        tosum = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
    else:
        cont = open(args.fname, encoding='utf8').read()
        paras = reform_paras_mdcn(cont, args.para_size)
        tosum = [{'text': p} for p in paras]
        open(yaml_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(tosum, allow_unicode=True))
    
    lock = Lock()
    def write_func():
        with lock:
            open(yaml_fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(tosum, allow_unicode=True))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i, it in enumerate(tosum):
        if not it.get('text') or it.get('summary'):
            continue
        h = pool.submit(tr_sum_text_safe, it, args, write_func)
        hdls.append(h)
    for h in hdls: 
        h.result()
    
    if args.md:
        title ='【总结】' + path.basename(args.fname)
        if ext == 'md':
            cont = open(args.fname, encoding='utf8').read()
            md_title, _ = get_md_title(cont)
            title = '【总结】' + md_title if md_title else title
        cont = f'# {title}\n\n' + \
            '\n'.join([
                it.get('summary', '') 
                for it in tosum
            ])
        md_fname = args.fname[:-len(ext)-1] + '_sum.md'
        open(md_fname, 'w', encoding='utf8').write(cont)
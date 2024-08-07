from .util import *
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import traceback

DFT_QA_PMT  = q='''
假如你是一个文档工程师和高级编辑，请阅读如下文本并提出五个问题，然后按照原文的意思来回答。

## 注意事项

1.  只需要输出问答，不需要重复原文
2.  不要省略概要前的格式（-   ）或（1.  ），否则我无法解析。

## 格式

-   问题1：{xxx}
-   回答1：{xxx}
-   问题2：{xxx}
-   回答2：{xxx}
-   问题3：{xxx}
-   回答3：{xxx}
-   问题4：{xxx}
-   回答4：{xxx}
-   问题5：{xxx}
-   回答5：{xxx}

## 要处理的文本

-  原文：{text}
'''

def tr_qa_text_safe(*args, **kw):
    try:
        tr_qa_text(*args, **kw)
    except:
        traceback.print_exc()

def tr_qa_text(it, args, write_func):
    RE_LIST = r'^(?:\-\x20{3})(?:问题|回答)\d+.+?$'
    if 'qas' not in it:
        ques = DFT_QA_PMT.replace('{text}', it['text'])
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
        ans = fix_lists(ans)
        qas = re.findall(RE_LIST, ans, flags=re.M)
        it['qas'] = [t[4:] for t in qas]
    write_func()


def qa_text(args):
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
        if not it.get('text') or it.get('qas'):
            continue
        h = pool.submit(tr_qa_text, it, args, write_func)
        hdls.append(h)
    for h in hdls: 
        h.result()
    
    if args.md:
        title ='【问答】' + path.basename(args.fname)
        if ext == 'md':
            cont = open(args.fname, encoding='utf8').read()
            md_title, _ = get_md_title(cont)
            title = '【问答】' + md_title if md_title else title
        cont = f'# {title}\n\n' + \
            '\n\n'.join([
                '\n\n'.join(it.get('qas', [])) 
                for it in tosum
            ])
        md_fname = args.fname[:-len(ext)-1] + '_qa.md'
        open(md_fname, 'w', encoding='utf8').write(cont)
import os
from io import BytesIO
from os import path
import re
import yaml
import functools
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import json_repair as json
from imgyaso.quant import pngquant
from .util import call_chatgpt_retry, set_openai_props, to_kebab, read_zip, is_pic, tomd, get_md_title, epub2html_pandoc
from .fmt import fmt_zh, fmt_publisher
from .md2skill_chunker import chunk_markdown

TRANS_BODY_PMT = '''
假设你是一个高级文档工程师和翻译员，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文文本翻译成中文。

## 注意事项

-   粗体（**bold**）和斜体（*itatic*）需要翻译翻译内容并保留符号。
-   内联代码（`code`）和代码块不需要翻译。
-   链接（[link](https://example.org)）需要翻译其内容，但保留网址。
-   列表，表格，引用块保留格式，翻译内容
-   原文可能有多行，不要漏掉任何一行，并且注意一定不要重复输出原文！！！

## 示例

原文：

[content]
-   [Feynman's learning method](https://wiki.example.org/feynmans_learning_method) is inspired by **Richard Feynman**, the Nobel Prize winner in physics. 

1.  With Feynman's skills, you can understand the knowledge points in depth in just `20 min`, and it is memorable and *hard to forget*. 

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

译文：

[content]
-   [费曼学习法](https://wiki.example.org/feynmans_learning_method)的灵感源于诺贝尔物理奖获得者**理查德·费曼**。

1.  运用费曼技巧，你只需花上`20 min`就能深入理解知识点，而且记忆深刻，*难以遗忘*。

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

## 以下是需要翻译的文本

[content]
{text}
[/content]
'''

TRANS_TITLE_PMT = '''
你是一个高级翻译，请参考示例，翻译指定的图书标题到中文。注意只需要输出翻译，不要输出其他任何东西。

## 示例

+   “with”不翻译，比如“deep learning with python”翻译为“python 深度学习”
+   “beginners guide”翻译为“初学者指南”
+   “cookbook”翻译为“秘籍”
+   “build xxx”翻译为“xxx构建指南”
+   “xxx in action”翻译为“xxx 实战”
+   “hands on xxx”翻译为“xxx 实用指南”
+   “practice xxx”翻译为“xxx 实践指南”
+   “unlock xxx”翻译为“xxx 解锁指南”
+   “xxx by example”翻译为“xxx 示例”
+   “pro xxx”翻译为“xxx 高级指南”
+   “xxx for yyy”翻译为“yyy 的 xxx”
+   “using xxx”翻译为“xxx 使用指南”
+   “introduction to xxx”或者“beginning xxx”翻译为“xxx 入门指南”
+   “quick start guide”翻译为“快速启动指南”
+   “playbook”翻译为“攻略书”

## 要翻译的标题

{text}
'''

FMT_PMT = '''
假设你是一个高级文档工程师，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文或中文文本排版。

请执行以下操作：

1. 重新划分标题层级（保留合理的# ## ###结构）
2. 优化段落格式，删除多余空行
3. 保留正文核心内容
4. 所有单独出现的变量名（`varName`），函数名（`funcName()`），类名（`ClassName`），路径名（`/path.to.xxx`），命令名（`cmdname`）以及它们的语句或表达式（`ClassName.funcName(var1 + var2, "cmd arg0 arg1"`）都需要添加反引号。如果上述东西被粗体（**）或者斜体（*）包围，去掉星号再加反引号。
5. 代码块前后加上三个反引号（```）

## 重要规则

- 不要修改正文内容的语义
- 不要删减有价值的信息
- 确保输出是标准Markdown格式
- 只返回处理后的内容，不要重复输出原文，也不要添加额外说明

## 示例

原文：

[content]
进入 /path/to/xxx 目录，找到 xxx.json。

在表达式 cvar = avar + bvar 中，加法运算符（+）将 avar 与 bvar 相加，得到它们的和 cvar

在 List.of(arg0, arg1, arg2) 中，List 接口的工厂方法 of() 接受一系列的元素，返回包含它们的只读列表。

之后我们这样调用 cmd 命令：cmd arg0 arg1 arg2。

if (condVar > someVal) {console.log("xxx")}
[/content]

排版后：

[content]
进入`/path/to/xxx`目录，找到`xxx.json`。

在表达式`cvar = avar + bvar`中，加法运算符（`+`）将`avar`与`bvar`相加，得到它们的和`cvar`

在`List.of(arg0, arg1, arg2)`中，`List`接口的工厂方法`of()`接受一系列的元素，返回包含它们的只读列表。

之后我们这样调用`cmd`命令：`cmd arg0 arg1 arg2`。

```
if (condVar > someVal) {console.log("xxx")}
```
[/content]

## 以下是需要排版的文本

[content]
{text}
[/content]
'''

README_TMPL = '''
# {name_cn}

> 原文：[{name}]()
> 
> 译者：[飞龙](https://github.com/wizardforcel)
> 
> 协议：[CC BY-NC-SA 4.0](http://creativecommons.org/licenses/by-nc-sa/4.0/)
'''.strip()

def split_chs(md):
    lines = md.split('\n')
    in_code = False
    for i, l in enumerate(lines):
        if '```' in l:
            in_code = not in_code
        elif not in_code and l.startwith('# ') and i != 0:
            lines[i] = '[split/]' + l
    return '\n'.join(lines).split('[split/]')

def tr_fmt_trans(chunks, idx, args, write_callback):
    print(f'[4] 处理分块 {idx+1}')
    raw = chunks[idx]['raw']
    fmt = chunks[idx]['fmt']
    trans = chunks[idx]['trans']
    if not fmt:
        ques = FMT_PMT.replace('{text}', raw)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        fmt = ans.replace('[content]', '').replace('[/content]', '')
        chunks[idx]['fmt'] = fmt
        write_callback()
    if not trans:
        ques = TRANS_BODY_PMT.replace('{text}', raw)
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        trans = ans.replace('[content]', '').replace('[/content]', '')
        chunks[idx]['trans'] = fmt_zh(trans)
        write_callback()

def trans_epub(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    if not args.fname.endswith('.epub'):
        print('请提供EPUB文件')
        return
    
    print('[1] 初始化元数据')
    name = path.basename(args.fname)[:-5]
    slug = to_kebab(name)
    proj_dir = path.join(path.dirname(args.fname), slug)
    os.makedirs(proj_dir, exist_ok=True)
    meta_dir = path.join(proj_dir, 'asset')
    os.makedirs(meta_dir, exist_ok=True)
    meta_fname = path.join(meta_dir, 'meta.yaml')
    if path.isfile(meta_fname):
        meta = yaml.safe_load(open(meta_fname, encoding='utf8').read())
    else:
        ques = TRANS_TITLE_PMT.replace('{text}', name)
        name_cn = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        meta = {
            'name': name,
            'slug': slug,
            'name_cn': name_cn,
        }
        open(meta_fname, 'w', encoding='utf8').write(yaml.safe_dump(meta))

    print('[2] 转换 html 和 md')
    html_fname = path.join(meta_dir, 'all.html')
    if path.isfile(html_fname):
        html = open(html_fname, encoding='utf8').read()
    else:
        epub = open(args.fname, 'rb').read()
        html = epub2html_pandoc(epub)
        html = fmt_publisher(html, args.fmt_mode)
        open(html_fname, 'w', encoding='utf8').write(html)
    
    md_fname = path.join(meta_dir, 'all.md')
    if path.isfile(md_fname):
        md = open(md_fname, encoding='utf8').read()
    else:
        md = tomd(html)
        open(md_fname, 'w', encoding='utf8').write(md)

    print('[3] 导出图像')
    img_dir = path.join(proj_dir, 'img')
    os.makedirs(img_dir, exist_ok=True)
    fdict = read_zip(args.fname)
    for iname, data in fdict.items():
        if not is_pic(iname): 
            continue
        data = pngquant(data)
        ifname = path.join(img_dir, path.basename(iname))
        open(ifname, 'wb').write(data)
        print(f'[3] {iname}')

    print('[4] 排版和翻译')
    chunk_fname = path.join(meta_dir, 'chunks.yaml')
    if path.isfile(chunk_fname):
        chunks = yaml.safe_load(open(chunk_fname, encoding='utf8').read())
    else:
        chunks = chunk_markdown(
            md, path.basename(args.fname)[:-5]).chunks
        chunks = [{
            'raw': c.content,
            'fmt': '',
            'trans': '',
        } for c in chunks]
        open(chunk_fname, 'w',  encoding='utf8') \
            .write(yaml.safe_dump(chunks, allow_unicode=True))

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            open(fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))
    
    for idx, c in enumerate(chunks):
        if c['fmt'] and c['trans']:
            continue
        h = pool.submit(
                tr_fmt_trans, 
                chunks, idx, args,
                functools.partial(write_callback, chunk_fname, chunks),
            )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: 
        h.result()
    hdls = []    

    print('[5] 分章节')
    chs_fname = path.join(meta_dir, 'chs.yaml')
    if path.isfile(chs_fname):
        chs = yaml.safe_load(open(chs_fname, encoding='utf8').read())
    else:
        md = '\n\n'.join(c['trans'] for c in chunks)
        chs = split_chs(md)
        open(chs_fname, 'w', encoding='utf8').write(yaml.safe_dump(chs, allow_unicode=True))
    
    l = len(str(len(chs)))
    for i, c in enumerate(chs):
        ch_fname = path.join(proj_dir, slug + '_' + str(i).zfill(l) + '.md')
        print(f'[5] {ch_fname}')
        open(ch_fname, 'w', encoding='utf8').write(c)

    print('[6] 生成 readme')
    readme = README_TMPL.replace('{name}', name).replace('{name_cn}', meta['name_cn'])
    readme_fname = path.join(proj_dir, 'README.md')
    open(readme_fname, 'w', encoding='utf8').write(readme)

    print('[6] 生成 summary')
    toc =[]
    for i, ch in enumerate(chs):
        title, _ = get_md_title(ch)
        if not title: continue
        ch_fname = slug + '_' + str(i).zfill(l) + '.md'
        toc.append(f'+   [{title}]({ch_fname})')
    summary = '\n'.join(toc)   
    summary_fname = path.join(proj_dir, 'SUMMARY.md')
    open(summary_fname, 'w', encoding='utf8').write(summary)

    print('[*] 完成')

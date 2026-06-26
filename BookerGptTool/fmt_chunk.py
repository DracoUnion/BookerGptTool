import copy
import math
from concurrent.futures import ThreadPoolExecutor
import os
from os import path
from .md2skill_chunker import chunk_markdown
from .util import group_chunks, set_openai_props, ask_chatgpt_retry, split_md_lines

CRTC_PMT = '''
假设你是一个高级文档工程师，你的任务是审核提供的 Markdown 文本的格式问题，然后参考格式要求和重要规则，提出排版建议。如果文本不需要排版，直接输出“[TEXT_PERFECT/]”。

## 格式要求

1. 重新划分标题层级（保留合理的# ## ###结构）
2. 优化段落格式，删除多余空行
3. 保留正文核心内容
4. 所有单独出现的变量名（`varName`），函数名（`funcName()`），类名（`ClassName`），路径名（`/path.to.xxx`），命令名（`cmdname`）以及它们的语句或表达式（`ClassName.funcName(var1 + var2, "cmd arg0 arg1"`）都需要添加反引号。如果上述东西被粗体（**）或者斜体（*）包围，去掉星号再加反引号。
5. 代码块前后加上三个反引号（```），里面的任何东西不要添加反引号。

## 重要规则

- 不要修改正文内容的语义
- 不要删减有价值的信息
- 只返回排版意见，不要重复输出原文，也不要输出修改后的文本
- 如果文本不需要排版，直接输出“[TEXT_PERFECT/]”。

## 排版前后示例

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

## 格式

[content]
1.  {意见1}
2.  {意见2}
3.  {意见3}
4.  ...
[/content]

## 要排版的文本

[content]
{text}
[/content]
'''

FIX_PMT = '''
假设你是一个高级文档工程师，请参考排版意见排版给定的 Markdown 文本。

## 重要规则

- 不要修改正文内容的语义
- 不要删减有价值的信息
- 不要重复输出原文，也不要添加额外信息，只输出排版后的文本

## 要排版的文本

[content]
{text}
[/content]

## 排版意见

[content]
{crtc}
[/content]
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

def tr_fmt_group_multi(text, res, idx, args):
    for i in range(args.round):
        ques = CRTC_PMT.replace('{text}', text)
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        crtc = ans.replace('[content]', '').replace('[/content]', '').strip()
        if '[TEXT_PERFECT/]' in crtc:
            res[idx] = text
            break
        ques = FIX_PMT.replace('{text}', text).replace('{crtc}', crtc)
        ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        text = ans.replace('[content]', '').replace('[/content]', '').strip()
        res[idx] = text

def tr_fmt_group(text, res, idx, args):
    ques = FMT_PMT.replace('{text}', text)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    ans = ans.replace('[content]', '').replace('[/content]', '')
    res[idx] = ans

def fmt_chunk_dir(args):
    dir = args.fname
    args.threads = int(args.threads ** 0.5)
    fnames = os.listdir(dir)
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = path.join(dir, f)
        h = pool.submit(fmt_chunk_file, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: h.result()
    hdls = []

def fmt_chunk_handle(args):
    if path.isfile(args.fname):
        fmt_chunk_file(args)
    else:
        fmt_chunk_dir(args)

def fmt_chunk_file(args):
    print(args)
    set_openai_props(args)
    if not args.fname.endswith('.md'):
        print('请提供 MD 文件')
        return
    ofname = args.fname[:-3] + '_fmt.md'
    if path.isfile(ofname):
        print(f'{args.fname} 已排版')
        return
    if args.fname.endswith('_fmt.md'):
        print(f'{args.fname} 已排版')
        return
    print(args.fname)
    md = open(args.fname, encoding='utf8').read()
    chunks = group_chunks(split_md_lines(md), args.limit)
    
    res = [''] * len(chunks)
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i, c in enumerate(chunks):
        fn = tr_fmt_group_multi if args.multi_round else tr_fmt_group
        h = pool.submit(fn, c, res, i, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: h.result()
    hdls = []

    
    open(ofname, 'w', encoding='utf8').write('\n\n'.join(res))
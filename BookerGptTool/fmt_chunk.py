import copy
import math
from concurrent.futures import ThreadPoolExecutor
import os
from os import path
from .md2skill_chunker import chunk_markdown
from .util import group_chunks, set_openai_props, call_chatgpt_retry

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

def tr_fmt_group(text, res, idx, args):
    ques = FMT_PMT.replace('{text}', text)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
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
    cres = chunk_markdown(md, args.fname).chunks
    chunks = [c.content for c in cres]
    chunks = group_chunks(chunks, args.limit)
    
    res = [''] * len(chunks)
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i, c in enumerate(chunks):
        h = pool.submit(tr_fmt_group, c, res, i, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: h.result()
    hdls = []

    
    open(ofname, 'w', encoding='utf8').write('\n\n'.join(res))
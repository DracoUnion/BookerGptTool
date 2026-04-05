import openai
import httpx
import os
import traceback
import yaml
import argparse
import copy
from os import path
import json
import random
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import functools
from .util import *

XHS_PMT = '''
假设你是一位小红书资深博主，请参考下面的素材，生成一篇吸引人的小红书笔记。

## 注意

1.  只需要输出笔记，不需要输出任何其它东西。
2.  二创要求：
    -   标题：保留原主题，但需用“二极管标题法”优化（正面/负面刺激+爆款词）；
    -   正文：基于原笔记核心观点，结尾留互动问题；
    -   标签：保留原笔记1-2个核心标签，新增2-3个相关长尾标签。
3.  标题创作规则
    -   不偏离原主题：如原标题是“正确 XXX 方法”，新标题需围绕“XXX”展开；
    -   优化表达：用“小白必看”“绝绝子”“我不允许”等爆款词替代原标题的平淡表述；
    -   加情绪刺激：正面或负面。
    -   emoji：标题前后需要添加含义相近的 emoji。
4.  正文创作规则
    -   风格：选择“轻松”“亲切”“热情”中的一种，贴合小红书主流调性；
    -   开篇：用“提出疑问”或“对比”方式；
    -   emoji：每行开头要配合含义相近的 emoji；
    -   尽可能保留素材的绝大部分观点，不要遗漏关键信息；
    -   差异化亮点：
        -   若原笔记信息单薄，补充专业细节；
        -   若原笔记是产品推荐，新增使用场景；
    -   互动引导：结尾用开放式问题。

## 素材

[content]
{text}
[/content]
'''

GZH_PMT = '''
假设你是一个资深公众号作者，请参考下面的素材，生成一篇有深度的公众号文章。


## 注意

+   文章应当在五千到一万字，需要有数据支撑
+   只需要输出文章，不需要输出任何其它东西

## 素材

[content]
{text}
[/content]
'''

FMT_PROMPT = '''
假设你是一个高级文档工程师，请参考下面的注意事项了解 Markdown 文档的格式，然后参考示例，将给定英文或中文文本排版。

请执行以下操作：

1. 删除所有广告内容（如"知识星球TOP"、微信号、二维码等推广信息）
2. 删除PDF转换产生的frontmatter元数据（---开头的YAML块）
3. 删除"## 第X页"等页面分隔标记
4. 清理乱码字符（如全角符号、行内##、连续的......等）
5. 重新划分标题层级（保留合理的# ## ###结构）
6. 优化段落格式，删除多余空行
7. 保留正文核心内容
8. 所有单独出现的变量名（`varName`），函数名（`funcName()`），类名（`ClassName`），路径名（`/path.to.xxx`），命令名（`cmdname`）以及它们的语句或表达式（`ClassName.funcName(var1 + var2, "cmd arg0 arg1"`）都需要添加反引号。如果上述东西被粗体（**）或者斜体（*）包围，去掉星号再加反引号。
9. 代码块前后加上三个反引号（```）

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

## 以下是需要排版的文本

[content]
{text}
[/content]
'''

def erchuang_single(args):
    suf = (
             'xhs' if args.style == 'xhs' 
        else 'gzh' if args.style ==  'gzh' 
        else 'fmt'
    )
    ofname = args.fname[:-3] + f'_{suf}.md'
    if path.isfile(ofname):
        print(f'{args.fname} 已生成')
        return
    cont = open(args.fname, encoding='utf8').read()
    pmt = (
             XHS_PMT if args.style == 'xhs' 
        else GZH_PMT if args.style == 'gzh'
        else FMT_PROMPT
    )
    ques = pmt.replace('{text}', cont)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    open(ofname, 'w', encoding='utf8').write(ans)
    print(ofname)

def gen_xhs_single_safe(args):
    try:
        erchuang_single(args)
    except:
        traceback.print_exc()

def erchuang_handle(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f) 
            for f in os.listdir(args.fname)
        ]
    fnames = [
        f for f in fnames 
        if extname(f) == 'md'
    ]
    if not fnames:
        print('请提供 MD 文件')
        return

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(gen_xhs_single_safe, args)
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []
            
    for h in hdls: h.result()
    
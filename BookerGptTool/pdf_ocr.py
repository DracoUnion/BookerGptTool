import requests
import tarfile
import numpy as np
from io import BytesIO
from os import path
import re
import os
import hashlib
import shutil
import yaml
import fitz
import functools
import cv2
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import json_repair as json
from imgyaso.quant import pngquant
from .clean_heading import clean_md_llm
from .util import (
    call_vlm_retry, 
    call_chatgpt_retry, 
    set_openai_props, 
    extname, 
    to_kebab,
)


README_TMPL = '''
# {name_cn}

> 原文：[{name}]()
> 
> 译者：[飞龙](https://github.com/wizardforcel)
> 
> 协议：[CC BY-NC-SA 4.0](http://creativecommons.org/licenses/by-nc-sa/4.0/)
'''.strip()

OCR_PMT = '''
你是一个专业的文档编辑助手。请查看给定图片，提取出其中的所有标题、段落、列表、表格、插图、引用、代码块等，并一定要忽略页眉和页脚，以给定 JSON  格式输出。注意只需要输出 JSON，不需要输出其它任何东西。

注意 BBOX 应该用相对坐标，即整个图片的一个比例，不要使用像素单位的绝对坐标！

## 格式

```
{
	"direction": "hirizonal|vertical",
	"contents": [
		{
			"type": "paragraph|title|list|table|quote|image|code",
			"markdown": "in markdown format",
			"bbox": [0.xmin, 0.ymin, 0.xmax, 0.ymax]
		},
		...
	]
}
```
'''

MERGE_PMT = '''
你是一个专业的文档编辑助手。你将会得到两段 Markdown 文本，分别位于第一页最后一行和第二页第一行，请判断它们是否属于同一段落。如果第一段文本没有结束（例如：缺少句号、引号，或者语义明显未完），请将其与下一页的开头合并为同一段落。 如果句子已经结束，则保留为独立段落。你应该为此输出`true`或者`false`，不要输出其它选项。

## 格式示例

```
true | false
```

## 第一段文本

[content]
{prev}
[/content]

## 第二段文本

[content]
{next}
[/content]
'''

POSTPROC_PMT = '''
你是一个专业的OCR后处理助手。下面是一份扫描文档经过OCR识别后得到的原始文本，每页以 [PAGE X] 分隔。

请你完成以下任务：

1. 纠正明显的OCR错误（如形近字混淆、标点错误、数字/字母误判）。
2. 将跨页的连续段落正确合并，不要保留分页标记。
3. 根据语义和缩进/编号特征，识别标题（用## 表示一级标题，### 表示二级标题）、列表项（用- 开头）。
4. 恢复正常的段落缩进（首行空两格不是必须，但请用空行分隔不同段落）。
5. 如果出现表格样式的文本，请将其转换为Markdown表格。

输出要求：

- 只输出处理后的最终文本，不要包含解释或额外注释，不要包含 [PAGE X] 标记。
- 保留原文的语言和所有信息，不要删减或总结。

原始OCR文本：

[content]
{text}
[/content]
'''

TOC_PMT = '''
你是一个文档修复专家，下面是从扫描件OCR得到的目录的 Markdown。文本中可能存在错字、层级混乱等问题。

请完成以下修复任务，并严格按照 Markdown 格式输出。

## 要求

1. **重建层级**：通过行首空格数量、编号模式（如“1.”“1.1”“1.1.1”）判断章/节/小节，在输出中用行首的井号（`#`）表示。
2. **只输出变更的目录**：例如，“标题1”的层级从一级变更到二级，就输出“## 标题1”。“标题2”层级没有变更，就不输出。

## 目录

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


def corp_img(img, bbox):
    xmin, ymin, xmax, ymax = bbox
    fmt_bytes = isinstance(img, bytes)
    if fmt_bytes:
        img = cv2.imdecode(
            np.frombuffer(img, np.uint8),
            cv2.IMREAD_UNCHANGED
        )
    h, w = img.shape[0], img.shape[1]
    xmin = int(w * xmin)
    xmax = int(w * xmax)
    ymin = int(h * ymin)
    ymax = int(h * ymax)
    img_pt = img[ymin:ymax + 1, xmin: xmax + 1]
    if 0 in img_pt.shape:
        img_pt = np.full([1, 1, 3], 255, np.uint8)
    if fmt_bytes:
        img_pt = bytes(cv2.imencode(
            '.png', img_pt, 
            [cv2.IMWRITE_PNG_COMPRESSION , 9]
        )[1])
    return img_pt

def ocr_json2md(j):
    mds = []
    for seg in j['contents']:
        if seg['type'] == 'image':
            bbox = seg['bbox']
            md = f'![](bbox={bbox})'
        elif seg['type'] == 'title':
            md = '# ' + seg['markdown']
        elif seg['type'] == 'list':
            md = '+   ' + seg['markdown']
        elif seg['type'] == 'code':
            md = '```\n' + seg['markdown'] + '\n```'
        elif seg['type'] == 'quote':
            md = '> ' + seg['markdown']
        else:
            md = seg['markdown']
        mds.append(md)
    return '\n\n'.join(mds).strip() \
        or '<!-- no content -->'
    

def tr_ocr_page(img, pages, idx, args, write_callback):
    print(f'[3] 识别页码 {idx + 1}')
    for i in range(args.retry):
        try:
            ans = call_vlm_retry(
                img, OCR_PMT, args.vmodel, args.temp, args.retry, args.max_tokens,
            )
            # ans = re.search(r'```\w*([\s\S]+?)```', ans).group(1)
            ans = ans.replace('```', '')
            j = json.loads(ans)
            pages[idx]['md'] = ocr_json2md(j)
            break
        except Exception as ex:
            print(f'OCR retry {i+1}: {str(ex)}')
            if i == args.retry - 1: raise ex
    write_callback()

def tr_merge_group(groups, idx, args, write_callback):
    print(f'[6] 处理分组合并 {idx + 1}')
    prev_line = groups[idx - 1]['mdcn'].strip()
    next_line = groups[idx]['mdcn'].strip()
    prev = re.search(r'^.+?\Z', prev_line, flags=re.M).group()
    next = re.search(r'\A.+?$', next_line, flags=re.M).group()

    ques = MERGE_PMT.replace('{prev}', prev) \
        .replace('{next}', next)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    merge = ans.replace('```', '').strip()
    groups[idx]['merge'] = int(merge == 'true')
    write_callback()
    

def tr_proc_img(img, pages, idx, img_dir, pdf_hash, write_callback):
    print(f'[4] 处理图像 {idx}')
    md = pages[idx]['md']
    pgno = pages[idx]['pgno']
    img_links = re.findall(r'!\[\]\(.+?\)', md)
    for j, link in enumerate(img_links):
        m = re.search(r'bbox=\[(\d+\.\d+),\x20(\d+\.\d+),\x20(\d+\.\d+),\x20(\d+\.\d+)\]', link)
        if not m: continue
        bbox = [float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))]
        img_pt = corp_img(img, bbox)
        img_pt = pngquant(img_pt)
        img_fname = f'{pdf_hash}_{pgno}_{j}.png'
        img_ffname = path.join(img_dir, img_fname)
        print(f'[5] {img_ffname}')
        open(img_ffname, 'wb').write(img_pt)
        md = md.replace(link, f'![](img/{img_fname})')
        pages[idx]['md'] = md
        write_callback()
    pages[idx]['img_proc'] = True

def tr_group_page(groups, idx, args, write_callback):
    print(f'[5] 处理页面合并 {idx}')
    text = '\n\n'.join(groups[idx]['raw'])
    ques = POSTPROC_PMT.replace('{text}', text)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    groups[idx]['md'] = ans
    write_callback()
    if args.trans:
        ques = TRANS_BODY_PMT.replace(
            '{text}', groups[idx]['md'])
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        ans = ans.replace('[content]', '').replace('[/content]', '').strip()
        groups[idx]['mdcn'] = ans
    else:
        groups[idx]['mdcn'] = groups[idx]['md']
    write_callback()

def pdf_ocr(args):
    print(args)
    set_openai_props(args)
    if not args.fname.endswith('.pdf'):
        print('请提供PDF文件')
        return
    
    slug = to_kebab(args.fname[:-4])
    pj_dir = slug if args.mkdir else '.'
    os.makedirs(pj_dir, exist_ok=True)
    md_fname = (
        path.join(pj_dir, f'{slug}.md')
        if args.mkdir
        else args.fname[:-4] + '.md'
    )
    if path.isfile(md_fname):
        print('PDF 已处理')
        return

    print(f'[1] 加载 {args.fname}')
    pdf = open(args.fname, 'rb').read()
    pdf_hash = hashlib.md5(pdf).hexdigest()
    doc: fitz.Document = fitz.open('pdf', BytesIO(pdf))

    yaml_fname = (
        path.join(pj_dir, 'meta.yaml') 
        if args.mkdir 
        else args.fname[:-4] + '.yaml'
    )
    print(f'[2] 初始化 {yaml_fname}')
    if path.isfile(yaml_fname):
        res = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
        pages = res['pages']
    else:
        pages = [{
            'pgno': i,
            'md': '',
            'merge': -1,
            'img_proc': False,
        } for i in range(len(doc))]
        res = {'pages': pages}
        open(yaml_fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))

    print(f'[3] 识别图像')
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            with open(fname, 'w', encoding='utf8') as f:
                f.write(yaml.safe_dump(res, allow_unicode=True))
    
    for i, g in enumerate(res['pages']):
        if g['md']: continue
        pgno = g['pgno']
        img = doc[pgno].get_pixmap(dpi=args.dpi).pil_tobytes('png')
        h = pool.submit(
            tr_ocr_page, 
            img, res['pages'], i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []
    for h in hdls: 
        h.result()
    hdls = []

        
    print(f'[4] 处理图片')
    img_dir = (
        path.join(pj_dir, 'img')
        if args.mkdir
        else args.fname[:-4] + '_imgs'
    )
    os.makedirs(img_dir, exist_ok=True)
    for i, g in enumerate(res['pages']):
        if g['img_proc']: continue
        pgno = g['pgno']
        img = doc[pgno].get_pixmap(dpi=args.dpi).pil_tobytes('png')
        h = pool.submit(
            tr_proc_img, 
            img, res['pages'], i, img_dir, pdf_hash,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: 
        h.result()
    hdls = []

    print(f'[5] 处理页间合并')
    if 'groups' in res:
        groups = res['groups']
    else:
        groups = mkgroups(res['pages'], args)
        res['groups'] = groups

    for i, g in enumerate(res['groups']):
        if g['md'] and g['mdcn']: continue
        h = pool.submit(
            tr_group_page, res['groups'], i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []
    for h in hdls: 
        h.result()
    hdls = []

    print(f'[6] 处理组间合并')
    res['groups'] = [g for g in res['groups'] if g['mdcn']]
    for i, g in enumerate(res['groups']):
        if i == 0: continue
        if g['merge'] != -1: continue
        h = pool.submit(
            tr_merge_group, 
            res['groups'], i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: 
        h.result()
    hdls = []

    full_text = ''
    for i, g in enumerate(res['groups']):
        print(f'[6] 生成全文 {i}')
        if g['merge'] != 1:
            full_text += '\n\n'
        full_text += g['mdcn']
    name_cn = ''
    if args.clean:
        full_text = clean_md_llm(full_text, args)
        name_cn = trans_title(args.fname[:-4], args)
        full_text = f'# {name_cn}\n\n{full_text}')

    print(f'[7] 修正目录')
    full_text = fix_toc(
        full_text, res, args,
        functools.partial(write_callback, yaml_fname, res),
    )
    
    print(f'[8] 写入 {md_fname}')
    open(md_fname, 'w', encoding='utf8').write(full_text)
    if args.mkdir:
        print(f'[8] 写入 README.md')
        name = args.fname[:-4]
        if not name_cn:
            name_cn = trans_title(name, args)
        readme = README_TMPL.replace('{name}', name).replace('{name_cn}', name_cn)
        readme_fname = path.join(pj_dir, 'README.md')
        open(readme_fname, 'w', encoding='utf8').write(readme)
        print(f'[8] 写入 SUMMARY.md')
        toc =[
            f'+   [{name_cn}](README.md)',
            f'+   [{name_cn}]({slug}.md)',
        ]
        summary = '\n'.join(toc)   
        summary_fname = path.join(pj_dir, 'SUMMARY.md')
        open(summary_fname, 'w', encoding='utf8').write(summary)

    print(f'[*] 处理完毕')

def fix_toc(full_text, res, args, write_callback):
    if 'toc' in res:
        toc = res['toc']
    else:
        toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
        ques = TOC_PMT.replace('{text}', '\n'.join(toc))
        ans =  call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        toc = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
        res['toc'] = toc
        write_callback()
    for lvl, title in toc:
        print(f'[7] {lvl} {title}')
        try:
            full_text = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', full_text, flags=re.M)
        except re.error:
            pass
    return full_text

def mkgroups(pages, args):
    groups =  [{
            'raw': [], 
            'md': '',
            'mdcn': '',
            'merge': -1,
        }]
    for p in pages:
        exi_len = sum(len(md) for md in groups[-1]['raw'])
        if exi_len > args.limit:
            groups.append({
                'raw': [], 
                'md': '', 
                'mdcn': '', 
                'merge': -1,
            })
        groups[-1]['raw'].append(
            f"[PAGE {p['pgno']}]\n\n{p['md']}"
        )
    groups = [g for g in groups if g['raw']]
    return groups

def trans_title(title, args):
    ques = TRANS_TITLE_PMT.replace('{text}', title)
    title_cn = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    return title_cn

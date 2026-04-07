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
from .util import call_vlm_retry, call_chatgpt_retry, set_openai_props, extname

OCR_PMT = '''
你是一个专业的文档编辑助手。请查看给定图片，提取出其中的所有标题、段落、列表、表格、插图、引用、代码块等，并一定要忽略页眉和页脚，以给定 JSON  格式输出。注意只需要输出 JSON，不需要输出其它任何东西。

## 格式

```
{
	"direction": "hirizonal|vertical",
	"contents": [
		{
			"type": "paragraph|title|list|table|quote|image|code",
			"markdown": "in markdown format",
			"bbox": [xmin, ymin, xmax, ymax]
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

1. **纠正错别字**：根据语义和常见目录词汇（如“第”“章”“节”“参考文献”“附录”）修正明显OCR错误。
2. **重建层级**：通过行首空格数量、编号模式（如“1.”“1.1”“1.1.1”）判断章/节/小节，在输出中用行首的井号（`#`）表示。

## 目录

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
    img_pt = img[ymin:ymax + 1, xmin: xmax + 1]
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
    prev = re.search(r'^.+?\Z', groups[idx - 1]['md'], flags=re.M).group()
    next = re.search(r'\A.+?$', groups[idx]['md'], flags=re.M).group()

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
        m = re.search(r'bbox=\[(\d+),\x20(\d+),\x20(\d+),\x20(\d+)\]', link)
        if not m: continue
        bbox = [int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))]
        img_pt = corp_img(img, bbox)
        img_pt = pngquant(img_pt)
        img_fname = f'{pdf_hash}_{pgno}_{j}.png'
        img_ffname = path.join(img_dir, img_fname)
        print(f'[5] {img_ffname}')
        open(img_ffname, 'wb').write(img_pt)
        md = md.replace(link, f'![](img/{img_fname})')
        pages[idx]['md'] = md
        write_callback()

def tr_group_page(groups, idx, args, write_callback):
    print(f'[5] 处理页面合并 {idx}')
    text = '\n\n'.join(groups[idx]['raw'])
    ques = POSTPROC_PMT.replace('{text}', text)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    groups[idx]['md'] = ans
    write_callback()
    

def pdf_ocr(args):
    print(args)
    set_openai_props(args.key, args.proxy, args.host)
    if not args.fname.endswith('.pdf'):
        print('请提供PDF文件')
        return
    md_fname = args.fname[:-4] + '.md'
    if path.isfile(md_fname):
        print('PDF 已处理')
        return

    print(f'[1] 加载 {args.fname}')
    pdf = open(args.fname, 'rb').read()
    pdf_hash = hashlib.md5(pdf).hexdigest()
    doc: fitz.Document = fitz.open('pdf', BytesIO(pdf))

    yaml_fname = args.fname[:-4] + '.yaml'
    print(f'[2] 初始化 {yaml_fname}')
    if path.isfile(yaml_fname):
        res = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
        pages = res['pages']
    else:
        pages = [{
            'pgno': i,
            'md': '',
            'merge': -1,
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
            open(fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(res, allow_unicode=True))
    
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
    img_dir = args.fname[:-4] + '_imgs'
    os.makedirs(img_dir, exist_ok=True)
    for i, g in enumerate(res['pages']):
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
        groups =  [{
            'raw': [], 
            'md': '',
            'merge': -1,
        }]
        for g in pages:
            exi_len = sum(len(md) for md in groups[-1]['raw'])
            if exi_len > args.limit:
                groups.append({'raw': [], 'md': ''})
            groups[-1]['raw'].append(
                f"[PAGE {g['pgno']}]\n\n{g['md']}"
            )
        groups = [g for g in groups if g['raw']]
        res['groups'] = groups

    for i, g in enumerate(res['groups']):
        if g['md']: continue
        h = pool.submit(
            tr_group_page, res['groups'], i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []
    for h in hdls: 
        h.result()
    hdls = []

    print(f'[6] 处理组间合并')
    res['groups'] = [g for g in res['groups'] if g['md']]
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
    for g in res['groups']:
        if g['merge'] != 1:
            full_text += '\n\n'
        full_text += g['md']

    print(f'[7] 修正目录')
    fix_toc(full_text, args)
    


    print(f'[7] 写入 {md_fname}')
    f = open(md_fname, 'w', encoding='utf8')
    
    f.close()
    print(f'[*] 处理完毕')

def fix_toc(full_text, args):
    toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
    ques = TOC_PMT.replace('{text}', '\n'.join(toc))
    ans =  call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    fix_toc = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
    for lvl, title in fix_toc:
        full_text = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', full_text, flags=re.M)
    return full_text
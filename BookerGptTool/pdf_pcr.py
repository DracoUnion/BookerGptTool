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
from .util import call_glmocr_retry, call_chatgpt_retry, set_openai_props, extname

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

def tr_ocr_page(img, res, idx, args, write_callback):
    print(f'[3] 识别页码 {idx + 1}')
    md = call_glmocr_retry(img, args.retry)
    res[idx]['md'] = md.strip()
    write_callback()

def tr_merge(res, idx, args, write_callback):
    print(f'[3] 处理合并 {idx + 1}')
    prev = re.sub(r'^.+?\Z', res[idx - 1]['md'], flags=re.M)
    next = re.sub(r'\A.+?$', res[idx]['md'], flags=re.M)

    ques = MERGE_PMT.replace('{prev}', prev) \
        .replace('{next}', next)
    ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    merge = re.search(r'```([\s\S]+?)```', ans).group(1)
    merge = json.loads(merge.strip())
    res[idx]['merge'] = int(merge)
    write_callback()
    

def tr_proc_img(img, res, idx, img_dir, pdf_hash, write_callback):
    md = res[idx]['md']
    img_links = re.findall(r'!\[\]\(.+?\)', md)
    for j, link in enumerate(img_links):
        m = re.search(r'bbox=\[(\d+),\x20(\d+),\x20(\d+),\x20(\d+)\]', link)
        if not m: continue
        xmin, ymin, xmax, ymax = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if isinstance(img, bytes):
            img = cv2.imdecode(
                np.frombuffer(img, np.uint8),
                cv2.IMREAD_UNCHANGED
            )
        img_pt = img[ymin:ymax + 1, xmin: xmax + 1]
        img_pt = cv2.imencode(
            '.png', bytes(img_pt), 
            [cv2.IMWRITE_PNG_COMPRESSION , 9]
        )
        img_pt = pngquant(img_pt)
        img_fname = f'{pdf_hash}_{idx}_{j}.png'
        img_ffname = path.join(img_dir, img_fname)
        print(f'[5] {img_ffname}')
        open(img_ffname, 'wb').write(img_pt)
        md = md.replace(link, f'![](img/{img_fname})')
        res[idx]['md'] = md
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
    doc = fitz.open('pdf', BytesIO(pdf))

    yaml_fname = args.fname[:-4] + '.yaml'
    print(f'[2] 初始化 {yaml_fname}')
    if path.isfile(yaml_fname):
        res = yaml.safe_load(open(yaml_fname, encoding='utf8').read())
    else:
        res = [{
            'pgno': i,
            'md': '',
            'merge': -1,
        } for i in range(len(doc))]

    print(f'[3] 识别图像')
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback(fname, res):
        with lock:
            open(fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(res))
    
    for i, it in enumerate(res):
        if it['md']: continue
        pgno = it['pgno']
        pix = doc[pgno].get_pixmap(dpi=args.dpi)
        buf = BytesIO()
        pix.save(buf)
        img = buf.getvalue()
        h = pool.submit(
            tr_ocr_page, 
            img, res, i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []
    for h in hdls: 
        h.result()
    hdls = []

    print(f'[4] 处理页间合并')
    res = [it for it in res if it['md']]
    for i, it in enumerate(res):
        if i == 0: continue
        if it['merge'] != -1: continue
        h = pool.submit(
            tr_merge, 
            res, i, args,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: 
        h.result()
    hdls = []
        
    print(f'[5] 处理图片')
    img_dir = args.fname[:-4] + '_imgs'
    os.makedirs(img_dir, exist_ok=True)
    for i, it in enumerate(res):
        pgno = it['pgno']
        pix = doc[pgno].get_pixmap(dpi=args.dpi)
        buf = BytesIO()
        pix.save(buf)
        img = buf.getvalue()
        h = pool.submit(
            tr_proc_img, 
            img, res, i, img_dir, pdf_hash,
            functools.partial(write_callback, yaml_fname, res),
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls: h.result()
            hdls = []

    for h in hdls: 
        h.result()
    hdls = []
    
    print(f'[6] 写入 {md_fname}')
    f = open(md_fname, 'w', encoding='utf8')
    for it in res:
        if it['merge'] == 0:
            f.write('\n\n')
        f.write(it['md'])
    f.close()
    print(f'[*] 处理完毕')
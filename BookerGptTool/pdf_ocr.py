import traceback
import copy
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
from pydantic import BaseModel
from .clean_heading import clean_md_llm
from .pdf_ocr_pmt import *
from .pdf_ocr_models import *
from .util import (
    call_vlm_retry, 
    ask_chatgpt_retry, 
    set_openai_props, 
    extname, 
    to_kebab,
    ext_code_block,
    ext_cont_block,
)

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

def ocr_res2md(r: OCRResult):
    mds = []
    for seg in r.contents:
        if seg.type == 'image':
            bbox = seg.bbox
            md = f'![](bbox={bbox})'
        elif seg.type == 'title':
            md = '# ' + seg.markdown
        elif seg.type == 'list':
            md = '+   ' + seg.markdown
        elif seg.type == 'code':
            md = '```\n' + seg.markdown + '\n```'
        elif seg.type == 'quote':
            md = '> ' + seg.markdown
        else:
            md = seg.markdown
        mds.append(md)
    return '\n\n'.join(mds).strip() \
        or '<!-- no content -->'
    

def tr_ocr_page(img, pages: List[Page], idx, args, write_callback):
    print(f'[3] 识别页码 {idx + 1}')

    parse_output = lambda ans: OCRResult(
        **json.loads(ext_code_block(ans))
    )
    res: OCRResult = call_vlm_retry(
        img, OCR_PMT, 
        model_name=args.vmodel, 
        temp=args.temp, 
        retry=args.retry, 
        max_tokens=args.max_tokens, parse_output=parse_output,
    )
    pages[idx].md = ocr_res2md(res)
    write_callback()

def tr_merge_group(groups: List[Group], idx, args, write_callback):
    print(f'[6] 处理分组合并 {idx + 1}')
    prev_line = groups[idx - 1].mdcn.strip()
    next_line = groups[idx].mdcn.strip()
    prev = re.search(r'^.+?\Z', prev_line, flags=re.M).group()
    next = re.search(r'\A.+?$', next_line, flags=re.M).group()

    ques = MERGE_PMT.replace('{prev}', prev) \
        .replace('{next}', next)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    merge = ans.replace('```', '').strip()
    groups[idx].merge = int(merge == 'true')
    write_callback()
    

def tr_proc_img(img, pages: List[Page], idx, img_dir, pdf_hash, write_callback):
    print(f'[4] 处理图像 {idx}')
    md = pages[idx].md
    pgno = pages[idx].pgno
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
        pages[idx].md = md
        write_callback()
    pages[idx].img_proc = True

def tr_group_page(groups: List[Group], idx, args, write_callback):
    print(f'[5] 处理页面合并 {idx}')
    text = '\n\n'.join(groups[idx].raw)
    ques = POSTPROC_PMT.replace('{text}', text)
    ans = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    groups[idx].md = ans
    write_callback()
    if args.trans:
        ques = TRANS_BODY_PMT.replace(
            '{text}', groups[idx].md)
        ans = ask_chatgpt_retry(
            ques, args.model, 
            args.temp, args.retry, 
            args.max_tokens,
            parse_output=ext_cont_block,
        )
        groups[idx].mdcn = ans
    else:
        groups[idx].mdcn = groups[idx].md
    write_callback()

def pdf_ocr(args):
    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if f.endswith('.pdf')]
    if not fnames:
        print('请提供 PDF 或目录')
        return

    args.threads = max(
        int(args.threads ** 0.5),
        int(args.threads / len(fnames))
    )
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(pdf_ocr_file_safe, args)
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []
    for h in hdls: 
        h.result()

def pdf_ocr_file_safe(args):
    try:
        pdf_ocr_file(args)
    except:
        traceback.print_exc()

def pdf_ocr_file(args):
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
        res = Meta(**res)
        pages = res.pages
    else:
        pages = [Page(pgno=i) for i in range(len(doc))]
        res = Meta(pages=pages)
        open(yaml_fname, 'w', encoding='utf8') \
                .write(yaml.safe_dump(res.dict(), allow_unicode=True))

    print(f'[3] 识别图像')
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    lock = Lock()
    def write_callback_mdl(fname, res):
        with lock:
            with open(fname, 'w', encoding='utf8') as f:
                obj = (
                    [r.dict() for r in res]
                    if isinstance(res, list)
                    else res.dict()
                )
                f.write(yaml.safe_dump(obj, allow_unicode=True))
    
    for i, g in enumerate(res.pages):
        if g.md: continue
        pgno = g.pgno
        img = doc[pgno].get_pixmap(dpi=args.dpi).pil_tobytes('png')
        h = pool.submit(
            tr_ocr_page, 
            img, res.pages, i, args,
            functools.partial(write_callback_mdl, yaml_fname, res),
        )
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []
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
    for i, g in enumerate(res.pages):
        if g.img_proc: continue
        pgno = g.pgno
        img = doc[pgno].get_pixmap(dpi=args.dpi).pil_tobytes('png')
        h = pool.submit(
            tr_proc_img, 
            img, res.pages, i, img_dir, pdf_hash,
            functools.partial(write_callback_mdl, yaml_fname, res),
        )
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: 
        h.result()
    hdls = []

    print(f'[5] 处理页间合并')
    if res.groups:
        groups = res.groups
    else:
        groups = mkgroups(res.pages, args)
        res.groups = groups

    for i, g in enumerate(res.groups):
        if g.md and g.mdcn: continue
        h = pool.submit(
            tr_group_page, res.groups, i, args,
            functools.partial(write_callback_mdl, yaml_fname, res),
        )
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []
    for h in hdls: 
        h.result()
    hdls = []

    print(f'[6] 处理组间合并')
    res.groups = [g for g in res.groups if g.mdcn]
    for i, g in enumerate(res.groups):
        if i == 0: continue
        if g.merge != -1: continue
        h = pool.submit(
            tr_merge_group, 
            res.groups, i, args,
            functools.partial(write_callback_mdl, yaml_fname, res),
        )
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: 
        h.result()
    hdls = []

    full_text = ''
    for i, g in enumerate(res.groups):
        print(f'[6] 生成全文 {i}')
        if g.merge != 1:
            full_text += '\n\n'
        full_text += g.mdcn
    name_cn = ''
    if args.clean:
        full_text = clean_md_llm(full_text, args)
        name_cn = trans_title(args.fname[:-4], args)
        full_text = f'# {name_cn}\n\n{full_text}'

    print(f'[7] 修正目录')
    full_text = fix_toc(
        full_text, res, args,
        functools.partial(write_callback_mdl, yaml_fname, res),
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

def fix_toc(full_text, res: Meta, args, write_callback):
    if res.toc:
        toc = res.toc
    else:
        toc = re.findall(r'^#+\x20+.+?$', full_text, re.M)
        ques = TOC_PMT.replace('{text}', '\n'.join(toc))
        ans =  ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
        toc = re.findall(r'^(#+)\x20+(.+?)$', ans, re.M)
        res.toc = toc
        write_callback()
    for lvl, title in toc:
        print(f'[7] {lvl} {title}')
        try:
            full_text = re.sub(r'^#+\x20+' + re.escape(title) + '$', f'{lvl} {title}', full_text, flags=re.M)
        except re.error:
            pass
    return full_text

def mkgroups(pages: List[Page], args) -> List[Group]:
    groups = [Group()]
    for p in pages:
        exi_len = sum(len(md) for md in groups[-1].raw)
        if exi_len > args.limit:
            groups.append(Group())
        groups[-1].raw.append(
            f"[PAGE {p.pgno}]\n\n{p.md}"
        )
    groups = [g for g in groups if g.raw]
    return groups

def trans_title(title, args):
    ques = TRANS_TITLE_PMT.replace('{text}', title)
    title_cn = ask_chatgpt_retry(ques, args.model, args.temp, args.retry, args.max_tokens)
    return title_cn

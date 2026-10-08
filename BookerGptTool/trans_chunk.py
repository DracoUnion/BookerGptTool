import re
import copy
import math
from concurrent.futures import ThreadPoolExecutor
import os
import logging
from os import path
from .md2skill_chunker import chunk_markdown
from .util import group_chunks, split_md_lines, ext_cont_block, render_prompt, gen_objs_md5, read_text, write_text
from .openai import logger as oai_logger
from .openai import set_openai_props, ask_chatgpt_retry
from .trans_chunk_pmt import *

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def tr_trans_group(text, res, idx, args):
    ques = render_prompt(TRANS_PMT, en=text)
    trans = ask_chatgpt_retry(ques, args.model, args, ext_cont_block).strip()
    for i in range(args.round):
        ques = render_prompt(TRANS_CHK_PMT, en=text, zh=trans)
        chks = ask_chatgpt_retry(ques, args.model, args, ext_cont_block).strip()
        if '[PERFECT/]' in chks:
            res[idx] = trans
            break
        ques = render_prompt(TRANS_FIX_PMT, en=text, zh=trans, checks=chks)
        trans = ask_chatgpt_retry(ques, args.model, args, ext_cont_block).strip()
        res[idx] = trans

def trans_chunk_handle(args):
    if path.isfile(args.fname):
        fnames = [args.fname]
        args.cache_dir = path.join(
            path.dirname(args.fname), 'asset')
    else:
        fnames = [
            path.join(rt, f)
            for rt, _, fs in os.walk(args.fname)
            for f in fs
        ] if args.recur else [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
        args.cache_dir = path.join(args.fname, 'asset')
    fnames = [
        f for f in fnames
        if f.endswith('md') and 
           not f.endswith('_fmt.md') 
    ]
    if args.excluding_re:
        fnames = [
            f for f in fnames
            if not re.search(args.excluding_re, f)
        ]
    if not fnames:
        logger.critical('请提供 MD 文件或目录')
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
        h = pool.submit(trans_chunk_file, args)
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: h.result()
    hdls = []


def trans_chunk_file(args):
    logger.info(args)
    set_openai_props(args)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    if not args.fname.endswith('.md'):
        logger.critical('请提供 MD 文件')
        return
    if args.fname.endswith('_trans.md'):
        logger.warn(f'{args.fname} 已排版')
        return
    if args.excluding_re and \
       re.search(args.excluding_re, args.fname):
        logger.warn(f'{args.fname} 已跳过')
        return
    ofname = args.fname[:-3] + '_trans.md'
    if path.isfile(ofname):
        logger.warn(f'{args.fname} 已排版')
        return
    logger.info(args.fname)
    md = open(args.fname, encoding='utf8').read()
    chunks = group_chunks(split_md_lines(md), args.limit)
    
    res = [''] * len(chunks)
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i, c in enumerate(chunks):
        fn = tr_trans_group
        h = pool.submit(fn, c, res, i, args)
        hdls.append(h)
        # if len(hdls) > args.threads:
        #     for h in hdls: h.result()
        #     hdls = []

    for h in hdls: h.result()
    hdls = []

    
    open(ofname, 'w', encoding='utf8').write('\n\n'.join(res))


def reg_subparser(subparsers):
    trans_chunk_parser = subparsers.add_parser("trans-chunk", help="翻译文本块")
    trans_chunk_parser.add_argument("fname", help="EPUB 文件名")
    trans_chunk_parser.add_argument("-t", "--threads", type=int, default=8, help="线程数")
    trans_chunk_parser.add_argument("-l", "--limit", type=int, default=8000, help="分块大小上限")
    trans_chunk_parser.add_argument("-rc", "--recur", action='store_true', help="是否递归")
    trans_chunk_parser.add_argument("-x", "--excluding-re", default='', help="排除文件的正则")
    trans_chunk_parser.add_argument("-r", "--round", type=int, default=3, help="修复轮次")
    trans_chunk_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    trans_chunk_parser.set_defaults(func=trans_chunk_handle)
import shutil
import logging
from .resplit_models import *
from .resplit_pmt import *
from .util import *
from .openai import *
from .openai import logger as oai_logger

def resplit_hdl(args):
    print(args)
    set_openai_props(args)
    if args.debug:
        oai_logger.setLevel(logging.DEBUG)
    dir = args.dir
    name = path.basename(path.abspath(dir))
    md_fnames = [
        f
        for f in os.listdir(dir)
        if f.endswith('.md') and f != 'README.md' and f != 'SUMMARY.md'
    ]

    if not md_fnames:
        print('请提供 MD 所在目录')
        return

    sorted(md_fnames)
    print(md_fnames)

    md = '\n\n'.join(
        open(path.join(dir, f), encoding='utf8').read()
        for f in md_fnames
    )
    res = ch_split_llm(md, args, args.limit)

    for md in md_fnames:
        os.remove(path.join(dir, md))

    chapters = [[]]
    lines = md.split('\n')
    for r in res:
        if r.split:
            chapters.append([])
        chapters[-1].append(lines[r.no])
    chapters = ['\n'.join(ch) for ch in chapters]

    l = len(str(len(chapters)))
    for i, ch in enumerate(chapters):
        fname = name + '_' + str(i).zfill(l) + '.md'
        print(fname)
        fname = path.join(dir, fname)
        open(fname, 'w', encoding='utf8').write(ch)

    if shutil.which('md-tool'):
        subp.run([
            'md-tool', 'summary', '.'
        ], shell=True, cwd=dir)

def tr_ch_split_llm(lines, args):
    cache_fname = path.join(
        args.dir, 'asset', 
        'chspl_' + gen_objs_md5(lines) + '.yaml'
    )
    res = read_yaml_model(cache_fname, List[ChapterSplitResult])
    if  res:
        return res
    part_str = json.dumps({"lines": lines}, ensure_ascii=False)
    ques = render_prompt(CH_SPLIT_PMT, text=part_str)
    parse_output = lambda s: parse_obj_as(
        List[ChapterSplitResult], 
        json_repair.loads(ext_code_block(s))
    )
    res: List[ChapterSplitResult] = ask_chatgpt_retry(ques, args.model, args, parse_output)
    write_yaml_model(cache_fname, res)
    return res

def ch_split_llm(md, args, limit=500):
    lines = md.split('\n')
    lines = [
        {
            'no': i,
            'line': l[:50] + '...' if len(l) > 50 else l,
        }
        for i, l in enumerate(lines)
    ]
    all_res: List[ChapterSplitResult] = []
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i in range(0, len(lines), limit):
        part = lines[i: i+limit]
        h = pool.submit(
            tr_ch_split_llm,
            part, args
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls:
                all_res += h.result()
            hdls = []

    for h in hdls:
        all_res += h.result()
    all_res.sort(key=lambda x: x.no)
    return all_res


def reg_subparser(subparsers):
    clean_parser = subparsers.add_parser("resplit", help="分章节")
    clean_parser.add_argument("dir", help="Markdown 文件所在目录")
    clean_parser.add_argument("-l", "--limit", type=int, default=500, help="行数")
    clean_parser.add_argument("-t", "--threads", type=int, default=8, help="线程数")
    clean_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    clean_parser.set_defaults(func=resplit_hdl)

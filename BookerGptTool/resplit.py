import shutil
import logging
from .resplit_models import *
from .resplit_pmt import *
from .resplit_agent import *
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
    lines = md.split('\n')
    res = ch_split_llm(md, args)

    for f in md_fnames:
        os.remove(path.join(dir, f))

    chapters = [[]]
    split_lines = {r.no for r in res}
    for i, l in enumerate(lines):
        if i in split_lines:
            chapters.append([])
        chapters[-1].append(l)
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
    agent = ResplitAgent(args)
    starts = agent.split(lines)
    checks = agent.check_split(lines, starts)
    judges = agent.judge_split(lines, starts, checks)
    return judges

def ch_split_llm(md, args):
    lines = md.split('\n')
    lines = [
        {
            'no': i,
            'line': l[:50] + '...' if len(l) > 50 else l,
        }
        for i, l in enumerate(lines)
    ]
    all_res: List[JudgeSplitAccResult] = []
    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for i in range(0, len(lines), args.limit - args.overlap):
        part = lines[i: i+args.limit]
        h = pool.submit(
            tr_ch_split_llm,
            part, args
        )
        hdls.append(h)
        if len(hdls) > args.threads:
            for h in hdls:
                all_res += h.result().chapter_starts
            hdls = []

    for h in hdls:
        all_res += h.result().chapter_starts
    all_res.sort(key=lambda x: x.no)
    return all_res


def reg_subparser(subparsers):
    clean_parser = subparsers.add_parser("resplit", help="分章节")
    clean_parser.add_argument("dir", help="Markdown 文件所在目录")
    clean_parser.add_argument("-l", "--limit", type=int, default=3000, help="行数")
    clean_parser.add_argument("-ol", "--overlap", type=int, default=50, help="重叠行数")
    clean_parser.add_argument("-t", "--threads", type=int, default=8, help="线程数")
    clean_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    clean_parser.set_defaults(func=resplit_hdl)

import shutil
from .resplit_models import *
from .resplit_pmt import *
from .util import *
from .openai import *

def resplit(args):
    print(args)
    set_openai_props(args)
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

    chapter_lines_map = {}
    for l in res:
        chapter_lines_map.setdefault(l.chapter, [])
        line = md[l.no]
        chapter_lines_map[l.chapter].append(line)

    for ch in sorted(chapter_lines_map.keys()):
        text = '\n'.join(chapter_lines_map[ch])
        fname = name + '_' + str(ch).zfill(3) + '.md'
        print(fname)
        fname = path.join(dir, fname)
        open(fname, 'w', encoding='utf8').write(text)

    if shutil.which('md-tool'):
        subp.run([
            'md-tool', 'summary', '.'
        ], shell=True, cwd=dir)

def ch_split_llm(md, args, limit=200):
    lines = md.split('\n')
    lines = [
        {
            'no': i,
            'line': l[:50] + '...' if len(l) > 50 else l,
        }
        for i, l in enumerate(lines)
    ]
    current = 0
    all_res: List[ChapterSplitResult] = []
    for i in range(0, len(lines), limit):
        part = lines[i: i+limit]
        part_str = json.dumps({"lines": part}, ensure_ascii=False)
        ques = render_prompt(CH_SPLIT_PMT, text=part_str, current=str(current))
        parse_output = lambda s: parse_obj_as(
            List[ChapterSplitResult], 
            json_repair.loads(ext_code_block(s))
        )
        res: List[ChapterSplitResult] = ask_chatgpt_retry(ques, args.model, args, parse_output)
        all_res += res
        current = res[-1].chapter
    return all_res

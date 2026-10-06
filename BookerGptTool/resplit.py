from .resplit_models import *
from .resplit_pmt import *
from .util import *
from .openai import *

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

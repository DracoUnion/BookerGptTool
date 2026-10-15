import re
import os
from os import path

from .util import (
    render_prompt,
    json_dump_model,
    gen_objs_md5,
    read_text,
    write_text,
    ext_cont_block,
    tok_en_zh,
    get_ngram_set,
    ngram_jaccard,
    ngram_coverage,
    repl_code_block,
    rec_code_block,
)
from .openai import ask_chatgpt_retry, set_openai_props
from .derep_pmt import *
from .derep_models import *


class DerepAgent:
    """封装所有 LLM 调用（生成文案、修复文案），以及确定性的重复度检测。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        self.pj_dir = (
            path.dirname(args.fname) + '_derep'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_derep'
        )
        self.asset_dir = path.join(self.pj_dir, 'asset')
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(self.asset_dir, exist_ok=True)
        set_openai_props(self.args)

    def gen_copy(self, source: str) -> str:
        """根据素材生成一篇全新文案（表述不同、核心观点一致）。"""
        cache_fname = path.join(
            self.asset_dir,
            'gen_' + gen_objs_md5(source) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        # source, blocks = repl_code_block(source)
        ques = render_prompt(GEN_PMT, source=source)
        res = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        ).strip()
        # res = rec_code_block(res, blocks)
        write_text(cache_fname, res)
        return res

    def fix_copy(self, source: str, copy: str, metrics: DupMetrics) -> str:
        """根据重复度指标与重复片段，重写文案以降低重复。"""
        cache_fname = path.join(
            self.asset_dir,
            'fix_' + gen_objs_md5(source, copy, metrics) + '.md'
        )
        if path.isfile(cache_fname) and path.getsize(cache_fname):
            return read_text(cache_fname)
        # source, source_blocks = repl_code_block(source)
        # copy, copy_blocks = repl_code_block(copy)
        ques = render_prompt(
            FIX_PMT,
            source=source,
            copy=copy,
            metrics=json_dump_model(metrics),
            repeated_ngrams='\n'.join(metrics.repeated_ngrams),
        )
        res = ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        ).strip()
        # res = rec_code_block(res, copy_blocks)
        write_text(cache_fname, res)
        return res

    @staticmethod
    def calc_metrics(source: str, copy: str, n: int = 3, threshold: float = 0.4) -> DupMetrics:
        """计算生成文案相对素材的 n-gram 重复度指标（确定性计算）。"""
        source = re.sub(r'```[\s\S]+?```', '', source)
        copy = re.sub(r'```[\s\S]+?```', '', copy)
        src_toks = tok_en_zh(source.lower())
        gen_toks = tok_en_zh(copy.lower())
        src_set = get_ngram_set(src_toks, n)
        gen_set = get_ngram_set(gen_toks, n)
        similarity = ngram_jaccard(source, copy, n)
        coverage = ngram_coverage(source, copy, n)
        repeated = sorted(src_set & gen_set)
        return DupMetrics(
            ngram_similarity=round(similarity, 4),
            ngram_coverage=round(coverage, 4),
            repeated_ngrams=[''.join(t) for t in repeated],
            is_duplicated=similarity >= threshold or coverage >= threshold,
        )

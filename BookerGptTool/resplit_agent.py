import shutil
import logging
from .resplit_models import *
from .resplit_pmt import *
from .util import *
from .openai import *

class ResplitAgent():

    def __init__(self, args):
        self.args = args
        self.asset_dir = path.join(
            getattr(self.args, 'dir', '.'), 'asset'
        )

    def split(self, lines):
        cache_fname = path.join(
            self.asset_dir,
            'chspl_' + gen_objs_md5(lines) + '.yaml'
        )
        res = read_yaml_model(cache_fname, ChapterSplitResult)
        if  res:
            return res
        ques = render_prompt(CH_SPLIT_PMT, text=json_dump_model(lines))
        parse_output = lambda s: parse_obj_as(
            ChapterSplitResult, 
            json_repair.loads(ext_code_block(s))
        )
        res: ChapterSplitResult = ask_chatgpt_retry(ques, self.args.model, self.args, parse_output)
        write_yaml_model(cache_fname, res)
        return res

    def check_split(self, lines, starts):
        cache_fname = path.join(
            self.asset_dir,
            'chk_spl_' + gen_objs_md5(lines, starts) + '.yaml'
        )
        res = read_yaml_model(cache_fname, CheckSplitResult)
        if  res:
            return res
        ques = render_prompt(
            CH_CHK_PMT, 
            text=json_dump_model(lines),
            starts=json_dump_model(starts),
        )
        parse_output = lambda s: parse_obj_as(
            CheckSplitResult, 
            json_repair.loads(ext_code_block(s))
        )
        res: CheckSplitResult = ask_chatgpt_retry(ques, self.args.model, self.args, parse_output)
        write_yaml_model(cache_fname, res)
        return res

    def judge_split(self, lines, starts, checks):
        cache_fname = path.join(
            self.asset_dir,
            'jdg_spl_' + gen_objs_md5(lines, starts, checks) + '.yaml'
        )
        res = read_yaml_model(cache_fname, JudgeSplitResult)
        if  res:
            return res
        ques = render_prompt(
            CH_JUDGE_PMT, 
            text=json_dump_model(lines),
            starts=json_dump_model(starts),
            checks=json_dump_model(checks)
        )
        parse_output = lambda s: parse_obj_as(
            JudgeSplitResult, 
            json_repair.loads(ext_code_block(s))
        )
        res: JudgeSplitResult = ask_chatgpt_retry(ques, self.args.model, self.args, parse_output)
        write_yaml_model(cache_fname, res)
        return res
# -*- coding: utf-8 -*-
"""
jike_art_agent.py —— 即刻发布文案的 LLM 智能体
"""

import json_repair

from .openai import ask_chatgpt_retry, set_openai_props
from .jike_art_models import JikeCopy
from .jike_art_pmt import GEN_JIKE_PMT
from .util import render_prompt, ext_code_block


class JikeArtAgent:
    """封装即刻发布文案生成的 LLM 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def gen_jike(self, article: str) -> JikeCopy:
        """根据文章生成即刻发布文案（正文 + 话题圈）。"""
        ques = render_prompt(GEN_JIKE_PMT, article=article)
        parse_output = lambda s: JikeCopy(
            **json_repair.loads(ext_code_block(s)))
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

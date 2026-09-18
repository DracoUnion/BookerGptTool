# -*- coding: utf-8 -*-
"""
podcast_agent.py —— 播客脚本与小宇宙发布文案的 LLM 智能体
"""

import json_repair

from .openai import ask_chatgpt_retry, set_openai_props
from .podcast_models import PodcastCopy
from .podcast_pmt import GEN_SCRIPT_PMT, GEN_XY_COPY_PMT
from .util import render_prompt, ext_code_block, ext_cont_block


class PodcastAgent:
    """封装播客脚本与小宇宙文案生成的 LLM 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def gen_script(self, article: str) -> str:
        """生成 15 分钟百家讲坛风格播客脚本，返回纯 Markdown。"""
        ques = render_prompt(GEN_SCRIPT_PMT, article=article)
        parse_output = lambda s: ext_cont_block(s)
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

    def gen_xy_copy(self, article: str, title: str) -> PodcastCopy:
        """生成小宇宙发布文案（标题 + 简介 + 文稿）。"""
        ques = render_prompt(GEN_XY_COPY_PMT, article=article, title=title)
        parse_output = lambda s: PodcastCopy(
            **json_repair.loads(ext_code_block(s)))
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

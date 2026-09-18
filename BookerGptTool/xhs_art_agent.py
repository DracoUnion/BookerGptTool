# -*- coding: utf-8 -*-
"""
xhs_art_agent.py —— 小红书轮播图与发布文案的 LLM 智能体
"""

import json_repair

from .openai import ask_chatgpt_retry, set_openai_props
from .xhs_art_models import XhsCard, XhsCopy
from .xhs_art_pmt import GEN_CARDS_PMT, GEN_COPY_PMT
from .util import render_prompt, ext_code_block


class XhsArtAgent:
    """封装小红书卡片生成与发布文案生成的 LLM 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def gen_cards(self, article: str):
        """根据文章生成 8-10 张小红书轮播卡片。"""
        ques = render_prompt(GEN_CARDS_PMT, article=article)
        parse_output = lambda s: [
            XhsCard(**c) for c in json_repair.loads(ext_code_block(s))
        ]
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

    def gen_copy(self, article: str) -> XhsCopy:
        """根据文章生成小红书发布文案（标题 + 正文 + 标签）。"""
        ques = render_prompt(GEN_COPY_PMT, article=article)
        parse_output = lambda s: XhsCopy(
            **json_repair.loads(ext_code_block(s)))
        return ask_chatgpt_retry(ques, self.model, self.args,
                                 parse_output=parse_output)

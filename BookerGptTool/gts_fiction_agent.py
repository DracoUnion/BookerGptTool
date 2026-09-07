import os

import uuid

from os import path

from .util import render_prompt

from .openai import ask_chatgpt_retry, set_openai_props

from .gts_fiction_pmt import (
    SETTING_PMT, ROLE_PMT, OUTLINE_PMT,
    DETAIL_PMT, BODY_PMT, POLISH_PMT,
    DFT_WRITE_CMD, DFT_POLISH_CMD,
)



class GtsFictionAgent:
    """封装所有 LLM 调用，一个方法对应一次调用。"""

    def __init__(self, model, args):
        self.model = model
        self.args = args
        set_openai_props(self.args)

    def _call(self, prompt):
        return ask_chatgpt_retry(prompt, self.model, self.args)

    def generate_setting(self, idea, write_command):
        prompt = render_prompt(SETTING_PMT, idea=idea, command=write_command)
        return self._call(prompt)

    def generate_roles(self, setting, write_command):
        prompt = render_prompt(ROLE_PMT, setting=setting, command=write_command)
        return self._call(prompt)

    def generate_outline(self, setting, roles, nchapters, write_command):
        prompt = render_prompt(
            OUTLINE_PMT,
            setting=setting,
            roles=roles,
            nchapters=str(nchapters),
            command=write_command,
        )
        return self._call(prompt)

    def generate_detail(self, setting, roles, outline, i, write_command):
        prompt = render_prompt(
            DETAIL_PMT,
            setting=setting,
            roles=roles,
            outline=outline,
            i=str(i),
            command=write_command,
        )
        return self._call(prompt)

    def generate_body(self, setting, roles, detail, i, write_command, nword):
        prompt = render_prompt(
            BODY_PMT,
            setting=setting,
            roles=roles,
            detail=detail,
            command=write_command,
            i=str(i),
            nword=str(nword),
        )
        return self._call(prompt)

    def polish_body(self, body, polish_command, style_example):
        prompt = render_prompt(
            POLISH_PMT,
            body=body,
            command=polish_command,
            style=style_example,
        )
        return self._call(prompt)

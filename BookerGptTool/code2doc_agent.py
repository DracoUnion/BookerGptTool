import os

import re

import copy

from concurrent.futures import ThreadPoolExecutor

from os import path

import logging

import json_repair as json

from .util import (
    ext_code_block,
    ext_cont_block,
    extname,
    render_prompt,
)

from .openai import ask_chatgpt_retry, set_openai_props

from .code2doc_pmt import *

from .code2doc_models import *



class Code2DocAgent:
    """封装 code2doc 的 LLM 调用，每个方法对应一个独立请求。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(self.args)


    def gen_overview(self, code: str) -> OverviewResult:
        """根据源码生成设计文档大纲。"""
        ques = render_prompt(OVVW_PMT, code=code)
        parse_output = lambda s: OverviewResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_vars_fields(
        self, code: str, vars_fields: str,
    ) -> VarFieldExtResult:
        """分析全局变量和类字段的类型及描述。"""
        ques = render_prompt(VAR_FLD_EXT_PMT, code=code, vars=vars_fields)
        parse_output = lambda s: VarFieldExtResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_func_method(self, code: str, func_name: str) -> str:
        """分析单个全局函数或类方法。"""
        ques = render_prompt(FUNC_MTD_EXT_PMT, code=code, func=func_name)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_key_components(self, code: str) -> str:
        """分析源码中的关键组件。"""
        ques = render_prompt(KEY_CMPN_PMT, code=code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_advice(self, code: str) -> str:
        """分析源码中的问题和优化建议。"""
        ques = render_prompt(ADVC_PMT, code=code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_others(self, code: str) -> str:
        """分析详细设计文档中的其它补充项目。"""
        ques = render_prompt(ETC_PMT, code=code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

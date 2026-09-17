from io import BytesIO

import pymupdf as pymu

from os import path

import os

import json

import logging

from pydantic import parse_obj_as

from typing import List, Optional, Callable, Tuple

from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block, ext_cont_block, to_kebab, render_prompt, read_pdf_text

from .openai import *

from .fin_report_models import *

from .fin_report_pmt import *
from .openai import *


class FinReportTools(ToolsMixin):
    """封装所有 LLM 调用的智能体类。"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.max_tokens = getattr(args, 'max_tokens', None) or 2000
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)
        self.proj_dir = (
            args.fname[:-4] + '_fin_report'
            if path.isfile(args.fname)
            else path.abspath(args.fname) +  '_fin_report'
        )
        os.makedirs(self.proj_dir, exist_ok=True)

    def _call(self, system_prompt: str, user_prompt: str,
              temperature: float = 0.0, max_tokens: Optional[int] = None,
              parse_output: Callable = None) -> str:
        """调用 LLM（system+user 消息），按 parse_output 解析结果并带重试。"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return call_llm_retry(
            messages, self.model,
            retry=self.retry,
            temp=temperature,
            max_tokens=max_tokens or self.max_tokens,
            parse_output=parse_output,
        )

    def tool_anls_fund(self, report: str) -> FundAnlsResult:
        """分析报告的基本面（增长、ROE、资本开支、毛利率等），返回 FundAnlsResult。"""
        ques = render_prompt(FUND_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            FundAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def tool_anls_value(self, report: str) -> ValueAnlsResult:
        """分析报告的估值（PE/PB 百分位、资金流向、拥挤度等），返回 ValueAnlsResult。"""
        ques = render_prompt(VAL_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            ValueAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def tool_anls_sentiment(self, report: str) -> SentiAnlsResult:
        """分析报告的市场情绪（风格、换手、分析师共识、动量等），返回 SentiAnlsResult。"""
        ques = render_prompt(SENTI_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            SentiAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def tool_extract(self, report: str) -> AnlsOutput:
        """完整提取报告的基本面、估值与情绪分析，返回 AnlsOutput。"""
        return AnlsOutput(
            fundamental=self.tool_anls_fund(report),
            value=self.tool_anls_value(report),
            sentiment=self.tool_anls_sentiment(report)
        )

    def tool_bull_initial(self, analysis: AnlsOutput) -> str:
        """生成看多方的初始论点。"""
        user_prompt = render_prompt(BULL_INITIAL_USER, analysis=analysis.json())
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def tool_bull_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
        """生成看多方对空方论点的反驳。"""
        user_prompt = render_prompt(
            BULL_REBUT_USER,
            analysis=analysis.json(),
            opponent_argument=opponent_argument,
        )
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def tool_bear_initial(self, analysis: AnlsOutput) -> str:
        """生成看空方的初始论点。"""
        user_prompt = render_prompt(BEAR_INITIAL_USER, analysis=analysis.json())
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def tool_bear_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
        """生成看空方对多方论点的反驳。"""
        user_prompt = render_prompt(
            BEAR_REBUT_USER,
            analysis=analysis.json(),
            opponent_argument=opponent_argument,
        )
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def tool_judge(self, analysis: AnlsOutput, bull_history: List[str], bear_history: List[str]) -> JudgeResult:
        """综合多方与空方论点，给出最终投资裁决（JudgeResult）。"""
        user_prompt = render_prompt(
            JUDGE_USER,
            analysis=analysis.json(),
            bull_history='\n'.join(bull_history),
            bear_history='\n'.join(bear_history),
        )
        parse_output = lambda s: \
            JudgeResult.model_validate_json(ext_code_block(s))
        res = self._call(
            JUDGE_SYSTEM_PROMPT, user_prompt,
            temperature=0.2,
            parse_output=parse_output,
        )
        return res

    def tool_read_input_file(self, fname: str):
        """读取待处理的 PDF/MD 文件。"""
        return read_pdf_text(open(fname, 'rb').read()) \
            if fname.endswith('.pdf') else \
            open(fname, encoding='utf8').read()

    def tool_list_input_files(self) -> List[str]:
        """获取待处理的 PDF/MD 文件列表。"""
        if path.isfile(self.args.fname):
            fnames = [self.args.fname]
        elif path.isdir(self.args.fname):
            fnames = [
                path.join(self.args.fname, fname)
                for fname in os.listdir(self.args.fname)
            ]
        else:
            fnames = []
        return [
            fname for fname in fnames 
            if fname.endswith('.pdf') or
               fname.endswith('.md')
        ]


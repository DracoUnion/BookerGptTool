from io import BytesIO

import pymupdf as pymu

from os import path

import os

import json

import logging

from pydantic import parse_obj_as

from typing import List, Optional, Callable, Tuple

from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block, ext_cont_block, to_kebab, render_prompt

from .openai import call_llm_retry, set_openai_props, ask_chatgpt_retry

from .fin_report_models import *

from .fin_report_pmt import *



class FinReportAgent:
    """封装所有 LLM 调用的智能体类。"""

    def __init__(self, args):
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.max_tokens = getattr(args, 'max_tokens', None) or 2000
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)

    def _call(self, system_prompt: str, user_prompt: str,
              temperature: float = 0.0, max_tokens: Optional[int] = None,
              parse_output: Callable = None) -> str:
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

    def anls_fund(self, report: str) -> FundAnlsResult:
        ques = render_prompt(FUND_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            FundAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def anls_value(self, report: str) -> ValueAnlsResult:
        ques = render_prompt(VAL_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            ValueAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def anls_sentiment(self, report: str) -> SentiAnlsResult:
        ques = render_prompt(SENTI_ANLS_PROMPT, report=report)
        parse_output = lambda s: \
            SentiAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def extract(self, report: str) -> AnlsOutput:
        return AnlsOutput(
            fundamental=self.anls_fund(report),
            value=self.anls_value(report),
            sentiment=self.anls_sentiment(report)
        )

    def bull_initial(self, analysis: AnlsOutput) -> str:
        user_prompt = render_prompt(BULL_INITIAL_USER, analysis=analysis.json())
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bull_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
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

    def bear_initial(self, analysis: AnlsOutput) -> str:
        user_prompt = render_prompt(BEAR_INITIAL_USER, analysis=analysis.json())
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bear_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
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

    def judge(self, analysis: AnlsOutput, bull_history: List[str], bear_history: List[str]) -> JudgeResult:
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

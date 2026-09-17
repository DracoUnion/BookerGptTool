from io import BytesIO

import pymupdf as pymu

from os import path

import os

import json

import logging

from pydantic import parse_obj_as

from typing import List, Optional, Callable, Tuple, Dict, Any

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

    # 工具名 -> OpenAI parameters 结构（type/properties/required）。
    # name 与 description 不再硬编码，由 get_tool_defs 从函数 __name__ / __doc__ 取得。
    # pydantic 模型参数用 Model.schema() 展开，不写死 {"type":"object"}。
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        # ── IO：Workspace 读写（继承自 ToolsMixin）─────────────
        **ToolsMixin._TOOL_PARAMS,

        # ── 输入文件 ──────────────────────────────────────────
        "tool_read_input_file": params_schema(
            required=['fname'],
            fname=base_schema('string', '待读取的 PDF/MD 文件路径'),
        ),
        "tool_list_input_files": params_schema(),

        # ── 分析：基本面 / 估值 / 情绪 ─────────────────────────
        "tool_anls_fund": params_schema(
            required=['report'],
            report=base_schema('string', '研报全文文本'),
        ),
        "tool_anls_value": params_schema(
            required=['report'],
            report=base_schema('string', '研报全文文本'),
        ),
        "tool_anls_sentiment": params_schema(
            required=['report'],
            report=base_schema('string', '研报全文文本'),
        ),
        "tool_extract": params_schema(
            required=['report'],
            report=base_schema('string', '研报全文文本'),
        ),

        # ── 多方 / 空方论点 ────────────────────────────────────
        "tool_bull_initial": params_schema(
            required=['analysis'],
            analysis=model_schema(AnlsOutput, '提取的分析结果（AnlsOutput）'),
        ),
        "tool_bull_rebut": params_schema(
            required=['analysis', 'opponent_argument'],
            analysis=model_schema(AnlsOutput, '提取的分析结果（AnlsOutput）'),
            opponent_argument=base_schema('string', '对方（空方）的论点'),
        ),
        "tool_bear_initial": params_schema(
            required=['analysis'],
            analysis=model_schema(AnlsOutput, '提取的分析结果（AnlsOutput）'),
        ),
        "tool_bear_rebut": params_schema(
            required=['analysis', 'opponent_argument'],
            analysis=model_schema(AnlsOutput, '提取的分析结果（AnlsOutput）'),
            opponent_argument=base_schema('string', '对方（多方）的论点'),
        ),

        # ── 裁决 ──────────────────────────────────────────────
        "tool_judge": params_schema(
            required=['analysis', 'bull_history', 'bear_history'],
            analysis=model_schema(AnlsOutput, '提取的分析结果（AnlsOutput）'),
            bull_history=str_list_schema('多方论点历史列表'),
            bear_history=str_list_schema('空方论点历史列表'),
        ),
    }


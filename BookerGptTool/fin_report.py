from io import BytesIO
import pymupdf as pymu
from os import path
import os
import json
import logging
from pydantic import parse_obj_as
from typing import List, Optional, Callable, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from .util import ext_code_block, ext_cont_block, call_llm_retry, set_openai_props, ask_chatgpt_retry
from .fin_report_models import *

from .fin_report_pmt import *

# 配置日志
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ===================== 工具函数 =====================
def read_pdf_text(data):
    pdf: pymu.Document = pymu.open('pdf', BytesIO(data))
    cont = '\n\n'.join([
        pg.get_text() for pg in pdf
    ])
    return cont


# ===================== Agent =====================


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
        ques = FUND_ANLS_PROMPT.replace('{report}', report)
        parse_output = lambda s: \
            FundAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def anls_value(self, report: str) -> ValueAnlsResult:
        ques = VAL_ANLS_PROMPT.replace('{report}', report)
        parse_output = lambda s: \
            ValueAnlsResult.model_validate_json(ext_code_block(s))
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output
        )

    def anls_sentiment(self, report: str) -> SentiAnlsResult:
        ques = SENTI_ANLS_PROMPT.replace('{report}', report)
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
        user_prompt = BULL_INITIAL_USER \
            .replace('{analysis}', analysis.json())
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bull_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
        user_prompt = BULL_REBUT_USER \
            .replace('{analysis}', analysis.json()) \
            .replace('{opponent_argument}', opponent_argument)
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bear_initial(self, analysis: AnlsOutput) -> str:
        user_prompt = BEAR_INITIAL_USER \
            .replace('{analysis}', analysis.json())
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bear_rebut(self, analysis: AnlsOutput, opponent_argument: str) -> str:
        user_prompt = BEAR_REBUT_USER \
            .replace('{analysis}', analysis.json()) \
            .replace('{opponent_argument}', opponent_argument)
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def judge(self, analysis: AnlsOutput, bull_history: List[str], bear_history: List[str]) -> JudgeResult:
        user_prompt = JUDGE_USER \
            .replace('{analysis}', analysis.json()) \
            .replace('{bull_history}', '\n'.join(bull_history)) \
            .replace('{bear_history}', '\n'.join(bear_history))
        parse_output = lambda s: \
            JudgeResult.model_validate_json(ext_code_block(s))
        res = self._call(
            JUDGE_SYSTEM_PROMPT, user_prompt,
            temperature=0.2,
            parse_output=parse_output,
        )
        return res


# ===================== 5. 协调器 (Orchestrator) =====================
class MultiReportOrchestrator:
    """
    管理多份研报的处理流水线：
    1. 并行提取
    2. 融合
    3. 多空初始立场
    4. 多轮辩论（可配置轮次）
    5. 裁决
    """

    def __init__(self, args):
        self.args = args
        self.proj_dir = getattr(
            args,
            'proj_dir',
            args.fname[:-4] + '_fin_report'
            if path.isfile(args.fname)
            else path.join(args.fname, 'fin_report'),
        )
        self.debate_rounds = getattr(args, 'rounds', 3)
        self.max_workers = getattr(args, 'threads', 5)
        os.makedirs(self.proj_dir, exist_ok=True)

        # 初始化 Agent
        self.agent = FinReportAgent(args)

    def process_single(self, report: str) -> OrchestratorResult:
        """
        处理多份研报，返回最终裁决报告和中间结果。
        """
        # ---------- 第一步：并行提取 ----------
        logger.info("生成初步分析...")
        anls_fname = path.join(self.proj_dir, 'anls.json')
        if(path.isfile(anls_fname)):
            anls_res = AnlsOutput.model_validate_json(open(anls_fname, encoding='utf8').read())
        else:
            anls_res = self.agent.extract(report)
            open(anls_fname, 'w', encoding='utf8').write(anls_res.json())

        # ---------- 第三步：多空初始立场 ----------
        logger.info("生成初始立场...")
        his_fname = path.join(self.proj_dir, 'history.json')
        if(path.isfile(his_fname)):
            history = json.loads(open(his_fname, encoding='utf8').read())
            bull_history, bear_history = history['bull'], history['bear']
        else:
            bull_initial = self.agent.bull_initial(anls_res)
            bear_initial = self.agent.bear_initial(anls_res)

            bull_history = [bull_initial]
            bear_history = [bear_initial]
            open(his_fname, 'w', encoding='utf8') \
                .write(json.dumps({
                    'bull': bull_history, 
                    'bear': bear_history
                }))

        # ---------- 第四步：多轮辩论 ----------
        for round_idx in range(len(bull_history), self.debate_rounds):
            logger.info(f"辩论第 {round_idx+1} 轮...")
            # 空方反驳多方最新观点
            bear_rebut = self.agent.bear_rebut(anls_res, bull_history[-1])
            bear_history.append(bear_rebut)
            # 多方反驳空方最新观点
            bull_rebut = self.agent.bull_rebut(anls_res, bear_history[-1])
            bull_history.append(bull_rebut)
            open(his_fname, 'w', encoding='utf8') \
                .write(json.dumps({
                    'bull': bull_history, 
                    'bear': bear_history
                }))

        # ---------- 第五步：裁决 ----------
        logger.info("生成最终裁决...")
        final_fname = path.join(self.proj_dir, 'final.md')
        if path.isfile(final_fname):
            final_verdict = open(final_fname, encoding='utf8').read()
        else:
            final_verdict = self.agent.judge(anls_res, bull_history, bear_history)
            open(final_fname, 'w', encoding='utf8').write(final_verdict)

        return OrchestratorResult(
            analysis=anls_res,
            bull_history=bull_history,
            bear_history=bear_history,
            final_verdict=final_verdict,
        )

    def _tr_extract(self, idx: int, report: str) -> Tuple[int, AnlsOutput]:
        return idx, self.agent.extract(report)

    def _parallel_extract(self, reports: List[str]) -> List[AnlsOutput]:
        """使用线程池并行提取"""
        res_fname = path.join(self.proj_dir, 'research,json')
        if path.isfile(res_fname):
            results = parse_obj_as(
                List[AnlsOutput],
                json.loads(open(res_fname, encoding='utf8').read())
            )
        else:
            results = [None for _ in range(len(reports))]
        pool = ThreadPoolExecutor(max_workers=self.max_workers)
        hdls = []

        for i, text in enumerate(reports):
            if not reports[i]:
                h = pool.submit(self._tr_extract, i, text)
                hdls.append(h)

        for h in hdls:
            idx, res = h.result()
            results[idx] = res
            logger.info(f"研报 {idx+1} 提取成功")
            open(res_fname, 'w', encoding='utf8') \
                .write(json.dumps([
                    it.model_dump() for it in results
                ]))

        return results

    def run(self) -> Optional[OrchestratorResult]:
        """执行 PDF 读取、研报处理和最终报告输出。"""
        print(self.args)
        fnames = self._get_pdf_files()
        if not fnames:
            print('请提供 PDF 文件或目录')
            return None

        ofname = self._get_output_fname()
        if path.isfile(ofname):
            print('PDF 已处理')
            return None

        reports = [
            read_pdf_text(open(fname, 'rb').read())
            for fname in fnames
        ]
        result = self.process_single(reports)

        print("\n" + "=" * 60)
        print("📊 最终裁决报告")
        print("=" * 60)
        print(result.final_verdict)
        open(ofname, 'w', encoding='utf8').write(result.final_verdict)
        return result

    def _get_pdf_files(self) -> List[str]:
        """获取待处理的 PDF 文件列表。"""
        if path.isfile(self.args.fname):
            fnames = [self.args.fname]
        elif path.isdir(self.args.fname):
            fnames = [
                path.join(self.args.fname, fname)
                for fname in os.listdir(self.args.fname)
            ]
        else:
            fnames = []
        return [fname for fname in fnames if fname.endswith('.pdf')]

    def _get_output_fname(self) -> str:
        """根据输入路径确定最终报告路径。"""
        return (
            self.args.fname[:-4] + '_report.md'
            if path.isfile(self.args.fname)
            else path.join(self.args.fname, 'report.md')
        )




def fin_report_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return MultiReportOrchestrator(args).run()

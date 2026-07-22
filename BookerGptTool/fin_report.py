from io import BytesIO
import fitz
from os import path
import os
import json
import logging
from pydantic import parse_obj_as
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from .util import ext_code_block, ext_cont_block, call_llm_retry, set_openai_props
from .fin_report_models import (
    ReportMeta,
    Fact,
    ResearcherOutput,
    DivergencePoint,
    FusionOutput,
    OrchestratorResult,
)

from .fin_report_pmt import (
    RESEARCHER_SYSTEM_PROMPT,
    RESEARCHER_EXTRACT_USER,
    FUSION_SYSTEM_PROMPT,
    FUSION_FUSE_USER,
    BULL_SYSTEM_PROMPT,
    BULL_INITIAL_USER,
    BULL_REBUT_USER,
    BEAR_SYSTEM_PROMPT,
    BEAR_INITIAL_USER,
    BEAR_REBUT_USER,
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER,
)

# 配置日志
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ===================== 工具函数 =====================
def read_pdf_text(data):
    pdf: fitz.Document = fitz.open('pdf', BytesIO(data))
    cont = '\n\n'.join([
        pg.get_text() for pg in pdf
    ])
    return cont


# ===================== Agent =====================


class FinReportAgent:
    """封装所有 LLM 调用的智能体类。"""

    def __init__(self, api_base: str, api_key: str, model: str, retry: int = 3, stream: bool = False):
        self.model = model
        self.max_tokens = 2000
        self.retry = retry
        self.stream = stream

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

    def extract(self, report_text: str) -> ResearcherOutput:
        user_prompt = RESEARCHER_EXTRACT_USER.format(report_text=report_text)
        parse_output = lambda s: \
            ResearcherOutput.model_validate_json(ext_code_block(s))
        return self._call(
            RESEARCHER_SYSTEM_PROMPT, user_prompt,
            temperature=0.0,
            parse_output=parse_output,
        )

    def fuse(self, extraction_results: List[ResearcherOutput]) -> FusionOutput:
        all_facts = []
        rating_list = []
        risk_set = set()
        for res in extraction_results:
            all_facts.extend(res.facts)
            rating = res.explicit_rating
            if rating and rating != "null":
                rating_list.append(rating)
            risk_set.update(res.explicit_risks)

        if not all_facts:
            return FusionOutput(
                consensus_facts=[],
                divergence_points=[],
                rating_distribution={},
                merged_risks=list(risk_set),
            )

        facts_json = json.dumps([f.model_dump() for f in all_facts], ensure_ascii=False, indent=2)
        user_prompt = FUSION_FUSE_USER.format(facts_json=facts_json)
        parse_output = lambda s: \
            FusionOutput.model_validate_json(ext_code_block(s))
        fused = self._call(
            FUSION_SYSTEM_PROMPT, user_prompt,
            temperature=0.1,
            parse_output=parse_output,
        )

        if not fused.consensus_facts:
            fused.consensus_facts = all_facts
        if not fused.rating_distribution:
            fused.rating_distribution = {r: rating_list.count(r) for r in set(rating_list)}
        if not fused.merged_risks:
            fused.merged_risks = list(risk_set)
        return fused

    def bull_initial(self, fused_data: FusionOutput) -> str:
        user_prompt = BULL_INITIAL_USER.format(
            consensus_facts=json.dumps([f.model_dump() for f in fused_data.consensus_facts], ensure_ascii=False, indent=2),
            divergence_points=json.dumps([d.model_dump() for d in fused_data.divergence_points], ensure_ascii=False, indent=2),
            rating_distribution=fused_data.rating_distribution,
            merged_risks=fused_data.merged_risks,
        )
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bull_rebut(self, fused_data: FusionOutput, opponent_argument: str) -> str:
        user_prompt = BULL_REBUT_USER.format(opponent_argument=opponent_argument)
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bear_initial(self, fused_data: FusionOutput) -> str:
        user_prompt = BEAR_INITIAL_USER.format(
            consensus_facts=json.dumps([f.model_dump() for f in fused_data.consensus_facts], ensure_ascii=False, indent=2),
            divergence_points=json.dumps([d.model_dump() for d in fused_data.divergence_points], ensure_ascii=False, indent=2),
            rating_distribution=fused_data.rating_distribution,
            merged_risks=fused_data.merged_risks,
        )
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def bear_rebut(self, fused_data: FusionOutput, opponent_argument: str) -> str:
        user_prompt = BEAR_REBUT_USER.format(opponent_argument=opponent_argument)
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            temperature=0.7,
            parse_output=ext_cont_block,
        )

    def judge(self, fused_data: FusionOutput, bull_history: List[str], bear_history: List[str]) -> str:
        user_prompt = JUDGE_USER.format(
            consensus_facts=json.dumps([f.model_dump() for f in fused_data.consensus_facts], ensure_ascii=False, indent=2),
            divergence_points=json.dumps([d.model_dump() for d in fused_data.divergence_points], ensure_ascii=False, indent=2),
            rating_distribution=fused_data.rating_distribution,
            merged_risks=fused_data.merged_risks,
            bull_history=chr(10).join(bull_history),
            bear_history=chr(10).join(bear_history),
        )
        raw = self._call(
            JUDGE_SYSTEM_PROMPT, user_prompt,
            temperature=0.2,
            parse_output=ext_cont_block,
        )
        return raw if raw else "裁决失败，请检查API配置。"


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

    def __init__(
        self,
        proj_dir,
        api_base: str,
        api_key: str,
        model: str,
        debate_rounds: int = 3,
        max_workers: int = 5,
        retry: int = 3,
        stream: bool = False,
    ):
        self.proj_dir = proj_dir
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.debate_rounds = debate_rounds
        self.max_workers = max_workers
        self.retry = retry
        self.stream = stream

        # 初始化 Agent
        self.agent = FinReportAgent(api_base, api_key, model, retry=retry, stream=stream)

    def process(self, reports: List[str]) -> OrchestratorResult:
        """
        处理多份研报，返回最终裁决报告和中间结果。
        """
        if not reports:
            raise ValueError("研报列表为空")

        logger.info(f"开始处理 {len(reports)} 份研报，并行提取...")
        # ---------- 第一步：并行提取 ----------
        extraction_results = self._parallel_extract(reports)

        # ---------- 第二步：融合 ----------
        logger.info("融合提取结果...")
        fused_fname = path.join(self.proj_dir, 'fused.json')
        if path.isfile(fused_fname):
            fused_data = json.loads(open(fused_fname, encoding='utf8').read())
            fused_data = FusionOutput(**fused_data)
        else:
            fused_data = self.agent.fuse(extraction_results)
            open(fused_fname, 'w', encoding='utf8') \
                .write(fused_data.model_dump_json())

        # ---------- 第三步：多空初始立场 ----------
        logger.info("生成初始立场...")
        his_fname = path.join(self.proj_dir, 'history.json')
        if(path.isfile(his_fname)):
            history = json.loads(open(his_fname, encoding='utf8').read())
            bull_history, bear_history = history['bull'], history['bear']
        else:
            bull_initial = self.agent.bull_initial(fused_data)
            bear_initial = self.agent.bear_initial(fused_data)

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
            bear_rebut = self.agent.bear_rebut(fused_data, bull_history[-1])
            bear_history.append(bear_rebut)
            # 多方反驳空方最新观点
            bull_rebut = self.agent.bull_rebut(fused_data, bear_history[-1])
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
            final_verdict = self.agent.judge(fused_data, bull_history, bear_history)
            open(final_fname, 'w', encoding='utf8').write(final_verdict)

        return OrchestratorResult(
            fused_data=fused_data,
            bull_history=bull_history,
            bear_history=bear_history,
            final_verdict=final_verdict,
        )

    def _parallel_extract(self, reports: List[str]) -> List[ResearcherOutput]:
        """使用线程池并行提取"""
        res_fname = path.join(self.proj_dir, 'research,json')
        if path.isfile(res_fname):
            results = parse_obj_as(
                List[ResearcherOutput],
                json.loads(open(res_fname, encoding='utf8').read())
            )
        else:
            results = [
                ResearcherOutput(
                    report_meta=ReportMeta(
                        title='', publisher='',
                        time='', industry='',
                    ),
                    facts=[],
                    explicit_rating='',
                    explicit_risks=[],
                )
                for _ in range(len(reports))
            ]
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_idx = {
                executor.submit(self.agent.extract, text): i 
                for i, text in enumerate(reports)
                if not results[i].facts
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res = future.result(timeout=60)
                    results[idx] = res
                    logger.info(f"研报 {idx+1} 提取成功")
                    open(res_fname, 'w', encoding='utf8') \
                        .write(json.dumps([
                            it.model_dump() for it in results
                        ]))
                except Exception as e:
                    logger.error(f"研报 {idx+1} 提取失败: {e}")
                    # 填充空结果以保持数量一致
                    results.append(ResearcherOutput(
                        report_meta=ReportMeta(title=None, publisher=None, time=None, industry=None),
                        facts=[],
                        explicit_rating=None,
                        explicit_risks=[],
                    ))
        return results


def fin_report_handle(args):
    print(args)
    set_openai_props(args)

    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]

    fnames = [
        f for f in fnames if f.endswith('.pdf')
    ]
    if not fnames:
        print('请提供 PDF 文件或目录')
        return

    ofname = (
        args.fname[:-4] + '_report.md'
        if path.isfile(args.fname)
        else path.join(args.fname, 'report.md')
    )
    if path.isfile(ofname):
        print('PDF 已处理')
        return

    proj_dir = (
        args.fname[:-4] + '_fin_report'
        if path.isfile(args.fname) 
        else path.join(args.fname, 'fin_report')
    )
    os.makedirs(proj_dir, exist_ok=True)

    reports = [
        read_pdf_text(open(f, 'rb').read())
        for f in fnames
    ]

    orchestrator = MultiReportOrchestrator(
        proj_dir=proj_dir,
        api_base=args.host,
        api_key=args.key,
        model=args.model,
        debate_rounds=args.rounds,
        max_workers=args.threads,
        retry=args.retry,
        stream=args.stream,
    )

    result = orchestrator.process(reports)

    print("\n" + "="*60)
    print("📊 最终裁决报告")
    print("="*60)
    print(result.final_verdict)

    open(ofname, 'w', encoding='utf8').write(result.final_verdict)
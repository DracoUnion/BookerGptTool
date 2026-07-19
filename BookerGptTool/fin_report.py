from io import BytesIO
import fitz
from os import path
import os
import asyncio
import json
import logging
import re
import time
from typing import List, Dict, Any, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI
from .util import ext_code_block, ext_cont_block, collect_stream_content

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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ===================== 工具函数 =====================
def read_pdf_text(data):
    pdf: fitz.Document = fitz.open('pdf', BytesIO(data))
    cont = '\n\n'.join([
        pg.get_text() for pg in pdf
    ])
    return cont


# ===================== 基类 =====================
class BaseAgent:
    """所有智能体的基类，封装通用的初始化和 LLM 调用逻辑"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, max_tokens: int = 2000, 
                 retry: int = 3, stream: bool = False,):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry = retry
        self.stream = stream

    def _call(self, system_prompt: str, user_prompt: str,
              max_tokens: Optional[int] = None, parse_output: Callable = None) -> str:
        """调用 LLM，返回原始文本响应，失败时自动重试"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        for attempt in range(1, self.retry + 1):
            try:
                logger.info(f'ques: {user_prompt}')
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=max_tokens or self.max_tokens,
                )
                oup = collect_stream_content(response) \
                    if self.stream \
                    else response.choices[0].message.content
                logger.info(f"ans: {oup}")
                if parse_output:
                    oup = parse_output(oup)
                return oup
            except Exception as e:
                logger.warning(f"{self.__class__.__name__} 第 {attempt}/{self.retry} 次调用失败: {e}")
                if attempt < self.retry:
                    wait = 2 ** attempt  # 指数退避: 2s, 4s, 8s ...
                    logger.info(f"{self.__class__.__name__} {wait}s 后重试...")
                    time.sleep(wait)
                else:
                    raise


# ===================== 1. 研究员 Agent (单份提取) =====================
class ResearcherAgent(BaseAgent):
    """单份研报提取，返回结构化 JSON"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)
        self.system_prompt = RESEARCHER_SYSTEM_PROMPT

    def extract(self, report_text: str) -> Dict[str, Any]:
        user_prompt = RESEARCHER_EXTRACT_USER.format(report_text=report_text)
        res = self._call(
            self.system_prompt, user_prompt,
            parse_output=ext_code_block,
        )
        return res


# ===================== 2. 融合仲裁官 (合并多份结果) =====================
class FusionAgent(BaseAgent):
    """合并多份研报的提取结果，生成共识、分歧、评级分布、风险并集"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def fuse(self, extraction_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        # 收集所有事实、评级、风险
        all_facts = []
        rating_list = []
        risk_set = set()
        for res in extraction_results:
            all_facts.extend(res.get("facts", []))
            rating = res.get("explicit_rating")
            if rating and rating != "null":
                rating_list.append(rating)
            risks = res.get("explicit_risks", [])
            risk_set.update(risks)

        # 如果没有事实，直接返回空融合
        if not all_facts:
            return {
                "consensus_facts": [],
                "divergence_points": [],
                "rating_distribution": {},
                "merged_risks": list(risk_set)
            }

        # 用 LLM 进行智能合并
        facts_json = json.dumps(all_facts, ensure_ascii=False, indent=2)
        user_prompt = FUSION_FUSE_USER.format(facts_json=facts_json)
        fused = self._call(
            FUSION_SYSTEM_PROMPT, user_prompt,
            parse_output=ext_code_block,
        )

        # 确保字段存在
        fused.setdefault("consensus_facts", all_facts)  # 降级：全部作为共识
        fused.setdefault("divergence_points", [])
        fused.setdefault("rating_distribution", {r: rating_list.count(r) for r in set(rating_list)})
        fused.setdefault("merged_risks", list(risk_set))
        return fused


# ===================== 3. 多方与空方 Agent =====================
class BullAgent(BaseAgent):
    """生成看多立场，并能够反驳对方"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def generate_initial(self, fused_data: Dict[str, Any]) -> str:
        user_prompt = BULL_INITIAL_USER.format(
            consensus_facts=json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2),
            divergence_points=json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2),
            rating_distribution=fused_data.get('rating_distribution', {}),
            merged_risks=fused_data.get('merged_risks', []),
        )
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            parse_output=ext_cont_block,
        )

    def rebut(self, fused_data: Dict[str, Any], opponent_argument: str) -> str:
        user_prompt = BULL_REBUT_USER.format(opponent_argument=opponent_argument)
        return self._call(
            BULL_SYSTEM_PROMPT, user_prompt,
            parse_output=ext_cont_block,
        )


class BearAgent(BaseAgent):
    """生成看空立场，并能够反驳对方"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def generate_initial(self, fused_data: Dict[str, Any]) -> str:
        user_prompt = BEAR_INITIAL_USER.format(
            consensus_facts=json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2),
            divergence_points=json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2),
            rating_distribution=fused_data.get('rating_distribution', {}),
            merged_risks=fused_data.get('merged_risks', []),
        )
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            parse_output=ext_cont_block,
        )

    def rebut(self, fused_data: Dict[str, Any], opponent_argument: str) -> str:
        user_prompt = BEAR_REBUT_USER.format(opponent_argument=opponent_argument)
        return self._call(
            BEAR_SYSTEM_PROMPT, user_prompt,
            parse_output=ext_cont_block,
        )


# ===================== 4. 裁判 Agent =====================
class JudgeAgent(BaseAgent):
    """综合所有辩论，给出最终裁决"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7, retry: int = 3, stream: bool = False):
        super().__init__(api_base, api_key, model, temperature, retry=retry, stream=stream)

    def judge(self, fused_data: Dict[str, Any], bull_history: List[str], bear_history: List[str]) -> str:
        user_prompt = JUDGE_USER.format(
            consensus_facts=json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2),
            divergence_points=json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2),
            rating_distribution=fused_data.get('rating_distribution', {}),
            merged_risks=fused_data.get('merged_risks', []),
            bull_history=chr(10).join(bull_history),
            bear_history=chr(10).join(bear_history),
        )
        raw = self._call(
            JUDGE_SYSTEM_PROMPT, user_prompt,
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
        api_base: str,
        api_key: str,
        model: str,
        debate_rounds: int = 3,
        max_workers: int = 5,
        retry: int = 3,
        stream: bool = False,
    ):
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.debate_rounds = debate_rounds
        self.max_workers = max_workers
        self.retry = retry
        self.stream = stream

        # 初始化各个Agent
        self.researcher = ResearcherAgent(api_base, api_key, model, temperature=0.0, retry=retry, stream=stream)
        self.fusion = FusionAgent(api_base, api_key, model, temperature=0.1, retry=retry, stream=stream)
        self.bull = BullAgent(api_base, api_key, model, temperature=0.7, retry=retry, stream=stream)
        self.bear = BearAgent(api_base, api_key, model, temperature=0.7, retry=retry, stream=stream)
        self.judge = JudgeAgent(api_base, api_key, model, temperature=0.2, retry=retry, stream=stream)

    def process(self, reports: List[str]) -> Dict[str, Any]:
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
        fused_data = self.fusion.fuse(extraction_results)

        # ---------- 第三步：多空初始立场 ----------
        logger.info("生成初始立场...")
        bull_initial = self.bull.generate_initial(fused_data)
        bear_initial = self.bear.generate_initial(fused_data)

        bull_history = [bull_initial]
        bear_history = [bear_initial]

        # ---------- 第四步：多轮辩论 ----------
        for round_idx in range(self.debate_rounds):
            logger.info(f"辩论第 {round_idx+1} 轮...")
            # 空方反驳多方最新观点
            bear_rebut = self.bear.rebut(fused_data, bull_history[-1])
            bear_history.append(bear_rebut)
            # 多方反驳空方最新观点
            bull_rebut = self.bull.rebut(fused_data, bear_history[-1])
            bull_history.append(bull_rebut)

        # ---------- 第五步：裁决 ----------
        logger.info("生成最终裁决...")
        final_verdict = self.judge.judge(fused_data, bull_history, bear_history)

        return {
            "fused_data": fused_data,
            "bull_history": bull_history,
            "bear_history": bear_history,
            "final_verdict": final_verdict
        }

    def _parallel_extract(self, reports: List[str]) -> List[Dict[str, Any]]:
        """使用线程池并行提取"""
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_idx = {executor.submit(self.researcher.extract, text): i for i, text in enumerate(reports)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    res = future.result(timeout=60)
                    results.append(res)
                    logger.info(f"研报 {idx+1} 提取成功")
                except Exception as e:
                    logger.error(f"研报 {idx+1} 提取失败: {e}")
                    # 填充空结果以保持数量一致
                    results.append({"report_meta": {}, "facts": [], "explicit_rating": None, "explicit_risks": []})
        return results


def fin_report_handle(args):

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

    reports = [
        read_pdf_text(open(f, 'rb').read())
        for f in fnames
    ]

    orchestrator = MultiReportOrchestrator(
        api_base=args.host,
        api_key=args.key,
        model=args.model,
        debate_rounds=args.rounds,
        max_workers=args.threads,
    )

    result = orchestrator.process(reports)

    print("\n" + "="*60)
    print("📊 最终裁决报告")
    print("="*60)
    print(result["final_verdict"])
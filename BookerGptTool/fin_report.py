import asyncio
import json
import logging
import re
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ===================== 工具函数 =====================
def clean_json(text: str) -> str:
    """移除 Markdown 代码块标记，提取 JSON"""
    pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else text


def safe_json_parse(text: str) -> dict:
    """安全解析 JSON，失败时返回空字典"""
    try:
        return json.loads(clean_json(text))
    except json.JSONDecodeError:
        logger.error(f"JSON 解析失败，原始内容: {text[:200]}...")
        return {}


# ===================== 1. 研究员 Agent (单份提取) =====================
class ResearcherAgent:
    """单份研报提取，返回结构化 JSON"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.0, max_tokens: int = 2000):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        self.system_prompt = """
你是一位严谨的金融研报研究员。从研报文本中提取关键信息，输出严格遵循以下 JSON 结构，不要添加额外文本：
{
  "report_meta": {"title": "...", "publisher": "...", "time": "YYYY-MM-DD", "industry": "..."},
  "facts": [{"fact_id": "F001", "category": "市场空间/财务数据/竞争格局/技术路线/政策环境", "content": "...", "value": "...", "source": "页码/章节"}],
  "explicit_rating": "买入/增持/中性/减持/卖出/超配/标配/低配 或 null",
  "explicit_risks": ["风险1", "风险2"]
}
注意：只提取客观事实，不臆测，无法获取的字段填 null。
"""

    def extract(self, report_text: str) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"请分析以下研报全文并输出JSON：\n\n{report_text}"}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )
            raw = response.choices[0].message.content
            cleaned = clean_json(raw)
            result = json.loads(cleaned)
            if "report_meta" not in result or "facts" not in result:
                raise ValueError("缺少必要字段")
            return result
        except Exception as e:
            logger.error(f"提取失败: {e}")
            # 返回空结构以便下游处理
            return {"report_meta": {}, "facts": [], "explicit_rating": None, "explicit_risks": []}


# ===================== 2. 融合仲裁官 (合并多份结果) =====================
class FusionAgent:
    """合并多份研报的提取结果，生成共识、分歧、评级分布、风险并集"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.0):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature

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
        prompt = f"""
你是一位客观的金融信息融合专家。给定多份研报提取的事实列表（可能包含重复或矛盾），请完成以下任务：

1. 识别出所有机构公认的**共识事实**（内容相同或高度相似，去重后保留最完整表述），输出为 consensus_facts 列表（格式与事实相同）。
2. 识别出**分歧点**（对同一主题的不同判断），输出为 divergence_points 列表，每个元素包含：
   - topic: 分歧主题
   - bull_view: 乐观方的观点及引用的事实ID（如有）
   - bear_view: 悲观方的观点及引用的事实ID（如有）
3. 统计评级分布：rating_distribution 字典。
4. 合并所有风险：merged_risks 列表（去重）。

输入事实列表：
{facts_json}

请直接输出JSON，格式如下：
{{
  "consensus_facts": [...],
  "divergence_points": [...],
  "rating_distribution": {{"买入": 2, "中性": 1, ...}},
  "merged_risks": ["风险1", ...]
}}
"""
        messages = [
            {"role": "system", "content": "你是一个数据融合助手，只输出JSON。"},
            {"role": "user", "content": prompt}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=3000
            )
            raw = response.choices[0].message.content
            fused = safe_json_parse(raw)
        except Exception as e:
            logger.error(f"融合LLM调用失败: {e}")
            fused = {}

        # 确保字段存在
        fused.setdefault("consensus_facts", all_facts)  # 降级：全部作为共识
        fused.setdefault("divergence_points", [])
        fused.setdefault("rating_distribution", {r: rating_list.count(r) for r in set(rating_list)})
        fused.setdefault("merged_risks", list(risk_set))
        return fused


# ===================== 3. 多方与空方 Agent =====================
class BullAgent:
    """生成看多立场，并能够反驳对方"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def _call(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": "你是一个专业的投资分析师。"},
            {"role": "user", "content": prompt}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"BullAgent 调用失败: {e}")
            return ""

    def generate_initial(self, fused_data: Dict[str, Any]) -> str:
        prompt = f"""
你是一位乐观的买方基金经理。基于以下融合数据生成看多立场报告。
数据：
共识事实：{json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2)}
分歧点：{json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2)}
评级分布：{fused_data.get('rating_distribution', {})}
风险列表：{fused_data.get('merged_risks', [])}

请输出"多方立场 (Bull Case)"，包含：
1. 核心结论
2. 支撑论据（每条引用事实ID或共识事实内容）
3. 对风险的反驳（说明为何这些风险可控或已price-in）
"""
        return self._call(prompt)

    def rebut(self, fused_data: Dict[str, Any], opponent_argument: str) -> str:
        prompt = f"""
你是一位乐观的买方基金经理。对方（空方）提出了以下论点：
{opponent_argument}

基于融合数据（同上），请针对对方论点进行逐条反驳，同时加强自己的看多立场。
输出"多方反驳"报告。
"""
        return self._call(prompt)


class BearAgent:
    """生成看空立场，并能够反驳对方"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.7):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def _call(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": "你是一个谨慎的风险分析师。"},
            {"role": "user", "content": prompt}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"BearAgent 调用失败: {e}")
            return ""

    def generate_initial(self, fused_data: Dict[str, Any]) -> str:
        prompt = f"""
你是一位谨慎的风控专家。基于以下融合数据生成看空立场报告。
数据：
共识事实：{json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2)}
分歧点：{json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2)}
评级分布：{fused_data.get('rating_distribution', {})}
风险列表：{fused_data.get('merged_risks', [])}

请输出"空方立场 (Bear Case)"，包含：
1. 核心顾虑
2. 风险论据（引用事实ID，指出乐观方忽略的盲区）
3. 对乐观预期的质疑（为何可能无法实现）
"""
        return self._call(prompt)

    def rebut(self, fused_data: Dict[str, Any], opponent_argument: str) -> str:
        prompt = f"""
你是一位谨慎的风控专家。对方（多方）提出了以下论点：
{opponent_argument}

请针对对方论点进行逐条反驳，指出其假设的脆弱性或数据解读的片面性，同时加强自己的看空立场。
输出"空方反驳"报告。
"""
        return self._call(prompt)


# ===================== 4. 裁判 Agent =====================
class JudgeAgent:
    """综合所有辩论，给出最终裁决"""

    def __init__(self, api_base: str, api_key: str, model: str, temperature: float = 0.2):
        self.client = OpenAI(base_url=api_base, api_key=api_key)
        self.model = model
        self.temperature = temperature

    def judge(self, fused_data: Dict[str, Any], bull_history: List[str], bear_history: List[str]) -> str:
        prompt = f"""
你是一位经验丰富的首席投资官。请基于以下所有材料，做出最终投资裁决。

融合数据：
共识事实：{json.dumps(fused_data.get('consensus_facts', []), ensure_ascii=False, indent=2)}
分歧点：{json.dumps(fused_data.get('divergence_points', []), ensure_ascii=False, indent=2)}
评级分布：{fused_data.get('rating_distribution', {})}
风险列表：{fused_data.get('merged_risks', [])}

多方全部发言：
{chr(10).join(bull_history)}

空方全部发言：
{chr(10).join(bear_history)}

请输出最终投资裁决报告，格式如下：
### 📊 最终投资裁决报告
**1. 综合评级：【买入/增持/中性/减持/卖出】**
**2. 核心投资逻辑：**（基于事实，200字内）
**3. 关键假设与催化剂：**（何种情况下观点升级或下调）
**4. 主要风险（空方观点的有效保留）：**（列出）
**5. 与原始研报评级的一致性：**（一致/修正/逆转及理由）
"""
        messages = [
            {"role": "system", "content": "你是一个客观、理性的首席投资官。"},
            {"role": "user", "content": prompt}
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=3000
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"JudgeAgent 调用失败: {e}")
            return "裁决失败，请检查API配置。"


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
    ):
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.debate_rounds = debate_rounds
        self.max_workers = max_workers

        # 初始化各个Agent
        self.researcher = ResearcherAgent(api_base, api_key, model, temperature=0.0)
        self.fusion = FusionAgent(api_base, api_key, model, temperature=0.1)
        self.bull = BullAgent(api_base, api_key, model, temperature=0.7)
        self.bear = BearAgent(api_base, api_key, model, temperature=0.7)
        self.judge = JudgeAgent(api_base, api_key, model, temperature=0.2)

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


# ===================== 使用示例 =====================
if __name__ == "__main__":
    # 配置你的 API（支持自定义 base_url）
    API_BASE = "http://localhost:8000/v1"   # 替换为你的地址
    API_KEY = "sk-xxx"
    MODEL = "Qwen/Qwen2.5-7B-Instruct"

    # 模拟多份研报文本（实际从文件读取）
    report1 = """
    报告名称：2026年新能源汽车行业深度
    发布机构：中信证券
    发布时间：2026-07-15
    2025年全球新能源汽车销量1800万辆，同比+35%。预计2026年突破2400万辆。
    评级：强于大市。
    风险：欧美贸易政策、碳酸锂价格波动。
    """
    report2 = """
    报告名称：新能源汽车2026年中展望
    发布机构：华泰证券
    发布时间：2026-07-10
    2025年全球新能源车销量1820万辆，增长34%。预计2026年2350万辆，渗透率27%。
    评级：增持。
    风险：技术路线切换、竞争加剧。
    """
    report3 = """
    报告名称：新能源车行业风险警示
    发布机构：海通证券
    发布时间：2026-06-28
    指出当前估值偏高，销量增速可能放缓至20%以下。
    评级：中性。
    风险：补贴退坡、库存高企。
    """

    reports = [report1, report2, report3]

    orchestrator = MultiReportOrchestrator(
        api_base=API_BASE,
        api_key=API_KEY,
        model=MODEL,
        debate_rounds=2,
        max_workers=3
    )

    result = orchestrator.process(reports)

    print("\n" + "="*60)
    print("📊 最终裁决报告")
    print("="*60)
    print(result["final_verdict"])
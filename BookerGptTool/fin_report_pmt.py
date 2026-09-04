"""
金融研报多智能体辩论系统 - 提示词模板
所有 LLM 提示词集中管理，便于统一修改和维护。
使用 Python str.format() 的 {} 作为占位符。
"""

FUND_ANLS_PROMPT = """
你是一位资深行业基本面分析师。请基于提供的行业报告，提取信息并以 JSON 格式返回，包含在三个反引号（```）中。

**重要要求：输出中必须包含`*_evidences`（证据）字段！**你不能只给出数字或趋势，必须引用报告原文中的具体数据或描述来支撑你的每个判断。

输出 JSON 格式如下：

```
{
    "institution": "机构名称",
    "industry": "行业名称",
    "date": "yyyymm",
    "revenue_growth": 营收同比增速（百分数，如 1~100），
    "profit_growth": 利润同比增速（百分数 1~100），
    "growth_evidences": ["引用报告原文中关于营收/利润增长的具体数据（例如：'报告指出营收同比+35%，环比+5%'）", ...],
    "roe_trend": "improving|stable|declining|unknown",
    "capex_trend": "expanding|stable|contracting|unknown",
    "capex_evidences": ["引用资本开支或产能扩张的描述", ...],
    "margin_trend": "improving|stable|declining|unknown",
    "roe_margin_evidences": ["引用ROE或毛利率变化的具体数字（例如：'ROE从12.5%提升至15.2%'）", ...],
    "earnings_revision": "upgraded|unchanged|downgraded|unknown",
    "prospect_score": 1~10 的整数,
    "key_risks": ["风险点1", "风险点2"],
    "risk_evidences": ["引用报告中提到的具体风险描述", ....]
}
```

报告内容：

[content]
{report}
[/content]
"""

###############################################################################

VAL_ANLS_PROMPT = """
你是一位估值与资金面分析师。请基于提供的行业报告，提取信息并以 JSON 格式返回，包含在三个反引号（```）中。

**重要要求：输出中必须包含`*_evidences`（证据）字段！**你不能只给出数字或趋势，必须引用报告原文中的具体数据或描述来支撑你的每个判断。

输出 JSON 格式如下：

```
{
    "institution": "机构名称",
    "industry": "行业名称",
    "date": "yyyymm",
    "pe_percentile": PE 历史分位数（0~100 整数）,
    "pb_percentile": PB 历史分位数（0~100 整数）,
    "valuation_assessment": "undervalued|fair|overvalued|unknown",
    "valuation_evidences": ["引用具体估值数据（例如：'当前PE为28倍，处于近5年45%分位'）", ...],
    "institutional_flow": "inflow|neutral|outflow|unknown",
    "retail_flow": "inflow|neutral|outflow|unknown",
    "flow_evidences": ["引用资金流向描述（例如：'北向资金近一月净流入120亿元'）", ...],
    "crowding_status": "low|moderate|high|unknown",
    "crowding_evidences": ["引用交易拥挤度描述（例如：'成交额占比从6%升至8.5%'）", ...]
    "valuation_score": 1~10 整数,
}

报告内容：

[content]
{report}
[/content]
"""

###############################################################################

SENTI_ANLS_PROMPT = """
请基于提供的行业报告，提取信息并以 JSON 格式返回，包含在三个反引号（```）中。

**重要要求：输出中必须包含`*_evidences`（证据）字段！**你不能只给出数字或趋势，必须引用报告原文中的具体数据或描述来支撑你的每个判断。

输出 JSON 格式如下：

```
{
    "institution": "机构名称",
    "industry": "行业名称",
    "date": "yyyymm",
    "market_style": "growth|value|neutral|unknown",
    "style_evidences": ["引用市场风格的描述（例如：'报告指出当前市场明显偏好成长风格'）", ...],
    "size_preference": "large|small|neutral|unknown",
    "turnover_heat": "cold|normal|hot|unknown",
    "turnover_evidences": ["引用换手率或交易热度数据（例如：'换手率处于历史60%分位'）", ...],
    "analyst_consensus": "bullish|neutral|bearish|unknown",
    "analyst_evidences": ["引用分析师一致预期的原文（例如：'主流机构上调盈利预测5-10%'）", ...],
    "momentum_direction": "up|down|consolidating|unknwon",
    "sentiment_score": 1~10 整数,
}
```

报告内容：

[content]
{report}
[/content]
"""

##############################################################################

# ===================== 3. 多方 Agent (BullAgent) =====================

BULL_SYSTEM_PROMPT = "你是一个专业的投资分析师。"

BULL_INITIAL_USER = """\
你是一位乐观的买方基金经理。基于以下融合数据生成看多立场报告。

数据：
```
{analysis}
```

请输出"多方立场 (Bull Case)"，包含：
1. 核心结论
2. 支撑论据（每条引用事实ID或共识事实内容）
3. 对风险的反驳（说明为何这些风险可控或已price-in）

注意输出一定包含在 [content] 和 [/content] 中间，否则无法解析！"""

BULL_REBUT_USER = """\
你是一位乐观的买方基金经理。对方（空方）提出了以下论点：

[content]
{opponent_argument}
[/content]

基于融合数据（同上），请针对对方论点进行逐条反驳，同时加强自己的看多立场。
输出"多方反驳"报告。

注意输出一定包含在 [content] 和 [/content] 中间，否则无法解析！"""


# ===================== 4. 空方 Agent (BearAgent) =====================

BEAR_SYSTEM_PROMPT = "你是一个谨慎的风险分析师。"

BEAR_INITIAL_USER = """\
你是一位谨慎的风控专家。基于以下融合数据生成看空立场报告。

数据：
```
{analysis}
```

请输出"空方立场 (Bear Case)"，包含：
1. 核心顾虑
2. 风险论据（引用事实ID，指出乐观方忽略的盲区）
3. 对乐观预期的质疑（为何可能无法实现）

注意输出一定包含在 [content] 和 [/content] 中间，否则无法解析！"""

BEAR_REBUT_USER = """\
你是一位谨慎的风控专家。对方（多方）提出了以下论点：

[content]
{opponent_argument}
[/content]

请针对对方论点进行逐条反驳，指出其假设的脆弱性或数据解读的片面性，同时加强自己的看空立场。
输出"空方反驳"报告。

注意输出一定包含在 [content] 和 [/content] 中间，否则无法解析！"""


# ===================== 5. 裁判 Agent (JudgeAgent) =====================

JUDGE_SYSTEM_PROMPT = "你是一个客观、理性的首席投资官。"

JUDGE_USER = """\
你是一位经验丰富的首席投资官。请基于以下所有材料，以 JSON 格式做出最终投资裁决，包含在三个反引号（```）中。

数据：
```
{analysis}
```

多方全部发言：

[content]
{bull_history}
[/content]

空方全部发言：

[content]
{bear_history}
[/content]

请输出最终投资裁决报告，格式如下：

```
{
    "institution": "机构名称",
    "industry": "行业名称",
    "date": "yyyymm",
    "overall_score": 1~10 整数,
    "recommendation" "overweight|neutral|underweight",
    "key_drivers": ["核心驱动因素", ...],
    "key_risks": ["核心风险", ...],
    "conclusion": "结论综述",
    "conclusio_evidences": ["引用具体数据及辩论结论", ...]
}
```
"""

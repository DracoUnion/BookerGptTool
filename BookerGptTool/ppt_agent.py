# -*- coding: utf-8 -*-
"""
ppt_agent.py —— 网页 PPT 子命令（ppt）的智能体。

参照 AGENTS.md 与 pdf_ocr_agent.py：本类**只封装大模型调用**，不碰 IO、不做缓存。
所有文件读写与缓存逻辑都在编排器（ppt.py）的 step_* 方法中完成。
"""

import json
from typing import Type

from pydantic import BaseModel, parse_obj_as

from .util import (
    ext_code_block,
    ext_cont_block,
    render_prompt,
    json_dump_model,
)
from .openai import (
    ask_chatgpt_retry,
    set_openai_props,
)
from .ppt_models import *
from .ppt_pmt import *


class PptAgent:
    """封装所有 LLM 调用的智能体类。

    每个大模型的调用实现为一个方法，负责：
    1. 用 render_prompt 渲染提示词（参数由编排器传入）；
    2. 调用 ask_chatgpt_retry（内部 call_llm_retry，带重试）；
    3. 通过 parse_output 回调解析输出（ext_code_block / ext_cont_block / parse_obj_as）。
    """

    def __init__(self, args) -> None:
        self.args = args
        self.model = args.model
        set_openai_props(args)

    # ── 内部辅助 ──────────────────────────────────────

    @staticmethod
    def _json(schema: Type[BaseModel], prompt: str, args) -> BaseModel:
        """调用 LLM 并把 ```json 代码块解析为 pydantic 对象。"""
        return ask_chatgpt_retry(
            prompt, args.model, args,
            parse_output=lambda s: parse_obj_as(
                schema, json.loads(ext_code_block(s))
            ),
        )

    @staticmethod
    def _text(prompt: str, args) -> str:
        """调用 LLM 并提取 [content]...[/content] 中的正文。"""
        return ask_chatgpt_retry(
            prompt, args.model, args,
            parse_output=ext_cont_block,
        )

    # ── 智能体方法（每个对应一次 LLM 调用）──────────────

    def gen_plan(
        self,
        material: str,
        themes_a: str,
        themes_b: str,
        audience: str = '',
        duration_minutes: str = '',
    ) -> DeckPlan:
        """根据素材与两套主题色预设生成整份 Deck 的规划（DeckPlan），自动选定风格 A/B。"""
        prompt = render_prompt(
            DECK_PLAN_PMT,
            MATERIAL=material,
            AUDIENCE=audience or '未提供，请根据素材推断受众',
            DURATION=duration_minutes or '未提供',
            THEMES_A=themes_a,
            THEMES_B=themes_b,
        )
        return self._json(DeckPlan, prompt, self.args)

    def write_deck(self, plan: DeckPlan, themes: str, layouts: str) -> str:
        """根据规划、主题色与版式参考生成全部幻灯片 HTML（仅 <section class="slide"> 块）。

        返回 HTML 字符串，由调用方负责填入模板并写盘。
        """
        prompt = render_prompt(
            DECK_WRITE_PMT,
            PLAN=json_dump_model(plan),
            THEMES=themes,
            LAYOUTS=layouts,
        )
        return self._text(prompt, self.args)
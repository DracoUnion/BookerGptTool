import json
import re
import logging
import json_repair
from typing import List

from pydantic import parse_obj_as

from .util import ext_code_block, ext_cont_block, render_prompt
from .openai import ask_chatgpt_retry, set_openai_props
from .md2wiki_pmt import *
from .md2wiki_models import WikiPagePlan, WikiFixDecision


logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class Md2WikiAgent:
    """封装所有 LLM 调用：每个方法对应 wiki 工作流中的一次独立 prompt 调用。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(self.args)

    def plan_pages(
        self,
        index: str, categories: str,
        title: str, source: str, url: str,
        summary: str, content: str,
    ) -> List[WikiPagePlan]:
        """根据一篇归档文章，规划需要创建或更新的知识页面（JSON 计划）。"""
        ques = render_prompt(
            WIKI_PLAN_PMT,
            persona=WIKI_PERSONA.strip(),
            index=index,
            categories=categories,
            title=title, source=source, url=url,
            summary=summary, content=content,
        )
        parse_output = lambda s: parse_obj_as(
            List[WikiPagePlan],
            json_repair.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output=parse_output,
        )

    def write_page(
        self,
        file: str, focus: str, index: str, category: str,
        existing: str,
        title: str, url: str, date: str,
        summary: str, content: str,
    ) -> str:
        """为单个页面编写 Markdown 正文（含 YAML frontmatter）。"""
        ques = render_prompt(
            WIKI_PAGE_PMT,
            persona=WIKI_PERSONA.strip(),
            file=file, focus=focus, index=index, category=category,
            existing=existing,
            title=title, url=url, date=date,
            summary=summary, content=content,
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output=ext_cont_block,
        )

    def fix_wikilinks(
        self,
        pages: str, broken: str,
        total: int, broken_count: int, budget: int,
    ) -> List[WikiFixDecision]:
        """针对一批坏链目标，产出改名/删除/保留的处理决策（JSON）。"""
        ques = render_prompt(
            WIKI_FIX_PMT,
            persona=WIKI_PERSONA.strip(),
            pages=pages, broken=broken,
            total=str(total), broken_count=str(broken_count),
            budget=str(budget),
        )
        parse_output = lambda s: parse_obj_as(
            List[WikiFixDecision],
            json_repair.loads(ext_code_block(s)),
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args, parse_output=parse_output,
        )

    @staticmethod
    def _json_dump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, indent=2)
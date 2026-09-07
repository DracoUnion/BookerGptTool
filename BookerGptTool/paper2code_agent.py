import os

import re

import shutil

import tarfile

from io import BytesIO

import logging

from os import path

import json_repair as json

import requests

from .paper2code_models import *

from .paper2code_pmt import *

from .util import ext_code_block, extname, render_prompt

from .openai import ask_chatgpt_retry, set_openai_props



class Paper2CodeAgent:
    """封装 paper2code 的 LLM 调用，每个方法对应一个独立请求。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(self.args)

    def gen_plan(self, paper: str) -> str:
        """根据论文生成复现计划。"""
        ques = render_prompt(PLAN_PMT, paper=paper)
        return ask_chatgpt_retry(ques, self.model, self.args)

    def gen_file_list(self, paper: str, plan: str) -> FListResult:
        """根据论文和计划生成项目文件列表及接口设计。"""
        ques = render_prompt(FLIST_PMT, paper=paper, plan=plan)
        parse_output = lambda s: FListResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_tasks(self, paper: str, plan: str, flist: str) -> TasksResult:
        """根据论文、计划和文件列表生成实现任务。"""
        ques = render_prompt(
            TASKS_PMT,
            paper=paper,
            plan=plan,
            flist=flist,
        )
        parse_output = lambda s: TasksResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_config(self, paper: str, plan: str, flist: str, tasks: str) -> str:
        """根据规划、文件列表和任务生成配置文件内容。"""
        ques = render_prompt(
            CFG_PMT,
            paper=paper,
            plan=plan,
            flist=flist,
            tasks=tasks,
        )
        ans = ask_chatgpt_retry(ques, self.model, self.args)
        return re.search(r'```\w*([\s\S]+?)```', ans).group(1)

    def gen_logic_analysis(
        self,
        paper: str,
        plan: str,
        flist: str,
        tasks: str,
        config: str,
        fname: str,
        fdesc: str,
    ) -> str:
        """为单个待实现文件生成逻辑分析。"""
        ques = render_prompt(
            ANLS_PMT,
            paper=paper,
            plan=plan,
            flist=flist,
            tasks=tasks,
            config=config,
            fname=fname,
            fdesc=fdesc,
        )
        return ask_chatgpt_retry(ques, self.model, self.args)

    def gen_code(
        self,
        paper: str,
        plan: str,
        flist: str,
        tasks: str,
        done_file_list: str,
        todo_file_name: str,
        logic_analysis: str,
    ) -> str:
        """根据单个文件的逻辑分析生成源码。"""
        ques = render_prompt(
            CODE_PMT,
            paper=paper,
            plan=plan,
            flist=flist,
            tasks=tasks,
            done_file_lst=done_file_list,
            todo_file_name=todo_file_name,
            logic_analysis=logic_analysis,
        )
        ans = ask_chatgpt_retry(ques, self.model, self.args)
        return re.search(r'```\w*([\s\S]+?)```', ans).group(1)

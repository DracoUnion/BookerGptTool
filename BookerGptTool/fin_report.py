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
from .openai import *
from .fin_report_models import *

from .fin_report_pmt import *

# 配置日志
logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ===================== 工具函数 =====================



# ===================== Agent =====================


from .fin_report_tools import FinReportTools



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
        self.proj_dir = (
            args.fname[:-4] + '_fin_report'
            if path.isfile(args.fname)
            else path.abspath(args.fname) +  '_fin_report'
        )
        os.makedirs(self.proj_dir, exist_ok=True)

        # 初始化 Agent
        self.tools = FinReportTools(args)

    def run(self) -> Optional[OrchestratorResult]:
        """执行 PDF 读取、研报处理和最终报告输出。"""
        print(self.args)
        fnames = self.tools.tool_list_input_files()
        if not fnames:
            print('请提供 PDF 或 MD 文件或目录')
            return None
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))

        call_llm_with_toolcall_retry(
            OVERALL_PMT, self.args.model, 
            self.tools.get_tool_defs(),
            self.tools.get_tool_dict(),
            tool_finish_name='tool_finish',
            retry=self.args.retry, 
            temp=self.args.temp, 
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            presence_penalty=self.args.presence_penalty,
            max_tokens=self.args.max_tokens,
            extra_body=self.args.extra_body,
        )

        logger.info(f'[*] 已完成，目标文件已写入 {self.pj_dir}')






def fin_report_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return MultiReportOrchestrator(args).run()


def reg_subparser(subparsers):
    fin_report_parser = subparsers.add_parser("fin-report", help="make financial report")
    fin_report_parser.add_argument("fname", help="PDF file name")
    fin_report_parser.set_defaults(func=fin_report_handle)

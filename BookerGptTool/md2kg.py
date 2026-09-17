import json
import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import ext_code_block
from .openai import call_llm_retry, set_openai_props
from .md2kg_models import *
from .md2kg_pmt import *

# 添加Pydantic导入用于自定义模型
from pydantic import BaseModel, Field

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)



# ============================================================================
# 1. 统一智能体
# ============================================================================
from .md2kg_tools import Md2KgTools
from .openai import call_llm_with_toolcall_retry

# ============================================================================
# 3. 调度协调器（Orchestrator）
# ============================================================================
class KnowledgeGraphOrchestrator:
    """协调整个流程：分块 → 并行抽取 → 冲突消解 → Schema对齐 → 评估 → 输出"""

    def __init__(self, args):
        """根据命令行参数初始化编排器。"""
        self.args = args
        self.max_workers = getattr(args, 'threads', 5)
        self.integration_threshold = getattr(args, 'threshold', 0.6)
        self.pj_dir = (
            path.dirname(args.fname) + '_md2kg'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_md2kg'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        # 初始化智能体
        self.tools = Md2KgTools(args)

    def run(self) -> Dict[str, Any]:
        """执行输入读取、知识图谱构建和结果输出的完整流程。"""
        logger.info(self.args)

        fnames = self.tools.tool_list_input_files()
        if not fnames:
            print('请提供 MD 文件或目录')
            return None
        
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








# ============================================================================
# 4. 处理入口
# ============================================================================
def md2kg_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return KnowledgeGraphOrchestrator(args).run()


def reg_subparser(subparsers):
    md2kg_parser = subparsers.add_parser("md2kg", help="md2kg")
    md2kg_parser.add_argument("fname", help="MD file name")
    md2kg_parser.set_defaults(func=md2kg_handle)
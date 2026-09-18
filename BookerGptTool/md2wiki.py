import logging
import os
from os import path
from typing import Dict, Any

from .openai import call_llm_with_toolcall_retry
from .md2wiki_tools import Md2WikiTools
from .md2wiki_pmt import OVERALL_PMT

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WikiOrchestrator:
    """协调整个流程：读取 → 切分 → 候选抽取 → 起草 → 输出"""

    def __init__(self, args):
        """根据命令行参数初始化编排器。"""
        self.args = args
        self.pj_dir = (
            path.dirname(args.fname) + '_md2wiki'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_md2wiki'
        )
        os.makedirs(self.pj_dir, exist_ok=True)
        # 初始化智能体
        self.tools = Md2WikiTools(args)

    def run(self) -> Dict[str, Any]:
        """执行输入读取、词条抽取起草和结果输出的完整流程。"""
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


def md2wiki_handle(args):
    """入口函数：创建编排器并运行完整流程。"""
    return WikiOrchestrator(args).run()


def reg_subparser(subparsers):
    md2wiki_parser = subparsers.add_parser("md2wiki", help="md2wiki")
    md2wiki_parser.add_argument("fname", help="MD file name")
    md2wiki_parser.set_defaults(func=md2wiki_handle)

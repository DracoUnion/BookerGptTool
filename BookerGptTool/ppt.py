# -*- coding: utf-8 -*-
"""
ppt.py —— 网页 PPT 子命令（ppt）的编排器。

将 guizang-ppt-skill 的网页 PPT 生成流程封装为命令行子命令：读取素材 → 生成 Deck 规划 →
生成幻灯片 HTML 并写入 index.html。整个流程通过「开放工具调用循环」驱动，工具见 ppt_tools.py。
"""

import logging
import os
from os import path
from typing import Dict, Any

from .openai import call_llm_with_toolcall_retry
from .ppt_tools import PptTools
from .ppt_pmt import OVERALL_PMT

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class PptOrchestrator:
    """编排器：通过工具调用循环驱动网页 PPT 的完整生成流程。"""

    def __init__(self, args):
        """根据命令行参数初始化编排器与工具集。"""
        self.args = args
        self.tools = PptTools(args)
        self.pj_dir = (
            path.dirname(args.fname) + '_ppt'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_ppt'
        )
        os.makedirs(self.pj_dir, exist_ok=True)

    def run(self) -> None:
        """启动工具调用循环，执行完整 PPT 生成流程。"""
        logger.info(self.args)
        logger.info('可用工具：%s', list(self.tools.get_tool_dict().keys()))

        call_llm_with_toolcall_retry(
            OVERALL_PMT, self.args.model,
            self.tools.get_tool_defs(),
            self.tools.get_tool_dict(),
            tool_finish_name='tool_finish',
            history_fname=path.join(self.pj_dir, 'history.yaml'),
            retry=self.args.retry,
            temp=self.args.temp,
            top_p=self.args.top_p,
            frequency_penalty=self.args.frequency_penalty,
            presence_penalty=self.args.presence_penalty,
            max_tokens=self.args.max_tokens,
            extra_body=self.args.extra_body,
        )

        logger.info(f'[*] 已完成，目标文件已写入 {self.pj_dir}')


def ppt(args):
    """入口函数：创建编排器并运行完整流程。"""
    return PptOrchestrator(args).run()


def reg_subparser(subparsers):
    parser = subparsers.add_parser(
        'ppt',
        help='根据素材生成单文件 HTML 的横向翻页网页 PPT（电子杂志风 / 瑞士国际主义风）',
    )
    parser.add_argument('fname', help='素材文件或目录（markdown/文本，作为 PPT 内容来源）')
    parser.set_defaults(func=ppt)

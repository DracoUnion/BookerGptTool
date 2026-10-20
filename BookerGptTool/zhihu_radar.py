# -*- coding: utf-8 -*-
"""zhihu-radar 子命令：抓取知乎首页推荐流，判断问题回答是否违背核心观点，拟定回答大纲并输出。"""
import logging
import os
import sys
from os import path
from typing import List

from .util import write_text, write_yaml_model, json_dump_model
from .openai import logger as oai_logger
from .zhihu_radar_agent import ZhihuRadarAgent
from .zhihu_radar_models import *
from .zhihu_radar_pmt import POINTS

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

OUT_FNAME = 'zhihu_radar_report.md'


def console_print(text: str) -> None:
    """打印到控制台，容错 Windows GBK 等编码不支持的字符（如数学负号）。"""
    try:
        print(text)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or 'utf-8'
        print(text.encode(enc, errors='replace').decode(enc, errors='replace'))


class ZhihuRadarOrchestrator:
    """编排器：抓取推荐流 → 判断违背 → 拟定大纲 → 输出报告。"""

    def __init__(self, args):
        self.args = args
        self.agent = ZhihuRadarAgent(args)
        self.pj_dir = self.agent.pj_dir
    

    # ── 步骤 1：抓取推荐流 ────────────────────────────────────

    def step_fetch(self) -> List[ZhihuQAItem]:
        logger.info('[1] 抓取知乎首页推荐流（%d 页）', self.args.pages)
        items = self.agent.fetch_feed(self.args.pages, self.args.count)
        logger.info('[1] 共获取 %d 条问题回答', len(items))
        return items

    # ── 步骤 2：判断是否违背核心观点 ─────────────────────────

    def step_judge(self, items: List[ZhihuQAItem]) -> List[RadarResult]:
        logger.info('[2] 大模型判断问题回答是否违背核心观点')
        verdicts = self.agent.judge_items(items, POINTS)
        vmap = {v.index: v for v in verdicts}
        results: List[RadarResult] = []
        for i, it in enumerate(items):
            v = vmap.get(i)
            if v is None:
                continue
            results.append(RadarResult(
                question_title=it.question_title,
                question_url=it.question_url,
                answer_id=it.answer_id,
                answer_url=it.answer_url,
                author=it.author,
                excerpt=it.excerpt,
                content=it.content,
                violates=v.violates,
                violated_point_ids=v.violated_point_ids,
                reason=v.reason,
            ))
        return results

    # ── 步骤 3：拟定回答大纲 ──────────────────────────────────

    def step_outline(self, results: List[RadarResult]) -> List[RadarResult]:
        violating = [r for r in results if r.violates]
        logger.info('[3] 为 %d 条违背观点的条目拟定回答大纲', len(violating))
        for r in violating:
            item = ZhihuQAItem(
                feed_id='',
                page=0,
                question_id='',
                question_title=r.question_title,
                question_url=r.question_url,
                answer_id=r.answer_id,
                answer_url=r.answer_url,
                author=r.author,
                excerpt=r.excerpt,
                content=r.content,
            )
            verdict = Verdict(
                index=0, violates=True,
                violated_point_ids=r.violated_point_ids,
                reason=r.reason,
            )
            outline = self.agent.gen_outline(item, POINTS, verdict)
            r.outline_title = outline.title
            r.outline = outline.outline
        return results

    # ── 步骤 4：输出报告 ──────────────────────────────────────

    def _build_md(self, results: List[RadarResult]) -> str:
        violating = [r for r in results if r.violates]
        obeying = [r for r in results if not r.violates]
        lines = [
            '# 知乎观点雷达报告',
            '',
            f'- 检查条目：{len(results)}',
            f'- 违背核心观点：{len(violating)}',
            f'- 未违背核心观点：{len(obeying)}',
            '',
        ]
        for i, r in enumerate(violating, 1):
            lines += [
                f'## {i}. {r.question_title}',
                '',
                f'- **链接**：{r.question_url}',
                f'- **答主**：{r.author}',
                f'- **被违背观点**：{"、".join("#" + p for p in r.violated_point_ids)}',
                '',
                '### 回答',
                '',
                r.excerpt or r.content,
                '',
                '### 判断理由',
                '',
                r.reason,
                '',
                '### 回答大纲',
                '',
                f'**{r.outline_title}**',
                '',
            ]
            for o in r.outline:
                o = o.strip()
                if o:
                    lines += [o, '']
            lines += ['---', '']
        if obeying:
            lines += ['## 未违背核心观点的条目', '']
            for i, r in enumerate(obeying, 1):
                lines += [
                    f'{i}. {r.question_title} — 被违背：无',
                    '',
                ]
        return '\n'.join(lines)

    def step_output(self, results: List[RadarResult]) -> str:
        logger.info('[4] 生成报告并保存')
        md = self._build_md(results)
        ofname = path.join(self.pj_dir, OUT_FNAME)
        write_text(ofname, md)
        # 同时保存结构化结果
        data_fname = path.join(self.pj_dir, 'zhihu_radar_results.yaml')
        write_yaml_model(data_fname, results)
        logger.info('[4] 已保存：%s / %s', ofname, data_fname)
        print('\n' + '=' * 70)
        console_print(md)
        print('=' * 70)
        return ofname

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        logger.info(self.args)
        if not POINTS:
            logger.critical('核心观点加载失败，中止')
            return None
        items = self.step_fetch()
        if not items:
            logger.critical('未抓取到任何推荐流内容，请检查 cookie 是否有效')
            return None
        results = self.step_judge(items)
        self.step_outline(results)
        ofname = self.step_output(results)
        logger.info('[*] 已完成，报告：%s', ofname)
        return ofname


def zhihu_radar(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    return ZhihuRadarOrchestrator(args).run()


def reg_subparser(subparsers):
    zh = subparsers.add_parser(
        'zhihu-radar', help='知乎观点雷达：抓取首页推荐流，判断问题回答是否违背核心观点并拟定回答大纲'
    )
    zh.add_argument(
        '-o', '--output', default='zhihu_radar',
        help='输出目录（报告与缓存），默认 zhihu_radar',
    )
    zh.add_argument(
        '-p', '--pages', type=int, default=3,
        help='抓取推荐流页数，默认 3',
    )
    zh.add_argument(
        '-c', '--count', type=int, default=20,
        help='每页条目数，默认 20',
    )
    zh.add_argument(
        '-pt', '--points', default='',
        help='核心观点文件路径，默认仓库 doc/zhihu_points.md',
    )
    zh.add_argument(
        '-b', '--browser', default='firefox,chrome,edge',
        help='读取 cookie 的浏览器（逗号分隔），默认 firefox,chrome,edge',
    )
    zh.add_argument(
        '-n', '--batch', type=int, default=10,
        help='单次大模型调用判断的条目数，默认 10',
    )
    zh.add_argument('-D', '--debug', action='store_true', help='调试模式')
    zh.set_defaults(func=zhihu_radar)
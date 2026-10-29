import re
import time
import logging
from os import path
from datetime import datetime

from .util import write_text, json_dump_model
from .openai import logger as oai_logger
from .bili_ai_proj_agent import BiliAiProjAgent
from .bili_ai_proj_models import *
from .bili_ai_proj_pmt import SEARCH_KEYWORDS

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

OUT_FNAME = 'bili_ai_proj.md'


def fmt_play(n: int) -> str:
    """将播放量格式化为中文可读形式，如 12345 → 1.2万。"""
    if n >= 100_000_000:
        return f'{n / 100_000_000:.1f}亿'
    if n >= 10_000:
        return f'{n / 10_000:.1f}万'
    return str(n)


class BiliAiProjOrchestrator:
    """编排器：B站搜索各关键词 → 大模型精选精品 AI 应用 → 打印并保存 md。"""

    def __init__(self, args):
        self.args = args
        self.agent = BiliAiProjAgent(args)
        self.pj_dir = self.agent.pj_dir

    def _resolve_keywords(self) -> List[str]:
        """解析关键词（-K 覆盖默认值），支持逗号/中文逗号分隔。"""
        kw = getattr(self.args, 'keywords', '') or ''
        res = [k.strip() for k in re.split(r'[,，]', kw) if k.strip()]
        return res or list(SEARCH_KEYWORDS)

    # ── 步骤 1：搜索 ─────────────────────────────────────────

    def step_search(self) -> List[BiliSearchResult]:
        logger.info('[1] 开始 B站搜索（时间倒序，每词取前 %d 页）', self.args.pages)
        keywords = self._resolve_keywords()
        reses = []
        for i, kw in enumerate(keywords):
            logger.info('[1] 搜索关键词：%s', kw)
            res = self.agent.search_videos(
                kw, self.args.pagesize, self.args.pages, self.args.sleep)
            logger.info('[1] %s → %d 条结果', kw, len(res.videos))
            reses.append(res)
            if i != len(keywords) - 1:
                time.sleep(self.args.sleep)
        return reses

    # ── 步骤 2：精选 ─────────────────────────────────────────

    def step_filter(self, reses: List[BiliSearchResult]) -> AppList:
        logger.info('[2] 大模型精选精品 AI 应用（目标 %d 个）', self.args.top)
        apps = self.agent.filter_apps(reses, self.args.top, self.args.pages)
        logger.info('[2] 精选出 %d 个应用', len(apps.apps))
        return apps

    # ── 步骤 3：输出 ─────────────────────────────────────────

    def _build_md(self, apps: AppList, total_candidates: int) -> str:
        now = datetime.now().strftime('%Y-%m-%d %H:%M')
        keywords = '、'.join(self._resolve_keywords())
        lines = [
            '# B站精选 · 精品 AI 应用 Top %d' % len(apps.apps),
            '',
            f'> 生成时间：{now}',
            f'> 数据来源：B站搜索（关键词：{keywords} · 按发布时间倒序 · 每词取前 {self.args.pages} 页 · 共 {total_candidates} 个候选）',
            f'> 筛选方式：大模型精选（{self.args.model}）',
            '',
            '---',
            '',
        ]
        for i, app in enumerate(apps.apps, 1):
            lines += [
                f'## {i}. {app.name} · {app.category}',
                '',
                f'- **UP主**：{app.author}',
                f'- **链接**：{app.url}',
                f'- **来源**：{app.source_keyword}　**播放**：{fmt_play(app.play)}　'
                f'**时长**：{app.duration or "-"}',
                f'- **推荐理由**：{app.reason}',
                '',
            ]
        return '\n'.join(lines)

    def step_output(self, apps: AppList, total_candidates: int) -> str:
        logger.info('[3] 生成报告并保存')
        md = self._build_md(apps, total_candidates)
        ofname = path.join(self.pj_dir, OUT_FNAME)
        write_text(ofname, md)
        logger.info('[3] 已保存：%s', ofname)
        print('\n' + '=' * 70)
        print(md)
        print('=' * 70)
        return ofname

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        logger.info(self.args)
        reses = self.step_search()
        total_candidates = sum(len(r.videos) for r in reses)
        if total_candidates == 0:
            logger.critical('未获取到任何搜索结果，请检查网络或稍后重试')
            return None
        apps = self.step_filter(reses)
        ofname = self.step_output(apps, total_candidates)
        logger.info('[*] 已完成，报告：%s', ofname)
        return ofname


def bili_ai_proj_handle(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    return BiliAiProjOrchestrator(args).run()


def reg_subparser(subparsers):
    bili_parser = subparsers.add_parser(
        "bili-ai-proj", help="B站搜索 AI 应用并精选 20 个精品应用"
    )
    bili_parser.add_argument(
        "-o", "--output", default='bili_ai_proj',
        help="输出目录（报告与缓存），默认 bili_ai_proj",
    )
    bili_parser.add_argument(
        "-K", "--keywords", default='',
        help='搜索关键词，逗号分隔，默认：ai 游戏,ai 项目,ai 应用,ai 软件,ai 网站',
    )
    bili_parser.add_argument(
        "-p", "--pagesize", type=int, default=20,
        help="每页条数，默认 20",
    )
    bili_parser.add_argument(
        "-P", "--pages", type=int, default=5,
        help="每个关键词搜索的页数，默认 5",
    )
    bili_parser.add_argument(
        "-n", "--top", type=int, default=20,
        help="精选应用数量上限，默认 20",
    )
    bili_parser.add_argument(
        "-s", "--sleep", type=float, default=1.5,
        help="相邻关键词请求间隔秒数，默认 1.5",
    )
    bili_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    bili_parser.set_defaults(func=bili_ai_proj_handle)
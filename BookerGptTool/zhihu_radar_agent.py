# -*- coding: utf-8 -*-
"""zhihu-radar 子命令的智能体：抓取知乎推荐流，判断问题回答是否违背核心观点并拟定回答大纲。"""
import json
import logging
import os
import re
import time
from os import path
from typing import List, Optional

import json_repair
import browser_cookie3 as bc
import requests
from pydantic import parse_obj_as

from .util import (
    render_prompt,
    json_dump_model,
    gen_objs_md5,
    ext_code_block,
    read_yaml_model,
    write_yaml_model,
)
from .openai import OpenAIClient
from .zhihu_radar_pmt import *
from .zhihu_radar_models import *

logger = logging.getLogger(__name__)


def _strip_html(text: str) -> str:
    """去除 HTML 标签，把 <br>/</p> 转为换行，反转义实体，压缩空白。"""
    if not text:
        return ''
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'[ \t\xa0]+', ' ', text)
    text = re.sub(r'\n\s*\n+', '\n', text)
    return text.strip()


def _parse_verdicts(s: str) -> List[Verdict]:
    """解析大模型对一批问题回答的判断结果 JSON。"""
    try:
        text = ext_code_block(s)
    except Exception:
        text = s
    data = json_repair.loads(text)
    if isinstance(data, dict) and 'verdicts' in data:
        data = data['verdicts']
    if not isinstance(data, list):
        data = [data]
    return parse_obj_as(List[Verdict], data)


def _parse_outline(s: str) -> AnswerOutline:
    """解析大模型拟定的回答大纲 JSON。"""
    try:
        text = ext_code_block(s)
    except Exception:
        text = s
    return AnswerOutline(**json_repair.loads(text))


class ZhihuRadarAgent:
    """封装 cookie 获取、推荐流抓取与两次大模型调用。"""

    FEED_URL = 'https://www.zhihu.com/api/v3/feed/topstory/recommend'
    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

    def __init__(self, args):
        self.model = args.model
        self.args = args
        self.pj_dir = path.abspath(args.output)
        self.asset_dir = path.join(self.pj_dir, 'asset')
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(self.asset_dir, exist_ok=True)
        self.llm = OpenAIClient.from_args(self.args)
        self._cookie_jar = None

    # ── cookie（browser_cookies3 包）──────────────────────────

    def _get_cookie_jar(self):
        """从浏览器读取 zhihu.com 域名的 cookie 并返回 CookieJar。"""
        if self._cookie_jar is not None:
            return self._cookie_jar            
        domain = '.zhihu.com'
        browsers = getattr(self.args, 'browser', '') or 'firefox,chrome,edge'
        browsers = [b.strip() for b in browsers.split(',') if b.strip()]
        if not browsers:
            browsers = ['firefox', 'chrome', 'edge']
        for bname in browsers:
            fn = getattr(bc, bname, None)
            if fn is None:
                logger.debug('browser_cookies3 无 %s()', bname)
                continue
            try:
                jar = fn(domain_name=domain)
                if jar and len(list(jar)) > 0:
                    logger.info('从浏览器 %s 读取到知乎 cookie', bname)
                    self._cookie_jar = jar
                    return jar
            except Exception as ex:
                logger.warning('浏览器 %s cookie 读取失败：%s', bname, ex)
        raise RuntimeError(
            '无法从浏览器读取知乎 cookie，请先用 Firefox/Chrome 登录 zhihu.com'
        )

    # ── 推荐流抓取 ───────────────────────────────────────────

    def _request_feed_page(self, params: dict) -> dict:
        """请求一页推荐流，返回原始 JSON；失败时重试。"""
        headers = {
            'User-Agent': self.UA,
            'Referer': 'https://www.zhihu.com/',
            'x-requested-with': 'fetch',
        }
        jar = self._get_cookie_jar()
        for attempt in range(4):
            try:
                r = requests.get(
                    self.FEED_URL, params=params, cookies=jar,
                    headers=headers, timeout=15,
                )
            except Exception as ex:
                logger.debug('知乎请求异常：%s', ex)
                time.sleep(2)
                continue
            if r.status_code != 200:
                logger.warning('知乎返回 %s，重试（第 %d 次）', r.status_code, attempt + 1)
                time.sleep(2 * (attempt + 1))
                continue
            try:
                d = r.json()
            except Exception:
                logger.warning('知乎响应非 JSON，重试（第 %d 次）', attempt + 1)
                time.sleep(1)
                continue
            if 'data' not in d:
                logger.warning('知乎响应缺少 data 字段，重试（第 %d 次）', attempt + 1)
                time.sleep(1)
                continue
            return d
        return {}

    def fetch_feed(self, pages: int, count: int = 20) -> List[ZhihuQAItem]:
        """抓取指定页数的推荐流并解析为问题回答列表，带文件缓存。"""
        items: List[ZhihuQAItem] = []
        seen = set()
        after_id = ''
        for page in range(1, pages + 1):
            params = {
                'action': 'down',
                'after_id': after_id,
                'count': count,
                'desktop': 'true',
                'page_number': page,
            }
            logger.info('[1] 抓取推荐流第 %d/%d 页', page, pages)
            d = self._request_feed_page(params)
            if not d:
                logger.warning('[1] 第 %d 页抓取失败', page)
                break
            for it in (d.get('data') or []):
                qa = self._parse_feed_item(it, page)
                if qa is None:
                    continue
                if qa.answer_id in seen:
                    continue
                seen.add(qa.answer_id)
                items.append(qa)
            paging = d.get('paging') or {}
            if paging.get('is_end'):
                break
            # 用 paging.next 中的 after_id 继续翻页
            nxt = paging.get('next') or ''
            m = re.search(r'[?&]after_id=([^&]*)', nxt)
            after_id = m.group(1) if m else str(len(items))
            if page < pages:
                time.sleep(1.0)

        return items

    def _parse_feed_item(self, it: dict, page: int) -> Optional[ZhihuQAItem]:
        """把一条 feed 条目解析为问题+回答；非回答类型或缺少 question 则返回 None。"""
        target = it.get('target') or {}
        if target.get('type') != 'answer':
            return None
        question = target.get('question') or {}
        qid = str(question.get('id') or '')
        qtitle = (question.get('title') or '').strip()
        if not qtitle:
            return None
        aid = str(target.get('id') or '')
        if not aid:
            return None
        author = ((target.get('author') or {}).get('name') or '').strip()
        excerpt = _strip_html(target.get('excerpt') or '')
        content = _strip_html(target.get('content') or '')
        return ZhihuQAItem(
            feed_id=str(it.get('id') or ''),
            page=page,
            question_id=qid,
            question_title=qtitle,
            question_url=f'https://www.zhihu.com/question/{qid}' if qid else '',
            answer_id=aid,
            answer_url=f'https://www.zhihu.com/question/{qid}/answer/{aid}' if qid else '',
            author=author,
            excerpt=excerpt[:300],
            content=content[:2000],
            voteup_count=int(target.get('voteup_count') or 0),
        )

    # ── 大模型调用：判断 ──────────────────────────────────────

    def judge_items(self, items: List[ZhihuQAItem], points: str) -> List[Verdict]:
        """调用大模型判断每条问题回答是否违背核心观点，带文件缓存。"""
        cache_fname = path.join(
            self.asset_dir,
            'judge_' + gen_objs_md5(points, items) + '.yaml',
        )
        r = read_yaml_model(cache_fname, List[Verdict])
        if r is not None:
            return r

        verdicts: List[Verdict] = []
        batch = getattr(self.args, 'batch', 10)
        for st in range(0, len(items), batch):
            sub = items[st:st + batch]
            compact = []
            for i, it in enumerate(sub):
                compact.append({
                    'index': st + i,
                    'question_title': it.question_title,
                    'answer_excerpt': (it.excerpt or it.content)[:300],
                })
            logger.info('[2] 判断第 %d-%d / %d 条', st + 1, st + len(sub), len(items))
            ques = render_prompt(
                JUDGE_USER_PMT,
                points=points,
                items=json_dump_model(compact),
            )
            msgs = [
                {'role': 'system', 'content': JUDGE_SYSTEM_PMT},
                {'role': 'user', 'content': ques},
            ]
            parse_output = lambda s: _parse_verdicts(s)
            res = self.llm.ask_chatgpt_retry(
                msgs, self.model, self.args,
                parse_output=parse_output,
            )
            for v in res:
                if 0 <= v.index < len(items):
                    verdicts.append(v)
        write_yaml_model(cache_fname, verdicts)
        return verdicts

    # ── 大模型调用：拟定回答大纲 ──────────────────────────────

    def gen_outline(
        self, item: ZhihuQAItem, points: str, verdict: Verdict,
    ) -> AnswerOutline:
        """为违背核心观点的问题+回答拟定符合观点的回答大纲，带文件缓存。"""
        cache_fname = path.join(
            self.asset_dir,
            'outline_' + gen_objs_md5(
                points, item.answer_id, verdict.reason,
            ) + '.yaml',
        )
        r = read_yaml_model(cache_fname, AnswerOutline)
        if r is not None:
            return r

        ques = render_prompt(
            OUTLINE_USER_PMT,
            points=points,
            question_title=item.question_title,
            answer_excerpt=(item.excerpt or item.content)[:300],
            violated_point_ids=json.dumps(verdict.violated_point_ids, ensure_ascii=False),
            reason=verdict.reason,
        )
        msgs = [
            {'role': 'system', 'content': OUTLINE_SYSTEM_PMT},
            {'role': 'user', 'content': ques},
        ]
        res = self.llm.ask_chatgpt_retry(
            msgs, self.model, self.args,
            parse_output=lambda s: _parse_outline(s),
        )
        write_yaml_model(cache_fname, res)
        return res

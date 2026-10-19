import re
import os
import time
import html
import logging
import json_repair
import requests
from os import path
from typing import List, Optional

from .util import (
    render_prompt,
    json_dump_model,
    gen_objs_md5,
    ext_code_block,
    read_yaml_model,
    write_yaml_model,
)
from .openai import ask_chatgpt_retry, set_openai_props
from .bili_ai_proj_pmt import *
from .bili_ai_proj_models import *

logger = logging.getLogger(__name__)


def _clean_title(title: str) -> str:
    """去除标题中的 <em class="keyword"> 等 HTML 标签并反转义。"""
    title = re.sub(r'<[^>]+>', '', title or '')
    return html.unescape(title).strip()


def _parse_app_list(s: str) -> AppList:
    """解析大模型输出为 AppList：优先取 ```代码栅栏``` 内 JSON，否则直接解析全文。"""
    try:
        text = ext_code_block(s)
    except Exception:
        text = s
    data = json_repair.loads(text)
    if not isinstance(data, dict) or 'apps' not in data:
        # 某些模型可能直接输出数组
        data = {'apps': data}
    return AppList.model_validate(data)


class BiliAiProjAgent:
    """封装 B站搜索与 LLM 精选调用。"""

    SEARCH_URL = 'https://api.bilibili.com/x/web-interface/search/type'
    UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

    def __init__(self, args):
        self.model = args.model
        self.args = args
        self.pj_dir = path.abspath(args.output)
        self.asset_dir = path.join(self.pj_dir, 'asset')
        os.makedirs(self.pj_dir, exist_ok=True)
        os.makedirs(self.asset_dir, exist_ok=True)
        set_openai_props(self.args)
        self._session: Optional[requests.Session] = None

    # ── B站搜索 ─────────────────────────────────────────────

    def _get_session(self) -> requests.Session:
        """获取带 buvid3 cookie 的会话（先访问 B站首页种 cookie，否则会 412）。"""
        if self._session is None:
            s = requests.Session()
            s.headers.update({
                'User-Agent': self.UA,
                'Referer': 'https://www.bilibili.com/',
            })
            s.get('https://www.bilibili.com/', timeout=15)
            self._session = s
        return self._session

    def _request_search_page(self, keyword: str, page: int, page_size: int) -> List[dict]:
        """调用 B站搜索接口获取一页，返回原始视频字典列表；失败时重试并重置 cookie。"""
        params = {
            'search_type': 'video',
            'keyword': keyword,
            'order': 'pubdate',  # 时间倒序
            'page': page,
            'page_size': page_size,
        }
        for attempt in range(4):
            s = self._get_session()
            try:
                r = s.get(self.SEARCH_URL, params=params, timeout=15)
            except Exception as ex:
                logger.debug('B站请求异常：%s', ex)
                time.sleep(2)
                continue
            if r.status_code == 412:
                # 反爬拦截：重置会话重取 cookie，指数退避
                logger.warning('B站返回 412 反爬拦截，重置会话重试（第 %d 次）', attempt + 1)
                self._session = None
                time.sleep(2 * (attempt + 1))
                continue
            try:
                d = r.json()
            except Exception:
                logger.warning('B站响应非 JSON，重试（第 %d 次）', attempt + 1)
                time.sleep(1)
                continue
            if d.get('code') != 0:
                logger.warning('B站接口返回 code=%s（%s），重试', d.get('code'), d.get('message'))
                time.sleep(1)
                continue
            data = d.get('data') or {}
            return data.get('result') or []
        return []

    def search_videos(
        self, keyword: str, page_size: int = 20, pages: int = 5, sleep: float = 1.5,
    ) -> BiliSearchResult:
        """按关键词搜索 B站视频（时间倒序，取前 pages 页），跨页去重，带文件缓存。"""
        cache_fname = path.join(
            self.asset_dir,
            'search_' + gen_objs_md5(keyword, page_size, pages) + '.yaml'
        )
        r = read_yaml_model(cache_fname, BiliSearchResult)
        if r:
            return r
        videos = []
        seen = set()
        for page in range(1, pages + 1):
            logger.info('[1] %s 第 %d/%d 页', keyword, page, pages)
            raw_list = self._request_search_page(keyword, page, page_size)
            for v in raw_list:
                bvid = v.get('bvid')
                if not bvid or bvid in seen:
                    continue
                seen.add(bvid)
                videos.append(BiliVideo(
                    keyword=keyword,
                    bvid=bvid,
                    title=_clean_title(v.get('title', '')),
                    author=v.get('author', ''),
                    url=v.get('arcurl', '') or
                         f'https://www.bilibili.com/video/{bvid}',
                    pubdate=v.get('pubdate') or None,
                    duration=v.get('duration', '') or '',
                    play=int(v.get('play') or 0),
                    like=int(v.get('like') or 0),
                    description=(v.get('description', '') or '').strip(),
                    tags=(v.get('tag', '') or '').strip(),
                ))
            if len(raw_list) < page_size:
                # 已到末页
                break
            if page < pages:
                time.sleep(sleep)
        res = BiliSearchResult(keyword=keyword, videos=videos)
        write_yaml_model(cache_fname, res)
        return res

    # ── LLM 精选 ────────────────────────────────────────────

    @staticmethod
    def _compact_candidates(results: List[BiliSearchResult]) -> List[dict]:
        """压缩候选数据（仅保留筛选所需字段，截断简介），避免提示词过大。"""
        return [
            {
                'keyword': r.keyword,
                'bvid': v.bvid,
                'title': v.title,
                'author': v.author,
                'url': v.url,
                'play': v.play,
                'duration': v.duration,
                'description': v.description[:120],
            }
            for r in results
            for v in r.videos
        ]

    def filter_apps(
        self, results: List[BiliSearchResult], top: int = 20, pages: int = 5,
    ) -> AppList:
        """调用大模型从候选视频中精选精品 AI 应用，带文件缓存。"""
        cache_fname = path.join(
            self.asset_dir,
            'filter_' + gen_objs_md5(results, top) + '.yaml'
        )
        r = read_yaml_model(cache_fname, AppList)
        if r:
            return r
        candidates = self._compact_candidates(results)
        ques = render_prompt(
            FILTER_USER_PROMPT,
            count=str(len(candidates)),
            top=str(top),
            pages=str(pages),
            results=json_dump_model(candidates),
        )
        logger.info('[2] 候选 %d 条，提示词约 %d 字符', len(candidates), len(ques))
        parse_output = lambda s: _parse_app_list(s)
        apps = ask_chatgpt_retry(
            [{'role': 'system', 'content': FILTER_SYSTEM_PROMPT},
             {'role': 'user', 'content': ques}],
            self.model, self.args,
            parse_output=parse_output,
        )
        write_yaml_model(cache_fname, apps)
        return apps

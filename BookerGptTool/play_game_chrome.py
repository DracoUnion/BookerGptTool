#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""play-game-chrome 子命令：通过 Chrome MCP 服务器在网页上玩游戏。

循环：调用 Chrome MCP 截取当前页面截图 -> 发送给视觉大模型 -> 解析模型返回的
JSON 操作 -> 通过 Chrome MCP 执行鼠标 / 键盘 / 滚动 / 跳转 -> 再次截图，
如此反复，直到模型判定结束。

Chrome MCP 服务器即 mcp-chrome-bridge（默认 http://127.0.0.1:12306/mcp），
通过浏览器扩展 / CDP 驱动真实 Chrome。本模块实现了一个极简的 MCP Streamable
HTTP 客户端（JSON-RPC over SSE），无需额外依赖。

注意：mcp-chrome-bridge 后端使用单例 Server，同一时刻只允许一个 MCP 客户端
会话。若连接被占用，请先断开其他客户端（如 Claude Code 里的 MCP 配置）。
"""

import base64
import copy
import io
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import json_repair
from pydantic import BaseModel

from .openai import (
    call_vlm_retry,
    set_openai_props,
)
from .openai import logger as oai_logger
from .util import render_prompt, ext_code_block

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ── 大模型提示词 ───────────────────────────────


PLAY_PMT = '''
你正在通过浏览器游玩一款网页游戏。以下是当前页面的截图，分辨率为 {w}×{h}
（宽×高），坐标原点在截图左上角，x 向右、y 向下，单位为像素。截图与你操作时
使用的视口坐标一一对应，请直接按截图上的位置给出坐标。

## 目标

{goal}

## 任务

观察截图，推理游戏当前状态，然后决定下一步操作。你只能看到当前这一帧画面，
请依据画面内容做出尽量合理的决策。

## 返回格式

只返回一个 JSON 对象，包含在三个反引号（```）中：

```
{"thought": "简短说明你观察到了什么、打算做什么", "actions": [{"type": "click", "x": 100, "y": 200}], "finish": false}
```

## 操作类型

- 点击：`{"type": "click", "x": 像素, "y": 像素}`
  （另支持 `"button": "right"` 与 `"double": true`）
- 键盘按键：`{"type": "key", "keys": ["space"]}；组合键如 {"type": "key", "keys": ["ctrl", "s"]}`
- 输入文本（同时按下 Shift 或直接键入）：`{"type": "type", "text": "要输入的字符串"}`
- 等待：`{"type": "wait", "ms": 500}`
- 滚动：`{"type": "scroll", "direction": "down", "amount": 3}`
  （direction 取值 up/down/left/right，amount 为 1~10 档）
- 跳转（非当前页时可跳到指定游戏地址）：`{"type": "navigate", "url": "https://..."}`

## 可用键位

{keys}

## 规则

1.  x 必须是 0~{w}、y 必须是 0~{h} 之间的整数。
2.  actions 允许为空数组，但不能为 null。
3.  上一步你执行了：{last}。如果多次操作后画面没有变化，请换一种策略，
    或直接把 "finish" 设为 true。
4.  当游戏结束、目标达成、或你认为无法继续时，把 "finish" 设为 true。
5.  不要执行危险操作（例如关闭页面等）。
'''.strip()

SPECIAL_KEYS = (
    'space, enter, shift, ctrl, alt, tab, esc, backspace, delete, insert, '
    'home, end, pageup, pagedown, up, down, left, right, capslock, '
    'f1~f12, 0~9, a~z, ArrowUp, ArrowDown, ArrowLeft, ArrowRight'
)


# ── 数据模型 ─────────────────────────────────


class GameAction(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: str = 'left'
    double: bool = False
    keys: Optional[List[str]] = None
    text: Optional[str] = None
    ms: int = 300
    direction: str = 'down'
    amount: int = 3
    url: Optional[str] = None


class PlayGameResp(BaseModel):
    thought: str = ''
    actions: List[GameAction] = []
    finish: bool = False


# ── MCP Streamable HTTP 客户端 ─────────────────


def _sse_messages(text: str) -> List[Dict[str, Any]]:
    """解析 SSE 文本，把所有 `event: message` 事件中的 JSON 依次返回。"""
    msgs: List[Dict[str, Any]] = []
    for block in text.split('\n\n'):
        event = ''
        data_lines: List[str] = []
        for line in block.splitlines():
            if line.startswith(':') or line == '':
                continue
            if line.startswith('event:'):
                event = line[6:].strip()
            elif line.startswith('data:'):
                data_lines.append(line[5:].strip())
        if event == 'message' and data_lines:
            try:
                msgs.append(json.loads('\n'.join(data_lines)))
            except json.JSONDecodeError:
                logger.warning('SSE data 不是合法 JSON：%r', data_lines)
    return msgs


def _find_value(obj: Any, keys: Tuple[str, ...]) -> Optional[str]:
    """递归查找字段名命中 keys 且值为非空 str 的第一项。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and v and k.lower() in keys:
                return v
            hit = _find_value(v, keys)
            if hit:
                return hit
    elif isinstance(obj, list):
        for it in obj:
            hit = _find_value(it, keys)
            if hit:
                return hit
    return None


def _to_png_bytes(b64: str) -> Tuple[bytes, Tuple[int, int]]:
    """把 base64 图片（可能带 data URL 前缀、可能是任意格式）规整为 PNG 字节。"""
    from PIL import Image
    if ',' in b64 and b64.strip().lower().startswith('data:image'):
        b64 = b64.split(',', 1)[1]
    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), img.size


class ChromeMcpClient:
    """针对 mcp-chrome-bridge 的极简 Streamable HTTP 客户端。

    按 MCP 规范做 initialize 握手建立 session，之后用 Mcp-Session-Id 头
    标识会话，经 `tools/call` 调用服务器暴露的 chrome_* 工具。
    """

    def __init__(self, endpoint: str = 'http://127.0.0.1:12306/mcp',
                 timeout: float = 120.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._req_id = 0

    # -- 底层 JSON-RPC ---------------------------------------------------

    def _post(self, method: str, params: Optional[dict] = None) -> Optional[dict]:
        self._req_id += 1
        body = {'jsonrpc': '2.0', 'id': self._req_id,
                'method': method, 'params': params or {}}
        headers = {
            'Accept': 'application/json, text/event-stream',
            'Content-Type': 'application/json',
        }
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        with httpx.Client(timeout=self.timeout) as c:
            resp = c.request('POST', self.endpoint, json=body, headers=headers)
            if resp.status_code >= 400:
                detail = ''
                try:
                    detail = str(resp.json().get('message', ''))
                except Exception:
                    detail = resp.text[:300]
                raise RuntimeError(
                    f'MCP {method} HTTP {resp.status_code}：{detail}'.strip()
                )
            sid = resp.headers.get('mcp-session-id')
            if sid:
                self.session_id = sid
            ct = (resp.headers.get('content-type') or '').lower()
            if 'text/event-stream' in ct:
                for msg in _sse_messages(resp.text):
                    if msg.get('id') != self._req_id:
                        continue
                    if 'error' in msg:
                        raise RuntimeError(f'MCP {method} 失败：{msg["error"]}')
                    return msg.get('result')
                return None  # 通知类请求没有 result
            data = json.loads(resp.text)
            if 'error' in data:
                raise RuntimeError(f'MCP {method} 失败：{data["error"]}')
            return data.get('result')

    # -- 生命周期 --------------------------------------------------------

    def connect(self) -> None:
        """握手初始化并建立会话；占用连接失败时给出明确提示。"""
        try:
            result = self._post('initialize', {
                'protocolVersion': '2025-03-26',
                'capabilities': {},
                'clientInfo': {'name': 'play-game-chrome', 'version': '1.0'},
            })
        except Exception as ex:
            raise RuntimeError(
                f'无法连接 Chrome MCP({self.endpoint})：{ex}\n'
                'mcp-chrome-bridge 同一时刻只允许一个客户端会话，'
                '请先断开其他占用该服务器的客户端。'
            ) from ex
        self._post('notifications/initialized', {})
        logger.info('已连接 Chrome MCP：%s',
                    (result or {}).get('serverInfo', {}))

    def close(self) -> None:
        """释放会话，让后续客户端可以占用。"""
        if not self.session_id:
            return
        try:
            with httpx.Client(timeout=10) as c:
                c.request('DELETE', self.endpoint,
                          headers={'Mcp-Session-Id': self.session_id})
        except Exception as ex:
            logger.debug('释放 MCP 会话失败：%s', ex)
        self.session_id = None

    # -- 工具调用 --------------------------------------------------------

    def list_tools(self) -> List[dict]:
        result = self._post('tools/list') or {}
        return result.get('tools', [])

    def call_tool(self, name: str, args: Optional[dict] = None) -> Any:
        """调用工具并返回原始 result（可能含 content 数组或扩展自有结构）。"""
        return self._post('tools/call', {
            'name': name, 'arguments': args or {},
        })

    def _tool_result_text(self, name: str, args: dict) -> str:
        res = self.call_tool(name, args)
        return _find_value(res, ('text',)) or json.dumps(
            res, ensure_ascii=False)[:20_000]

    # -- 页面相关高层操作 ------------------------------------------------

    def list_tabs(self) -> List[dict]:
        res = self.call_tool('get_windows_and_tabs') or {}
        tabs: List[dict] = []
        for win in res.get('windows', []) or []:
            for t in win.get('tabs', []) or []:
                tabs.append({
                    'tabId': t.get('id'),
                    'url': t.get('url'),
                    'title': t.get('title'),
                })
        return tabs

    def navigate(self, url: str) -> None:
        if not url:
            return
        self.call_tool('chrome_navigate', {'url': url})

    def screenshot(self) -> Tuple[bytes, Tuple[int, int]]:
        """截取当前页，返回规整后的 PNG 字节与 (宽, 高)。

        优先用 chrome_computer(截图与点击共用一套屏幕坐标，保证坐标对齐)，
        失败时退回 chrome_screenshot(storeBase64)。
        """
        b64 = None
        try:
            res = self.call_tool('chrome_computer', {'action': 'screenshot'})
            b64 = _find_value(res, (
                'base64', 'data', 'image', 'screenshot',
                'imageData', 'png', 'b64',
            ))
        except Exception as ex:
            logger.debug('chrome_computer 截图失败：%s', ex)
        if not b64:
            try:
                res = self.call_tool('chrome_screenshot', {
                    'storeBase64': True, 'savePng': False,
                })
                b64 = _find_value(res, (
                    'base64', 'data', 'image', 'screenshot',
                    'imageData', 'png', 'b64',
                ))
            except Exception as ex:
                logger.debug('chrome_screenshot 截图失败：%s', ex)
        if not b64:
            raise RuntimeError('无法从 Chrome MCP 获取页面截图')
        return _to_png_bytes(b64)


# ── 解析与执行 ───────────────────────────────


def parse_res(ans: str) -> PlayGameResp:
    """解析大模型返回的 JSON，容错修复并校验。"""
    data = json_repair.loads(ext_code_block(ans))
    if not isinstance(data, dict):
        raise ValueError(f'期望 JSON 对象，实际得到：{type(data).__name__}')
    if 'done' in data and 'finish' not in data:
        data['finish'] = data['done']
    return PlayGameResp(**data)


def summarize(actions: List[GameAction]) -> str:
    """把操作序列转为人类可读文本，用于下一帧提示。"""
    if not actions:
        return '没有操作'
    parts = []
    for a in actions:
        if a.type == 'click':
            parts.append('双击' if a.double else '点击' + f'({a.x}, {a.y})')
        elif a.type == 'key':
            parts.append(f'按键 {", ".join(a.keys or [])}')
        elif a.type == 'type':
            parts.append(f'键入 {a.text}')
        elif a.type == 'wait':
            parts.append(f'等待 {a.ms}ms')
        elif a.type == 'scroll':
            parts.append(f'向下滚动 {a.amount} 档' if a.direction == 'down'
                         else f'{a.direction} 滚动 {a.amount} 档')
        elif a.type == 'navigate':
            parts.append(f'跳转 {a.url}')
        else:
            parts.append(a.type)
    return '；'.join(parts)


def exec_action(mcp: ChromeMcpClient, act: GameAction) -> None:
    """把单个浏览器操作翻译成 Chrome MCP 工具调用并执行。"""
    if act.type == 'click':
        action = ('double_click' if act.double else
                  ('right_click' if act.button == 'right' else 'left_click'))
        mcp.call_tool('chrome_computer', {
            'action': action,
            'coordinates': {'x': int(act.x or 0), 'y': int(act.y or 0)},
        })
    elif act.type == 'key':
        mcp.call_tool('chrome_computer', {
            'action': 'key',
            'text': ' '.join(act.keys or []),
        })
    elif act.type == 'type':
        mcp.call_tool('chrome_computer', {
            'action': 'type',
            'text': act.text or '',
        })
    elif act.type == 'wait':
        mcp.call_tool('chrome_computer', {
            'action': 'wait',
            'duration': max(0, (act.ms or 0)) / 1000,
        })
    elif act.type == 'scroll':
        mcp.call_tool('chrome_computer', {
            'action': 'scroll',
            'scrollDirection': act.direction,
            'scrollAmount': max(1, min(10, act.amount or 3)),
        })
    elif act.type == 'navigate':
        mcp.call_tool('chrome_navigate', {'url': act.url or ''})
    else:
        logger.warn(f'未知操作类型：{act.type}')


# ── 主流程 ───────────────────────────────────


def play_game_chrome(args) -> None:
    # Windows 控制台多为 GBK 编码，替换而非抛错，避免常特殊字符就崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(errors='replace')
    set_openai_props(args)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)

    mcp = ChromeMcpClient(args.mcp, timeout=args.timeout)
    model = args.vmodel or args.model
    try:
        mcp.connect()

        if args.list_tools:
            for t in mcp.list_tools():
                print(t.get('name') + '\t' + (t.get('description') or ''))
            return

        if args.list_tabs:
            for t in mcp.list_tabs():
                print(f"{t['tabId']}\t{t.get('title')}\t{t.get('url')}")
            return

        if args.url:
            logger.info('跳转到游戏页面：%s', args.url)
            mcp.navigate(args.url)
            time.sleep(1.5)

        logger.info('模型：%s | 目标：%s', model, args.goal)
        if args.save_png:
            os.makedirs(args.save_png, exist_ok=True)

        # 限制单次 JSON 解析失败的重试次数，避免无效答复无限刷接口
        vg_args = copy.copy(args)
        vg_args.retry = min(args.retry, 20)

        last = '无'
        for step in range(1, args.max_steps + 1):
            png, (w, h) = mcp.screenshot()
            if args.save_png:
                open(
                    os.path.join(args.save_png, f'{step:04d}.png'), 'wb'
                ).write(png)

            prompt = render_prompt(
                PLAY_PMT,
                w=str(w), h=str(h), keys=SPECIAL_KEYS,
                goal=args.goal, last=last,
            )
            resp: PlayGameResp = call_vlm_retry(
                png, prompt, model, vg_args,
                parse_output=parse_res,
            )

            logger.info(f'[step {step}] {resp.thought}')
            for act in resp.actions:
                exec_action(mcp, act)
            last = summarize(resp.actions)
            logger.info(f'  执行：{last}')

            if resp.finish:
                logger.info('大模型判定游戏结束，退出循环')
                break
            time.sleep(args.interval)
        else:
            logger.warn(f'达到最大步数 {args.max_steps}，退出')
    finally:
        mcp.close()


# ── 子命令注册 ───────────────────────────────


def reg_subparser(subparsers):
    p = subparsers.add_parser(
        'play-game-chrome', help='通过 Chrome MCP 在网页上玩游戏'
    )
    p.add_argument(
        '--mcp', default='http://127.0.0.1:12306/mcp',
        help='Chrome MCP 服务器地址',
    )
    p.add_argument(
        '-u', '--url', default='',
        help='游戏页面 URL（可选；为空则操作当前活动标签页）',
    )
    p.add_argument(
        '-g', '--goal',
        default='这是一款网页游戏。请尽量推进游戏进度、获得更高的分数。',
        help='LLM 的游戏目标',
    )
    p.add_argument('-ms', '--max-steps', type=int, default=1000, help='最大循环步数')
    p.add_argument(
        '-i', '--interval', type=float, default=0.5,
        help='每步之间的等待秒数',
    )
    p.add_argument('-sp', '--save-png', default='', help='截图保存目录')
    p.add_argument(
        '-to', '--timeout', type=float, default=120.0,
        help='MCP 请求超时秒数',
    )
    p.add_argument(
        '-lt', '--list-tools', action='store_true',
        help='列出 Chrome MCP 暴露的工具并退出',
    )
    p.add_argument(
        '-lb', '--list-tabs', action='store_true',
        help='列出当前打开的标签页并退出',
    )
    p.add_argument('-D', '--debug', action='store_true', help='调试模式')
    p.set_defaults(func=play_game_chrome)
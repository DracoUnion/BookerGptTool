#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""play-game 子命令：使用大模型玩游戏。

循环：截取目标窗口截图 -> 发送给视觉大模型 -> 解析模型返回的 JSON 操作
-> 执行鼠标 / 键盘输入 -> 再次截图，如此反复，直到模型判定结束。

窗口通过窗口标题或进程号（PID）指定。
依赖 pywin32（Win32 API 封装）完成窗口定位、截图与输入模拟。
"""

import copy
import io
import logging
import os
import string
import sys
import time
from typing import List, Optional, Tuple

import json_repair
from pydantic import BaseModel

from .openai import (
    call_vlm_retry,
    set_openai_props,
)
from .openai import logger as oai_logger
from .util import render_prompt, ext_code_block

# pywin32 仅适用于 Windows；受保护导入，保证本模块在非 Windows 也能被正常导入。
if sys.platform == 'win32':
    import win32api
    import win32con
    import win32gui
    import win32process
else:
    win32api = win32con = win32gui = win32process = None

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# ── 大模型提示词 ───────────────────────────────


PLAY_PMT = '''
你正在玩一款游戏：{window}。当前截图是窗口客户区，分辨率为 {w}×{h}
（宽×高），坐标原点在截图左上角，x 向右、y 向下，单位为像素。

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

- 鼠标点击：`{"type": "click", "x": 像素, "y": 像素, "button": "left | right", "clicks": 1}`
- 键盘按键：`{"type": "key", "keys": ["space"]}；组合键如 {"type": "key", "keys": ["ctrl", "s"]}`
- 输入文本：`{"type": "type", "text": "要输入的字符串"}`
- 等待：`{"type": "wait", "ms": 500}`

## 可用键位

{keys}

## 规则

1.  x 必须是 0~{w}、y 必须是 0~{h} 之间的整数。
2.  actions 允许为空数组，但不能为 null。
3.  上一步你执行了：{last}。如果多次操作后画面没有变化，请换一种策略，
    或直接把 "finish" 设为 true。
4.  当游戏结束、目标达成、或你认为无法继续时，把 "finish" 设为 true。
5.  不要执行危险操作（例如 Alt+F4、Win 键等）。
'''.strip()

SPECIAL_KEYS = (
    'space, enter, shift, ctrl, alt, tab, esc, backspace, delete, insert, '
    'home, end, pageup, pagedown, up, down, left, right, capslock, '
    'f1~f12, 0~9, a~z'
)


# ── 数据模型 ─────────────────────────────────


class GameAction(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    button: str = 'left'
    clicks: int = 1
    keys: Optional[List[str]] = None
    text: Optional[str] = None
    ms: int = 300


class PlayGameResp(BaseModel):
    thought: str = ''
    actions: List[GameAction] = []
    finish: bool = False


# ── Win32 平台层（pywin32）───────────────────


def _need_win32() -> None:
    """pywin32 仅在 Windows 上可用，缺失时给出清晰提示。"""
    if win32api is None:
        raise RuntimeError(
            'play-game 需要 pywin32（仅 Windows），请先安装：pip install pywin32'
        )


# ── 窗口定位 ─────────────────────────────────


def _enum_windows() -> List[Tuple[int, int, str]]:
    """枚举所有可见顶层窗口，返回 (hwnd, pid, title)。"""
    _need_win32()
    res = []

    def cb(hwnd, lparam):
        if win32gui.IsWindowVisible(hwnd) and \
                win32gui.GetWindowTextLength(hwnd) > 0:
            pid, _tid = win32process.GetWindowThreadProcessId(hwnd)
            res.append(
                (int(hwnd), int(pid), win32gui.GetWindowText(hwnd))
            )
        return True

    win32gui.EnumWindows(cb, 0)
    return res


def find_window(target: str) -> Optional[int]:
    """按窗口标题（模糊匹配）或进程号查找窗口，返回 hwnd。"""
    wins = _enum_windows()
    if not target:
        return None
    if target.isdigit():
        pid = int(target)
        cand = [w for w in wins if w[1] == pid]
    else:
        name = target.lower()
        exact = [w for w in wins if w[2].lower() == name]
        cand = exact or [w for w in wins if name in w[2].lower()]
    if not cand:
        return None
    fg = win32gui.GetForegroundWindow()
    for w in cand:
        if w[0] == int(fg):
            return w[0]
    return cand[0][0]


def get_window_title(hwnd: int) -> str:
    _need_win32()
    return win32gui.GetWindowText(hwnd)


def restore_if_minimized(hwnd: int) -> bool:
    """窗口最小化时先还原，返回是否执行了还原。"""
    _need_win32()
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        return True
    return False


# ── 截图 ─────────────────────────────────────


def get_client_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """返回窗口客户区在屏幕上的绝对坐标 (x0, y0, x1, y1)。"""
    _need_win32()
    l, t, r, b = win32gui.GetClientRect(hwnd)
    x0, y0 = win32gui.ClientToScreen(hwnd, (l, t))
    x1, y1 = win32gui.ClientToScreen(hwnd, (r, b))
    return (x0, y0, x1, y1)


def grab_window_png(hwnd: int) -> Tuple[bytes, Tuple[int, int]]:
    """截取窗口客户区，返回 (PNG 字节, (宽, 高))。"""
    from PIL import ImageGrab
    x0, y0, x1, y1 = get_client_rect(hwnd)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        raise RuntimeError(
            f'客户区尺寸非法：{w}x{h}，窗口可能被最小化或不可见'
        )
    img = ImageGrab.grab(bbox=(x0, y0, x1, y1))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue(), (w, h)


# ── 输入模拟 ─────────────────────────────────


def _tap_unicode(char: str) -> None:
    """以 Unicode 方式敲入单个字符（keybd_event 的 bScan 仅支持单字节）。"""
    _need_win32()
    code = ord(char)
    if code > 0xFF:
        logger.warn(f'无法以 keybd_event 键入非 ASCII 字符：{char!r}')
        return
    win32api.keybd_event(0, code, win32con.KEYEVENTF_UNICODE, 0)
    win32api.keybd_event(
        0, code,
        win32con.KEYEVENTF_UNICODE | win32con.KEYEVENTF_KEYUP,
        0,
    )


VK_MAP = {
    'backspace': 0x08, 'tab': 0x09, 'enter': 0x0D, 'esc': 0x1B,
    'escape': 0x1B, 'space': 0x20, 'capslock': 0x14,
    'pageup': 0x21, 'pagedown': 0x22,
    'end': 0x23, 'home': 0x24,
    'left': 0x25, 'up': 0x26, 'right': 0x27, 'down': 0x28,
    'insert': 0x2D, 'delete': 0x2E, 'del': 0x2E,
    'shift': 0x10, 'ctrl': 0x11, 'control': 0x11, 'alt': 0x12,
    'lshift': 0xA0, 'rshift': 0xA1, 'lctrl': 0xA2, 'rctrl': 0xA3,
    'lalt': 0xA4, 'ralt': 0xA5, 'win': 0x5B, 'lwin': 0x5B, 'rwin': 0x5C,
    'numlock': 0x90, 'scrolllock': 0x91, 'printscreen': 0x2C, 'pause': 0x13,
    ';': 0xBA, '=': 0xBB, '+': 0xBB, ',': 0xBC, '-': 0xBD,
    '.': 0xBE, '/': 0xBF, '`': 0xC0, "'": 0xDE,
    '[': 0xDB, '\\': 0xDC, ']': 0xDD,
}
for _i in range(10):
    VK_MAP[str(_i)] = 0x30 + _i
for _i, _ch in enumerate(string.ascii_lowercase):
    VK_MAP[_ch] = 0x41 + _i
for _i in range(1, 13):
    VK_MAP[f'f{_i}'] = 0x6F + _i


def press_keys(keys: List[str]) -> None:
    """按下指定键位；多个键表示同时按下（组合键）。"""
    _need_win32()
    vks = []
    for k in keys:
        k = k.lower()
        if k in VK_MAP:
            vks.append(VK_MAP[k])
        elif len(k) == 1:
            # 未收录的单个字符（标点等）直接以 Unicode 输入
            _tap_unicode(k)
        else:
            raise ValueError(f'不支持的按键：{k}')
    if not vks:
        return
    for vk in vks:
        win32api.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def type_text(text: str) -> None:
    """逐字符键入字符串。"""
    for ch in text:
        _tap_unicode(ch)


def mouse_click(x: int, y: int, button: str = 'left', clicks: int = 1) -> None:
    """在屏幕绝对坐标上点击。"""
    _need_win32()
    win32api.SetCursorPos((int(x), int(y)))
    if button == 'right':
        down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
    else:
        down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
    for _ in range(max(1, int(clicks))):
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)


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
            parts.append(f'点击({a.x}, {a.y})')
        elif a.type == 'key':
            parts.append(f'按键 {", ".join(a.keys or [])}')
        elif a.type == 'type':
            parts.append(f'键入 {a.text}')
        elif a.type == 'wait':
            parts.append(f'等待 {a.ms}ms')
        else:
            parts.append(a.type)
    return '；'.join(parts)


def exec_action(act: GameAction, rect: Tuple[int, int, int, int]) -> None:
    """执行单个操作。rect 为窗口客户区屏幕坐标 (x0, y0, x1, y1)。"""
    x0, y0, x1, y1 = rect
    if act.type == 'click':
        if act.x is None or act.y is None:
            logger.warn(f'click 缺少坐标，跳过：{act}')
            return
        # 把窗口内相对坐标换算成屏幕绝对坐标，并限制在窗口内
        x = max(x0, min(x0 + act.x, x1 - 1))
        y = max(y0, min(y0 + act.y, y1 - 1))
        mouse_click(x, y, act.button, act.clicks)
    elif act.type == 'key':
        press_keys(act.keys or [])
    elif act.type == 'type':
        type_text(act.text or '')
    elif act.type == 'wait':
        time.sleep(max(0, act.ms) / 1000)
    else:
        logger.warn(f'未知操作类型：{act.type}')


# ── 主流程 ───────────────────────────────────


def play_game(args) -> None:
    if sys.platform != 'win32':
        raise SystemExit('play-game 仅支持 Windows 平台')
    # Windows 控制台多为 GBK 编码，窗口标题 / 模型输出可能含其不支持的字符，
    # 替换而非抛错，避免一遇到特殊字符就崩溃。
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(errors='replace')
    set_openai_props(args)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)

    if args.list_windows:
        for _hwnd, pid, title in _enum_windows():
            print(f'{pid}\t{title}')
        return

    if not args.target:
        logger.fatal('请提供窗口名称或进程号（或使用 --list-windows 查看）')
        return

    hwnd = find_window(args.target)
    if hwnd is None:
        logger.fatal(f'未找到窗口：{args.target}')
        print('可用窗口（PID\t标题）：')
        wins = _enum_windows()
        if not wins:
            print('（没有任何可见窗口）')
        for _hwnd, pid, title in wins[:args.list_max]:
            print(f'{pid}\t{title}')
        return

    title = get_window_title(hwnd)
    model = args.vmodel or args.model
    if restore_if_minimized(hwnd):
        time.sleep(1)

    logger.info(f'窗口：{title} | 模型：{model} | 目标：{args.goal}')

    if args.save_png:
        os.makedirs(args.save_png, exist_ok=True)

    # 限制单次 JSON 解析失败的重试次数，避免无效答复无限刷接口
    vg_args = copy.copy(args)
    vg_args.retry = min(args.retry, 20)

    last = '无'
    for step in range(1, args.max_steps + 1):
        if restore_if_minimized(hwnd):
            time.sleep(0.5)

        png, (w, h) = grab_window_png(hwnd)
        if args.save_png:
            open(
                os.path.join(args.save_png, f'{step:04d}.png'),
                'wb'
            ).write(png)

        prompt = render_prompt(
            PLAY_PMT,
            window=title, goal=args.goal,
            w=str(w), h=str(h), keys=SPECIAL_KEYS, last=last,
        )
        resp: PlayGameResp = call_vlm_retry(
            png, prompt, model, vg_args,
            parse_output=parse_res,
        )

        logger.info(f'[step {step}] {resp.thought}')
        rect = get_client_rect(hwnd)
        for act in resp.actions:
            exec_action(act, rect)
        last = summarize(resp.actions)
        logger.info(f'  执行：{last}')

        if resp.finish:
            logger.info('大模型判定游戏结束，退出循环')
            break
        time.sleep(args.interval)
    else:
        logger.warn(f'达到最大步数 {args.max_steps}，退出')


# ── 子命令注册 ───────────────────────────────


def reg_subparser(subparsers):
    p = subparsers.add_parser(
        'play-game', help='use LLM to play a game in a window'
    )
    p.add_argument(
        'target', nargs='?', default='',
        help='window title (substring) or PID',
    )
    p.add_argument(
        '-g', '--goal',
        default='这是一款游戏。请尽量推进游戏进度、获得更高的分数。',
        help='game goal for the LLM',
    )
    p.add_argument('-ms', '--max-steps', type=int, default=1000, help='max loop steps')
    p.add_argument(
        '-i', '--interval', type=float, default=0.5,
        help='seconds to wait between steps',
    )
    p.add_argument('-sp', '--save-png', default='', help='dir to save screenshots')
    p.add_argument(
        '-lw', '--list-windows', action='store_true',
        help='list visible windows (PID\\ttitle) and exit',
    )
    p.add_argument(
        '-lm', '--list-max', type=int, default=50,
        help='max windows to print when target not found',
    )
    p.add_argument('-D', '--debug', action='store_true', help='debug mode')
    p.set_defaults(func=play_game)

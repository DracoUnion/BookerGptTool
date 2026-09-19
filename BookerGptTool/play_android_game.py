#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""play-android-game 子命令：使用大模型 + adb 操作安卓模拟器/设备玩游戏。

循环：adb 截屏 -> 发送给视觉大模型 -> 解析模型返回的 JSON 操作
-> adb 执行触摸 / 按键 / 滑动 -> 再次截屏，如此反复，直到模型判定结束。

设备通过 adb 串口（serial）或序号指定，也可自动选择唯一已连接设备。
依赖 adb（Android Debug Bridge）客户端，需提前安装并加入 PATH。
"""

import copy
import io
import logging
import os
import shutil
import subprocess
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

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def _need_adb() -> str:
    """返回 adb 可执行文件路径；缺失时给出清晰提示。"""
    adb = os.environ.get('ADB', 'adb')
    p = shutil.which(adb)
    if p is None:
        raise RuntimeError(
            'play-android-game 需要 adb（Android Debug Bridge）。'
            '请安装后加入 PATH，或用环境变量 ADB 指定绝对路径。'
        )
    return p


# ── adb 封装 ─────────────────────────────────


class Adb:
    """adb 命令的极简封装。"""

    def __init__(self, serial: Optional[str] = None, adb: str = 'adb'):
        self.adb = adb
        self.serial = serial

    def _args(self, *args: str) -> List[str]:
        if self.serial:
            return [self.adb, '-s', self.serial, *list(args)]
        return [self.adb, *list(args)]

    def wm_size(self):
        wm = self.run('shell', 'wm size').strip()
        res = wm.split(':')[-1].strip()
        fw, fh = res.lower().split('x')
        w, h = int(fw), int(fh)
        return w, h

    def tap(self, x: int, y: int):
        return self.run('shell', f'input tap {x} {y}')

    def swipe(self, x: int, y: int, x2: int, y2: int, ms: int):
        return self.run('shell', f'input swipe {x} {y} {x2} {y2} {ms}')

    def key(self, key: str):
        return self.run('shell', f'input keyevent {key}')

    def text(self, text: str):
        safe = (text or '').replace(' ', '%s')
        return self.run('shell', f'input text {safe}')

    def run(self, *args: str, timeout: int = 60) -> str:
        """运行一条 adb 命令，返回 stdout 文本；失败时抛出 RuntimeError。"""
        cmd = self._args(*args)
        logger.debug('$ %s', ' '.join(cmd))
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"adb {' '.join(args)} 退出码 {proc.returncode}: "
                f'{proc.stderr.strip()}'
            )
        return proc.stdout


def list_devices(adb: str = 'adb') -> List[Tuple[str, str]]:
    """枚举已连接设备，返回 [(serial, state)]，如 [('emulator-5554', 'device')]。"""
    out = subprocess.run(
        [adb, 'devices'], capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=30,
    ).stdout
    res = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            res.append((parts[0], parts[1]))
    return res


# ── 数据模型 ─────────────────────────────────


class GameAction(BaseModel):
    type: str
    x: Optional[int] = None
    y: Optional[int] = None
    text: Optional[str] = None
    keys: Optional[List[str]] = None
    ms: int = 300


class PlayGameResp(BaseModel):
    thought: str = ''
    actions: List[GameAction] = []
    finish: bool = False


# ── 操作执行（全部走 adb）───────────────────


def exec_action(adb: Adb, act: GameAction, w: int, h: int) -> None:
    """执行单个操作。w、h 为屏幕（设备坐标）逻辑宽高。"""
    if act.type == 'tap':
        if act.x is None or act.y is None:
            logger.warn(f'tap 缺少坐标，跳过：{act}')
            return
        x = max(0, min(int(act.x), w - 1))
        y = max(0, min(int(act.y), h - 1))
        adb.tap(x, y)
    elif act.type == 'swipe':
        if act.x is None or act.y is None:
            logger.warn(f'swipe 缺少坐标，跳过：{act}')
            return
        # 支持 {text: "dx:dy"} 表示滑动向量
        if act.text and ':' in act.text:
            dx, dy = act.text.split(':', 1)
            x2 = max(0, min(int(act.x) + int(dx), w - 1))
            y2 = max(0, min(int(act.y) + int(dy), h - 1))
        else:
            x2, y2 = act.x, act.y
        adb.swipe(act.x, act.y, x2, y2, act.ms)
    elif act.type == 'key':
        for key in (act.keys or []):
            adb.key(key)
    elif act.type == 'text':
        # %s 是 adb 的空格转义，禁止其它危险字符注入
        adb.text(act.text)
    elif act.type == 'wait':
        time.sleep(max(0, act.ms) / 1000)
    else:
        logger.warn(f'未知操作类型：{act.type}')


# ── adb 截屏 ─────────────────────────────────


def grab_screen_png(adb: Adb) -> Tuple[bytes, Optional[Tuple[int, int]]]:
    """截取设备屏幕，返回 (PNG 字节, (宽, 高))。

    使用 `exec-out screencap -p` 直接输出到管道。
    """
    proc = subprocess.run(
        adb._args('exec-out', 'screencap', '-p'),
        capture_output=True, timeout=120,
    )
    png = proc.stdout
    if not png:
        raise RuntimeError('screencap 返回空数据')
    dim = None
    try:
        from PIL import Image
        dim = Image.open(io.BytesIO(png)).size
    except Exception:
        pass
    return png, dim


# ── 解析 ─────────────────────────────────────


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
        if a.type == 'tap':
            parts.append(f'点击({a.x}, {a.y})')
        elif a.type == 'swipe':
            parts.append(f'滑动({a.x}, {a.y})')
        elif a.type == 'key':
            parts.append(f'按键 {", ".join(a.keys or [])}')
        elif a.type == 'text':
            parts.append(f'输入 {a.text}')
        elif a.type == 'wait':
            parts.append(f'等待 {a.ms}ms')
        else:
            parts.append(a.type)
    return '；'.join(parts)


# ── 提示词 ───────────────────────────────────


def _play_pmt(w: int, h: int) -> str:
    return f'''你正在玩一款安卓游戏：{{device}}。当前截图是设备屏幕，逻辑分辨率为
{w}×{h}（宽×高），坐标原点在截图左上角，x 向右、y 向下，单位为像素。

## 目标

{{goal}}

## 任务

观察截图，推理游戏当前状态，然后决定下一步操作。你只能看到当前这一帧画面，
请依据画面内容做出尽量合理的决策。

## 返回格式

只返回一个 JSON 对象，包含在三个反引号（```）中：

```
{{"thought": "简短说明你观察到了什么、打算做什么", "actions": [{{"type": "tap", "x": 500, "y": 800}}], "finish": false}}
```

## 操作类型

- 点击：`{{"type": "tap", "x": 像素, "y": 像素}}`
- 滑动：`{{"type": "swipe", "x": 起点x, "y": 起点y, "text": "dx:dy", "ms": 毫秒}}`
- 按键：`{{"type": "key", "keys": ["back"]}}（支持 back/home/menu/volumeup/dpadup 等）`
- 输入文本：`{{"type": "text", "text": "要输入的字符串"}}`（仅 ASCII 安全字符）
- 等待：`{{"type": "wait", "ms": 500}}`

## 规则

1.  x 必须是 0~{w}、y 必须是 0~{h} 之间的整数。
2.  actions 允许为空数组，但不能为 null。
3.  上一步你执行了：{{last}}。如果多次操作后画面没有变化，请换一种策略，
    或直接把 "finish" 设为 true。
4.  当游戏结束、目标达成、或你认为无法继续时，把 "finish" 设为 true。
5.  不要执行危险操作（例如长按电源键强制关机）。
6.  如需输入中文，可转换为拼音（仅 ASCII）或拼音首字母。
'''.strip()


# ── 主流程 ───────────────────────────────────


def play_android_game(args) -> None:
    set_openai_props(args)
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)

    adb = _need_adb()

    if args.list_devices:
        for serial, state in list_devices(adb):
            print(f'{serial}\t{state}')
        return

    # 设备选择
    if args.target:
        serial = args.target
    else:
        devices = list_devices(adb)
        ready = [s for s, st in devices if st == 'device']
        if len(ready) == 1:
            serial = ready[0]
        elif not ready:
            logger.fatal('未发现已连接设备。请连接设备/模拟器，或用 --list-devices 查看。')
            return
        else:
            logger.fatal('检测到多个设备，请用 target 参数指定串口号：%s',
                         ', '.join(s for s, _ in devices))
            return

    adbc = Adb(serial, adb=adb)
    device_desc = serial
    model = args.vmodel or args.model

    logger.info(f'设备：{device_desc} | 模型：{model} | 目标：{args.goal}')

    if args.save_png:
        os.makedirs(args.save_png, exist_ok=True)

    # 获取逻辑分辨率（像素），失败时兜底
    w, h = 1080, 1920
    try:
        w, h = adbc.wm_size()
    except Exception as e:
        logger.warn(f'获取分辨率失败：{e}，使用 1080×1920 兜底')

    # 限制单次 JSON 解析失败的重试次数，避免无效答复无限刷接口
    vg_args = copy.copy(args)
    vg_args.retry = min(args.retry, 20)

    prompt = _play_pmt(w, h)
    last = '无'
    for step in range(1, args.max_steps + 1):
        png, _dim = grab_screen_png(adbc)
        if args.save_png:
            open(os.path.join(args.save_png, f'{step:04d}.png'), 'wb').write(png)

        prompt_now = render_prompt(
            prompt, device=device_desc, goal=args.goal, last=last,
        )
        resp: PlayGameResp = call_vlm_retry(
            png, prompt_now, model, vg_args,
            parse_output=parse_res,
        )

        logger.info(f'[step {step}] {resp.thought}')
        for act in resp.actions:
            exec_action(adbc, act, w, h)
            time.sleep(args.interval)
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
        'play-android-game', help='用 LLM + adb 在安卓设备上玩游戏'
    )
    p.add_argument(
        'target', nargs='?', default='',
        help='adb 序列号（如 emulator-5554）或设备索引',
    )
    p.add_argument(
        '-g', '--goal',
        default='这是一款安卓游戏。请尽量推进游戏进度、获得更高的分数。',
        help='LLM 的游戏目标',
    )
    p.add_argument('-ms', '--max-steps', type=int, default=1000, help='最大循环步数')
    p.add_argument(
        '-i', '--interval', type=float, default=0.2,
        help='操作之间的等待秒数',
    )
    p.add_argument('-sp', '--save-png', default='', help='截图保存目录')
    p.add_argument(
        '-ld', '--list-devices', action='store_true',
        help='列出已连接的 adb 设备并退出',
    )
    p.add_argument('-D', '--debug', action='store_true', help='调试模式')
    p.set_defaults(func=play_android_game)
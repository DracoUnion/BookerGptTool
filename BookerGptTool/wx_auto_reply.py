# -*- coding: utf-8 -*-
"""wx-auto-reply 子命令：后台自动回复 PC 微信白名单好友的最新消息。

定位与发送复用 WeChat-AI-AutoReply 项目 ``wechat_ai_bot_dev.py`` 的核心能力
（只搬运行引擎，不搬 GUI）：

1. Win32 窗口定位（Qt51514QWindowIcon / WeChatMainWndForPC）与 PrintWindow 离屏抓图；
2. RapidOCR 识别顶栏标题与聊天气泡，按绿底/白底判流向，白名单精确匹配 + 红点几何锚定；
3. 剪贴板原子写入 + keybd_event 注入 Ctrl+V/Enter 粘贴发送，差分绿底增量闭环验收，
   发送前强制校验前台窗口归属与顶栏目标（防串窗串发）。

AI 回复不再用 ``requests`` 直连，而是复用 ``.openai.OpenAIClient``，
与 wx-summary 等子命令共用 ``-k/-H/-m/-tm`` 等参数和环境变量。

可选依赖（惰性导入，未安装不影响 BookerGptTool 其他子命令）：:

    pip install rapidocr_onnxruntime pyperclip

用法示例：:

    python -m BookerGptTool wx-auto-reply --config wechat_config.json
    python -m BookerGptTool wx-auto-reply --once --check-interval 1
"""

import os
import re
import time
import json
import hashlib
import ctypes
import logging
from ctypes import wintypes
from os import path

import numpy as np
from PIL import Image, ImageGrab

from .util import read_text, render_prompt
from .openai import OpenAIClient, logger as oai_logger

logger = logging.getLogger(__name__)

# =============================================================================
# 1. 配置命名与默认值
# =============================================================================

DEFAULT_CONFIG = {
    "whitelist_mode": True,
    "whitelist": [],
    "blacklist": ["文件传输助手", "微信团队", "订阅号", "公众号", "服务号",
                  "腾讯新闻", "微信支付", "服务通知"],
    "system_prompt": "你现在是微信账号的主人（真实本人）。请以我（主人本人）的第一人称、"
                     "自然口语、亲切随和的日常语气直接回复微信好友的消息。就像我本人在用"
                     "手机打字聊天一样自然。严禁透露任何AI、机器人或助理身份，严禁使用客套"
                     "机械的客服腔，严禁输出任何解释或markdown格式，直接输出我回复的那句话。",
    "reply_prefix": "",
    "include_prefix": False,
    "reply_delay_seconds": 0.0,
    "check_interval_seconds": 0.8,
    "ignored_keywords": ["按住鼠标", "语音输入文字", "按住说话", "按Enter发送", "发送(S)"],
    "friend_personas": {},
}


def cfg_float(dct, key, default):
    """健壮读取数字配置项：非法/缺失时回退默认值。"""
    try:
        return float(dct.get(key, default))
    except Exception:
        return float(default)


def load_config(fname=None):
    """读取回复配置 JSON 文件，缺失/损坏时回退默认配置。"""
    cfg = dict(DEFAULT_CONFIG)
    if fname and path.isfile(fname):
        try:
            text = read_text(fname) or '{}'
            data = json.loads(text)
            if isinstance(data, dict):
                cfg.update(data)
        except Exception as exc:
            logger.warning('读取配置文件 %s 失败: %s', fname, exc)
    return cfg


# =============================================================================
# 2. Win32 句柄与基础能力（惰性 DPI）
# =============================================================================

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


def set_dpi_aware():
    """进程级 DPI 感知（必须在任何 GDI/窗口操作前调用一次）。"""
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def ensure_default_desktop():
    """绑定到 Default 桌面，保证后台能访问微信窗口。"""
    try:
        h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)
            user32.CloseDesktop(h_desk)
    except Exception:
        pass


def get_dpi_scale():
    try:
        hdc = user32.GetDC(0)
        log_w = gdi32.GetDeviceCaps(hdc, 8)      # HORZRES
        phys_w = gdi32.GetDeviceCaps(hdc, 118)   # DESKTOPHORZRES
        user32.ReleaseDC(0, hdc)
        return (phys_w / float(log_w)) if log_w else 1.0
    except Exception:
        return 1.0


def compute_wechat_layout(W, H):
    """基于微信 PC 客户端原生物理布局结构计算关键区域（Window-Size Invariant）。"""
    return {
        "sidebar_w": max(55, int(0.055 * W)),
        "chat_start_x": max(320, int(0.285 * W)),
        "header_h": max(85, int(0.110 * H)),
        "input_h": min(220, max(140, int(0.160 * H))),
        "total_w_px": W,
        "total_h_px": H,
    }


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)


def get_wechat_hwnd():
    """定位微信主聊天窗口。

    返回 (hwnd, 版本号)：(3=微信3.x, 4=微信4.x)，未找到返回 (None, None)。
    """
    ensure_default_desktop()
    candidates = []

    def enum_proc(hwnd, lp):
        class_buff = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buff, 256)
        c = class_buff.value
        if "Qt51514QWindowIcon" in c or "WeChatMainWndForPC" in c:
            wname = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, wname, 256)
            t = wname.value
            is_shell = (t == "Weixin")  # 微信 4.0 空壳代理窗口标题固定为 "Weixin"
            ver = 4 if "Qt" in c else 3
            candidates.append((hwnd, ver, t, is_shell))
            if is_shell:
                try:
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE 隐藏空壳代理窗口
                except Exception:
                    pass
        return True

    # 其他 BookerGptTool 模块可能给同一 user32.EnumWindows 设置了
    # 不同的 WINFUNCTYPE；这里显式恢复本模块的回调签名，避免 ctypes
    # 报“expected WinFunctionType instance instead of WinFunctionType”。
    user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]
    user32.EnumWindows.restype = wintypes.BOOL
    cb = WNDENUMPROC(enum_proc)
    user32.EnumWindows(cb, 0)
    real_candidates = [c for c in candidates if not c[3]]
    if real_candidates:
        return real_candidates[0][0], real_candidates[0][1]
    if candidates:
        return candidates[0][0], candidates[0][1]
    return None, None


def grab_wechat_window(hwnd):
    """PrintWindow / DWM 离屏静默抓取微信窗口画面，返回 (img, rect, scale)。"""
    try:
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 0 or h <= 0:
            w, h = 1100, 750
        scale = get_dpi_scale()

        hwnd_dc = None
        mfc_dc = None
        save_bitmap = None
        img = None
        try:
            hwnd_dc = user32.GetWindowDC(hwnd)
            mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
            save_bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
            gdi32.SelectObject(mfc_dc, save_bitmap)

            result = user32.PrintWindow(hwnd, mfc_dc, 2)
            if not result:
                result = user32.PrintWindow(hwnd, mfc_dc, 0)

            if result:
                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ('biSize', wintypes.DWORD),
                        ('biWidth', wintypes.LONG),
                        ('biHeight', wintypes.LONG),
                        ('biPlanes', wintypes.WORD),
                        ('biBitCount', wintypes.WORD),
                        ('biCompression', wintypes.DWORD),
                        ('biSizeImage', wintypes.DWORD),
                        ('biXPelsPerMeter', wintypes.LONG),
                        ('biYPelsPerMeter', wintypes.LONG),
                        ('biClrUsed', wintypes.DWORD),
                        ('biClrImportant', wintypes.DWORD),
                    ]
                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = w
                bmi.biHeight = -h  # top-down
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0

                buf = ctypes.create_string_buffer(w * h * 4)
                gdi32.GetDIBits(mfc_dc, save_bitmap, 0, h, buf,
                                ctypes.byref(bmi), 0)
                img = Image.frombuffer(
                    'RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
        finally:
            if save_bitmap:
                gdi32.DeleteObject(save_bitmap)
            if mfc_dc:
                gdi32.DeleteDC(mfc_dc)
            if hwnd_dc:
                user32.ReleaseDC(hwnd, hwnd_dc)

        if img is not None and np.array(img).max() > 10:
            return img, rect, scale
        # 备用：物理截屏（仅当在正常屏幕内时）
        if rect.left > -1000 and rect.top > -1000:
            bbox = (int(rect.left * scale), int(rect.top * scale),
                    int(rect.right * scale), int(rect.bottom * scale))
            fallback_img = ImageGrab.grab(bbox=bbox).convert('RGB')
            return fallback_img, rect, scale
        return img, rect, scale
    except Exception:
        return None, None, 1.0


def activate_wechat(hwnd):
    """Win32 穿透提权：突破 Windows 防抢焦点 (ASFW)，赋予微信合法键盘输入。"""
    try:
        ensure_default_desktop()
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.04)

        cur_fore = user32.GetForegroundWindow()
        cur_thread = user32.GetWindowThreadProcessId(cur_fore, None) if cur_fore else 0
        wx_thread = user32.GetWindowThreadProcessId(hwnd, None)
        my_thread = kernel32.GetCurrentThreadId()

        attached_cur = attached_my = False
        try:
            if cur_thread and cur_thread != wx_thread:
                attached_cur = bool(
                    user32.AttachThreadInput(cur_thread, wx_thread, True))
            if my_thread and my_thread != wx_thread:
                attached_my = bool(
                    user32.AttachThreadInput(my_thread, wx_thread, True))
            # SWP_NOSIZE | SWP_NOMOVE = 0x0003，末尾 0x0040 不激活窗口
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0003 | 0x0040)
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SwitchToThisWindow(hwnd, True)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0003)
        finally:
            if attached_cur:
                user32.AttachThreadInput(cur_thread, wx_thread, False)
            if attached_my:
                user32.AttachThreadInput(my_thread, wx_thread, False)

        time.sleep(0.03)
        return user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def post_click_client_point(hwnd, client_x, client_y):
    """通过 Win32 消息向微信窗口直接发送局部点击（物理鼠标 0 移动）。"""
    try:
        lParam = (int(client_y) << 16) | (int(client_x) & 0xFFFF)
        user32.PostMessageW(hwnd, 0x0200, 0x0000, lParam)  # WM_MOUSEMOVE
        time.sleep(0.01)
        user32.PostMessageW(hwnd, 0x0201, 0x0001, lParam)  # WM_LBUTTONDOWN
        time.sleep(0.02)
        user32.PostMessageW(hwnd, 0x0202, 0x0000, lParam)  # WM_LBUTTONUP
        time.sleep(0.03)
    except Exception:
        pass


# =============================================================================
# 3. OCR 引擎（惰性初始化）
# =============================================================================
_OCR = None


def get_ocr():
    """初始化并缓存 RapidOCR 引擎。未安装 rapidocr_onnxruntime 时抛中文提示。"""
    global _OCR
    if _OCR is not None:
        return _OCR
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise RuntimeError(
            '未安装 rapidocr_onnxruntime，无法进行 OCR 识别。'
            '请执行 pip install rapidocr_onnxruntime')
    # 与 wechat_ai_bot_dev.py 相同：优先加载本地 ch_PP-OCRv4 模型目录
    models_dir = path.join(path.dirname(path.abspath(__file__)), 'models')
    det_model = path.join(models_dir, "ch_PP-OCRv4_det_infer.onnx")
    cls_model = path.join(models_dir, "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    rec_model = path.join(models_dir, "ch_PP-OCRv4_rec_infer.onnx")
    if path.isfile(det_model) and path.isfile(rec_model):
        try:
            _OCR = RapidOCR(det_model_path=det_model, cls_model_path=cls_model,
                            rec_model_path=rec_model)
            return _OCR
        except Exception:
            pass
    _OCR = RapidOCR()
    return _OCR


def ocr_full_window(img, ocr_engine):
    """对整窗等比下采样 OCR 并映射回原始坐标，返回 res（[box, txt, score]）。"""
    W, H = img.size
    target_w = 1000
    ratio = 1000.0 / W if W > 1100 else 1.0
    if ratio < 1.0:
        img_for_ocr = img.resize(
            (target_w, int(H * ratio)), Image.Resampling.BILINEAR)
        res_raw, _ = ocr_engine(img_for_ocr)
        return [
            ([[pt[0] / ratio, pt[1] / ratio] for pt in b], txt, sc)
            for b, txt, sc in (res_raw or [])
        ]
    res_raw, _ = ocr_engine(img)
    return res_raw or []


# =============================================================================
# 4. 名称匹配与红点判定（白名单门禁 / 群聊防火墙）
# =============================================================================

def normalize_for_match(text):
    if not text:
        return ""
    t = str(text).lower().strip()
    t = re.sub(r'\s+', '', t)
    t = t.replace('0', 'o').replace('1', 'l').replace('i', 'l')
    return re.sub(r'[\^_\~\-\.\,\'\"\`\*\#\@\!\:\;\|\/\\]', '', t)


def is_group_chat_name(text):
    """群聊防火墙：群关键词或名字末尾括号人数 (25) / ( 25)。"""
    if not text:
        return False
    t = str(text).strip()
    if re.search(r'群聊|交流群|互助群|通知群|工作群|家族群|同乡群|部门群', t):
        return True
    name_seg = re.split(r'\s+', t, maxsplit=1)[0]
    if re.search(r'[\(（]\d+[\)）]$', name_seg):
        return True
    parts = re.split(r'\s+', t)
    if len(parts) >= 2 and re.match(r'^[\(（]\d+[\)）]$', parts[1]):
        return True
    return False


def is_name_matched_strict(contact_name, whitelist):
    """白名单精确匹配：全等 / 前缀真名匹配 / 截断容错。

    返回 (是否命中, 命中的白名单原文)。
    """
    if not contact_name or len(str(contact_name).strip()) < 2:
        return False, ""
    c_raw = str(contact_name).strip()
    raw_variants = [c_raw]
    stripped = re.sub(r'^\d{1,2}(?=[^\d])', '', c_raw).strip()  # 剥离行首红点数
    if stripped and len(stripped) >= 2 and stripped != c_raw:
        raw_variants.append(stripped)

    for r in raw_variants:
        if is_group_chat_name(r):
            return False, ""
    for r in raw_variants:
        norm_ocr = normalize_for_match(r)
        if not norm_ocr:
            continue
        for t in whitelist:
            t_clean = t.strip()
            if not t_clean:
                continue
            norm_w = normalize_for_match(t_clean)
            if not norm_w:
                continue
            if norm_ocr == norm_w:
                return True, t_clean
            if norm_ocr.startswith(norm_w):
                # 越配防护：名字后紧跟普通字符（可能是更长名字）一律拒绝
                m = re.match(re.escape(t_clean), r, re.IGNORECASE)
                if m:
                    tail = r[m.end():]
                    if tail and re.match(r'[0-9A-Za-z一-鿿]', tail[0]):
                        continue
                return True, t_clean
            if norm_w.startswith(norm_ocr) and len(norm_w) <= len(norm_ocr) + 1:
                return True, t_clean
    return False, ""


def has_red_badge_by_text_anchor(arr, text_box, layout):
    """以联系人文字 Box 为基准锚定头像右上角红点/数字红圈区域判定。"""
    total_w = layout["total_w_px"]
    total_h = layout["total_h_px"]
    txt_left_x = min(pt[0] for pt in text_box)
    txt_top_y = min(pt[1] for pt in text_box)
    x1 = max(0, int(txt_left_x - 36))
    x2 = max(0, int(txt_left_x - 3))
    y1 = max(0, int(txt_top_y - 20))
    y2 = min(total_h, int(txt_top_y + 20))
    patch = arr[y1:y2, x1:x2]
    if patch.size == 0:
        return False
    pr, pg, pb = (patch[:, :, i].astype(int) for i in range(3))
    red_mask = (pr >= 200) & (pg <= 110) & (pb <= 110) & (pr > pg + 65)
    return int(np.sum(red_mask)) >= 15


def extract_active_chat_title(res, layout):
    """顶栏好友昵称提取：严格锁定在 X>=chat_start_x、Y<=header_h 的区域。"""
    chat_start_x = layout["chat_start_x"]
    header_h = layout["header_h"]
    total_w = layout["total_w_px"]
    exclude = {"最小化", "最大化", "关闭", "设置", "聊天信息", "搜索", "表情",
               "发送", "文件", "截图", "语音聊天", "视频聊天", "…", "...", "口",
               "-", "x", "X"}
    title_candidates = []
    for b, txt, sc in (res or []):
        cx = (b[0][0] + b[1][0]) / 2.0
        cy = (b[0][1] + b[2][1]) / 2.0
        t_clean = txt.strip()
        if chat_start_x - 30 <= cx <= chat_start_x + int(0.42 * (total_w - chat_start_x)) and cy <= header_h:
            if t_clean in exclude:
                continue
            title_candidates.append((cx, t_clean))
    title_candidates.sort(key=lambda x: x[0])
    return " ".join(t for _, t in title_candidates)


# =============================================================================
# 5. 聊天气泡解析（真彩流向聚合）
# =============================================================================

BUILTIN_IGNORED_KEYWORDS = ["按住鼠标", "语音输入文字", "按住说话",
                            "按Enter发送", "按Ctrl+Enter发送", "发送(S)"]


def parse_chat_bubbles_chromatic(res, img_rgb, layout, ignored_keywords=None):
    """解析聊天气泡：绿底=我方发出，非绿底偏左=好友发来，垂直<40px合并多行。"""
    arr = np.array(img_rgb)
    W, H = img_rgb.size
    chat_start_x = layout["chat_start_x"]
    header_h = layout["header_h"]
    input_h = layout["input_h"]

    all_ignored = list(BUILTIN_IGNORED_KEYWORDS)
    for kw in (ignored_keywords or []):
        if kw and str(kw).strip() and str(kw).strip() not in all_ignored:
            all_ignored.append(str(kw).strip())

    raw_lines = []
    for b, txt, sc in (res or []):
        cx = (b[0][0] + b[1][0]) / 2.0
        cy = (b[0][1] + b[2][1]) / 2.0
        c_clean = txt.strip()
        if not c_clean:
            continue
        if any(bad in c_clean for bad in all_ignored):
            continue
        if any(bad in c_clean for bad in ["P以H白", "y< o O", "查看更多", "以下为新消息", "置顶聊天"]):
            continue
        if cx > (chat_start_x + 15) and (header_h + 15) < cy < (H - int(input_h * 0.70)):
            if (re.match(r'^\d{1,2}:\d{2}$', c_clean)
                    or re.match(r'^\d{1,2}/\d{2}$', c_clean)
                    or re.match(r'^\d{4}-\d{2}-\d{2}', c_clean)):
                continue
            if c_clean in ["昨天", "前天", "星期一", "星期二", "星期三", "星期四",
                           "星期五", "星期六", "星期日", "查看更多消息", "重新编辑", "发送"]:
                continue
            if any(c_clean.startswith(p) for p in ["[转账]", "微信转账", "已收款",
                                                   "已被接收", "￥", "你撤回了一条消息"]):
                continue
            if "拍了拍" in c_clean:
                continue

            min_x = max(0, int(min(pt[0] for pt in b) - 15))
            max_x = min(W, int(max(pt[0] for pt in b) + 15))
            min_y = max(0, int(min(pt[1] for pt in b) - 8))
            max_y = min(H, int(max(pt[1] for pt in b) + 8))
            patch = arr[min_y:max_y, min_x:max_x]
            pr, pg, pb = (patch[:, :, i].astype(int) for i in range(3))
            green_mask = (pg > pr + 18) & (pg > pb + 18) & (pg > 45)
            has_green_bg = int(np.sum(green_mask)) > 15

            box_left = min(pt[0] for pt in b)
            is_incoming = (not has_green_bg) and \
                (box_left < (chat_start_x + int(0.55 * (W - chat_start_x))))
            raw_lines.append((cy, box_left, box_left, is_incoming, c_clean))

    raw_lines.sort(key=lambda x: x[0])
    merged_bubbles = []
    for cy, b_left, b_right, is_inc, text in raw_lines:
        if (merged_bubbles and is_inc == merged_bubbles[-1]['is_incoming']
                and abs(cy - merged_bubbles[-1]['last_y']) < 40):
            merged_bubbles[-1]['text'] += "，" + text
            merged_bubbles[-1]['last_y'] = cy
        else:
            merged_bubbles.append({
                'is_incoming': is_inc,
                'first_y': cy,
                'last_y': cy,
                'text': text})
    return merged_bubbles


def extract_pending_incoming_messages(chat_bubbles):
    """取最后一条我方绿底气泡之后的所有好友气泡聚合为待回复文本。"""
    if not chat_bubbles:
        return [], ""
    last_outgoing_idx = -1
    for i in range(len(chat_bubbles) - 1, -1, -1):
        if not chat_bubbles[i]['is_incoming']:
            last_outgoing_idx = i
            break
    if last_outgoing_idx == -1:
        pending_bubbles = [b for b in chat_bubbles if b['is_incoming']]
    else:
        pending_bubbles = [b for b in chat_bubbles[last_outgoing_idx + 1:] if b['is_incoming']]
    texts = [b['text'].strip() for b in pending_bubbles if b['text'].strip()]
    return pending_bubbles, "，".join(texts)


# =============================================================================
# 6. AI 回复生成（复用 OpenAIClient）
# =============================================================================

WX_AR_SYSTEM_PMT = '''你是微信账号的主人本人（真实真人），正在微信上以第一人称与好友【{sender}】聊天，当前时间是{now_time}。

{supplement}

【该好友专属人设】：{persona}
【对话规则】：直接输出发给对方的一句中文，口语化自然、简练得体、有话直说。
严禁括号描写心理活动，严禁透露任何AI、机器人或助理身份，严禁客套机械的客服腔，
严禁输出解释或markdown格式。直接输出要发送的那句话。'''


def clean_ai_reply(reply):
    if not reply:
        return ""
    reply = re.sub(r' thinking.*? response', '', reply, flags=re.DOTALL)
    reply = re.sub(r'^[（(][^）)]*[）)]\s*', '', reply)
    reply = re.sub(r'\s*[（(][^）)]*[）)]$', '', reply)
    reply = re.sub(r'[*_#`]', '', reply).strip()
    if reply.startswith(('"', '“')) and reply.endswith(('"', '”')):
        reply = reply[1:-1].strip()
    return reply


def generate_ai_reply(llm, sender, message_content, cfg, args, state):
    """调用 OpenAIClient 生成回复，天然支持 --retry / --temp 等参数。"""
    now_time = time.strftime("%H:%M")
    custom_prompt = (cfg.get("system_prompt") or "").strip()
    persona_desc = ((cfg.get("friend_personas") or {}).get(sender) or "").strip()
    supplement = custom_prompt or "说话自然随和、像真实好友一样口语化。"
    persona = persona_desc or "幽默风趣、随和自然的真实好友"
    system_prompt = render_prompt(
        WX_AR_SYSTEM_PMT, sender=sender, now_time=now_time,
        supplement=supplement, persona=persona)

    history = state['history'].get(sender, [])
    msgs = [{"role": "system", "content": system_prompt}]
    msgs.extend(history[-8:])
    msgs.append({"role": "user", "content": message_content})

    logger.info('正在调用大模型 (%s) 回复【%s】...', args.model, sender)
    try:
        reply = llm.ask_chatgpt_retry(msgs, args.model, args)
        cleaned = clean_ai_reply(reply)
        if not cleaned:
            logger.warning('模型回复经清洗后为空，跳过【%s】', sender)
            return ""
        history.append({"role": "user", "content": message_content})
        history.append({"role": "assistant", "content": cleaned})
        state['history'][sender] = history[-10:]
        return cleaned
    except Exception as exc:
        logger.error('大模型调用失败: %s', exc)
        return ""


# =============================================================================
# 7. 剪贴板粘贴发送（差分绿底闭环验收）
# =============================================================================

def safe_clipboard_copy(text, retries=3, delay=0.03):
    """带冲突退避重试的剪贴板写入。"""
    import pyperclip
    for i in range(retries):
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            if i < retries - 1:
                time.sleep(delay)
    return False


def _clipboard_paste():
    import pyperclip
    return pyperclip.paste()


def count_green_bubble_metrics(img, layout):
    """计算聊天区底部两个特征带绿底像素数（发送闭环验收用）。"""
    if not img:
        return 0, 0
    try:
        W, H = img.size
        arr_v = np.array(img)
        chat_start_x = layout["chat_start_x"]
        input_h = layout["input_h"]
        x1_tot = int(chat_start_x + 0.35 * (W - chat_start_x))
        y1_tot = max(0, int(H - input_h - 220))
        patch_tot = arr_v[y1_tot:min(H, int(H - input_h + 10)), x1_tot:min(W, int(W - 10))]
        tot_green = 0
        if patch_tot.size > 0:
            pr, pg, pb = (patch_tot[:, :, i].astype(int) for i in range(3))
            tot_green = int(np.sum((pg > pr + 18) & (pg > pb + 18) & (pg > 45)))
        x1_bot = int(chat_start_x + 0.35 * (W - chat_start_x))
        y1_bot = max(0, int(H - input_h - 80))
        patch_bot = arr_v[y1_bot:int(H - input_h), x1_bot:min(W, int(W - 20))]
        bot_green = 0
        if patch_bot.size > 0:
            pr_b, pg_b, pb_b = (patch_bot[:, :, i].astype(int) for i in range(3))
            bot_green = int(np.sum((pg_b > pr_b + 18) & (pg_b > pb_b + 18) & (pg_b > 45)))
        return tot_green, bot_green
    except Exception:
        return 0, 0


def verify_diff_bubble_success(img_pre, img_post, layout):
    """差分绿底增量闭环验收：True=明确成功 / False=明确失败 / UNCERTAIN=无法判定。"""
    if img_pre is None or img_post is None:
        return "UNCERTAIN"
    try:
        if np.array(img_pre).max() <= 10 or np.array(img_post).max() <= 10:
            return "UNCERTAIN"
    except Exception:
        return "UNCERTAIN"
    pre_tot, pre_bot = count_green_bubble_metrics(img_pre, layout)
    post_tot, post_bot = count_green_bubble_metrics(img_post, layout)
    if (post_bot >= 40 and (post_bot - pre_bot) >= 30) or (post_tot - pre_tot) >= 50:
        return True
    return False


def _send_keys_ctrl_v_enter():
    """keybd_event 注入 Ctrl+V 粘贴与 Enter 发送。"""
    user32.keybd_event(0x11, 0, 0, 0)   # Ctrl down
    user32.keybd_event(0x56, 0, 0, 0)   # V down
    time.sleep(0.02)
    user32.keybd_event(0x56, 0, 0x0002, 0)  # V up
    user32.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl up
    time.sleep(0.04)
    user32.keybd_event(0x0D, 0, 0, 0)   # Enter down
    time.sleep(0.02)
    user32.keybd_event(0x0D, 0, 0x0002, 0)  # Enter up
    time.sleep(0.04)


def send_reply_instant(hwnd, layout, target, final_reply, state, dry_run=False):
    """极速静默发送：预检顶栏→剪贴板原子写入→激活前台→校验归属→粘贴回车→差分校验。

    返回 True=已按发出处理；False=发送失败（防串发熔断）。
    """
    # ── 发送前核验（抓图 OCR 顶栏，越权即熔断）──
    img_now, _, _ = grab_wechat_window(hwnd)
    if not img_now:
        logger.warning('发送前核验失败：无法获取微信画面，终止发送')
        return False
    cur_W, cur_H = img_now.size
    cur_layout = compute_wechat_layout(cur_W, cur_H)
    ocr_engine = get_ocr()
    res_now = ocr_full_window(img_now, ocr_engine)
    cur_title = extract_active_chat_title(res_now, cur_layout)
    is_right_target = is_name_matched_strict(cur_title, [target])[0] if cur_title else False

    if not is_right_target:
        found_cand_y = None
        sidebar_w = cur_layout["sidebar_w"]
        chat_start_x = cur_layout["chat_start_x"]
        for b, txt, sc in res_now:
            cx = (b[0][0] + b[1][0]) / 2.0
            cy = (b[0][1] + b[2][1]) / 2.0
            if sidebar_w <= cx <= chat_start_x and (cur_layout["header_h"] + 5) <= cy <= (cur_H - 20):
                if is_name_matched_strict(txt.strip(), [target])[0]:
                    found_cand_y = cy
                    break
        if found_cand_y is not None:
            logger.info('发送前纠偏：正在重定向锁定【%s】...', target)
            post_click_client_point(hwnd, int(0.16 * cur_W), int(found_cand_y))
            time.sleep(0.30)
            img_chk, _, _ = grab_wechat_window(hwnd)
            if not img_chk:
                logger.error('纠偏后无法抓图复核顶栏，放弃发送')
                return False
            chk_W, chk_H = img_chk.size
            chk_layout = compute_wechat_layout(chk_W, chk_H)
            hdr = img_chk.crop((0, 0, chk_W, int(chk_layout["header_h"] + 15)))
            res_hdr_raw, _ = ocr_engine(hdr)
            res_hdr = [([[pt[0], pt[1]] for pt in b], txt, sc)
                       for b, txt, sc in (res_hdr_raw or [])]
            chk_title = extract_active_chat_title(res_hdr, chk_layout)
            if not (chk_title and is_name_matched_strict(chk_title, [target])[0]):
                logger.error('纠偏后顶栏仍为【%s】，放弃发送防串发', chk_title or '非目标')
                return False
            logger.info('已切入【%s】聊天视窗（顶栏复核通过）', target)
        else:
            logger.error('未在列表中定位到目标【%s】，放弃发送防串发', target)
            return False

    logger.info('正在回复【%s】: %s', target, final_reply)
    if dry_run:
        return True

    # ── 剪贴板原子快照保护 ──
    user_old_clip = None
    try:
        user_old_clip = _clipboard_paste()
    except Exception:
        pass
    user_orig_hwnd = user32.GetForegroundWindow()
    send_success = False
    send_unverified = False

    for attempt in range(1, 3):
        if not safe_clipboard_copy(final_reply, retries=3, delay=0.03):
            logger.warning('剪贴板写入受阻（尝试 %d/2），稍后重试...', attempt)
            time.sleep(0.05)
            continue
        time.sleep(0.02)
        img_pre, _, _ = grab_wechat_window(hwnd)
        activate_wechat(hwnd)
        time.sleep(0.03)
        if user32.GetForegroundWindow() != hwnd:
            logger.error('微信未夺取到前台焦点（尝试 %d/2），熔断防串窗', attempt)
            time.sleep(0.15)
            continue
        # 点击输入框聚焦（0 鼠标物理移动）
        input_safe_y = int(cur_H - cur_layout["input_h"] * 0.55)
        post_click_client_point(hwnd, int(0.55 * cur_W), input_safe_y)
        time.sleep(0.03)
        if user32.GetForegroundWindow() != hwnd:
            logger.error('点击输入框时焦点脱失，终止击键（尝试 %d/2）', attempt)
            continue
        _send_keys_ctrl_v_enter()

        # 还原用户原本的前台窗口
        try:
            if user_orig_hwnd and user_orig_hwnd != hwnd:
                user32.SetForegroundWindow(user_orig_hwnd)
        except Exception:
            pass

        time.sleep(0.35)  # 等待气泡渲染沉降
        img_post, _, _ = grab_wechat_window(hwnd)
        verdict = verify_diff_bubble_success(img_pre, img_post, cur_layout)
        if verdict is True:
            send_success = True
            break
        elif verdict == "UNCERTAIN":
            logger.warning('闭环验收画面异常，按已发出处理防重复')
            send_success = True
            send_unverified = True
            break
        else:
            logger.warning('发送闭环验收未通过（尝试 %d/2），强力二次重试...', attempt)
            time.sleep(0.12)

    if user_old_clip:
        safe_clipboard_copy(user_old_clip, retries=2, delay=0.02)

    if send_success:
        if send_unverified:
            logger.warning('发送未验证：已登记防重复，请留意好友【%s】是否收到', target)
        else:
            logger.info('已静默回复【%s】成功！（物理鼠标 0 移动）', target)
    else:
        logger.error('发送脱靶告警：2 次重试后均未检测到绿底气泡增量')
    return send_success


# =============================================================================
# 8. 左右双轨协同扫描
# =============================================================================

def new_state():
    return {
        "history": {},
        "last_processed_sig": {},
        "send_fail_retry": {},
        "last_clicked_time": {},
        "last_chat_hash": None,
        "last_sidebar_hash": None,
        "last_full_scan": 0.0,
    }


def scan_and_reply_wechat(hwnd, cfg, llm, args, state):
    """单轮扫描：像素哨兵→OCR→白名单门禁→待回复提取→AI生成→静默发送。"""
    ensure_default_desktop()
    ocr_engine = get_ocr()

    img_wechat, _, _ = grab_wechat_window(hwnd)
    if not img_wechat:
        return
    W, H = img_wechat.size
    if W <= 100 or H <= 100:
        return
    layout = compute_wechat_layout(W, H)
    arr_wechat = np.array(img_wechat)

    # ── 像素级动态感知哨兵（画面无变化且无红点则跳过 OCR）──
    crop_x1 = max(330, int(layout["chat_start_x"] + 10))
    crop_y1 = max(0, int(layout["header_h"] + 10))
    crop_x2 = min(W, int(W - 5))
    crop_y2 = min(H, int(H - int(layout["input_h"] * 0.70)))
    chat_patch = arr_wechat[crop_y1:crop_y2, crop_x1:crop_x2]
    curr_chat_hash = hashlib.md5(chat_patch.tobytes()).hexdigest()

    sidebar_patch = arr_wechat[:, :layout["chat_start_x"]]
    sr, sg, sb = (sidebar_patch[:, :, i].astype(int) for i in range(3))
    has_red_pixels = int(np.sum((sr >= 190) & (sg <= 115) & (sb <= 115) & (sr > sg + 55))) >= 15
    curr_sidebar_hash = hashlib.md5(sidebar_patch.tobytes()).hexdigest()
    now_ts = time.time()

    if (curr_chat_hash == state['last_chat_hash']
            and curr_sidebar_hash == state['last_sidebar_hash']
            and not has_red_pixels
            and (now_ts - state['last_full_scan']) < 1.0):
        return
    state['last_chat_hash'] = curr_chat_hash
    state['last_sidebar_hash'] = curr_sidebar_hash
    state['last_full_scan'] = now_ts

    res = ocr_full_window(img_wechat, ocr_engine)
    if not res:
        return

    whitelist_mode = cfg.get("whitelist_mode", True)
    whitelist = [w for w in (cfg.get("whitelist") or []) if isinstance(w, str)]
    blacklist = [b for b in (cfg.get("blacklist") or []) if isinstance(b, str)]
    ignored_keywords = cfg.get("ignored_keywords") or []

    def is_valid_non_whitelist_target(name_str):
        if not name_str or len(str(name_str).strip()) < 2:
            return False
        n = str(name_str).strip()
        if any(b_name in n for b_name in blacklist if b_name.strip()):
            return False
        if is_group_chat_name(n):
            return False
        if n in ["微信", "搜索", "文件传输助手", "朋友圈", "订阅号", "服务通知", "腾讯新闻"]:
            return False
        return True

    sidebar_w = layout["sidebar_w"]
    chat_start_x = layout["chat_start_x"]

    # ── 轨 1: 左侧会话看板（绿色激活项 & 红点列表）──
    session_items = []
    for b, txt, sc in res:
        cx = (b[0][0] + b[1][0]) / 2.0
        cy = (b[0][1] + b[2][1]) / 2.0
        if sidebar_w <= cx <= chat_start_x and (layout["header_h"] + 5) <= cy <= (layout["total_h_px"] - 20):
            session_items.append((cy, cx, txt.strip(), b))
    session_items.sort(key=lambda x: x[0])

    active_green_text = None
    max_green_count = 0
    red_badge_candidates = []
    for cy, cx, text_clean, b in session_items:
        if any(b_name in text_clean for b_name in blacklist if b_name.strip()):
            continue
        min_y = max(0, int(cy - 18))
        max_y = min(H, int(cy + 18))
        patch = arr_wechat[min_y:max_y, sidebar_w:chat_start_x]
        pr, pg, pb = (patch[:, :, i].astype(int) for i in range(3))
        green_pixels = int(np.sum((pg > pr + 25) & (pg > pb + 25) & (pg > 80)))
        if green_pixels > 1200 and green_pixels > max_green_count:
            max_green_count = green_pixels
            active_green_text = text_clean
        if whitelist_mode:
            matched, target = is_name_matched_strict(text_clean, whitelist)
        else:
            matched = is_valid_non_whitelist_target(text_clean)
            clean_name = re.sub(r'^\d{1,2}(?=[^\d])', '', text_clean).strip()
            target = re.split(r'\s+', clean_name, maxsplit=1)[0].strip()
        if matched and has_red_badge_by_text_anchor(arr_wechat, b, layout):
            red_badge_candidates.append((cy, target))

    # ── 轨 2: 右侧微观聊天区（顶栏标题）──
    active_right_title = extract_active_chat_title(res, layout)

    # ── 会话目标识别与门禁 ──
    current_active_target = None
    if whitelist_mode:
        if active_right_title:
            matched_right, right_target = is_name_matched_strict(
                active_right_title, whitelist)
            if matched_right:
                current_active_target = right_target
        if not current_active_target and active_green_text:
            matched_left, left_target = is_name_matched_strict(
                active_green_text, whitelist)
            if matched_left:
                current_active_target = left_target
    else:
        if active_right_title and is_valid_non_whitelist_target(active_right_title):
            current_active_target = active_right_title.strip()
        elif active_green_text and is_valid_non_whitelist_target(active_green_text):
            clean_green = re.sub(r'^\d{1,2}(?=[^\d])', '', active_green_text).strip()
            current_active_target = re.split(r'\s+', clean_green, maxsplit=1)[0].strip()

    if current_active_target:
        chat_bubbles = parse_chat_bubbles_chromatic(
            res, img_wechat, layout, ignored_keywords)
        if chat_bubbles:
            pending_bubbles, combined_text = extract_pending_incoming_messages(
                chat_bubbles)
            if pending_bubbles and combined_text:
                current_sig = (current_active_target, combined_text,
                               int(pending_bubbles[-1]['last_y']), len(chat_bubbles))
                if state['last_processed_sig'].get(current_active_target) != current_sig:
                    # 简短停顿聚合连发
                    time.sleep(0.35)
                    img_latest, _, _ = grab_wechat_window(hwnd)
                    if img_latest:
                        latest_patch = np.array(
                            img_latest)[crop_y1:crop_y2, crop_x1:crop_x2]
                        if hashlib.md5(latest_patch.tobytes()).hexdigest() != curr_chat_hash:
                            time.sleep(0.15)
                            img_settled, _, _ = grab_wechat_window(hwnd)
                            if img_settled:
                                res_lat = ocr_full_window(img_settled, ocr_engine)
                                bubbles_lat = parse_chat_bubbles_chromatic(
                                    res_lat, img_settled, layout, ignored_keywords)
                                pending_lat, combined_lat = extract_pending_incoming_messages(
                                    bubbles_lat)
                                if pending_lat and combined_lat:
                                    pending_bubbles = pending_lat
                                    combined_text = combined_lat

                    logger.info('收到当前好友【%s】新消息(%d条): "%s"',
                                current_active_target, len(pending_bubbles), combined_text)
                    ai_reply = generate_ai_reply(
                        llm, current_active_target, combined_text, cfg, args, state)
                    if ai_reply:
                        prefix = cfg.get("reply_prefix", "") if cfg.get("include_prefix", False) else ""
                        final_reply = f"{prefix}{ai_reply}"
                        reply_delay = cfg_float(cfg, "reply_delay_seconds", 0.0)
                        if reply_delay > 0:
                            time.sleep(reply_delay)
                        success = send_reply_instant(
                            hwnd, layout, current_active_target, final_reply,
                            state, dry_run=args.dry_run)
                        if success:
                            state['last_processed_sig'][current_active_target] = current_sig
                            state['send_fail_retry'].pop(current_sig, None)
                        else:
                            fail_count = state['send_fail_retry'].get(current_sig, 0) + 1
                            state['send_fail_retry'][current_sig] = fail_count
                            if fail_count >= 2:
                                state['last_processed_sig'][current_active_target] = current_sig
                                state['send_fail_retry'].pop(current_sig, None)
                                logger.error('好友【%s】连续 2 次发送脱靶，放弃该条回复', current_active_target)
                            else:
                                logger.warning('好友【%s】第 %d 次发送未成，下个周期补偿重试',
                                               current_active_target, fail_count)
                    else:
                        logger.warning('AI 回复为空，登记签名防空转【%s】', current_active_target)
                        state['last_processed_sig'][current_active_target] = current_sig
                    return
                elif current_sig in state['send_fail_retry']:
                    return

    # ── 协同通道 B: 当前会话已回完，极速切入其他白名单红点 ──
    if red_badge_candidates:
        valid_candidates = []
        for cand_y, cand_target in red_badge_candidates:
            if current_active_target and cand_target == current_active_target:
                continue
            if active_right_title and is_name_matched_strict(
                    active_right_title, [cand_target])[0]:
                continue
            if cand_target in state['last_clicked_time'] and \
                    (now_ts - state['last_clicked_time'][cand_target]) < 3.0:
                continue
            valid_candidates.append((cand_y, cand_target))
        if valid_candidates:
            cand_y, cand_target = valid_candidates[0]
            state['last_clicked_time'][cand_target] = now_ts
            logger.info('已回完当前会话，极速切入白名单好友【%s】未读红点', cand_target)
            activate_wechat(hwnd)
            click_x = int(sidebar_w + 0.45 * (chat_start_x - sidebar_w))
            post_click_client_point(hwnd, click_x, int(cand_y))
            time.sleep(0.35)
            # 强制下一次循环全量 OCR
            state['last_chat_hash'] = None
            state['last_sidebar_hash'] = None
            state['last_full_scan'] = 0.0
            return


# =============================================================================
# 9. 命令入口
# =============================================================================

def wx_auto_reply_handle(args):
    """wx-auto-reply 主处理函数（守护循环）。"""
    if args.debug:
        logging.basicConfig(level=logging.DEBUG,
                            format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO,
                            format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s')

    set_dpi_aware()
    ensure_default_desktop()
    cfg = load_config(args.config)
    state = new_state()
    llm = OpenAIClient.from_args(args)
    interval = getattr(args, 'check_interval', None) or \
        cfg_float(cfg, "check_interval_seconds", 0.8)

    logger.info('=' * 56)
    logger.info('wx-auto-reply 已启动：白名单模式=%s，白名单=%s',
                cfg.get("whitelist_mode", True), cfg.get("whitelist"))
    logger.info('检查间隔=%.1fs，config=%s，dry-run=%s',
                interval, args.config or '(默认)', args.dry_run)
    logger.info('=' * 56)

    max_scans = 1 if args.once else args.limit
    scans_done = 0
    while True:
        scans_done += 1
        try:
            hwnd, ver = get_wechat_hwnd()
            if hwnd:
                scan_and_reply_wechat(hwnd, cfg, llm, args, state)
            else:
                logger.warning('未检测到微信窗口（第 %d 轮扫描）', scans_done)
            if max_scans and scans_done >= max_scans:
                logger.info('已扫描 %d 轮，退出。', scans_done)
                return
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info('收到中断信号，退出')
            return
        except RuntimeError as exc:
            # OCR 未安装等运行时错误
            logger.error(exc)
            return
        except Exception as exc:
            logger.error('扫描异常: %s', exc)
            if max_scans and scans_done >= max_scans:
                logger.info('已扫描 %d 轮（含异常），退出。', scans_done)
                return
            time.sleep(1.0)


def reg_subparser(subparsers):
    """注册 wx-auto-reply 子命令。"""
    pmt = subparsers.add_parser(
        'wx-auto-reply', help='后台自动回复 PC 微信白名单好友的最新消息')
    pmt.add_argument('--config', default='wechat_config.json',
                     help='回复配置 JSON 文件路径（默认 ./wechat_config.json）')
    pmt.add_argument('--once', action='store_true',
                     help='只扫描一轮就退出（用于联调）')
    pmt.add_argument('--limit', type=int, default=0,
                     help='最多扫描多少轮后退出（0 表示不限）')
    pmt.add_argument('--dry-run', action='store_true',
                     help='只识别并生成回复，不真正发送')
    # AI 参数（与 wx-summary 对齐）
    pmt.add_argument('--check-interval', type=float,
                     help='扫描间隔秒数（覆盖配置文件 check_interval_seconds）')
    pmt.add_argument('-m', '--model',
                     default=os.environ.get('OPENAI_CHAT_MODEL', 'gpt-3.5-turbo'),
                     help='模型名称')
    pmt.add_argument('-k', '--key',
                     default=os.environ.get('OPENAI_API_KEY'),
                     help='OpenAI API Key')
    pmt.add_argument('-H', '--host',
                     default=os.environ.get('OPENAI_BASE_URL'),
                     help='API 地址')
    pmt.add_argument('-r', '--retry', type=int, default=10,
                     help='LLM 调用重试次数')
    pmt.add_argument('-tm', '--temp', type=float, default=0.7, help='温度')
    pmt.add_argument('-tp', '--top-p', type=float, help='top-p')
    pmt.add_argument('-fp', '--frequency-penalty', type=float, help='频率惩罚')
    pmt.add_argument('-pp', '--presence-penalty', type=float, help='存在惩罚')
    pmt.add_argument('-mt', '--max-tokens', type=int, default=None,
                     help='最大 token 数')
    pmt.add_argument('-ua', '--user-agent',
                     default='claude-cli/2.1.41 (external, cli)',
                     help='HTTP User-Agent 请求头')
    pmt.add_argument('-st', '--stream', action='store_true', help='流式输出模式')
    pmt.add_argument('-ct', '--conn-timeout', type=int, default=60,
                     help='连接超时秒数')
    pmt.add_argument('-rt', '--read-timeout', type=int, default=120,
                     help='读取超时秒数')
    pmt.add_argument('-rr', '--repetition-regex', default='', help='重复检测正则')
    pmt.add_argument('-eb', '--extra-body', help='额外请求体')
    pmt.add_argument('-nt', '--no-think', action='store_true', help='关闭思考模式')
    pmt.add_argument('-D', '--debug', action='store_true', help='调试模式')
    pmt.set_defaults(func=wx_auto_reply_handle)
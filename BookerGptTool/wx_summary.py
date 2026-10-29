# -*- coding: utf-8 -*-
"""wx-summary 子命令：读取微信 AI 相关所有群名称、获取消息记录、总结核心观点。

数据流程：
1. 用 win32api/win32process 枚举进程，定位 WeChat.exe / Weixin.exe 主进程；
2. 优先用 pywxdump.get_wx_info 从进程内存提取数据库密钥 key；若 pywxdump 未安装或失败，
   则从注册表 HKCU\\Software\\Tencent\\WeChat 的 FileSavePath 读取数据目录，
   回退到 Documents/WeChat Files；
3. 从 wx_dir 定位 Msg/MicroMsg.db 与 Msg/Multi/MSG0..8.db，用 pywxdump.batch_decrypt
   （不可用时用自带 SQLCipher 解密函数 decrypt_db）解密到临时目录；
4. 连接解密后的 MicroMsg.db，查询 Contact 表群聊（UserName LIKE '%@chatroom'），
   取 Remark 或 NickName 作为群名，建立 {wxid: 群名} 映射；
5. 正则筛选 AI 相关群，遍历解密后的 MSG*.db 读取各群最近纯文本消息；
6. 用 OpenAIClient 并行总结各群（消息过多时分段总结再二次汇总），
   输出控制台摘要与 Markdown 报告。
"""

import os
import re
import hmac
import shutil
import hashlib
import sqlite3
import logging
import datetime
import tempfile
import time
from os import path
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import write_text, render_prompt
from .openai import OpenAIClient, logger as oai_logger

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# AI 群名称关键词（大小写不敏感）
RE_AI_GROUPS = re.compile(
    r'AI|AIGC|GPT|Claude|LLM|大模型|机器学习|DeepSeek|GLM|CogVLM|CodeGeeX|盘古|'
    r'Sora|Agent|prompt|Prompt|obsidian|Atlas|智能体|AIMusic|MLEveryday',
    re.I,
)

# 分段总结：每段最多约 400 条消息或 8000 字
SEG_MAX_MSGS = 400
SEG_MAX_CHARS = 8000

# ── 提示词 ────────────────────────────────────────────────────────────

WX_SUMMARY_PMT = '''请总结以下微信群「{group}」关于 AI 领域的聊天记录，重点总结核心观点和讨论主题。
要求输出结构化总结，包含四部分（用中文）：

1. 话题：本段聊天记录围绕的主要话题
2. 核心观点：讨论中提出的关键观点、共识与分歧
3. 资源推荐：群友推荐的模型、工具、论文、项目或链接
4. 痛点机会：反映出的用户痛点与潜在产品/商业机会

聊天记录（每条为 "[时间] 内容"）：
{content}
'''

WX_MERGE_PMT = '''以下是微信群「{group}」聊天记录分段总结后的要点，请将其合并去重，形成一份完整统一的总结。
要求输出结构化总结，包含四部分（用中文）：

1. 话题：本群讨论的主要话题
2. 核心观点：整体讨论达成的关键观点与共识
3. 资源推荐：群内推荐的重要资源
4. 痛点机会：反映出的用户痛点与潜在机会

各段要点：
{content}

请基于以上要点综合输出，不要遗漏重要内容。
'''

WX_SYNTH_PMT = '''以下是多个微信 AI 群的聊天总结，请综合这些群的信息，提炼跨群的核心观点。
要求输出（用中文）：

1. 模型格局：当前社区关注的模型/技术格局
2. 用户痛点：各群反映出的共同痛点
3. 发展趋势：未来值得关注的发展趋势
4. 产品机会：可落地的产品/创业机会

各群总结：
{content}
'''


# ── 微信进程定位 ──────────────────────────────────────────────────────

def find_wechat_process():
    """用 win32api/win32process 枚举进程，定位 WeChat.exe / Weixin.exe 主进程。

    返回 (pid, exe 名)，未找到返回 None。
    """
    exe_names = ('WeChat.exe', 'Weixin.exe')
    try:
        import win32api
        import win32process
        import win32con
    except ImportError:
        logger.warning('未安装 pywin32（win32api），无法枚举微信进程')
        return None
    # 进程枚举接口：pywin32 的 EnumProcesses 在 win32process（新版）或 win32api（老版）
    proc_module = win32process if hasattr(win32process, 'EnumProcesses') else win32api
    for pid in proc_module.EnumProcesses():
        try:
            h = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False, pid)
            try:
                exe = win32process.GetModuleFileNameEx(h, 0)
                if path.basename(exe) in exe_names:
                    return pid, path.basename(exe)
            finally:
                win32api.CloseHandle(h)
        except Exception:
            # 权限不足等，跳过
            continue
    return None


def get_wx_dir_from_reg():
    """从注册表读取微信数据目录，回退到 Documents/WeChat Files。

    返回 wxid 级数据目录（内含 Msg 的目录）；找不到则返回 WeChat Files 根目录。
    """
    base_dir = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Tencent\WeChat') as key:
            val, _ = winreg.QueryValueEx(key, 'FileSavePath')
        base_dir = val if val and val != 'MyDocument:' else None
    except Exception:
        base_dir = None
    if not base_dir:
        base_dir = path.join(
            os.environ.get('USERPROFILE', path.expanduser('~')), 'Documents')
    wx_root = path.join(base_dir, 'WeChat Files')
    if path.isdir(wx_root):
        for sub in os.listdir(wx_root):
            sub_path = path.join(wx_root, sub)
            if path.isdir(sub_path) and path.isdir(path.join(sub_path, 'Msg')):
                return sub_path
    return wx_root


def get_wx_info():
    """提取微信信息（key/wx_dir）。

    优先用 pywxdump.get_wx_info 从内存提取密钥；pywxdump 未安装或失败时回退注册表
    定位数据目录。密钥提取失败时抛出带中文提示的 RuntimeError。
    """
    proc = find_wechat_process()
    if proc:
        logger.info('检测到微信进程: pid=%d, exe=%s', *proc)
    else:
        logger.warning('未检测到微信进程，请确认微信已登录并保持运行')
    info = {}
    try:
        import pywxdump
        infos = pywxdump.get_wx_info(pywxdump.WX_OFFS, is_print=True)
        if infos:
            info = infos[0]
    except ImportError:
        logger.warning('未安装 pywxdump（pip install pywxdump），无法从内存提取数据库密钥')
    except Exception as exc:
        logger.warning('pywxdump 提取微信信息失败: %s', exc)
    window_dir = info.get('wx_dir') or get_wx_dir_from_reg()
    key = info.get('key') if info else None
    if not key:
        raise RuntimeError(
            '微信数据库密钥提取失败！请检查：'
            '1) 微信已登录并处于运行状态；'
            '2) 微信版本受 pywxdump 支持；'
            '3) 已安装 pywxdump（pip install pywxdump）。')
    return info, key, window_dir


# ── 数据库解密 ────────────────────────────────────────────────────────

def collect_msg_dbs(wx_dir):
    """从 wx_dir 定位消息数据库。

    兼容 WeChat 3.x（Msg/MicroMsg.db、Msg/Multi/MSG*.db）与
    WeChat 4.x（db_storage/message/micro_msg.db、db_storage/message/msg/MSG*.db）。
    """
    candidates = [
        path.join(wx_dir, 'Msg'),
        path.join(wx_dir, 'db_storage', 'message'),
    ]
    dbs = []
    for msg_dir in candidates:
        micro = path.join(msg_dir, 'MicroMsg.db')
        if not path.isfile(micro):
            micro = path.join(msg_dir, 'micro_msg.db')
        if path.isfile(micro) and micro not in dbs:
            dbs.append(micro)
        for multi_dir in (path.join(msg_dir, 'Multi'), path.join(msg_dir, 'msg')):
            for i in range(9):
                db = path.join(multi_dir, 'MSG%d.db' % i)
                if path.isfile(db) and db not in dbs:
                    dbs.append(db)
    if not dbs:
        raise RuntimeError(
            '未在 %s 找到任何微信消息数据库，请检查微信数据目录' % wx_dir)
    return dbs


def decrypt_db(key, db_path, out_path):
    """自带 SQLCipher 解密实现（参考微信标准解密算法），key 动态传入。

    key 为 64 位十六进制字符串。算法：
    salt=文件前16字节 -> mac_salt=salt^0x3A -> PBKDF2-HMAC-SHA1(64000次) 派生密钥，
    页签名校验通过后按 AES-CBC 每 4096 字节解密，写出 "SQLite format 3\\0" 头。
    """
    try:
        from Cryptodome.Cipher import AES
    except ImportError:
        from Crypto.Cipher import AES  # noqa
    SQLITE_FILE_HEADER = 'SQLite format 3\x00'
    if len(key) != 64:
        raise ValueError('数据库密钥必须为 64 位十六进制字符串')
    password = bytes.fromhex(key.strip())
    with open(db_path, 'rb') as f:
        blist = f.read()
    if len(blist) < 4096:
        raise ValueError('%s 文件过小，疑似损坏' % db_path)
    first = blist[16:4096]
    salt = blist[:16]
    mac_salt = bytes([b ^ 0x3A for b in salt])
    byte_h = hashlib.pbkdf2_hmac('sha1', password, salt, 64000, 32)
    mac_key = hashlib.pbkdf2_hmac('sha1', byte_h, mac_salt, 2, 32)
    hash_mac = hmac.new(mac_key, blist[16:4064], hashlib.sha1)
    hash_mac.update(b'\x01\x00\x00\x00')
    if hash_mac.digest() != first[-32:-12]:
        raise ValueError('密钥错误，无法解密 %s' % db_path)
    os.makedirs(path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as de:
        de.write(SQLITE_FILE_HEADER.encode('utf8'))
        for i in range(0, len(blist), 4096):
            tblist = blist[i:i + 4096] if i > 0 else blist[16:i + 4096]
            if len(tblist) < 48:
                break
            de.write(AES.new(byte_h, AES.MODE_CBC,
                             tblist[-48:-32]).decrypt(tblist[:-48]))
            de.write(tblist[-48:])
    return out_path


def decrypt_dbs(key, db_paths, dec_dir):
    """解密数据库列表到 dec_dir，返回 {数据库原名: 解密后路径}。

    优先使用 pywxdump.batch_decrypt(key, db_paths, dec_dir)；不可用时逐个调用
    自带 decrypt_db 解密。
    """
    os.makedirs(dec_dir, exist_ok=True)
    tried = False
    try:
        import pywxdump
        pywxdump.batch_decrypt(key, db_paths, dec_dir, is_print=False)
        tried = True
    except ImportError:
        pass
    except Exception as exc:
        logger.warning('pywxdump.batch_decrypt 失败，尝试自带解密: %s', exc)
    if not tried:
        for db in db_paths:
            out = path.join(dec_dir, 'de_' + path.basename(db))
            try:
                decrypt_db(key, db, out)
            except Exception as exc:
                logger.warning('解密失败 %s: %s', db, exc)
    # 收集解密产物（batch_decrypt 会按相对目录放置 de_*.db）
    de_map = {}
    for root, _, files in os.walk(dec_dir):
        for f in files:
            if f.startswith('de_') and f.endswith('.db'):
                de_map[f[3:]] = path.join(root, f)
    return de_map


def de_find(de_map, *names):
    """按多个候选数据库名在解密产物中查找路径。"""
    for n in names:
        if n in de_map:
            return de_map[n]
    return None


# ── 群名与消息读取 ────────────────────────────────────────────────────

def load_chatrooms(micro_db):
    """从解密后的 MicroMsg.db 读取所有群聊，返回 {wxid: 群名}。

    查询 Contact 表 WHERE UserName LIKE '%@chatroom'，取 Remark 或 NickName 作为群名。
    """
    chatrooms = {}
    if not micro_db or not path.isfile(micro_db):
        logger.warning('解密后的 MicroMsg.db 不存在，无法读取群名单')
        return chatrooms
    conn = None
    try:
        conn = sqlite3.connect(micro_db)
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute('PRAGMA table_info(Contact)')]
        if 'UserName' not in cols:
            logger.warning('Contact 表缺少 UserName 列: %s', cols)
            return chatrooms
        sel = [c for c in ('Remark', 'NickName') if c in cols]
        if not sel:
            logger.warning('Contact 表缺少 Remark/NickName 列，群名将为空')
            return chatrooms
        sql = 'SELECT UserName, %s FROM Contact WHERE UserName LIKE ?' % ', '.join(sel)
        for row in cur.execute(sql, ('%@chatroom',)):
            username = row[0]
            name = ''
            for i in range(1, len(row)):
                if row[i] and str(row[i]).strip():
                    name = str(row[i]).strip()
                    break
            if username and name:
                chatrooms[str(username)] = name
    except sqlite3.Error as exc:
        logger.warning('读取 MicroMsg.db 失败: %s', exc)
    finally:
        if conn:
            conn.close()
    return chatrooms


def filter_ai_chatrooms(chatrooms):
    """按关键词正则筛选 AI 相关群，返回 {wxid: 群名}。"""
    return {
        wxid: name for wxid, name in chatrooms.items()
        if RE_AI_GROUPS.search(name)
    }


def load_group_messages(de_map, wxid, cutoff, max_per_group):
    """从解密后的 de_MSG*.db 查询某群消息（Type=1 纯文本消息）。

    返回按时间升序的 [ts, text] 列表，最多 max_per_group 条（取最近）。
    """
    msgs = []
    for i in range(9):
        de = de_map.get('MSG%d.db' % i)
        if not de or not path.isfile(de):
            continue
        conn = None
        try:
            conn = sqlite3.connect(de)
            cur = conn.cursor()
            cols = [r[1] for r in cur.execute('PRAGMA table_info(MSG)')]
            talker = 'StrTalker' if 'StrTalker' in cols else \
                ('Talker' if 'Talker' in cols else None)
            if not talker or 'CreateTime' not in cols or 'StrContent' not in cols:
                continue
            sql = ('SELECT CreateTime, StrContent FROM MSG '
                   'WHERE %s=? AND Type=1 AND CreateTime>=? ORDER BY CreateTime'
                   % talker)
            for row in cur.execute(sql, (wxid, int(cutoff))):
                text = (row[1] or '').strip()
                if not text:
                    continue
                msgs.append([int(row[0]), text])
        except sqlite3.Error as exc:
            logger.debug('查询 %s 失败: %s', de, exc)
        finally:
            if conn:
                conn.close()
    msgs.sort(key=lambda x: x[0])
    if max_per_group > 0 and len(msgs) > max_per_group:
        msgs = msgs[-max_per_group:]
    return msgs


def format_msg_lines(msgs):
    """将 [ts, text] 列表格式化为 "[时间] 内容" 行。"""
    return [
        '[%s] %s' % (
            datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M'),
            text)
        for ts, text in msgs
    ]


# ── LLM 总结 ──────────────────────────────────────────────────────────

def split_segments(lines):
    """把消息行按数量/字数切成多段。"""
    segs, cur, cur_chars = [], [], 0
    for line in lines:
        cur.append(line)
        cur_chars += len(line)
        if len(cur) >= SEG_MAX_MSGS or cur_chars >= SEG_MAX_CHARS:
            segs.append('\n'.join(cur))
            cur, cur_chars = [], 0
    if cur:
        segs.append('\n'.join(cur))
    return segs


def ask_summary(llm, group_name, content, args, merge=False):
    """调用 LLM 生成一段总结文本。"""
    pmt = WX_MERGE_PMT if merge else WX_SUMMARY_PMT
    ques = render_prompt(pmt, group=group_name, content=content)
    return llm.ask_chatgpt_retry(ques, args.model, args).strip()


def summarize_group(llm, group_name, lines, args):
    """总结一个群的聊天记录；消息过多时分段总结再二次汇总。"""
    if not lines:
        return '（该群没有可总结的文本消息）'
    segs = split_segments(lines)
    if len(segs) == 1:
        return ask_summary(llm, group_name, segs[0], args)
    logger.info('群「%s」消息较多，分成 %d 段分别总结', group_name, len(segs))
    res = [''] * len(segs)
    pool = ThreadPoolExecutor(max(1, min(args.threads, len(segs))))
    futs = {
        pool.submit(ask_summary, llm, group_name, seg, args): i
        for i, seg in enumerate(segs)
    }
    for fut in as_completed(futs):
        i = futs[fut]
        try:
            res[i] = fut.result()
        except Exception as exc:
            logger.error('群「%s」分段 %d 总结失败: %s', group_name, i, exc)
            res[i] = ''
    points = [r for r in res if r]
    if not points:
        raise RuntimeError('群「%s」全部分段总结失败' % group_name)
    return ask_summary(llm, group_name, '\n\n'.join(points), args, merge=True)


def summarize_groups(llm, groups, args):
    """并行总结多个群。groups: [(群名, 消息行列表)]，返回 {群名: 总结}。"""
    results = {}
    if not groups:
        return results
    pool = ThreadPoolExecutor(args.threads)
    futs = {
        pool.submit(summarize_group, llm, name, lines, args): name
        for name, lines in groups
    }
    for fut in as_completed(futs):
        name = futs[fut]
        try:
            results[name] = fut.result()
        except Exception as exc:
            logger.error('总结群「%s」失败: %s', name, exc)
            results[name] = None
    return results


def synthesize_cross(llm, summaries, args):
    """跨群综合核心观点（模型格局/痛点/趋势/产品机会）。"""
    valid = {n: s for n, s in summaries.items() if s}
    if not valid:
        return None
    if len(valid) == 1:
        return '（仅一个 AI 群，无需跨群综合）'
    content = '\n\n'.join('### %s\n%s' % (n, s) for n, s in valid.items())
    ques = render_prompt(WX_SYNTH_PMT, content=content)
    return llm.ask_chatgpt_retry(ques, args.model, args).strip()


# ── 输出 ──────────────────────────────────────────────────────────────

def print_console(msg_counts, summaries):
    """控制台打印：AI 群清单（群名+消息数）+ 每个群的总结摘要。"""
    logger.info('=' * 50)
    logger.info('AI 群清单（群名 / 消息数）：')
    for name, cnt in sorted(msg_counts.items(), key=lambda kv: -kv[1]):
        logger.info('  %s / %d 条', name, cnt)
    logger.info('-' * 50)
    for name, summary in summaries.items():
        logger.info('【%s】', name)
        logger.info(summary or '（无总结）')
        logger.info('-' * 50)


def build_report(wxid_label, wx_dir, time_range, ai_groups, msg_counts,
                 summaries, synthesis):
    """生成 Markdown 报告文本。"""
    L = []
    L.append('# 微信 AI 群消息总结报告')
    L.append('')
    L.append('## 总览')
    L.append('')
    L.append('* 数据来源：本地微信数据库（wxid: %s）' % wxid_label)
    L.append('* 数据目录：%s' % wx_dir)
    L.append('* 统计时间：%s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    L.append('* 时间范围：%s' % time_range)
    L.append('* AI 相关群数量：%d' % len(ai_groups))
    L.append('* 消息总量：%d' % sum(msg_counts.values()))
    L.append('')
    L.append('## AI 群清单')
    L.append('')
    L.append('| 群名 | 消息数 |')
    L.append('| --- | --- |')
    for name, cnt in sorted(msg_counts.items(), key=lambda kv: -kv[1]):
        L.append('| %s | %d |' % (name, cnt))
    L.append('')
    L.append('## 各群核心观点')
    L.append('')
    for name, cnt in sorted(msg_counts.items(), key=lambda kv: -kv[1]):
        L.append('### %s（%d 条）' % (name, cnt))
        L.append('')
        L.append(summaries.get(name) or '（总结失败）')
        L.append('')
    L.append('## 跨群综合核心观点')
    L.append('')
    L.append(synthesis or '（暂无）')
    L.append('')
    return '\n'.join(L)


# ── 处理入口 ──────────────────────────────────────────────────────────

def wx_summary_handle(args):
    """wx-summary 主处理函数。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    try:
        info, key, wx_dir = get_wx_info()
    except RuntimeError as exc:
        logger.error(exc)
        return
    wxid_label = (info.get('wxid') or path.basename(wx_dir) or '未知')
    work_dir = tempfile.mkdtemp(prefix='wx_summary_')
    dec_dir = path.join(work_dir, 'dec')
    try:
        # 1. 定位并解密消息数据库
        db_paths = collect_msg_dbs(wx_dir)
        logger.info('定位到 %d 个消息数据库，解密到 %s ...', len(db_paths), dec_dir)
        de_map = decrypt_dbs(key, db_paths, dec_dir)
        if not de_map:
            raise RuntimeError(
                '微信消息数据库全部解密失败：密钥可能不正确或微信版本不受支持，'
                '请重启微信后重试')
        # 2. 读取群名单并筛选 AI 群
        micro_db = de_find(de_map, 'MicroMsg.db', 'micro_msg.db')
        chatrooms = load_chatrooms(micro_db)
        logger.info('共读取到 %d 个群聊', len(chatrooms))
        ai_groups = filter_ai_chatrooms(chatrooms)
        if not ai_groups:
            logger.error('未找到名称匹配 AI 关键词的群')
            return
        logger.info('筛选出 %d 个 AI 相关群', len(ai_groups))
        # 3. 按群读取消息
        cutoff = time.time() - args.days * 86400
        time_range = '%s ~ %s' % (
            datetime.datetime.fromtimestamp(cutoff).strftime('%Y-%m-%d'),
            datetime.datetime.now().strftime('%Y-%m-%d'))
        group_lines, msg_counts = {}, {}
        for wxid, name in ai_groups.items():
            msgs = load_group_messages(de_map, wxid, cutoff, args.max_per_group)
            msg_counts[name] = len(msgs)
            if not msgs:
                logger.warning('群「%s」在时间范围内没有纯文本消息', name)
                continue
            lines = format_msg_lines(msgs)
            group_lines[name] = lines
            # 按群分组保存消息文本到临时文件
            safe = re.sub(r'[^\w一-鿿]+', '_', name) or 'group'
            write_text(path.join(work_dir, safe + '.txt'), '\n'.join(lines))
        if not group_lines:
            logger.error('所有 AI 群都没有可用的文本消息，无法生成总结')
            return
        msg_total = sum(len(v) for v in group_lines.values())
        logger.info('共获取 %d 个 AI 群 %d 条消息', len(group_lines), msg_total)
        # 4. LLM 总结（并行）与跨群综合
        llm = OpenAIClient.from_args(args)
        summaries = summarize_groups(
            llm, [(name, lines) for name, lines in group_lines.items()], args)
        synthesis = synthesize_cross(llm, summaries, args)
        # 5. 控制台输出
        print_console(msg_counts, summaries)
        # 6. 保存完整报告
        report = build_report(wxid_label, wx_dir, time_range, ai_groups,
                              msg_counts, summaries, synthesis)
        write_text(args.output, report)
        logger.info('报告已保存到 %s', path.abspath(args.output))
        if args.keep_dec:
            logger.info('已保留解密临时目录：%s', work_dir)
    except RuntimeError as exc:
        logger.error(exc)
    finally:
        if not args.keep_dec:
            shutil.rmtree(work_dir, ignore_errors=True)


def reg_subparser(subparsers):
    """注册 wx-summary 子命令。"""
    pmt = subparsers.add_parser(
        'wx-summary', help='读取微信 AI 相关群消息并总结核心观点')
    pmt.add_argument('--days', type=int, default=60,
                     help='最近多少天的消息（默认 60）')
    pmt.add_argument('--threads', type=int, default=4,
                     help='并行总结线程数（默认 4）')
    pmt.add_argument('--max-per-group', type=int, default=1500,
                     help='单群最多取多少条消息（默认 1500，防超长）')
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
    pmt.add_argument('-tm', '--temp', type=float, default=1, help='温度')
    pmt.add_argument('-tp', '--top-p', type=float, help='top-p')
    pmt.add_argument('-fp', '--frequency-penalty', type=float,
                     help='频率惩罚')
    pmt.add_argument('-pp', '--presence-penalty', type=float,
                     help='存在惩罚')
    pmt.add_argument('-mt', '--max-tokens', type=int, default=None,
                     help='最大 token 数')
    pmt.add_argument('-ua', '--user-agent',
                     default='claude-cli/2.1.41 (external, cli)',
                     help='HTTP User-Agent 请求头')
    pmt.add_argument('-st', '--stream', action='store_true',
                     help='流式输出模式')
    pmt.add_argument('-ct', '--conn-timeout', type=int, default=60,
                     help='连接超时秒数')
    pmt.add_argument('-rt', '--read-timeout', type=int, default=120,
                     help='读取超时秒数')
    pmt.add_argument('-rr', '--repetition-regex', default='',
                     help='重复检测正则')
    pmt.add_argument('-eb', '--extra-body', help='额外请求体')
    pmt.add_argument('-nt', '--no-think', action='store_true',
                     help='关闭思考模式')
    pmt.add_argument('--keep-dec', action='store_true',
                     help='保留解密临时目录不清理')
    pmt.add_argument('--output', default='./wx-summary-report.md',
                     help='报告输出路径')
    pmt.add_argument('-D', '--debug', action='store_true', help='调试模式')
    pmt.set_defaults(func=wx_summary_handle)
# -*- coding: utf-8 -*-

"""re 子命令的 x64dbg/x32dbg 封装层：直接使用 x64dbg_automate 库。

不经过 MCP，底层通过 ZeroMQ 与 x64dbg 内的 xauto 插件通信。
负责：Python 3.10 的 StrEnum 兼容补丁、客户端连接（本地会话/远程/启动新会话）、
以及封装的调试工具方法（内存、寄存器、断点、单步、反汇编等）。
"""

import enum
import logging
import os
import sys
import time
from os import path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Python 3.10 StrEnum 兼容补丁 ──────────────────────────────
# x64dbg_automate 0.9.2 使用 enum.StrEnum（Python 3.11+）。在 3.10 上
# 注入兼容子类后再 import 库。
if not hasattr(enum, 'StrEnum'):
    class _StrEnum(str, enum.Enum):
        def __str__(self):
            return self.value
    enum.StrEnum = _StrEnum

try:
    import x64dbg_automate as X
    from x64dbg_automate import X64DbgClient
    from x64dbg_automate.models import (
        BreakpointType, MutableRegister, StandardBreakpointType,
        HardwareBreakpointType, MemoryBreakpointType, PageRightsConfiguration,
    )
    _XDBG_AVAIL = True
except Exception as ex:  # pragma: no cover
    X64DbgClient = None
    _XDBG_AVAIL = False
    _XDBG_IMPORT_ERR = str(ex)


class XautoError(Exception):
    """x64dbg_automate 封装层错误"""


def _as_client(target: str = 'x64dbg') -> X64DbgClient:
    """解析目标架构对应的客户端类（x64dbg/x32dbg 为同一实现）。"""
    return X64DbgClient


class XautoClient:
    """对单个 x64dbg/x32dbg 会话的封装，全部方法返回可 JSON 序列化的 dict。"""

    TARGET_LABEL = 'x64dbg'

    def __init__(self, target: str = 'x64dbg', connect_timeout: float = 12.0,
                 x64dbg_path: str = ''):
        """
        Args:
            target: 'x64dbg' 或 'x32dbg'（仅用于标签/日志）。
            connect_timeout: 构建客户端时连接/握手超时秒数。
            x64dbg_path: x64dbg/x32dbg 可执行文件路径（start_session 需要）。
        """
        if not _XDBG_AVAIL:
            raise XautoError(f'x64dbg_automate 不可用：{_XDBG_IMPORT_ERR}')
        self.target = target
        self.TARGET_LABEL = target
        self.connect_timeout = connect_timeout
        self.client = None      # X64DbgClient 实例
        self.x64dbg_path = x64dbg_path or ''
        self.connected = False
        self.session_pid = None
        self.last_error = ''

    # ── 生命周期 ──────────────────────────────────────────────

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出本机活动会话（来自 lockfile 发现）。"""
        if not _XDBG_AVAIL:
            return []
        try:
            return [
                {
                    'pid': s.pid,
                    'window_title': s.window_title,
                    'cmdline': s.cmdline,
                    'host': s.host,
                    'port': s.sess_req_rep_port,
                }
                for s in X64DbgClient.list_sessions()
            ]
        except Exception as ex:
            self.last_error = f'list_sessions 失败：{ex}'
            return []

    def connect_remote(self, host: str, req_rep_port: int,
                       pub_sub_port: int) -> bool:
        """连接到远程/指定端口的 x64dbg 会话。"""
        try:
            self.client = X64DbgClient.connect_remote(
                host, int(req_rep_port), int(pub_sub_port))
            self.connected = True
            self.session_pid = None
            self.last_error = ''
            return True
        except Exception as ex:
            self._fail(ex, 'connect_remote')
            return False

    def attach_existing(self, session_pid: int) -> bool:
        """附加到本机已运行的 x64dbg 会话（按 PID/发现结果）。"""
        try:
            self.client = _as_client()()
            self.client.attach_session(int(session_pid))
            self.connected = True
            self.session_pid = int(session_pid)
            self.last_error = ''
            return True
        except Exception as ex:
            self._fail(ex, 'attach_existing')
            return False

    def attach_session(self, session_pid: int) -> bool:
        """同 attach_existing（xauto 命名兼容）。"""
        return self.attach_existing(session_pid)

    def start_session(self, target_exe: str = '', cmdline: str = '',
                      current_dir: str = '') -> bool:
        """启动一个新的 x64dbg 会话（可选加载目标 exe）。"""
        try:
            self.client = _as_client()(self.x64dbg_path or None)
            self.client.start_session(target_exe, cmdline, current_dir)
            self.connected = True
            self.session_pid = self.client.session_pid
            self.last_error = ''
            return True
        except Exception as ex:
            self._fail(ex, 'start_session')
            return False

    def detach(self) -> bool:
        """分离当前会话（调试器进程保持运行）。"""
        try:
            if self.client is not None:
                self.client.detach_session()
            self.connected = False
            self.session_pid = None
            return True
        except Exception as ex:
            self._fail(ex, 'detach')
            return False

    def terminate(self) -> bool:
        """结束当前会话（终止调试器进程）。"""
        try:
            if self.client is not None:
                self.client.terminate_session()
            self.connected = False
            self.session_pid = None
            return True
        except Exception as ex:
            self._fail(ex, 'terminate')
            return False

    def _fail(self, ex: Exception, op: str) -> None:
        self.last_error = f'{self.TARGET_LABEL}.{op} 失败：{ex}'
        logger.warning(self.last_error)

    def _require(self):
        if not self.connected or self.client is None:
            raise XautoError(
                f'{self.TARGET_LABEL} 未连接。请先 connect_remote / attach_existing '
                '/ start_session，或者运行 tool_x64_status 查看会话。')

    # ── 调试控制 ──────────────────────────────────────────────

    def pause(self) -> bool:
        try:
            self._require()
            return bool(self.client.pause())
        except Exception as ex:
            self._fail(ex, 'pause')
            return False

    def go(self) -> bool:
        """继续运行（F9）。"""
        try:
            self._require()
            return bool(self.client.go())
        except Exception as ex:
            self._fail(ex, 'go')
            return False

    def step_into(self, count: int = 1) -> bool:
        """单步进入（F7）。"""
        try:
            self._require()
            return bool(self.client.stepi(int(count) or 1))
        except Exception as ex:
            self._fail(ex, 'stepi')
            return False

    def step_over(self, count: int = 1) -> bool:
        """单步步过（F8）。"""
        try:
            self._require()
            return bool(self.client.stepo(int(count) or 1))
        except Exception as ex:
            self._fail(ex, 'stepo')
            return False

    def run_to(self, address: str) -> bool:
        """运行到指定地址（下一次性断点到该地址后 go）。"""
        try:
            self._require()
            val = int(str(address), 0)
            if not self.client.set_breakpoint(val, singleshoot=True):
                return False
            return bool(self.client.go())
        except Exception as ex:
            self._fail(ex, 'run_to')
            return False

    def ret(self, frames: int = 1) -> bool:
        """执行到当前函数返回。"""
        try:
            self._require()
            return bool(self.client.ret(int(frames) or 1))
        except Exception as ex:
            self._fail(ex, 'ret')
            return False

    # ── 寄存器 ────────────────────────────────────────────────

    def get_regs(self) -> Dict[str, Any]:
        """读取全部寄存器（x64 上下文含 rax..r15/rip，x32 含 eax..edi/eip）。"""
        try:
            self._require()
            regs = self.client.get_regs()
            context = {}
            if hasattr(regs, 'context'):
                c = regs.context
                if hasattr(c, 'model_dump'):
                    context = c.model_dump()
                elif isinstance(c, dict):
                    context = c
            return {'registers': self._jsonable(context)}
        except Exception as ex:
            self._fail(ex, 'get_regs')
            return {}

    def get_reg(self, reg: str) -> int:
        try:
            self._require()
            return int(self.client.get_reg(str(reg)))
        except Exception as ex:
            self._fail(ex, f'get_reg({reg})')
            return 0

    def set_reg(self, reg: str, value: int) -> bool:
        try:
            self._require()
            return bool(self.client.set_reg(str(reg), int(value)))
        except Exception as ex:
            self._fail(ex, 'set_reg')
            return False

    # ── 内存 ──────────────────────────────────────────────────

    def read_memory(self, address: str, size: int) -> str:
        """读取内存并以 hex 输出（每行 16 字节）。"""
        try:
            self._require()
            addr = int(str(address), 0)
            data = self.client.read_memory(addr, int(size))
            return _hexdump(data, ascii_=True)
        except Exception as ex:
            self._fail(ex, 'read_memory')
            return f'{self.last_error}'

    def read_bytes(self, address: str, size: int) -> bytes:
        try:
            self._require()
            return self.client.read_memory(int(str(address), 0), int(size))
        except Exception as ex:
            self._fail(ex, 'read_bytes')
            return b''

    def read_string(self, address: str, size: int = 256) -> str:
        """读取内存并以 UTF-8 文本输出。"""
        raw = self.read_bytes(address, size)
        try:
            return raw.split(b'\x00')[0].decode('utf8', 'replace')
        except Exception:
            return raw.decode('utf8', 'replace')

    def write_memory(self, address: str, data: str, is_hex: bool = True) -> bool:
        """写内存。data 为十六进制（is_hex=True）或原始文本（is_hex=False）。"""
        try:
            self._require()
            addr = int(str(address), 0)
            if is_hex:
                payload = bytes.fromhex(data.replace(' ', '').replace('\n', ''))
            else:
                payload = data.encode('utf8')
            return bool(self.client.write_memory(addr, payload))
        except Exception as ex:
            self._fail(ex, 'write_memory')
            return False

    def memset(self, address: str, val: int, size: int = 1) -> bool:
        """用单字节值填充内存。"""
        try:
            self._require()
            return bool(self.client.memset(int(str(address), 0),
                                           int(val) & 0xFF, int(size)))
        except Exception as ex:
            self._fail(ex, 'memset')
            return False

    def memmap(self) -> List[Dict[str, Any]]:
        try:
            self._require()
            pages = self.client.memmap()
            return [self._dump_model(p) for p in pages]
        except Exception as ex:
            self._fail(ex, 'memmap')
            return []

    def virt_alloc(self, size: int = 0x1000, addr: int = 0) -> int:
        try:
            self._require()
            return int(self.client.virt_alloc(int(size), int(addr)))
        except Exception as ex:
            self._fail(ex, 'virt_alloc')
            return 0

    def virt_query(self, addr: str) -> Dict[str, Any]:
        try:
            self._require()
            p = self.client.virt_query(int(str(addr), 0))
            return self._dump_model(p) if p else {}
        except Exception as ex:
            self._fail(ex, 'virt_query')
            return {}

    # ── 断点 ──────────────────────────────────────────────────

    def set_breakpoint(self, address: str, singleshoot: bool = False) -> bool:
        try:
            self._require()
            return bool(self.client.set_breakpoint(
                int(str(address), 0), singleshoot=singleshoot))
        except Exception as ex:
            self._fail(ex, 'set_breakpoint')
            return False

    def set_hardware_breakpoint(self, address: str, size: int = 1,
                                bp_type: str = 'x') -> bool:
        try:
            self._require()
            return bool(self.client.set_hardware_breakpoint(
                int(str(address), 0),
                bp_type=HardwareBreakpointType[bp_type], size=int(size)))
        except Exception as ex:
            self._fail(ex, 'set_hardware_breakpoint')
            return False

    def set_memory_breakpoint(self, address: str, bp_type: str = 'a',
                              singleshoot: bool = False) -> bool:
        try:
            self._require()
            return bool(self.client.set_memory_breakpoint(
                int(str(address), 0),
                bp_type=MemoryBreakpointType[bp_type], singleshoot=singleshoot))
        except Exception as ex:
            self._fail(ex, 'set_memory_breakpoint')
            return False

    def clear_breakpoint(self, address: str = '') -> bool:
        try:
            self._require()
            addr = int(str(address), 0) if address else None
            return bool(self.client.clear_breakpoint(addr))
        except Exception as ex:
            self._fail(ex, 'clear_breakpoint')
            return False

    def clear_hardware_breakpoint(self, address: str = '') -> bool:
        try:
            self._require()
            addr = int(str(address), 0) if address else None
            return bool(self.client.clear_hardware_breakpoint(addr))
        except Exception as ex:
            self._fail(ex, 'clear_hardware_breakpoint')
            return False

    def clear_memory_breakpoint(self, address: str = '') -> bool:
        try:
            self._require()
            addr = int(str(address), 0) if address else None
            return bool(self.client.clear_memory_breakpoint(addr))
        except Exception as ex:
            self._fail(ex, 'clear_memory_breakpoint')
            return False

    def get_breakpoints(self, bp_type: str = 'BpNormal') -> List[Dict[str, Any]]:
        try:
            self._require()
            t = BreakpointType[bp_type]
            bps = self.client.get_breakpoints(t)
            return [self._dump_model(b) for b in bps]
        except Exception as ex:
            self._fail(ex, 'get_breakpoints')
            return []

    # ── 标签/注释/符号 ────────────────────────────────────────

    def set_label(self, address: str, text: str) -> bool:
        try:
            self._require()
            return bool(self.client.set_label_at(int(str(address), 0), text))
        except Exception as ex:
            self._fail(ex, 'set_label')
            return False

    def get_label(self, address: str) -> str:
        try:
            self._require()
            return str(self.client.get_label_at(int(str(address), 0)))
        except Exception as ex:
            self._fail(ex, 'get_label')
            return ''

    def set_comment(self, address: str, text: str) -> bool:
        try:
            self._require()
            return bool(self.client.set_comment_at(int(str(address), 0), text))
        except Exception as ex:
            self._fail(ex, 'set_comment')
            return False

    def get_comment(self, address: str) -> str:
        try:
            self._require()
            return str(self.client.get_comment_at(int(str(address), 0)))
        except Exception as ex:
            self._fail(ex, 'get_comment')
            return ''

    def get_symbol(self, address: str) -> Dict[str, Any]:
        try:
            self._require()
            s = self.client.get_symbol_at(int(str(address), 0))
            return self._dump_model(s) if s else {}
        except Exception as ex:
            self._fail(ex, 'get_symbol')
            return {}

    # ── 反汇编/汇编 ──────────────────────────────────────────

    def disassemble_at(self, address: str) -> Dict[str, Any]:
        try:
            self._require()
            ins = self.client.disassemble_at(int(str(address), 0))
            return self._dump_model(ins) if ins else {}
        except Exception as ex:
            self._fail(ex, 'disassemble_at')
            return {}

    def disassemble_range(self, address: str, count: int = 10) -> str:
        """反汇编从 address 开始的 count 条指令，返回文本（每条一行）。"""
        lines = []
        addr = int(str(address), 0)
        for _ in range(max(1, int(count or 10))):
            try:
                ins = self.client.disassemble_at(addr)
                if not ins:
                    break
                m = self._dump_model(ins)
                text = m.get('instruction') or m.get('instruction_text') or ''
                lines.append(f'{addr:#x}  {text}  (size={getattr(ins, "instr_size", 0)})')
                addr += getattr(ins, 'instr_size', None) or 1
            except Exception:
                break
        return '\n'.join(lines)

    def assemble_at(self, address: str, instruction: str) -> int:
        """在地址处汇编单条指令，返回指令大小。"""
        try:
            self._require()
            return int(self.client.assemble_at(
                int(str(address), 0), instruction) or 0)
        except Exception as ex:
            self._fail(ex, 'assemble_at')
            return 0

    def eval(self, expression: str) -> Dict[str, Any]:
        """求值表达式（x64dbg 表达式语法）。"""
        try:
            self._require()
            val, ok = self.client.eval_sync(expression)
            return {'value': val, 'ok': bool(ok)}
        except Exception as ex:
            self._fail(ex, f'eval({expression})')
            return {'value': 0, 'ok': False, 'error': self.last_error}

    def cmd(self, command: str) -> bool:
        """执行 x64dbg 命令（cmd_sync）。"""
        try:
            self._require()
            return bool(self.client.cmd_sync(command))
        except Exception as ex:
            self._fail(ex, 'cmd')
            return False

    # ── 进程状态 ──────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """简明状态（供 tool_x64_status 汇总）。"""
        if not self.connected or self.client is None:
            return {'connected': False, 'error': self.last_error or '未连接'}
        info = {
            'connected': True,
            'debugging': False,
            'running': False,
            'debugee_pid': None,
        }
        try:
            info['debugging'] = bool(self.client.is_debugging())
            info['running'] = bool(self.client.is_running())
            info['debugee_pid'] = self.client.debugee_pid()
        except Exception as ex:
            info['error'] = f'查询状态失败：{ex}'
        return info

    # ── 工具：从已枚举数据 dump（pydantic → dict）─────────────

    @staticmethod
    def _jsonable(obj, depth: int = 0) -> Any:
        """递归把 bytes/bytearray 等不可 JSON 序列化的值转为可序列化形式。"""
        if depth > 12:
            return str(obj)
        if isinstance(obj, bytes):
            return obj.hex(' ')
        if isinstance(obj, bytearray):
            return bytes(obj).hex(' ')
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if hasattr(obj, 'model_dump'):
            try:
                return XautoClient._jsonable(obj.model_dump(), depth + 1)
            except Exception:
                return str(obj)
        if isinstance(obj, dict):
            return {str(k): XautoClient._jsonable(v, depth + 1)
                    for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [XautoClient._jsonable(v, depth + 1) for v in obj]
        try:
            return str(obj)
        except Exception:
            return repr(obj)

    @staticmethod
    def _dump_model(obj) -> Dict[str, Any]:
        if obj is None:
            return {}
        if hasattr(obj, 'model_dump'):
            try:
                return obj.model_dump()
            except Exception:
                pass
        if isinstance(obj, dict):
            return obj
        return {'value': str(obj)}


def _hexdump(data: bytes, ascii_: bool = True) -> str:
    """字节 → 十六进制转储文本（每行 16 字节 + ASCII）。"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = ' '.join(f'{b:02x}' for b in chunk)
        hexs = hexs.ljust(16 * 3 - 1)
        if ascii_:
            asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            lines.append(f'{i:08x}  {hexs}  |{asc}|')
        else:
            lines.append(f'{i:08x}  {hexs}')
    return '\n'.join(lines)
# -*- coding: utf-8 -*-

"""re 子命令的 IDA 封装层：通过 idalib 直接调用 IDA API（去除 MCP 桥接）。

依赖：
- idapro 包（官方 IDA Library Python module），通过配置文件定位 IDA 安装目录
  （%APPDATA%\\Hex-Rays\\IDA Pro\\ida-config.json -> ida-install-dir）。
- IDA 9.0+（含 idalib.dll）。已在 D:\\Program Files\\IDA94ProBable_CN 验证。

注意：idalib 是进程内单数据库模型——open_database 一次加载一个二进制，
此后 ida_* 模块可用；close_database 后需重新 open_database。
open_database 会在当前工作目录创建 .i64 数据库，因此必须先 os.chdir 到可写目录。
"""

import logging
import os
import sys
import traceback
from os import path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import idapro
    _IDALIB_AVAIL = True
    _IDALIB_ERR = ''
except Exception as _ex:  # pragma: no cover
    idapro = None
    _IDALIB_AVAIL = False
    _IDALIB_ERR = str(_ex)


class IdaError(Exception):
    """idalib 封装层错误"""


class IdaClient:
    """对 IDA（idalib）的封装，全部方法返回可 JSON 序列化的对象。"""

    def __init__(self, workdir: str = ''):
        """
        Args:
            workdir: open_database 时使用的可写工作目录（数据库 .i64 存放处）。
        """
        if not _IDALIB_AVAIL:
            raise IdaError(f'idapro/idalib 不可用：{_IDALIB_ERR}')
        self.workdir = path.abspath(workdir or '.')
        os.makedirs(self.workdir, exist_ok=True)
        self.db_open = False
        self.last_error = ''
        self.input_path = ''
        # 延迟加载 IDA 模块（open_database 后才可用）
        self._modules = {}

    # ── 生命周期 ──────────────────────────────────────────────

    def _chdir(self):
        """切到工作目录（idalib 在当前目录创建 .i64）。"""
        try:
            os.chdir(self.workdir)
        except Exception as ex:
            raise IdaError(f'无法切换到工作目录 {self.workdir}：{ex}')

    def open_database(self, file_name: str, run_auto_analysis: bool = True) -> bool:
        """打开二进制并跑自动分析。返回是否成功。"""
        try:
            self._chdir()
            rc = idapro.open_database(
                file_name, run_auto_analysis=run_auto_analysis)
            if rc != 0:
                self.last_error = f'open_database 失败，返回码 {rc}'
                logger.warning(self.last_error)
                return False
            self.db_open = True
            self.input_path = file_name
            self.last_error = ''
            return True
        except Exception as ex:
            self.last_error = f'open_database 异常：{ex}'
            logger.warning(self.last_error)
            return False

    def close_database(self, save: bool = True) -> bool:
        """关闭数据库。save 是否保存 .i64。"""
        try:
            if self.db_open:
                idapro.close_database(save=save)
            self.db_open = False
            self.input_path = ''
            return True
        except Exception as ex:
            self.last_error = f'close_database 异常：{ex}'
            logger.warning(self.last_error)
            return False

    def _mod(self, name: str):
        """按需 import IDA 模块（lazy，避免在未 open_database 时导入失败）。"""
        if name in self._modules:
            return self._modules[name]
        if not self.db_open:
            raise IdaError('数据库未打开。请先调用 tool_ida_open_database。')
        try:
            mod = __import__('ida_%s' % name, fromlist=['*'])
        except ImportError:
            try:
                mod = __import__(name, fromlist=['*'])
            except ImportError as ex:
                raise IdaError(f'IDA 模块 {name} 不可用：{ex}')
        self._modules[name] = mod
        return mod

    def _require_open(self):
        if not self.db_open:
            raise IdaError('数据库未打开。请先调用 tool_ida_open_database。')

    # ── 状态 ──────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """当前 idalib 状态。"""
        if not self.db_open:
            return {'connected': False, 'error': self.last_error or '未打开数据库'}
        try:
            import ida_funcs
            return {
                'connected': True,
                'input': self.input_path,
                'func_qty': int(ida_funcs.get_func_qty()),
            }
        except Exception as ex:
            return {'connected': True, 'input': self.input_path,
                    'error': f'状态查询失败：{ex}'}

    # ── 函数枚举 ──────────────────────────────────────────────

    def list_functions(self, count: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """列出函数（名称+起止地址+大小）。"""
        self._require_open()
        try:
            ida_funcs = self._mod('funcs')
            fns = []
            ea = ida_funcs.get_next_func_ea(0)
            skip = offset
            while ea != -1 and len(fns) < count:
                if skip > 0:
                    skip -= 1
                else:
                    f = ida_funcs.get_func(ea)
                    end = f.end_ea if f else ea
                    fns.append({
                        'start': ea,
                        'start_hex': hex(ea),
                        'end': end,
                        'name': ida_funcs.get_func_name(ea) or '',
                    })
                ea = ida_funcs.get_next_func_ea(ea)
            return fns
        except Exception as ex:
            self.last_error = f'list_functions 失败：{ex}'
            return []

    def get_function_by_name(self, name: str) -> Dict[str, Any]:
        """按名称查函数（若 name 是地址串也支持）。"""
        self._require_open()
        try:
            ida_funcs = self._mod('funcs')
            ida_name = self._mod('name')
            ea = None
            # 先尝试地址
            try:
                cand = int(str(name), 0)
                f = ida_funcs.get_func(cand)
                if f:
                    ea = f.start_ea
            except Exception:
                pass
            # 再按名称
            if ea is None:
                ea = self._resolve_ea(name)
            if ea is None:
                return {}
            f = ida_funcs.get_func(ea)
            return {
                'ea': ea,
                'ea_hex': hex(ea),
                'name': ida_funcs.get_func_name(ea) or ida_name.get_name(ea) or '',
                'start': f.start_ea if f else ea,
                'end': f.end_ea if f else ea,
            }
        except Exception as ex:
            self.last_error = f'get_function_by_name 失败：{ex}'
            return {}

    def get_function_by_address(self, address: str) -> Dict[str, Any]:
        """按地址查函数。"""
        self._require_open()
        try:
            ea = int(str(address), 0)
            ida_funcs = self._mod('funcs')
            ida_name = self._mod('name')
            f = ida_funcs.get_func(ea)
            if not f:
                return {}
            start = f.start_ea
            return {
                'ea': start,
                'ea_hex': hex(start),
                'name': ida_funcs.get_func_name(start) or ida_name.get_name(start) or '',
                'start': start,
                'end': f.end_ea,
            }
        except Exception as ex:
            self.last_error = f'get_function_by_address 失败：{ex}'
            return {}

    # ── 反编译 / 反汇编 ──────────────────────────────────────

    def decompile_function(self, address_or_name: str) -> str:
        """反编译函数（Hex-Rays），返回伪代码。"""
        self._require_open()
        try:
            ida_hexrays = self._mod('hexrays')
            ida_funcs = self._mod('funcs')
            ea = self._resolve_ea(address_or_name)
            if ea is None:
                return f'找不到目标：{address_or_name}'
            try:
                ida_hexrays.init_hexrays_plugin()
            except Exception:
                pass
            cf = ida_hexrays.decompile(ea)
            if not cf:
                return f'反编译失败（无结果）：{address_or_name}'
            return str(cf)
        except Exception as ex:
            self.last_error = f'decompile_function 失败：{ex}'
            return f'反编译异常：{ex}'

    def disassemble_function(self, address_or_name: str) -> str:
        """反汇编整个函数（逐条指令）。"""
        self._require_open()
        try:
            ida_funcs = self._mod('funcs')
            idc = __import__('idc', fromlist=['*'])
            ea = self._resolve_ea(address_or_name)
            if ea is None:
                return f'找不到目标：{address_or_name}'
            f = ida_funcs.get_func(ea)
            if not f:
                # 单指令
                return idc.generate_disasm_line(ea, 0)
            lines = []
            a = f.start_ea
            while a < f.end_ea and len(lines) < 500:
                line = idc.generate_disasm_line(a, 0)
                lines.append(f'{a:#x}  {line}')
                size = idc.get_item_size(a) or 1
                a += size
            return '\n'.join(lines)
        except Exception as ex:
            self.last_error = f'disassemble_function 失败：{ex}'
            return f'反汇编异常：{ex}'

    def disassemble_at(self, address: str, count: int = 1) -> str:
        """从地址反汇编 count 条指令。"""
        self._require_open()
        try:
            idc = __import__('idc', fromlist=['*'])
            ea = int(str(address), 0)
            lines = []
            a = ea
            for _ in range(max(1, int(count or 1))):
                line = idc.generate_disasm_line(a, 0)
                if not line:
                    break
                lines.append(f'{a:#x}  {line}')
                size = idc.get_item_size(a) or 1
                a += size
            return '\n'.join(lines)
        except Exception as ex:
            self.last_error = f'disassemble_at 失败：{ex}'
            return f'反汇编异常：{ex}'

    def _resolve_ea(self, address_or_name: str) -> Optional[int]:
        """把地址串或名称解析为有效 EA。"""
        try:
            cand = int(str(address_or_name), 0)
            return cand
        except Exception:
            pass
        try:
            idc = __import__('idc', fromlist=['*'])
            ea = idc.get_name_ea_simple(str(address_or_name))
            if ea and ea != -1:
                return ea
        except Exception:
            pass
        return None

    # ── 字符串 / 导入 ────────────────────────────────────────

    def list_strings(self, filter: str = '', count: int = 200) -> List[Dict[str, Any]]:
        """列出字符串（通过 ida_strlist + ida_bytes.get_strlit_contents）。"""
        self._require_open()
        try:
            sl = self._mod('strlist')
            bts = self._mod('bytes')
            n = sl.get_strlist_qty()
            out = []
            for i in range(min(n, count)):
                si = sl.string_info_t()
                if not sl.get_strlist_item(si, i):
                    continue
                text = ''
                try:
                    s = bts.get_strlit_contents(si.ea, si.length, si.type)
                    text = s.decode('utf8', 'replace') if isinstance(s, bytes) else (s or '')
                except Exception:
                    try:
                        data = bts.get_bytes(si.ea, si.length)
                        text = data.split(b'\x00')[0].decode('utf8', 'replace') if data else ''
                    except Exception:
                        text = ''
                if filter and filter.lower() not in text.lower():
                    continue
                out.append({'ea': si.ea, 'ea_hex': hex(si.ea), 'text': text})
            return out
        except Exception as ex:
            self.last_error = f'list_strings 失败：{ex}'
            return []

    def list_imports(self, count: int = 200) -> List[Dict[str, Any]]:
        """列出导入函数（IDA 9：使用 ida_nalt 的导入 API）。"""
        self._require_open()
        try:
            nalt = self._mod('nalt')
            ida_name = self._mod('name')
            out = []
            n_mods = nalt.get_import_module_qty()
            for mi in range(n_mods):
                mod_name = nalt.get_import_module_name(mi)
                if not mod_name:
                    continue

                def cb(ea, name, ord_):
                    if len(out) >= count:
                        return False
                    out.append({'module': mod_name, 'ea': ea, 'ea_hex': hex(ea),
                                'name': name or ''})
                    return True

                try:
                    nalt.enum_import_names(mi, cb)
                except Exception as ex:
                    logger.warning('enum_import_names 失败：%s', ex)
                    continue
            return out
        except Exception as ex:
            self.last_error = f'list_imports 失败：{ex}'
            return []

    # ── 交叉引用 ──────────────────────────────────────────────

    def get_xrefs_to(self, address_or_name: str) -> List[Dict[str, Any]]:
        """查询对指定地址的交叉引用（含数据/代码引用）。"""
        self._require_open()
        try:
            xref = self._mod('xref')
            ida_funcs = self._mod('funcs')
            bad = getattr(xref, 'BADADDR', -1)
            ea = self._resolve_ea(address_or_name)
            if ea is None:
                return []
            out = []
            seen = set()
            guard = 0
            # 代码引用
            x = xref.get_first_cref_to(ea)
            while x != bad and x != -1 and guard < 500:
                guard += 1
                if x not in seen:
                    seen.add(x)
                    out.append({'from': x, 'from_hex': hex(x), 'type': 'code',
                                'func': ida_funcs.get_func_name(x) or ''})
                x = xref.get_next_cref_to(ea, x)
            # 数据引用
            try:
                guard = 0
                x = xref.get_first_dref_to(ea)
                while x != bad and x != -1 and guard < 500:
                    guard += 1
                    if x not in seen:
                        seen.add(x)
                        out.append({'from': x, 'from_hex': hex(x), 'type': 'data',
                                    'func': ida_funcs.get_func_name(x) or ''})
                    x = xref.get_next_dref_to(ea, x)
            except Exception:
                pass
            return out
        except Exception as ex:
            self.last_error = f'get_xrefs_to 失败：{ex}'
            return []

    def get_callers(self, address_or_name: str) -> List[Dict[str, Any]]:
        """查询调用指定地址的函数（代码交叉引用）。"""
        self._require_open()
        try:
            xref = self._mod('xref')
            ida_funcs = self._mod('funcs')
            bad = getattr(xref, 'BADADDR', -1)
            ea = self._resolve_ea(address_or_name)
            if ea is None:
                return []
            out = []
            seen = set()
            guard = 0
            x = xref.get_first_cref_to(ea)
            while x != bad and x != -1 and guard < 500:
                guard += 1
                if x not in seen:
                    seen.add(x)
                    out.append({'caller': x, 'caller_hex': hex(x),
                                'func': ida_funcs.get_func_name(x) or ''})
                x = xref.get_next_cref_to(ea, x)
            return out
        except Exception as ex:
            self.last_error = f'get_callers 失败：{ex}'
            return []

    def get_callees(self, address_or_name: str) -> List[Dict[str, Any]]:
        """查询指定函数调用的所有目标（通过反汇编找 call/jmp）。"""
        self._require_open()
        try:
            ida_funcs = self._mod('funcs')
            idc = __import__('idc', fromlist=['*'])
            ea = self._resolve_ea(address_or_name)
            if ea is None:
                return []
            f = ida_funcs.get_func(ea)
            if not f:
                return []
            out = []
            seen = set()
            a = f.start_ea
            guard = 0
            while a < f.end_ea and guard < 2000:
                guard += 1
                try:
                    mnem = idc.print_insn_mnem(a)
                except Exception:
                    break
                if mnem in ('call', 'jmp'):
                    try:
                        target = idc.get_operand_value(a, 0)
                    except Exception:
                        target = 0
                    if target and target not in seen and target > 0x10000:
                        seen.add(target)
                        out.append({
                            'target': target,
                            'target_hex': hex(target),
                            'name': ida_funcs.get_func_name(target) or '',
                            'mnem': mnem,
                            'from': a,
                        })
                try:
                    size = idc.get_item_size(a) or 1
                except Exception:
                    size = 1
                a += size
            return out
        except Exception as ex:
            self.last_error = f'get_callees 失败：{ex}'
            return []

    # ── 标签 / 注释 / 重命名 ─────────────────────────────────

    def get_name(self, address: str) -> str:
        """读取地址处的名称。"""
        self._require_open()
        try:
            ida_name = self._mod('name')
            ea = int(str(address), 0)
            return ida_name.get_name(ea) or ''
        except Exception as ex:
            self.last_error = f'get_name 失败：{ex}'
            return ''

    def set_name(self, address: str, name: str) -> bool:
        """重命名地址（设标签）。"""
        self._require_open()
        try:
            ida_name = self._mod('name')
            ea = int(str(address), 0)
            return bool(ida_name.set_name(ea, name))
        except Exception as ex:
            self.last_error = f'set_name 失败：{ex}'
            return False

    def get_comment(self, address: str) -> str:
        """读取地址处注释。"""
        self._require_open()
        try:
            idaapi = __import__('idaapi', fromlist=['*'])
            ea = int(str(address), 0)
            c = idaapi.get_cmt(ea, 0)
            return c or ''
        except Exception as ex:
            self.last_error = f'get_comment 失败：{ex}'
            return ''

    def set_comment(self, address: str, comment: str) -> bool:
        """设置地址处注释。"""
        self._require_open()
        try:
            idaapi = __import__('idaapi', fromlist=['*'])
            ea = int(str(address), 0)
            return bool(idaapi.set_cmt(ea, comment, 0))
        except Exception as ex:
            self.last_error = f'set_comment 失败：{ex}'
            return False

    # ── 内存 / 数据读取 ──────────────────────────────────────

    def read_bytes(self, address: str, size: int = 16) -> str:
        """读取内存字节并以 hex 输出。"""
        self._require_open()
        try:
            ida_bytes = self._mod('bytes')
            ea = int(str(address), 0)
            data = ida_bytes.get_bytes(ea, max(1, int(size)))
            if not data:
                return f'读取失败：{address}'
            return _hexdump(data)
        except Exception as ex:
            self.last_error = f'read_bytes 失败：{ex}'
            return f'读取异常：{ex}'

    def read_dword(self, address: str) -> int:
        try:
            self._require_open()
            ida_bytes = self._mod('bytes')
            ea = int(str(address), 0)
            return int(ida_bytes.get_dword(ea))
        except Exception as ex:
            self.last_error = f'read_dword 失败：{ex}'
            return 0

    def read_qword(self, address: str) -> int:
        try:
            self._require_open()
            ida_bytes = self._mod('bytes')
            ea = int(str(address), 0)
            return int(ida_bytes.get_qword(ea))
        except Exception as ex:
            self.last_error = f'read_qword 失败：{ex}'
            return 0

    def get_segments(self) -> List[Dict[str, Any]]:
        """列出程序段（segments）。"""
        self._require_open()
        try:
            ida_segment = self._mod('segment')
            out = []
            n = ida_segment.get_segm_qty()
            for i in range(n):
                s = ida_segment.getnseg(i)
                out.append({
                    'start': s.start_ea,
                    'start_hex': hex(s.start_ea),
                    'end': s.end_ea,
                    'name': ida_segment.get_segm_name(s) or '',
                })
            return out
        except Exception as ex:
            self.last_error = f'get_segments 失败：{ex}'
            return []


def _hexdump(data: bytes) -> str:
    """字节 → 十六进制转储文本。"""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = ' '.join(f'{b:02x}' for b in chunk).ljust(47)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        lines.append(f'{i:08x}  {hexs}  |{asc}|')
    return '\n'.join(lines)
# -*- coding: utf-8 -*-

"""re 子命令的 MCP 桥接层：连接 IDA / x64dbg / x32dbg 的 MCP 服务器。

支持两种传输：
- stdio（NDJSON 逐行 JSON-RPC）：IDA MCP 服务器（zeromcp 帧格式）。
- streamable-http（POST JSON-RPC，可选 Mcp-Session-Id）：x64dbg / x32dbg。

服务器配置默认取自 ~/.claude.json 的 mcpServers，也可用命令行参数覆盖。
工具名保持与 MCP tools/list 一致；本模块只负责连接、列工具、调用返回。
"""

import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from os import path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_MCP_DEFAULT_CONFIG = path.expanduser('~/.claude.json')


class McpError(Exception):
    """MCP 桥接错误"""


# ── 服务器配置解析 ──────────────────────────────────────────────

def load_mcp_servers(args) -> Dict[str, Dict[str, Any]]:
    """从 args 解析要连接的 MCP 服务器。

    优先级：argparse 显式覆盖 > mcp-config 文件(~/.claude.json 缺省)的 mcpServers。
    返回 {server_key: spec}；spec 形如：
      {'type':'stdio','command':'...','args':[...]}    # IDA
      {'type':'http','url':'...','headers':{...}}       # x64dbg/x32dbg
    """
    servers: Dict[str, Dict[str, Any]] = {}

    # 读取配置文件
    cfg_path = getattr(args, 'mcp_config', '') or _MCP_DEFAULT_CONFIG
    cfg = {}
    if os.path.isfile(cfg_path):
        try:
            cfg = json.loads(open(cfg_path, encoding='utf-8').read()) or {}
        except Exception as ex:
            logger.warning('读取 MCP 配置 %s 失败：%s', cfg_path, ex)
    mcp = cfg.get('mcpServers') or {}

    # ---- IDA（stdio：ida-pro-mcp 服务器） ----
    if not getattr(args, 'no_ida', False):
        ida_cmd = getattr(args, 'idacmd', '') or ''
        ida_args = getattr(args, 'idaargs', '') or ''
        if ida_cmd:
            args_list = [a for a in ida_args.split() if a] if isinstance(ida_args, str) else list(ida_args or [])
            servers['ida'] = {'type': 'stdio', 'command': ida_cmd, 'args': args_list}
        elif 'ida-pro-mcp' in mcp:
            spec = mcp['ida-pro-mcp']
            cmd = spec.get('command') or ''
            if cmd:
                s = {'type': 'stdio', 'command': cmd,
                     'args': list(spec.get('args') or [])}
                if spec.get('env'):
                    s['env'] = dict(spec['env'])
                servers['ida'] = s
            elif spec.get('url'):
                servers['ida'] = {
                    'type': 'http', 'url': spec['url'],
                    'headers': dict(spec.get('headers') or {}),
                }

    # ---- x64dbg ----
    x64_url = getattr(args, 'x64dbgurl', '') or ''
    x64_tok = getattr(args, 'x64dbgtoken', '') or ''
    if not getattr(args, 'no_x64dbg', False):
        if x64_url:
            headers = {'Authorization': f'Bearer {x64_tok}'} if x64_tok else {}
            servers['x64dbg'] = {'type': 'http', 'url': x64_url, 'headers': headers}
        elif 'x64dbg' in mcp:
            spec = mcp['x64dbg']
            if not spec.get('disabled') and spec.get('url'):
                servers['x64dbg'] = {
                    'type': 'http', 'url': spec['url'],
                    'headers': dict(spec.get('headers') or {}),
                }

    # ---- x32dbg ----
    x32_url = getattr(args, 'x32dbgurl', '') or ''
    x32_tok = getattr(args, 'x32dbgtoken', '') or ''
    if not getattr(args, 'no_x32dbg', False):
        if x32_url:
            headers = {'Authorization': f'Bearer {x32_tok}'} if x32_tok else {}
            servers['x32dbg'] = {'type': 'http', 'url': x32_url, 'headers': headers}
        elif 'x32dbg' in mcp:
            spec = mcp['x32dbg']
            if not spec.get('disabled') and spec.get('url'):
                servers['x32dbg'] = {
                    'type': 'http', 'url': spec['url'],
                    'headers': dict(spec.get('headers') or {}),
                }

    return servers


# ── MCP 客户端 ─────────────────────────────────────────────────

def _loads_msg(line: bytes) -> Optional[Dict[str, Any]]:
    """解析一行 JSON-RPC 消息；失败返回 None。"""
    try:
        msg = json.loads(line.decode('utf-8', 'replace'))
        return msg if isinstance(msg, dict) else None
    except Exception:
        return None


class McpClient:
    """单个 MCP 服务器的同步客户端（stdio 或 streamable-http）。"""

    def __init__(self, key: str, spec: Dict[str, Any], connect_timeout: float = 30.0):
        self.key = key
        self.spec = spec
        self.connect_timeout = connect_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._http: Optional[requests.Session] = None
        self._q: 'queue.Queue[Optional[Dict[str, Any]]]' = queue.Queue()
        self._lock = threading.Lock()
        self._id = 0
        self._session_id = ''
        self.tools: List[Dict[str, Any]] = []
        self.last_error = ''

    # ── 生命周期 ────────────────────────────────────────────────

    def connect(self) -> None:
        """建立连接并完成 initialize 握手。"""
        typ = self.spec.get('type', 'stdio')
        try:
            if typ == 'http':
                self._connect_http()
            else:
                self._connect_stdio()
            self._rpc('initialize', {
                'protocolVersion': '2025-03-26',
                'capabilities': {},
                'clientInfo': {'name': 'BookerGptTool-re', 'version': '1.0.0'},
            }, timeout=self.connect_timeout)
            self._notify('notifications/initialized', {})
        except Exception as ex:
            self.last_error = f'{self.key} 初始化失败：{ex}'
            self.close()
            raise McpError(self.last_error) from ex

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    # ── 传输建立 ────────────────────────────────────────────────

    def _connect_stdio(self) -> None:
        cmd = self.spec.get('command') or ''
        args = list(self.spec.get('args') or [])
        if not cmd:
            raise McpError(f'{self.key} stdio 缺 command')
        env = os.environ.copy()
        env.update(self.spec.get('env') or {})
        err_fname = path.join(path.expanduser('~'), f'{self.key}_mcp_stderr.log')
        try:
            self._proc = subprocess.Popen(
                [cmd] + args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=open(err_fname, 'a', encoding='utf-8', errors='replace'),
                env=env,
            )
        except Exception as ex:
            raise McpError(f'{self.key} 启动失败：{ex}') from ex
        threading.Thread(target=self._read_stdio, daemon=True).start()

    def _read_stdio(self) -> None:
        """后台线程：持续读取 stdout，把行消息放入队列。"""
        try:
            while self._proc is not None and self._proc.poll() is None:
                line = self._proc.stdout.readline()
                if not line:
                    break
                if line.strip():
                    msg = _loads_msg(line)
                    if msg is not None:
                        self._q.put(msg)
        except Exception:
            pass
        finally:
            try:
                self._q.put(None)
            except Exception:
                pass

    def _connect_http(self) -> None:
        url = self.spec.get('url') or ''
        if not url:
            raise McpError(f'{self.key} http 缺 url')
        self._http = requests.Session()

    # ── JSON-RPC 调用 ───────────────────────────────────────────

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: Optional[float] = None) -> Dict[str, Any]:
        """发起请求并等待响应，返回 result；错误抛 McpError。"""
        if self._proc is None and self._http is None:
            raise McpError(f'{self.key} 未连接')
        timeout = timeout or self.connect_timeout
        with self._lock:
            self._id += 1
            rid = self._id
            payload = {'jsonrpc': '2.0', 'id': rid, 'method': method}
            if params:
                payload['params'] = params
            if self._http is not None:
                msg = self._http_post(payload, timeout)
            else:
                self._proc.stdin.write((json.dumps(payload) + '\n').encode('utf-8'))
                try:
                    self._proc.stdin.flush()
                except Exception:
                    pass
                msg = self._recv(rid, timeout)
        return self._unwrap(msg, method)

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """发送通知（无响应）。"""
        if self._proc is None and self._http is None:
            return
        payload = {'jsonrpc': '2.0', 'method': method}
        if params:
            payload['params'] = params
        with self._lock:
            if self._http is not None:
                try:
                    self._http_post(payload, 15.0, notify=True)
                except Exception:
                    pass
            else:
                try:
                    self._proc.stdin.write((json.dumps(payload) + '\n').encode('utf-8'))
                    self._proc.stdin.flush()
                except Exception:
                    pass

    def _recv(self, rid: int, timeout: float) -> Dict[str, Any]:
        """从队列取回 id 匹配的响应；超时抛错。"""
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise McpError(f'{self.key} 请求 {rid} 超时')
            try:
                msg = self._q.get(timeout=remain)
            except queue.Empty:
                continue
            if msg is None:
                raise McpError(f'{self.key} 连接已关闭（进程退出）')
            mid = msg.get('id')
            if mid is None:
                continue  # 服务器主动通知，忽略
            if mid != rid:
                continue
            return msg

    def _http_post(self, payload: Dict[str, Any], timeout: float,
                   notify: bool = False) -> Optional[Dict[str, Any]]:
        """HTTP 传输：POST 一条消息，解析 JSON 或 SSE 响应。"""
        url = self.spec.get('url') or ''
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        headers.update(self.spec.get('headers') or {})
        if self._session_id:
            headers['Mcp-Session-Id'] = self._session_id
        resp = self._http.post(
            url, data=json.dumps(payload).encode('utf-8'),
            headers=headers, timeout=timeout,
        )
        # 记录服务端下发的会话 id（streamable-http 用）
        sid = resp.headers.get('Mcp-Session-Id')
        if sid:
            self._session_id = sid
        if notify:
            return None
        ctype = (resp.headers.get('Content-Type') or '').lower()
        text = resp.text or ''
        if 'text/event-stream' in ctype or text.lstrip().startswith('data:'):
            msg = self._parse_sse(text)
        else:
            try:
                msg = json.loads(text)
            except Exception as ex:
                raise McpError(f'{self.key} 非法 JSON 响应：{text[:300]} ({ex})') from ex
        return msg if isinstance(msg, dict) else None

    @staticmethod
    def _parse_sse(text: str) -> Optional[Dict[str, Any]]:
        """解析 SSE data: 行，取最后一条 JSON-RPC 消息。"""
        msg = None
        buf = []
        for line in text.splitlines():
            line = line.rstrip('\r')
            if line.startswith('data:'):
                data = line[5:].strip()
                if data:
                    buf.append(data)
                elif buf:
                    try:
                        msg = json.loads('\n'.join(buf))
                    except Exception:
                        msg = None
                    buf = []
        if buf:
            try:
                msg = json.loads('\n'.join(buf))
            except Exception:
                msg = None
        return msg if isinstance(msg, dict) else None

    @staticmethod
    def _unwrap(msg: Optional[Dict[str, Any]], method: str) -> Dict[str, Any]:
        if msg is None:
            raise McpError(f'响应为空：{method}')
        if 'error' in msg and msg['error']:
            err = msg['error']
            raise McpError(f'{method} 错误：{err.get("message") or err}')
        result = msg.get('result')
        return result if isinstance(result, dict) else (result or {})

    # ── 高层工具接口 ────────────────────────────────────────────

    def list_tools(self, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
        """拉取该服务器的工具列表。"""
        result = self._rpc('tools/list', timeout=timeout)
        tools = result.get('tools') or []
        self.tools = [t for t in tools if isinstance(t, dict)]
        return self.tools

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  timeout: Optional[float] = None) -> Dict[str, Any]:
        """调用工具，返回规范化结果。"""
        timeout = timeout or self.connect_timeout
        result = self._rpc('tools/call', {'name': name, 'arguments': arguments or {}},
                           timeout=timeout)
        # 规范化 content -> 文本
        parts = []
        for block in result.get('content') or []:
            if not isinstance(block, dict):
                continue
            typ = block.get('type')
            if typ == 'text':
                parts.append(str(block.get('text') or ''))
            elif typ in ('image', 'audio', 'resource'):
                parts.append(f'[{typ}]')
        output = '\n'.join(parts)
        structured = result.get('structuredContent')
        return {
            'ok': not bool(result.get('isError')),
            'server': self.key,
            'tool': name,
            'output': output,
            'structured': structured,
            'error': output if result.get('isError') else '',
        }

    def status(self) -> Dict[str, Any]:
        return {
            'connected': self._proc is not None or self._http is not None,
            'tool_count': len(self.tools),
            'error': self.last_error,
        }
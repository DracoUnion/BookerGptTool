# -*- coding: utf-8 -*-

"""re 子命令工具集：基础工具 + IDA MCP 桥接 + x64dbg/x32dbg 直连工具。

- 基础工具与工作区工具继承自 ToolsMixin。
- IDA：通过 MCP（stdio）桥接，动态注册 tool_mcp_ida_*。
- x64dbg / x32dbg：直接使用 x64dbg_automate 库（ZeroMQ），不经过 MCP。
  为两个目标各生成一组 tool_x64_* / tool_x32_* 静态工具（工厂方法 + 静态 Schema）。
"""

import json
import logging
import os
import re
import subprocess
import sys
import traceback
from os import path
from typing import Any, Callable, Dict, List, Optional, Type

from .util import (
    gen_objs_md5, read_yaml_model, write_yaml_model, read_text, write_text,
    render_prompt, json_dump_model, ext_code_block,
)
from .openai import *
from .re_models import *
from .re_pmt import *
from . import re_mcp as re_mcp_mod
from . import re_xauto as re_xauto_mod

logger = logging.getLogger(__name__)


def _norm_safe(name: str) -> str:
    """把 MCP 工具名转为合法下划线标识符片段（保留字母数字下划线）。"""
    s = re.sub(r'[^\w]', '_', name)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'tool'


class ReTools(ToolsMixin):
    """逆向工程智能体工具集。

    基础工具与工作区工具继承自 ToolsMixin；
    IDA 用 MCP 桥接（动态注册）；x64dbg/x32dbg 用 x64dbg_automate 直连（静态工具）。
    """

    # 默认项目工作目录
    pj_dir = '.'

    def __init__(self, args):
        """初始化：保存参数、配置 OpenAI、连接 IDA MCP 与 x64dbg/x32dbg。"""
        super(ToolsMixin, self).__init__()
        set_openai_props(args)
        self.args = args
        self.model = getattr(args, 'model', '')
        self.retry = getattr(args, 'retry', 3)
        self.temperature = getattr(args, 'temp', 0.0)
        self.max_tokens = getattr(args, 'max_tokens', 4000)
        self.stream = getattr(args, 'stream', False)
        self.project_id = getattr(args, 'project', None) or 're_project'
        self.pj_dir = path.abspath(self.project_id)
        os.makedirs(self.pj_dir, exist_ok=True)

        # IDA MCP 客户端
        self.mcp_clients: Dict[str, re_mcp_mod.McpClient] = {}
        self._dyn_tool_names: List[str] = []
        # 已注册的 xauto 工具名（实例属性，避免类属性共享）
        self._xauto_tool_names: List[str] = []

        # x64dbg / x32dbg 直连客户端（x64dbg_automate）
        self.xauto: Dict[str, re_xauto_mod.XautoClient] = {}
        if getattr(args, 'no_xauto', False):
            logger.info('已禁用 x64dbg/x32dbg（-no-xauto）')
        else:
            x64_path = getattr(args, 'x64dbg_path', '') or ''
            x32_path = getattr(args, 'x32dbg_path', '') or ''
            for target, xpath in (('x64dbg', x64_path), ('x32dbg', x32_path)):
                try:
                    self.xauto[target] = re_xauto_mod.XautoClient(target, x64dbg_path=xpath)
                except Exception as ex:
                    logger.warning('%s 封装初始化失败：%s', target, ex)

        self._connect_mcp(args)
        self._register_xauto_tools()

    # ══════════════════════════════════════════════════════════
    # IDA MCP 连接（动态工具）
    # ══════════════════════════════════════════════════════════

    def _connect_mcp(self, args) -> None:
        """按配置连接 IDA MCP 服务器并发现工具。"""
        servers = re_mcp_mod.load_mcp_servers(args)
        for key, spec in servers.items():
            if key != 'ida':
                continue  # 只保留 IDA；x64dbg/x32dbg 走 xauto
            client = re_mcp_mod.McpClient(key, spec)
            try:
                client.connect()
                self.mcp_clients[key] = client
            except Exception as ex:
                logger.warning('MCP[%s] 连接失败：%s', key, ex)
                continue
            try:
                tools = client.list_tools()
                logger.info('MCP[%s] 已连接，发现 %d 个工具', key, len(tools))
            except Exception as ex:
                logger.warning('MCP[%s] 已连接，但工具枚举失败：%s', key, ex)
        self._register_dyn_tools()

    def _register_dyn_tools(self) -> None:
        """为每个 MCP 服务器的每个工具注册 tool_mcp_<server>_<name> 包装方法。"""
        for key, client in self.mcp_clients.items():
            prefix = _norm_safe(key)
            for t in client.tools:
                name = str(t.get('name') or '')
                if not name:
                    continue
                safe = _norm_safe(name)
                meth_name = f'tool_mcp_{prefix}_{safe}'
                if hasattr(self, meth_name):
                    continue
                wrapper = self._make_wrapper(key, name, t)
                wrapper.__re_schema__ = t
                setattr(self, meth_name, wrapper)
                self._dyn_tool_names.append(meth_name)

    def _make_wrapper(self, server: str, tool_name: str, schema: Dict[str, Any]):
        """构造动态工具方法：调用 MCP tools/call 并返回规范化结果。"""
        description = str(schema.get('description') or f'调用 {server} 的 {tool_name}')
        def _wrap(**kwargs):
            client = self.mcp_clients.get(server)
            if client is None:
                return MCPToolResult(
                    ok=False, server=server, tool=tool_name,
                    error=f'{server} 未连接',)
            try:
                res = client.call_tool(tool_name, kwargs or None)
                return MCPToolResult(
                    ok=res['ok'], server=server, tool=tool_name,
                    output=res['output'], structured=res['structured'],
                    error=res['error'],)
            except Exception as ex:
                return MCPToolResult(
                    ok=False, server=server, tool=tool_name,
                    error=f'{tool_name} 调用失败：{ex}',)
        _wrap.__name__ = f'tool_mcp_{_norm_safe(server)}_{_norm_safe(tool_name)}'
        _wrap.__doc__ = description
        _wrap.__re_schema__ = schema
        return _wrap

    # ══════════════════════════════════════════════════════════
    # x64dbg/x32dbg 静态工具（x64dbg_automate 直连）
    # ══════════════════════════════════════════════════════════

    # 每个静态工具的委托目标方法名
    _XTOOL_DELEGATES = {
        'x64': 'x64dbg',
        'x32': 'x32dbg',
    }

    def _register_xauto_tools(self) -> None:
        """为 x64dbg/x32dbg 生成 tool_x64_* / tool_x32_* 工具方法。

        方法名：tool_{prefix}_{op}，其中 op 见 _XTOOL_OPS（每项 = XautoClient 方法名）。
        """
        for prefix, target in self._XTOOL_DELEGATES.items():
            for op, opname in _XTOOL_OPS.items():
                meth = f'tool_{prefix}_{op}'
                if hasattr(self, meth):
                    continue
                setattr(self, meth, self._make_xauto_wrapper(target, opname, prefix, op))
                self._xauto_tool_names.append(meth)

    def _make_xauto_wrapper(self, target: str, opname: str, prefix: str, op: str):
        """构造 x64dbg/x32dbg 工具方法：委托到 self.xauto[target] 的 opname 方法。"""
        def _wrap(**kwargs):
            client = self.xauto.get(target)
            if client is None:
                return {'error': f'{target} 未初始化（库不可用或已禁用）'}
            try:
                fn = getattr(client, opname)
                res = fn(**kwargs)
                if isinstance(res, dict) and 'error' not in res:
                    res['_target'] = target
                return res
            except Exception as ex:
                return {'error': f'{target}.{opname} 异常：{ex}'}
        _wrap.__name__ = f'tool_{prefix}_{op}'
        doc = _XTOOL_DOCS.get(op, f'对 {target} 执行 {opname}')
        _wrap.__doc__ = doc
        return _wrap

    # 已注册的 xauto 工具名由 __init__ 中的 self._xauto_tool_names 维护

    # ══════════════════════════════════════════════════════════
    # 基础工具
    # ══════════════════════════════════════════════════════════

    def _render(self, pmt: str, **kw) -> str:
        """渲染提示词；统一注入 _JSON_OUT 输出格式段。"""
        kw.setdefault('_JSON_OUT', _JSON_OUT)
        return pmt.format(**kw)

    @staticmethod
    def _parse_to(model: Type[BaseModel]) -> Callable:
        """构造解析回调：去掉 ``` 围栏后按 pydantic 模型解析。"""
        def f(s: str) -> BaseModel:
            t = s.strip()
            if t.startswith('```'):
                t = ext_code_block(t)
            return model.model_validate_json(t)
        return f

    def _call_llm(self, pmt: str, out_model: Type[BaseModel], *,
                  max_tokens: Optional[int] = None, **kw) -> BaseModel:
        """调用 LLM 生成结构化结果，带文件缓存与 AI 标记。"""
        user = self._render(pmt, **kw)
        cache_fname = path.join(
            self.pj_dir, f't_{out_model.__name__.lower()}_{gen_objs_md5(user)}.yaml')
        r = read_yaml_model(cache_fname, out_model)
        if r:
            return r
        mt = max_tokens if max_tokens is not None else self.max_tokens
        res = call_llm_retry(
            user, self.model,
            retry=self.retry, temp=self.temperature,
            max_tokens=mt,
            parse_output=self._parse_to(out_model))
        write_yaml_model(cache_fname, res)
        return res

    def _state_fname(self) -> str:
        return path.join(self.pj_dir, 're_state.yaml')

    def tool_load_state(self) -> ReState:
        """读取当前任务状态机。"""
        st = read_yaml_model(self._state_fname(), ReState)
        if st:
            return st
        return ReState(project_id=self.project_id, goal=getattr(self.args, 'goal', ''))

    def tool_update_state(self, stage: str = '',
                          goal: str = '', notes: Optional[List[str]] = None,
                          artifacts: Optional[List[str]] = None,
                          next_action: str = '') -> ReState:
        """更新任务状态机，返回完整新状态。每轮收到工具结果后调用。"""
        st = self.tool_load_state()
        if stage:
            st.stage = stage
        if goal:
            st.goal = goal
        if notes:
            st.notes = list(dict.fromkeys(st.notes + notes))
        if artifacts:
            st.artifacts = list(dict.fromkeys(st.artifacts + artifacts))
        if next_action:
            st.next_action = next_action
        write_yaml_model(self._state_fname(), st)
        return st

    def tool_note(self, text: str) -> bool:
        """记录一条分析笔记到 notes.md（追加）。"""
        write_text(path.join(self.pj_dir, 'notes.md'), text + '\n', append=True)
        return True

    def tool_bash(self, command: str, timeout_sec: int = 120,
                  cwd: str = '') -> Dict[str, Any]:
        """在项目工作目录执行 shell 命令，返回 stdout/stderr/退出码。"""
        cwd = path.abspath(cwd or self.pj_dir)
        os.makedirs(cwd, exist_ok=True)
        try:
            r = subprocess.run(
                command, shell=True, cwd=cwd, env=os.environ.copy(),
                capture_output=True, text=True,
                timeout=max(1, int(timeout_sec or 120)),
            )
            return {
                'returncode': r.returncode,
                'stdout': (r.stdout or '')[:20000],
                'stderr': (r.stderr or '')[:8000],
            }
        except subprocess.TimeoutExpired as ex:
            return {
                'returncode': -1,
                'stdout': (getattr(ex, 'stdout', b'') or b'').decode('utf8', 'replace')[:20000],
                'stderr': f'[超时 {timeout_sec}s]',
            }
        except Exception as ex:
            return {'returncode': -1, 'stdout': '', 'stderr': f'执行异常：{ex}'}

    def tool_readfile(self, fname: str, max_chars: int = 20000) -> str:
        """读取文件文本内容（绝对路径或项目相对路径）。"""
        fp = fname if path.isabs(fname) else path.join(self.pj_dir, fname)
        text = read_text(fp)
        if text is None:
            return f'文件不存在：{fp}'
        if max_chars and len(text) > max_chars:
            return text[:max_chars] + f'\n...[截断，共 {len(text)} 字符]'
        return text

    def tool_writefile(self, fname: str, text: str, append: bool = False) -> bool:
        """写入文本文件（绝对路径或项目相对路径）。"""
        fp = fname if path.isabs(fname) else path.join(self.pj_dir, fname)
        write_text(fp, text, append)
        return True

    def tool_list_files(self, pattern: str = '') -> List[str]:
        """列出项目工作区文件（pattern 为 glob，如 *.txt）。"""
        files = []
        for root, _, fnames in os.walk(self.pj_dir):
            for f in fnames:
                rel = path.relpath(path.join(root, f), self.pj_dir)
                if rel.startswith('__pycache__'):
                    continue
                if pattern and not pathmatch(pattern, rel):
                    continue
                files.append(rel)
        return sorted(files)

    # ══════════════════════════════════════════════════════════
    # MCP 管理工具（IDA）
    # ══════════════════════════════════════════════════════════

    def tool_mcp_status(self) -> List[McpServerStatus]:
        """查看各调试器连接状态：IDA(MCP)、x64dbg/x32dbg(直连)。"""
        statuses = []
        client = self.mcp_clients.get('ida')
        if client is None:
            statuses.append(McpServerStatus(name='ida', connected=False,
                                            tool_count=0,
                                            error='未连接（IDA MCP 不可用）'))
        else:
            st = client.status()
            statuses.append(McpServerStatus(
                name='ida', connected=st['connected'],
                tool_count=st['tool_count'], error=st['error']))
        for target in ('x64dbg', 'x32dbg'):
            xc = self.xauto.get(target)
            if xc is None:
                statuses.append(McpServerStatus(name=target, connected=False,
                                                tool_count=0,
                                                error='x64dbg_automate 库不可用'))
            else:
                st = xc.status()
                statuses.append(McpServerStatus(
                    name=target, connected=st.get('connected', False),
                    tool_count=len(_XTOOL_OPS),
                    error=st.get('error', '')))
        return statuses

    def tool_mcp_list_tools(self, server: str = '') -> List[Dict[str, Any]]:
        """列出某调试器的工具。server: ida/x64dbg/x32dbg；空=全部。"""
        result = []
        if not server or server == 'ida':
            for key, client in self.mcp_clients.items():
                for t in client.tools:
                    result.append({
                        'server': key,
                        'name': t.get('name'),
                        'description': t.get('description'),
                        'inputSchema': t.get('inputSchema'),
                    })
        if not server or server in ('x64dbg', 'x32dbg'):
            for prefix, target in self._XTOOL_DELEGATES.items():
                if server and prefix != ('x64' if server == 'x64dbg' else 'x32'):
                    continue
                for op, opname in _XTOOL_OPS.items():
                    result.append({
                        'server': target,
                        'name': f'tool_{prefix}_{op}',
                        'description': _XTOOL_DOCS.get(op, ''),
                        'inputSchema': {},
                    })
        if not result:
            return [{'error': f'服务器 {server or "全部"} 未连接或没有工具'}]
        return result

    def tool_mcp_call(self, server: str, tool: str,
                      arguments: Dict[str, Any] = None) -> MCPToolResult:
        """按名称调用 IDA MCP 工具（兜底）。x64dbg/x32dbg 请直接用 tool_x64_*/tool_x32_*。"""
        client = self.mcp_clients.get(server)
        if client is None:
            return MCPToolResult(ok=False, server=server, tool=tool,
                                 error=f'{server} 未连接（仅支持 ida）')
        try:
            res = client.call_tool(tool, arguments or {})
            return MCPToolResult(ok=res['ok'], server=server, tool=tool,
                                 output=res['output'], structured=res['structured'],
                                 error=res['error'])
        except Exception as ex:
            return MCPToolResult(ok=False, server=server, tool=tool,
                                 error=f'{tool} 调用失败：{ex}')

    # ══════════════════════════════════════════════════════════
    # x64dbg/x32dbg 生命周期管理
    # ══════════════════════════════════════════════════════════

    def _xauto_call(self, target: str, opname: str, **kwargs) -> Any:
        """内部工具：调 x64dbg/x32dbg 客户端方法（供各 tool_x64_* 复用）。"""
        client = self.xauto.get(target)
        if client is None:
            return {'error': f'{target} 未初始化'}
        return getattr(client, opname)(**kwargs)

    # ══════════════════════════════════════════════════════════
    # 分析辅助工具
    # ══════════════════════════════════════════════════════════

    def tool_analyze_code(self, code: str, question: str = '') -> AnalysisResult:
        """用大模型分析一段反汇编/反编译/源码，返回结构化要点。"""
        return self._call_llm(ANALYZE_CODE_PMT, AnalysisResult,
                              question=question, code=code)

    def tool_rebuild_report(self) -> ReFinalReport:
        """根据当前状态与笔记重建最终报告（兜底，供收尾阶段使用）。"""
        st = self.tool_load_state()
        notes = read_text(path.join(self.pj_dir, 'notes.md')) or ''
        rep = self._call_llm(REBUILD_REPORT_PMT, ReFinalReport,
                             state_json=json_dump_model(st), notes=notes)
        return rep

    # ══════════════════════════════════════════════════════════
    # 工具参数 Schema（含静态 x64dbg/x32dbg 工具与动态 IDA 工具）
    # ══════════════════════════════════════════════════════════

    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # ── 基础 ───────────────────────────────────────────────
        "tool_bash": params_schema(
            required=['command'],
            command=base_schema('string', '要执行的 shell 命令'),
            timeout_sec=base_schema('integer', '超时秒数，默认 120'),
            cwd=base_schema('string', '工作目录，缺省项目工作区'),
        ),
        "tool_readfile": params_schema(
            required=['fname'],
            fname=base_schema('string', '文件路径（绝对或项目相对）'),
            max_chars=base_schema('integer', '最多返回字符数，默认 20000'),
        ),
        "tool_writefile": params_schema(
            required=['fname', 'text'],
            fname=base_schema('string', '文件路径（绝对或项目相对）'),
            text=base_schema('string', '要写入的文本内容'),
            append=base_schema('boolean', '是否追加'),
        ),
        "tool_list_files": params_schema(
            pattern=base_schema('string', 'glob 过滤，如 *.txt'),
        ),
        "tool_note": params_schema(
            required=['text'],
            text=base_schema('string', '分析笔记内容'),
        ),
        "tool_load_state": params_schema(),
        "tool_update_state": params_schema(
            stage=base_schema('string', '当前阶段'),
            goal=base_schema('string', '逆向目标'),
            notes=str_list_schema('新增笔记'),
            artifacts=str_list_schema('新增产物文件路径'),
            next_action=base_schema('string', '下一步动作'),
        ),

        # ── MCP 管理（IDA） ────────────────────────────────────
        "tool_mcp_status": params_schema(),
        "tool_mcp_list_tools": params_schema(
            server=base_schema('string', '服务器名 ida/x64dbg/x32dbg；空=全部'),
        ),
        "tool_mcp_call": params_schema(
            required=['server', 'tool'],
            server=base_schema('string', '服务器名 ida'),
            tool=base_schema('string', '工具名'),
            arguments=base_schema('object', '工具参数字典'),
        ),

        # ── 分析辅助 ──────────────────────────────────────────
        "tool_analyze_code": params_schema(
            required=['code'],
            code=base_schema('string', '反汇编/反编译/源码片段'),
            question=base_schema('string', '分析问题'),
        ),
        "tool_rebuild_report": params_schema(),
    }

    def get_tool_defs(self) -> List:
        """返回所有工具的 OpenAI 函数定义（含静态 xauto 工具与动态 IDA 工具）。"""
        defs = super().get_tool_defs()
        # 静态 xauto 工具（tool_x64_*/tool_x32_*）
        for name in self._xauto_tool_names:
            fn = getattr(self, name, None)
            if fn is None:
                continue
            params = _XTOOL_PARAMS.get(name, None)
            if params is None:
                continue
            defs.append(func_schema(
                name,
                self._clean_doc(fn.__doc__),
                params,
            ))
        # 动态 IDA 工具
        dyn_defs = []
        for name in self._dyn_tool_names:
            fn = getattr(self, name)
            schema = getattr(fn, '__re_schema__', None)
            if not schema:
                continue
            input_schema = schema.get('inputSchema') or {}
            dyn_defs.append(func_schema(
                name,
                self._clean_doc(fn.__doc__),
                {
                    'type': 'object',
                    'properties': (input_schema.get('properties') or {}),
                    'required': (input_schema.get('required') or []),
                },
            ))
        return defs + dyn_defs


# ── x64dbg/x32dbg 静态工具定义 ─────────────────────────────────

# op -> XautoClient 方法名
_XTOOL_OPS: Dict[str, str] = {
    'status': 'status',
    'list_sessions': 'list_sessions',
    'connect_remote': 'connect_remote',
    'attach': 'attach_existing',
    'start_session': 'start_session',
    'detach': 'detach',
    'terminate': 'terminate',
    'pause': 'pause',
    'go': 'go',
    'step_into': 'step_into',
    'step_over': 'step_over',
    'run_to': 'run_to',
    'ret': 'ret',
    'get_regs': 'get_regs',
    'get_reg': 'get_reg',
    'set_reg': 'set_reg',
    'read_memory': 'read_memory',
    'read_string': 'read_string',
    'write_memory': 'write_memory',
    'memset': 'memset',
    'memmap': 'memmap',
    'virt_alloc': 'virt_alloc',
    'virt_query': 'virt_query',
    'set_breakpoint': 'set_breakpoint',
    'set_hardware_breakpoint': 'set_hardware_breakpoint',
    'set_memory_breakpoint': 'set_memory_breakpoint',
    'clear_breakpoint': 'clear_breakpoint',
    'clear_hardware_breakpoint': 'clear_hardware_breakpoint',
    'clear_memory_breakpoint': 'clear_memory_breakpoint',
    'get_breakpoints': 'get_breakpoints',
    'set_label': 'set_label',
    'get_label': 'get_label',
    'set_comment': 'set_comment',
    'get_comment': 'get_comment',
    'get_symbol': 'get_symbol',
    'disassemble_at': 'disassemble_at',
    'disassemble_range': 'disassemble_range',
    'assemble_at': 'assemble_at',
    'eval': 'eval',
    'cmd': 'cmd',
}

_XTOOL_DOCS: Dict[str, str] = {
    'status': '查看 x64dbg/x32dbg 连接与调试状态',
    'list_sessions': '列出本机活动的 x64dbg/x32dbg 会话（PID/窗口标题/端口）',
    'connect_remote': '连接到指定 host:req_rep_port:pub_sub_port 的 x64dbg/x32dbg 会话',
    'attach': '附加到本机已运行的 x64dbg/x32dbg 会话（按 PID）',
    'start_session': '启动新的 x64dbg/x32dbg 会话（可选加载目标 exe）',
    'detach': '分离当前会话（调试器保持运行）',
    'terminate': '终止当前会话（结束调试器进程）',
    'pause': '暂停调试（F12）',
    'go': '继续运行（F9）',
    'step_into': '单步进入（F7）',
    'step_over': '单步步过（F8）',
    'run_to': '运行到指定地址（一次性断点）',
    'ret': '执行到当前函数返回',
    'get_regs': '读取全部寄存器',
    'get_reg': '读取单个寄存器值',
    'set_reg': '写单个寄存器',
    'read_memory': '读取内存并以十六进制转储输出',
    'read_string': '读取内存为 UTF-8 文本',
    'write_memory': '写内存（十六进制字节或原始文本）',
    'memset': '用单字节值填充内存区域',
    'memmap': '列出进程内存映射',
    'virt_alloc': '在目标进程中分配虚拟内存',
    'virt_query': '查询虚拟内存页信息',
    'set_breakpoint': '设软件断点',
    'set_hardware_breakpoint': '设硬件断点（r/w/x，size）',
    'set_memory_breakpoint': '设内存断点（r/w/x/a）',
    'clear_breakpoint': '清除软件断点（空=全部）',
    'clear_hardware_breakpoint': '清除硬件断点（空=全部）',
    'clear_memory_breakpoint': '清除内存断点（空=全部）',
    'get_breakpoints': '列出断点（bp_type: BpNormal/BpHardware/BpMemory 等）',
    'set_label': '在地址处设置标签',
    'get_label': '读取地址处的标签',
    'set_comment': '在地址处设置注释',
    'get_comment': '读取地址处的注释',
    'get_symbol': '读取地址处的符号',
    'disassemble_at': '反汇编单条指令',
    'disassemble_range': '从地址反汇编 count 条指令（文本）',
    'assemble_at': '在地址处汇编单条指令，返回指令大小',
    'eval': '求值 x64dbg 表达式',
    'cmd': '执行 x64dbg 命令',
}

# 生成静态工具的参数 Schema（对两个目标各一份，方法名 tool_x64_*/tool_x32_*）
def _xauto_schema(op: str) -> Dict[str, Any]:
    """构造单个 op 的参数 Schema。"""
    common = {
        'connect_remote': params_schema(
            required=['host', 'req_rep_port', 'pub_sub_port'],
            host=base_schema('string', '主机名/IP'),
            req_rep_port=base_schema('integer', 'REQ/REP 端口'),
            pub_sub_port=base_schema('integer', 'PUB/SUB 端口'),
        ),
        'attach': params_schema(
            required=['session_pid'],
            session_pid=base_schema('integer', 'x64dbg/x32dbg 会话 PID（来自 list_sessions）'),
        ),
        'start_session': params_schema(
            target_exe=base_schema('string', '要加载的目标可执行文件路径（可空）'),
            cmdline=base_schema('string', '命令行参数'),
            current_dir=base_schema('string', '工作目录'),
        ),
        'step_into': params_schema(
            count=base_schema('integer', '步数，默认 1'),
        ),
        'step_over': params_schema(
            count=base_schema('integer', '步数，默认 1'),
        ),
        'run_to': params_schema(
            required=['address'],
            address=base_schema('string', '目标地址（0x 前缀十六进制）'),
        ),
        'ret': params_schema(
            frames=base_schema('integer', '返回帧数，默认 1'),
        ),
        'get_reg': params_schema(
            required=['reg'],
            reg=base_schema('string', '寄存器名，如 rax/rip/eip'),
        ),
        'set_reg': params_schema(
            required=['reg', 'value'],
            reg=base_schema('string', '寄存器名'),
            value=base_schema('integer', '要写入的值'),
        ),
        'read_memory': params_schema(
            required=['address', 'size'],
            address=base_schema('string', '地址（0x 前缀）'),
            size=base_schema('integer', '字节数'),
        ),
        'read_string': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
            size=base_schema('integer', '最多字节数，默认 256'),
        ),
        'write_memory': params_schema(
            required=['address', 'data'],
            address=base_schema('string', '地址（0x 前缀）'),
            data=base_schema('string', '十六进制字节串（is_hex=true）或原始文本'),
            is_hex=base_schema('boolean', '是否十六进制，默认 true'),
        ),
        'memset': params_schema(
            required=['address', 'val', 'size'],
            address=base_schema('string', '地址（0x 前缀）'),
            val=base_schema('integer', '字节值 0-255'),
            size=base_schema('integer', '填充字节数'),
        ),
        'virt_alloc': params_schema(
            size=base_schema('integer', '分配大小，默认 0x1000'),
            addr=base_schema('integer', '首选地址，默认 0'),
        ),
        'virt_query': params_schema(
            required=['addr'],
            addr=base_schema('string', '地址（0x 前缀）'),
        ),
        'set_breakpoint': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
            singleshoot=base_schema('boolean', '一次性断点，默认 false'),
        ),
        'set_hardware_breakpoint': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
            bp_type=base_schema('string', 'r/w/x，默认 x'),
            size=base_schema('integer', '访问大小，默认 1'),
        ),
        'set_memory_breakpoint': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
            bp_type=base_schema('string', 'r/w/x/a，默认 a'),
            singleshoot=base_schema('boolean', '一次性断点，默认 false'),
        ),
        'clear_breakpoint': params_schema(
            address=base_schema('string', '地址（0x 前缀）；空=全部'),
        ),
        'clear_hardware_breakpoint': params_schema(
            address=base_schema('string', '地址（0x 前缀）；空=全部'),
        ),
        'clear_memory_breakpoint': params_schema(
            address=base_schema('string', '地址（0x 前缀）；空=全部'),
        ),
        'get_breakpoints': params_schema(
            bp_type=base_schema('string', 'BpNormal/BpHardware/BpMemory 等，默认 BpNormal'),
        ),
        'set_label': params_schema(
            required=['address', 'text'],
            address=base_schema('string', '地址（0x 前缀）'),
            text=base_schema('string', '标签文本'),
        ),
        'get_label': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
        ),
        'set_comment': params_schema(
            required=['address', 'text'],
            address=base_schema('string', '地址（0x 前缀）'),
            text=base_schema('string', '注释文本'),
        ),
        'get_comment': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
        ),
        'get_symbol': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
        ),
        'disassemble_at': params_schema(
            required=['address'],
            address=base_schema('string', '地址（0x 前缀）'),
        ),
        'disassemble_range': params_schema(
            required=['address'],
            address=base_schema('string', '起始地址（0x 前缀）'),
            count=base_schema('integer', '指令条数，默认 10'),
        ),
        'assemble_at': params_schema(
            required=['address', 'instruction'],
            address=base_schema('string', '地址（0x 前缀）'),
            instruction=base_schema('string', '汇编指令文本'),
        ),
        'eval': params_schema(
            required=['expression'],
            expression=base_schema('string', 'x64dbg 表达式'),
        ),
        'cmd': params_schema(
            required=['command'],
            command=base_schema('string', 'x64dbg 命令'),
        ),
    }
    return common.get(op, params_schema())


# 静态工具 Schema：tool_x64_* / tool_x32_*
_XTOOL_PARAMS: Dict[str, Dict[str, Any]] = {}
for _prefix in ('x64', 'x32'):
    for _op in _XTOOL_OPS:
        _XTOOL_PARAMS[f'tool_{_prefix}_{_op}'] = _xauto_schema(_op)


def pathmatch(pattern: str, rel: str) -> bool:
    """简易 glob 匹配（支持 * 与 ?，跨平台，忽略大小写）。"""
    if not pattern:
        return True
    p = pattern.replace('\\', '/').lower()
    r = rel.replace('\\', '/').lower()
    re_parts = []
    for ch in p:
        if ch == '*':
            re_parts.append('.*')
        elif ch == '?':
            re_parts.append('.')
        else:
            re_parts.append(re.escape(ch))
    return bool(re.match('^' + ''.join(re_parts) + '$', r))
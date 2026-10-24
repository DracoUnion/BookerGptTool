# -*- coding: utf-8 -*-

"""re 子命令工具集：基础工具 + IDA/x64dbg/x32dbg MCP 桥接工具。

工具类继承 ToolsMixin，所有 tool_* 方法自动进入 tool-call 循环。
MCP 工具是“动态发现”的：连接服务器后把每个 tools/list 结果注册为
tool_mcp_<server>_<name>，并同步生成 _TOOL_PARAMS 条目。
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

logger = logging.getLogger(__name__)


def _norm_safe(name: str) -> str:
    """把 MCP 工具名转为合法下划线标识符片段（保留字母数字下划线）。"""
    s = re.sub(r'[^\w]', '_', name)
    s = re.sub(r'_+', '_', s).strip('_')
    return s or 'tool'


class ReTools(ToolsMixin):
    """逆向工程智能体工具集。

    基础工具与工作区工具继承自 ToolsMixin；MCP 桥接工具在连接后动态注册。
    """

    # 默认项目工作目录
    pj_dir = '.'

    def __init__(self, args):
        """初始化：保存参数、配置 OpenAI、连接 MCP 服务器。"""
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
        # MCP 服务器：{key: McpClient}
        self.mcp_clients: Dict[str, re_mcp_mod.McpClient] = {}
        # 动态注册的工具名集合
        self._dyn_tool_names: List[str] = []
        self._connect_mcp(args)

    # ══════════════════════════════════════════════════════════
    # MCP 连接
    # ══════════════════════════════════════════════════════════

    def _connect_mcp(self, args) -> None:
        """按配置连接 IDA / x64dbg / x32dbg 并发现工具。"""
        servers = re_mcp_mod.load_mcp_servers(args)
        for key, spec in servers.items():
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
    # MCP 管理工具
    # ══════════════════════════════════════════════════════════

    def tool_mcp_status(self) -> List[McpServerStatus]:
        """查看各 MCP 服务器（IDA/x64dbg/x32dbg）连接状态与工具数。"""
        statuses = []
        for key in ('ida', 'x64dbg', 'x32dbg'):
            client = self.mcp_clients.get(key)
            if client is None:
                statuses.append(McpServerStatus(name=key, connected=False,
                                                tool_count=0,
                                                error='未配置或未连接'))
            else:
                st = client.status()
                statuses.append(McpServerStatus(
                    name=key, connected=st['connected'],
                    tool_count=st['tool_count'], error=st['error']))
        return statuses

    def tool_mcp_list_tools(self, server: str = '') -> List[Dict[str, Any]]:
        """列出某 MCP 服务器的全部工具（名称/描述/参数）。server 缺省列出全部。"""
        result = []
        for key, client in self.mcp_clients.items():
            if server and key != server:
                continue
            for t in client.tools:
                result.append({
                    'server': key,
                    'name': t.get('name'),
                    'description': t.get('description'),
                    'inputSchema': t.get('inputSchema'),
                })
        if not result:
            return [{'error': f'服务器 {server or "全部"} 未连接或没有工具'}]
        return result

    def tool_mcp_call(self, server: str, tool: str,
                      arguments: Dict[str, Any] = None) -> MCPToolResult:
        """按名称调用任意 MCP 服务器上的工具（兜底通道）。"""
        client = self.mcp_clients.get(server)
        if client is None:
            return MCPToolResult(ok=False, server=server, tool=tool,
                                 error=f'{server} 未连接')
        try:
            res = client.call_tool(tool, arguments or {})
            return MCPToolResult(ok=res['ok'], server=server, tool=tool,
                                 output=res['output'], structured=res['structured'],
                                 error=res['error'])
        except Exception as ex:
            return MCPToolResult(ok=False, server=server, tool=tool,
                                 error=f'{tool} 调用失败：{ex}')

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
    # 工具参数 Schema（含动态 MCP 工具）
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

        # ── MCP 管理 ──────────────────────────────────────────
        "tool_mcp_status": params_schema(),
        "tool_mcp_list_tools": params_schema(
            server=base_schema('string', '服务器名 ida/x64dbg/x32dbg；空=全部'),
        ),
        "tool_mcp_call": params_schema(
            required=['server', 'tool'],
            server=base_schema('string', '服务器名 ida/x64dbg/x32dbg'),
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
        """返回所有工具的 OpenAI 函数定义（含动态发现的 MCP 工具）。"""
        defs = super().get_tool_defs()
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

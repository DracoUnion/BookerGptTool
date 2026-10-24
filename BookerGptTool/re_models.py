# -*- coding: utf-8 -*-

"""re 子命令 Pydantic 模型：逆向工程智能体状态机与最终报告。"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class ReState(BaseModel):
    """逆向工程任务状态机"""
    project_id: str = Field(..., description="项目ID")
    goal: str = Field('', description="用户给出的逆向目标")
    stage: str = Field('PLAN', description="当前阶段")
    artifacts: List[str] = Field(default_factory=list, description="已产出文件路径")
    notes: List[str] = Field(default_factory=list, description="分析笔记")
    next_action: str = Field('', description="下一步动作")


class ReFinding(BaseModel):
    """一条逆向分析结论"""
    title: str = Field(..., description="结论标题")
    detail: str = Field('', description="结论详情")
    evidence: str = Field('', description="证据（如地址/函数名/寄存器值/工具输出摘录）")


class ReFinalReport(BaseModel):
    """逆向工程最终交付报告"""
    status: str = Field(..., description="done|blocked|needs_approval")
    stage: str = Field('', description="最终阶段")
    summary: str = Field(..., description="总体结论摘要")
    findings: List[ReFinding] = Field(default_factory=list, description="主要发现")
    artifacts: List[str] = Field(default_factory=list, description="产物文件列表")
    blockers: List[str] = Field(default_factory=list, description="未解决的阻塞项")
    next_action: str = Field('', description="建议的下一步/人工检查项")


class McpServerStatus(BaseModel):
    """单个 MCP 服务器连接状态"""
    name: str = Field(..., description="服务器名")
    connected: bool = Field(False, description="是否连接成功")
    tool_count: int = Field(0, description="已发现工具数")
    error: str = Field('', description="错误信息")


class AnalysisResult(BaseModel):
    """大模型对汇编/反编译代码的分析结果"""
    summary: str = Field(..., description="总体分析摘要")
    key_points: List[str] = Field(default_factory=list, description="关键要点")
    risks: List[str] = Field(default_factory=list, description="风险/可疑点")
    next_actions: List[str] = Field(default_factory=list, description="建议的后续动作")
    ai_generated: bool = Field(True, description="是否 AI 生成")


class MCPToolResult(BaseModel):
    """一次 MCP tools/call 的规范化结果"""
    ok: bool = Field(..., description="是否成功")
    server: str = Field('', description="服务器名")
    tool: str = Field('', description="工具名")
    output: str = Field('', description="文本输出（多段用换行连接）")
    structured: Any = Field(None, description="结构化内容（若有）")
    error: str = Field('', description="错误信息")
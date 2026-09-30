import json
import logging
import os
import re
import subprocess
import hashlib
import shutil
import xml.etree.ElementTree as ET
from os import path
from typing import List, Optional, Dict, Any, Callable

from .util import (
    gen_objs_md5, read_yaml_model, write_yaml_model, read_text, write_text,
    render_prompt, json_dump_model,
)
from .openai import *
from .auto_research_models import *
from .auto_research_pmt import AUTO_RESEARCH_ROUND_PROMPT

logger = logging.getLogger(__name__)

_ARXIV_NS = {'a': 'http://www.w3.org/2005/Atom'}
STAGE_ORDER = [
    'PROJECT_INIT', 'LITERATURE', 'GAP', 'HYPOTHESIS', 'EXPERIMENT_PLAN',
    'APPROVAL', 'RUN_EXPERIMENT', 'RESULT_CONFIRM', 'WRITING',
    'INTERNAL_REVIEW', 'SUBMISSION_PACKAGE',
]


class AutoResearchTools(ToolsMixin):
    """合规科研自动化流水线工具集。

    所有工具方法默认返回可直接序列化为 JSON 的对象（dict 或 Pydantic 模型），
    供 tool-call 循环回填给大模型。检索类工具带文件缓存；副作用类工具不缓存。
    """

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、创建项目工作区目录。"""
        super(ToolsMixin, self).__init__()
        set_openai_props(args)
        self.args = args
        self.pj_dir = path.abspath(getattr(args, 'project', None) or 'auto_research')
        os.makedirs(self.pj_dir, exist_ok=True)
        self.project_id = path.basename(self.pj_dir) or self.pj_dir

    # ── 内部辅助 ────────────────────────────────────────────────

    def _cache_fname(self, prefix: str, *objs) -> str:
        return path.join(self.pj_dir, f'{prefix}_{gen_objs_md5(*objs)}.yaml')

    def _load_state(self) -> PipelineState:
        state = read_yaml_model(path.join(self.pj_dir, 'pipeline_state.yaml'),
                                PipelineState)
        if state:
            return state
        return PipelineState(project_id=self.project_id, stage='PROJECT_INIT')

    def _save_state(self, state: PipelineState) -> None:
        write_yaml_model(path.join(self.pj_dir, 'pipeline_state.yaml'), state)

    def _list_workspace_text_files(self) -> List[str]:
        return [
            path.join(self.pj_dir, f)
            for root, _, fnames in os.walk(self.pj_dir)
            for f in fnames
            if f.endswith(('.md', '.txt', '.json', '.yaml', '.bib', '.tex'))
            and not f.startswith('history')
        ]

    # ── 1. 文献检索（真实 API，带缓存） ──────────────────────────

    def tool_search_arxiv(self, query: str, max_results: int = 10,
                          categories: Optional[List[str]] = None) -> PaperSearchResult:
        """检索 arXiv（真实 API），返回论文元数据。不得编造引用。"""
        cache_fname = self._cache_fname(
            'arxiv', query, max_results, categories)
        r = read_yaml_model(cache_fname, PaperSearchResult)
        if r:
            return r
        max_results = max(1, min(int(max_results or 10), 50))
        if categories:
            cat_terms = ' OR ALL:'.join(
                f'cat:{c}' for c in categories
            )
            search_query = f'(all:"{query}") AND ALL:{cat_terms}'
        else:
            search_query = f'all:"{query}"'
        from urllib.parse import quote
        url = ('http://export.arxiv.org/api/query?'
               f'search_query={quote(search_query)}'
               f'&max_results={max_results}&sortBy=relevance')
        papers: List[Citation] = []
        try:
            resp = request_retry('GET', url, timeout=30)
            root = ET.fromstring(resp.text)
            for entry in root.findall('a:entry', _ARXIV_NS):
                eid = entry.findtext('a:id', default='', namespaces=_ARXIV_NS)
                aid = eid.split('/abs/')[-1]
                title = ' '.join(
                    entry.findtext('a:title', default='', namespaces=_ARXIV_NS)
                        .split())
                abstract = ' '.join(
                    entry.findtext('a:summary', default='', namespaces=_ARXIV_NS)
                        .split())
                published = entry.findtext('a:published', default='',
                                           namespaces=_ARXIV_NS)
                year = int(published[:4]) if published and published[:4].isdigit() else None
                authors = [
                    a.findtext('a:name', default='', namespaces=_ARXIV_NS)
                    for a in entry.findall('a:author', _ARXIV_NS)
                ]
                cats = [
                    c.attrib.get('term', '')
                    for c in entry.findall('a:category', _ARXIV_NS)
                ]
                papers.append(Citation(
                    source_id=aid, title=title, authors=authors, year=year,
                    venue='arXiv', url=f'https://arxiv.org/abs/{aid}',
                    abstract=abstract, categories=cats, verified=True,
                ))
        except Exception as ex:
            logger.warning('arXiv 检索失败：%s', ex)
        result = PaperSearchResult(query=query, papers=papers)
        write_yaml_model(cache_fname, result)
        return result

    def _s2_headers(self) -> Dict[str, str]:
        """可选 Semantic Scholar API key（环境变量 S2_API_KEY），提高限流额度。"""
        key = os.environ.get('S2_API_KEY', '').strip()
        return {'x-api-key': key} if key else {}

    def _s2_request(self, url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            resp = request_retry('GET', url, params=params,
                                 headers=self._s2_headers(), timeout=30)
            if resp.status_code == 429:
                logger.warning('Semantic Scholar 限流(429)；可用环境变量 '
                               'S2_API_KEY 提高额度。返回空结果。')
                return None
            return resp.json()
        except Exception as ex:
            logger.warning('Semantic Scholar 请求失败：%s', ex)
            return None

    def tool_search_semantic_scholar(self, query: str, limit: int = 10) -> PaperSearchResult:
        """检索 Semantic Scholar（真实 API），返回论文元数据。"""
        cache_fname = self._cache_fname('s2', query, limit)
        r = read_yaml_model(cache_fname, PaperSearchResult)
        if r:
            return r
        limit = max(1, min(int(limit or 10), 100))
        papers: List[Citation] = []
        data = self._s2_request(
            'https://api.semanticscholar.org/graph/v1/paper/search',
            {'query': query, 'limit': limit,
             'fields': 'title,authors,year,venue,externalIds,abstract'})
        if data:
            for p in data.get('data', []) or []:
                ext = p.get('externalIds') or {}
                papers.append(Citation(
                    source_id=p.get('paperId', ''),
                    title=p.get('title', ''),
                    authors=[a.get('name', '') for a in (p.get('authors') or [])],
                    year=p.get('year'),
                    venue=p.get('venue', ''),
                    doi=ext.get('DOI', ''),
                    url=ext.get('ArXiv', ''),
                    abstract=p.get('abstract', ''),
                    verified=True,
                ))
        result = PaperSearchResult(query=query, papers=papers)
        write_yaml_model(cache_fname, result)
        return result

    def tool_get_citation_graph(self, paper_id: str, direction: str = 'citations',
                                limit: int = 10) -> CitationGraphResult:
        """查一篇论文的施引（citations）或被引（references，即该文引用的文献）。"""
        normalized = direction.lower()
        if normalized in ('in', 'backward', 'back', 'references', 'ref'):
            normalized = 'references'
        else:
            normalized = 'citations'
        cache_fname = self._cache_fname('cite', paper_id, normalized, limit)
        r = read_yaml_model(cache_fname, CitationGraphResult)
        if r:
            return r
        limit = max(1, min(int(limit or 10), 100))
        papers: List[Citation] = []
        data = self._s2_request(
            f'https://api.semanticscholar.org/graph/v1/paper/{paper_id}/{normalized}',
            {'limit': limit, 'fields': 'title,authors,year,venue,externalIds'})
        if data:
            for it in data.get('data', []) or []:
                p = it.get('citingPaper') or it.get('citedPaper') or it
                ext = p.get('externalIds') or {}
                papers.append(Citation(
                    source_id=p.get('paperId', ''),
                    title=p.get('title', ''),
                    authors=[a.get('name', '') for a in (p.get('authors') or [])],
                    year=p.get('year'),
                    venue=p.get('venue', ''),
                    url=ext.get('ArXiv', ''),
                    verified=True,
                ))
        result = CitationGraphResult(
            paper_id=paper_id, direction=normalized, papers=papers)
        write_yaml_model(cache_fname, result)
        return result

    # ── 2. 知识检索（本地朴素检索，带缓存） ──────────────────────

    def tool_retrieve_knowledge(self, query: str, top_k: int = 5) -> KnowledgeResult:
        """在工作区保存的文本中检索与 query 相关的片段（词袋相关度）。"""
        cache_fname = self._cache_fname('know', query, top_k)
        r = read_yaml_model(cache_fname, KnowledgeResult)
        if r:
            return r
        top_k = max(1, int(top_k or 5))
        tokens = set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]{2,}', query))
        scored: List[KnowledgeHit] = []
        for fname in self._list_workspace_text_files():
            try:
                content = read_text(fname)
            except Exception:
                continue
            rel = path.relpath(fname, self.pj_dir)
            for para in content.splitlines():
                para = para.strip()
                if not para or len(para) < 6:
                    continue
                ptoks = set(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]{2,}', para))
                inter = len(tokens & ptoks)
                if inter > 0:
                    score = inter / max(1, len(tokens))
                    if score >= 0.33:
                        scored.append(KnowledgeHit(
                            source=rel, score=round(score, 3), snippet=para[:300]))
        scored.sort(key=lambda h: h.score, reverse=True)
        result = KnowledgeResult(query=query, hits=scored[:top_k])
        write_yaml_model(cache_fname, result)
        return result

    # ── 3. 假设卡 ────────────────────────────────────────────────

    def _next_hyp_id(self) -> str:
        hyp_fname = path.join(self.pj_dir, 'hypotheses.yaml')
        hyps = read_yaml_model(hyp_fname, None) or []
        return f'hyp_{len(hyps) + 1:03d}'

    def tool_create_hypothesis_card(
        self, question: str, prediction: str,
        baselines: Optional[List[str]] = None,
        metrics: Optional[List[str]] = None,
        risks: Optional[List[str]] = None,
        contribution: str = '',
        references: Optional[List[str]] = None,
    ) -> HypothesisCard:
        """生成并保存一条候选假设卡（研究问题需可检验、基线/指标/风险齐全）。"""
        card = HypothesisCard(
            id=self._next_hyp_id(),
            question=question,
            prediction=prediction,
            baselines=baselines or [],
            metrics=metrics or [],
            risks=risks or [],
            contribution=contribution,
            references=references or [],
        )
        hyp_fname = path.join(self.pj_dir, 'hypotheses.yaml')
        hyps = read_yaml_model(hyp_fname, None) or []
        hyps = [h.dict() if hasattr(h, 'dict') else h for h in hyps]
        hyps.append(card.model_dump())
        write_yaml_model(hyp_fname, hyps)
        return card

    # ── 4. 沙箱执行 ─────────────────────────────────────────────

    def tool_run_in_sandbox(self, command: str, timeout_sec: int = 120,
                            env: Optional[Dict[str, str]] = None) -> ExperimentRun:
        """在隔离沙箱目录执行命令，返回日志与退出码。所有实验结果须经此工具。"""
        sandbox = path.join(self.pj_dir, 'sandbox')
        os.makedirs(sandbox, exist_ok=True)
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        run_id = 'run_' + hashlib.md5(command.encode('utf8')).hexdigest()[:8]
        try:
            r = subprocess.run(
                command, shell=True, cwd=sandbox, env=run_env,
                capture_output=True, text=True,
                timeout=max(1, int(timeout_sec or 120)),
            )
            return ExperimentRun(
                run_id=run_id, command=command,
                logs=(r.stdout or '') + (r.stderr or ''),
                returncode=r.returncode,
                status='ok' if r.returncode == 0 else 'error',
            )
        except subprocess.TimeoutExpired as ex:
            return ExperimentRun(
                run_id=run_id, command=command,
                logs=(getattr(ex, 'stdout', b'') or b'').decode('utf8', 'replace')
                     + f'\n[超时 {timeout_sec}s]',
                returncode=-1, status='timeout',
            )
        except Exception as ex:
            return ExperimentRun(
                run_id=run_id, command=command,
                logs=f'执行异常：{ex}', returncode=-1, status='error',
            )

    # ── 5. git 与实验记录 ───────────────────────────────────────

    def tool_git_commit(self, message: str,
                        files: Optional[List[str]] = None) -> Dict[str, Any]:
        """在工作区仓库提交代码版本（保留可复现实验的代码 commit）。"""
        if not path.isdir(path.join(self.pj_dir, '.git')):
            subprocess.run(['git', 'init'], cwd=self.pj_dir, capture_output=True)
        add_cmd = ['git', 'add'] + (files if files else ['-A'])
        subprocess.run(add_cmd, cwd=self.pj_dir, capture_output=True)
        r = subprocess.run(['git', 'commit', '-m', message], cwd=self.pj_dir,
                           capture_output=True, text=True)
        return {
            'message': message,
            'returncode': r.returncode,
            'output': (r.stdout or '') + (r.stderr or ''),
        }

    def tool_log_mlflow(self, run_id: str = '',
                        params: Optional[Dict[str, Any]] = None,
                        metrics: Optional[Dict[str, Any]] = None,
                        artifacts: Optional[List[str]] = None) -> ExperimentRun:
        """记录一次实验运行的参数、指标与产物到 runs.yaml。"""
        run_id = run_id or 'run_' + hashlib.md5(
            json_dump_model((params, metrics)).encode('utf8')).hexdigest()[:8]
        entry = ExperimentRun(
            run_id=run_id, params=params or {}, metrics=metrics or {},
            artifacts=artifacts or [], status='logged',
        )
        runs_fname = path.join(self.pj_dir, 'runs.yaml')
        runs = read_yaml_model(runs_fname, None) or []
        runs = [r.dict() if hasattr(r, 'dict') else r for r in runs]
        runs.append(entry.model_dump())
        write_yaml_model(runs_fname, runs)
        return entry

    # ── 6. 引用校验 / LaTeX ─────────────────────────────────────

    def tool_validate_citations(self, latex: str, bibtex: str) -> CitationValidation:
        """校验 LaTeX 中每个 \\\\cite 是否有对应 bibtex 条目。"""
        cite_keys = set()
        for grp in re.findall(r'\\cite\{([^}]+)\}', latex or ''):
            for k in grp.split(','):
                k = k.strip()
                if k:
                    cite_keys.add(k)
        bib_keys = set(
            m.strip() for m in re.findall(r'@\w+\s*\{\s*([^,]+)\s*,', bibtex or '')
        )
        missing = sorted(cite_keys - bib_keys)
        unused = sorted(bib_keys - cite_keys)
        valid = not missing
        issues = []
        if missing:
            issues.append(f'缺失引用：{", ".join(missing)}')
        if len(cite_keys) == 0 and bibtex.strip():
            issues.append('LaTeX 中未发现任何 \\\\cite')
        return CitationValidation(
            valid=valid, missing_citations=missing,
            unused_citations=unused, issues=issues,
        )

    def tool_compile_latex(self, tex_source: str) -> LatexCompileResult:
        """编译 LaTeX 为 PDF（需已安装 xelatex/pdflatex）。"""
        latex = shutil.which('xelatex') or shutil.which('pdflatex')
        if not latex:
            return LatexCompileResult(
                success=False, log='未安装 LaTeX（xelatex/pdflatex）')
        sandbox = path.join(self.pj_dir, 'sandbox')
        os.makedirs(sandbox, exist_ok=True)
        write_text(path.join(sandbox, 'main.tex'), tex_source)
        try:
            r = subprocess.run(
                [latex, '-interaction=nonstopmode', '-halt-on-error', 'main.tex'],
                cwd=sandbox, capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired as ex:
            return LatexCompileResult(
                success=False, log='LaTeX 编译超时：' +
                                   ((ex.stdout or b'') + (ex.stderr or b'')).decode('utf8', 'replace'))
        pdf_path = path.join(sandbox, 'main.pdf')
        success = path.isfile(pdf_path)
        return LatexCompileResult(
            success=success,
            pdf_path=pdf_path if success else '',
            log=((r.stdout or '') + (r.stderr or ''))[:8000],
        )

    # ── 7. 审批 / 状态机 ────────────────────────────────────────

    def tool_request_user_approval(self, stage: str,
                                   payload: str = '') -> ApprovalResult:
        """向人类请求阶段门禁/关键决策审批（交互读控制台）。"""
        print('\n=== 需要人类审批 ===')
        print(f'阶段：{stage}')
        if payload:
            print(f'说明：{payload}')
        try:
            ans = input('[y]批准 / n 拒绝 / 或输入备注文字（视为批准并记录）：> ').strip()
        except (EOFError, KeyboardInterrupt):
            return ApprovalResult(
                stage=stage, approved=False,
                comment='未收到输入（EOF），默认不批准',
                record=f'{stage}_denied_eof')
        low = ans.lower()
        if low in ('', 'y', 'yes', '1', 'true'):
            approved, comment = True, ''
        elif low in ('n', 'no', '0', 'false'):
            approved, comment = False, ''
        else:
            approved, comment = True, ans
        record = f'{stage}_{"approved" if approved else "denied"}'
        return ApprovalResult(
            stage=stage, approved=approved, comment=comment, record=record)

    def tool_update_pipeline_state(
        self, stage: Optional[str] = None,
        gates: Optional[Dict[str, bool]] = None,
        artifacts: Optional[List[str]] = None,
        citations: Optional[List[str]] = None,
        results: Optional[List[str]] = None,
        approvals: Optional[List[str]] = None,
        next_action: str = '',
    ) -> PipelineState:
        """更新流水线状态机，返回完整新状态。每轮收到工具结果后务必调用。"""
        state = self._load_state()
        if stage:
            state.stage = stage
        if gates:
            state.gates.update(gates)
        if artifacts:
            state.artifacts = list(dict.fromkeys(state.artifacts + artifacts))
        if citations:
            state.citations = list(dict.fromkeys(state.citations + citations))
        if results:
            state.results = list(dict.fromkeys(state.results + results))
        if approvals:
            state.approvals = list(dict.fromkeys(state.approvals + approvals))
        if next_action:
            state.next_action = next_action
        self._save_state(state)
        return state

    # ── 工具参数 Schema ─────────────────────────────────────────

    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # 文献检索
        "tool_search_arxiv": params_schema(
            required=['query'],
            query=base_schema('string', '检索词'),
            max_results=base_schema('integer', '最大返回数 1~50，默认 10'),
            categories=str_list_schema('arXiv 分类过滤，如 cs.CL'),
        ),
        "tool_search_semantic_scholar": params_schema(
            required=['query'],
            query=base_schema('string', '检索词'),
            limit=base_schema('integer', '最大返回数 1~100，默认 10'),
        ),
        "tool_get_citation_graph": params_schema(
            required=['paper_id'],
            paper_id=base_schema('string', '论文ID（S2 paperId 或 arXiv ID）'),
            direction=base_schema('string', 'citations（施引）/ references（该文引用的文献）'),
            limit=base_schema('integer', '最大返回数，默认 10'),
        ),
        # 知识检索
        "tool_retrieve_knowledge": params_schema(
            required=['query'],
            query=base_schema('string', '检索词'),
            top_k=base_schema('integer', '返回条数，默认 5'),
        ),
        # 假设卡
        "tool_create_hypothesis_card": params_schema(
            required=['question', 'prediction'],
            question=base_schema('string', '研究问题'),
            prediction=base_schema('string', '可检验预测'),
            baselines=str_list_schema('基线方法'),
            metrics=str_list_schema('评估指标'),
            risks=str_list_schema('已知风险'),
            contribution=base_schema('string', '潜在贡献点'),
            references=str_list_schema('支撑引用 source_id 列表'),
        ),
        # 沙箱 / git / 实验记录
        "tool_run_in_sandbox": params_schema(
            required=['command'],
            command=base_schema('string', '要执行的 shell 命令'),
            timeout_sec=base_schema('integer', '超时秒数，默认 120'),
            env=str_str_map_schema('附加环境变量'),
        ),
        "tool_git_commit": params_schema(
            required=['message'],
            message=base_schema('string', '提交信息'),
            files=str_list_schema('要提交的文件，缺省全部'),
        ),
        "tool_log_mlflow": params_schema(
            run_id=base_schema('string', '运行ID，缺省自动生成'),
            params=base_schema('object', '实验参数'),
            metrics=base_schema('object', '实验指标'),
            artifacts=str_list_schema('产物文件路径'),
        ),
        # 引用校验 / LaTeX
        "tool_validate_citations": params_schema(
            required=['latex', 'bibtex'],
            latex=base_schema('string', 'LaTeX 正文'),
            bibtex=base_schema('string', 'bibtex 条目'),
        ),
        "tool_compile_latex": params_schema(
            required=['tex_source'],
            tex_source=base_schema('string', 'LaTeX 源码'),
        ),
        # 审批 / 状态机
        "tool_request_user_approval": params_schema(
            required=['stage'],
            stage=base_schema('string', '申请审批的阶段/门禁'),
            payload=base_schema('string', '审批说明'),
        ),
        "tool_update_pipeline_state": params_schema(
            stage=base_schema('string', '当前阶段（有效值见状态机）'),
            gates=base_schema('object', '门禁通过情况，如 {G1: true}'),
            artifacts=str_list_schema('新增产物路径'),
            citations=str_list_schema('新增已确认引用 source_id'),
            results=str_list_schema('新增已确认实验 run_id'),
            approvals=str_list_schema('新增审批记录'),
            next_action=base_schema('string', '下一步动作'),
        ),
    }
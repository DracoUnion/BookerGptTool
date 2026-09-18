# -*- coding: utf-8 -*-
"""
md2wiki_tools.py —— LLM Wiki（含完整 ingest / query / lint / graph / discover 工作流）工具集。

以「开放工具调用循环」被编排器调用。读取类工具为纯 IO；生成类工具（摘要、实体/概念抽取、
query、lint 语义分析、graph 推断、overview 更新）内部调用大模型并对结果做文件缓存。
"""

import json
import logging
import os
import re
import shutil
from os import path
from typing import List, Optional, Dict, Any, Callable

from .util import (ext_code_block, gen_objs_md5, read_yaml_model, write_yaml_model,
                   read_text, write_text, to_kebab)
from .openai import *
from .md2wiki_models import *
from .md2wiki_pmt import *

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 页面类型 → wiki 子目录
PAGE_SECTION = {
    'source': 'sources',
    'entity': 'entities',
    'concept': 'concepts',
    'synthesis': 'syntheses',
    'change': 'changes',
}

# 支持的原始文件类型 → 提取方式
RAW_EXTS = {'.md', '.txt', '.markdown', '.yaml', '.yml', '.json',
            '.py', '.pdf', '.html', '.htm'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'}

WIKILINK_RE = re.compile(r'\[\[([^\]]+)\]\]')


def _titlecase(s: str) -> str:
    """文件名 slug → TitleCase（OpenAI、SamAltman、RAG）。"""
    parts = re.split(r'[-_\s]+', s.strip())
    return ''.join(p[:1].upper() + p[1:] for p in parts if p)


class Md2WikiTools(ToolsMixin):
    """LLM Wiki 工具集：config / ingest / query / lint / graph / discover 全工作流。"""

    def __init__(self, args):
        """初始化工具集：解析 wiki 工作区、配置 OpenAI、建目录。"""
        super(ToolsMixin, self).__init__()
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.temperature = getattr(args, 'temp', 0.0)
        self.max_tokens = getattr(args, 'max_tokens', 2000)
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)
        # WIKI_ROOT：优先命令行 --workspace，否则 pj_dir
        ws = getattr(args, 'workspace', None)
        self.wiki_root = os.path.abspath(ws) if ws else self._default_root()
        os.makedirs(self.wiki_root, exist_ok=True)
        self.pj_dir = self.wiki_root
        self._ensure_structure()

    def _default_root(self) -> str:
        fname = self.args.fname
        return (
            path.dirname(fname) + '_md2wiki'
            if path.isfile(fname) else
            path.abspath(fname) + '_md2wiki'
        )

    # ── 路径安全 ──────────────────────────────────────────
    def _res(self, *parts: str) -> str:
        """把相对路径解析到 WIKI_ROOT 内，阻止路径穿越。"""
        p = os.path.abspath(path.join(self.wiki_root, *parts))
        if not (p == self.wiki_root or p.startswith(self.wiki_root + os.sep)):
            raise ValueError(f'非法路径：{parts!r}（只能访问工作区内的文件）')
        return p

    def _config_path(self) -> str:
        return self._res('config.yaml')

    def _discoveries_dir(self) -> str:
        return self._res('.discoveries')

    def _ensure_structure(self):
        """确保 wiki 工作区目录与三个基础文件存在。"""
        for sub in ['raw', 'wiki/sources', 'wiki/entities', 'wiki/concepts',
                    'wiki/syntheses', 'wiki/archive', 'graph', 'outputs', '.discoveries']:
            os.makedirs(self._res(sub), exist_ok=True)
        today = os.environ.get('MJ2W_DATE', '')
        import datetime
        today = today or datetime.datetime.now().strftime('%Y-%m-%d')
        if not path.isfile(self._res('wiki/index.md')):
            write_text(self._res('wiki/index.md'),
                       f'# Wiki Index\n\n_Last updated: {today}_\n')
        if not path.isfile(self._res('wiki/overview.md')):
            write_text(self._res('wiki/overview.md'),
                       '# Wiki Overview\n\n_No content yet, will be auto-generated after first ingestion._\n')
        if not path.isfile(self._res('wiki/log.md')):
            write_text(self._res('wiki/log.md'),
                       '# Wiki Log\n\nAppend-only chronological record of all operations.\n\n'
                       "Format: `## [YYYY-MM-DD] <operation> | <title>`\n\n---\n")

    # ── LLM 调用辅助 ──────────────────────────────────────
    def _call(self, system_prompt: str, user_prompt: str,
              max_tokens: Optional[int] = None,
              parse_output: Callable = None) -> str:
        """调用 LLM（system+user 消息），按 parse_output 解析结果并带重试。"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return call_llm_retry(
            messages, self.model,
            retry=self.retry,
            temp=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            parse_output=parse_output,
        )

    def _json(self, schema: Callable, system_prompt: str, user_prompt: str) -> Any:
        parse_output = lambda s: parse_obj_as(schema, json.loads(ext_code_block(s)))
        return self._call(system_prompt, user_prompt, parse_output=parse_output)

    def _text(self, system_prompt: str, user_prompt: str) -> str:
        return self._call(system_prompt, user_prompt, parse_output=ext_cont_block)

    # ============================================================
    # 一、Workspace / Config
    # ============================================================
    def tool_workspace_show(self):
        """显示当前 wiki 工作区路径与目录状态。"""
        return {
            'wiki_root': self.wiki_root,
            'raw_files': len(self.tool_list_raw_sources()),
            'wiki_pages': {
                s: len(self._list_md(f'wiki/{s}'))
                for s in ['sources', 'entities', 'concepts', 'syntheses']
            },
        }

    def tool_workspace_set(self, path):
        """设置 wiki 工作区路径并重建目录结构（path 替换当前 workspace）。"""
        self.wiki_root = os.path.abspath(path)
        os.makedirs(self.wiki_root, exist_ok=True)
        self.pj_dir = self.wiki_root
        self._ensure_structure()
        return {'wiki_root': self.wiki_root}

    def tool_read_config(self):
        """读取 config.yaml（无则返回默认配置）。"""
        cfg_path = self._config_path()
        if path.isfile(cfg_path):
            data = yaml.safe_load(read_text(cfg_path)) or {}
        else:
            data = {
                'wiki': {'name': 'My Wiki', 'max_pages_per_ingest': 15},
                'topics': [{'name': 'General', 'keywords': [], 'priority': 'low'}],
            }
            write_text(cfg_path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        return parse_obj_as(WikiConfig, data)

    def tool_write_config(self, config):
        """保存 config.yaml（WikiConfig）。"""
        write_text(self._config_path(), yaml.safe_dump(
            config.dict(), allow_unicode=True, sort_keys=False))
        return {'saved': 'config.yaml'}

    # ============================================================
    # 二、raw 来源（只读）
    # ============================================================
    def tool_list_raw_sources(self):
        """列出 raw/ 目录下所有原始文件路径。"""
        raw_root = self._res('raw')
        if not path.isdir(raw_root):
            return []
        return [
            path.relpath(path.join(root, f), self.wiki_root).replace(os.sep, '/')
            for root, _, files in os.walk(raw_root)
            for f in files
        ]

    def tool_read_raw(self, fname):
        """读取 raw/ 下某个原始文件内容（只读，禁止修改）。"""
        p = self._res(fname)
        if not (p.startswith(self._res('raw')) or p.startswith(self._res('.'))):
            raise ValueError('只能读取 raw/ 下的原始文件')
        return read_text(p)

    def tool_read_gaps(self):
        """读取 .discoveries/gaps.json（无则返回空）。"""
        gp = self._res('.discoveries/gaps.json')
        if path.isfile(gp):
            return parse_obj_as(DiscoveryGaps, json.loads(read_text(gp)))
        return DiscoveryGaps()

    def tool_write_gaps(self, gaps):
        """写入 .discoveries/gaps.json（DiscoveryGaps）。"""
        write_text(self._res('.discoveries/gaps.json'),
                   json.dumps(gaps.dict(), ensure_ascii=False, indent=2))
        return {'saved': '.discoveries/gaps.json'}

    def _list_md(self, sub):
        p = self._res(sub)
        if not path.isdir(p):
            return []
        return sorted(f for f in os.listdir(p) if f.endswith('.md'))

    def tool_list_pages(self, section=''):
        """列出 wiki 下的页面文件（section 可选 sources|entities|concepts|syntheses，空为全部）。"""
        if section:
            return self._list_md(f'wiki/{section}')
        return {
            s: self._list_md(f'wiki/{s}')
            for s in ['sources', 'entities', 'concepts', 'syntheses']
        }

    def tool_read_wiki_context(self, recent=5):
        """读取 index.md、overview.md 与最近的 sources 页面，返回上下文文本。"""
        parts = []
        for f in ['wiki/index.md', 'wiki/overview.md']:
            p = self._res(f)
            if path.isfile(p):
                parts.append(f'===== {f} =====\n' + read_text(p))
        recent_srcs = self._list_md('wiki/sources')[-recent:]
        for s in recent_srcs:
            p = self._res(f'wiki/sources/{s}')
            parts.append(f'===== wiki/sources/{s} =====\n' + read_text(p)[:3000])
        return '\n\n'.join(parts)

    def tool_read_page(self, fname):
        """读取 wiki 下某页面文件全文（fname 形如 wiki/entities/OpenAI.md）。"""
        p = self._res(fname)
        if not p.startswith(self._res('wiki')):
            raise ValueError('只能读取 wiki/ 下的页面')
        return read_text(p)

    def tool_read_sources_bulk(self, fnames):
        """批量读取多个页面，返回 {路径: 内容前 1500 字符} 便于语义分析。"""
        out = {}
        for f in (fnames or []):
            try:
                out[f] = self.tool_read_page(f)[:1500]
            except Exception as e:
                out[f] = f'(读取失败: {e})'
        return out

    def tool_input_source(self, source_path, topic='inbox'):
        """把任意文件归档到 raw/<topic>/（建目录、防重名），返回归档路径。"""
        if not path.isfile(source_path):
            raise ValueError(f'文件不存在：{source_path}')
        topic = re.sub(r'[^a-z0-9-]', '', (topic or 'inbox').lower().replace('_', '-'))[:32] or 'inbox'
        base = to_kebab(path.basename(source_path)).rsplit('.', 1)[0]
        ext = path.splitext(source_path)[1].lower()
        dst_dir = self._res('raw', topic)
        os.makedirs(dst_dir, exist_ok=True)
        dst = path.join(dst_dir, f'{base}{ext}')
        n = 1
        while path.exists(dst):
            dst = path.join(dst_dir, f'{base}-{n}{ext}')
            n += 1
        shutil.copy2(source_path, dst)
        rel = path.relpath(dst, self.wiki_root).replace(os.sep, '/')
        return {'archived': rel, 'source_file': rel, 'source_type': self._source_type(ext)}

    @staticmethod
    def _source_type(ext: str) -> str:
        if ext in IMAGE_EXTS:
            return 'image'
        if ext == '.pdf':
            return 'pdf'
        if ext == '.docx':
            return 'docx'
        if ext == '.pptx':
            return 'pptx'
        if ext in ('.xlsx', '.csv'):
            return 'xlsx'
        return 'markdown'

    def tool_extract_content(self, source_file):
        """把 raw/ 下文件内容转为 Markdown 文本（md/txt 直读，pdf 用 pymupdf；图片/office 返回占位说明）。"""
        p = self._res(source_file)
        ext = path.splitext(p)[1].lower()
        if ext in RAW_EXTS - {'.pdf', '.html', '.htm'} or ext in IMAGE_EXTS:
            return read_text(p) if os.path.isfile(p) else ''
        if ext == '.html' or ext == '.htm':
            t = read_text(p)
            m = re.search(r'<body[^>]*>([\s\S]+)</body>', t)
            return m.group(1) if m else t
        if ext == '.pdf':
            try:
                import pymupdf as pymu
                doc = pymu.open(p)
                return '\n\n'.join(pg.get_text() for pg in doc)
            except Exception as e:
                return f'(PDF 解析失败：{e})'
        if ext in IMAGE_EXTS:
            return f'(图片 {source_file}，请用视觉模型识别)'
        return f'(暂不支持该格式 {ext}，请先转换为 Markdown)'

    def tool_check_ingested(self, slug):
        """检查 log.md 中是否已存在某文件的 ingest 记录（按文件名匹配）。"""
        log = read_text(self._res('wiki/log.md')) if path.isfile(self._res('wiki/log.md')) else ''
        return {'ingested': slug.lower() in log.lower()}

    # ============================================================
    # 三、Ingest：摘要 + 实体/概念抽取（LLM + 缓存）
    # ============================================================
    def tool_summarize_source(self, content, title, source_file, source_type='markdown', date=''):
        """生成来源摘要页（WikiSourceSummary），按内容缓存。"""
        ck = gen_objs_md5(content, title, source_file)
        cache = self._res('.discoveries/src-summary-' + ck + '.yaml')
        r = read_yaml_model(cache, WikiSourceSummary)
        if r: return r
        user = SOURCE_SUMMARY_PMT.format(
            content=content, title=title or to_kebab(path.basename(source_file)),
            source_file=source_file, source_type=source_type, date=date,
        )
        r = self._json(WikiSourceSummary, SOURCE_SUMMARY_SYSTEM, user)
        if not r.name:
            r.name = to_kebab(path.basename(source_file))
        if not r.raw_path:
            r.raw_path = source_file
        write_yaml_model(cache, r)
        return r

    def tool_extract_entities(self, content, context):
        """从文档抽取实体页（List[WikiEntity]），按内容缓存。"""
        cache = self._res('.discoveries/ent-' + gen_objs_md5(content, context) + '.yaml')
        r = read_yaml_model(cache, List[WikiEntity])
        if not r:
            user = ENTITY_EXT_PMT.format(content=content, context=context or '（尚无 wiki 内容）')
            r = self._json(List[WikiEntity], ENTITY_EXT_SYSTEM, user)
            write_yaml_model(cache, r)
        return r

    def tool_extract_concepts(self, content, context):
        """从文档抽取概念页（List[WikiConcept]），按内容缓存。"""
        cache = self._res('.discoveries/ccpt-' + gen_objs_md5(content, context) + '.yaml')
        r = read_yaml_model(cache, List[WikiConcept])
        if not r:
            user = CONCEPT_EXT_PMT.format(content=content, context=context or '（尚无 wiki 内容）')
            r = self._json(List[WikiConcept], CONCEPT_EXT_SYSTEM, user)
            write_yaml_model(cache, r)
        return r

    def tool_detect_contradictions(self, page_content, new_content):
        """比较已有页面与新内容，返回矛盾描述列表（List[str]）。"""
        ck = gen_objs_md5(page_content, new_content)
        cache = self._res('.discoveries/contra-' + ck + '.yaml')
        r = read_yaml_model(cache, List[str])
        if not r:
            user = CONTRADICTION_PMT.format(page_content=page_content, new_content=new_content)
            r = self._json(List[str], CONTRADICTION_SYSTEM, user)
            write_yaml_model(cache, r)
        return r

    def tool_update_overview(self, new_summary):
        """根据当前 overview/index 与来源摘要，重写 wiki/overview.md。"""
        ck = gen_objs_md5(new_summary)
        cache = self._res('.discoveries/ov-" + ck + ".yaml') if False else None
        idx = path.isfile(self._res('wiki/index.md')) and read_text(self._res('wiki/index.md')) or ''
        ov = path.isfile(self._res('wiki/overview.md')) and read_text(self._res('wiki/overview.md')) or ''
        user = OVERVIEW_UPDATE_PMT.format(overview=ov, index=idx, new_summary=new_summary)
        new_ov = self._text(OVERVIEW_UPDATE_SYSTEM, user)
        write_text(self._res('wiki/overview.md'), new_ov)
        return {'saved': 'wiki/overview.md'}

    # ============================================================
    # 四、页面写入 / index / log
    # ============================================================
    def _render(self, page) -> str:
        """把页面模型渲染为带 frontmatter 的 Markdown。"""
        pd = page.dict()
        meta = {
            'title': pd.get('title') or pd.get('name'),
            'type': pd.get('type'),
            'tags': pd.get('tags', []),
            'sources': pd.get('sources', []),
            'last_updated': pd.get('updated') or pd.get('last_updated'),
        }
        body = _render_body(page)
        return f'---\n{yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()}\n---\n\n{body}'

    def _page_path(self, page) -> str:
        sect = PAGE_SECTION.get(page.type)
        if not sect:
            raise ValueError(f'未知页面类型：{page.type}')
        name = getattr(page, 'name', '') or page.title
        return self._res(f'wiki/{sect}/{name}.md')

    def tool_write_page(self, page):
        """写入一张 wiki 页面（WikiEntity/WikiConcept/WikiSourceSummary 等），返回路径与是否更新。"""
        if isinstance(page, dict):
            page = _model_from_page(page)
        dst = self._page_path(page)
        existed = path.isfile(dst)
        write_text(dst, self._render(page))
        return {'path': path.relpath(dst, self.wiki_root).replace(os.sep, '/'),
                'updated': existed}

    def tool_update_index(self):
        """根据 wiki 目录重建 index.md（按 section 分节）。"""
        lines = ['# Wiki Index',
                 f'_Last updated: {basename_today()}_', '']
        for sect in ['sources', 'entities', 'concepts', 'syntheses']:
            lines.append(f'## {sect.capitalize()}')
            for f in self._list_md(f'wiki/{sect}'):
                title = path.splitext(f)[0]
                lines.append(f'- [{title}](wiki/{sect}/{f})')
            lines.append('')
        write_text(self._res('wiki/index.md'), '\n'.join(lines))
        return {'saved': 'wiki/index.md'}

    def tool_append_log(self, action, title, files=None):
        """向 wiki/log.md 追加一条操作记录。"""
        import datetime
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        entry = f'## [{ts}] {action} | {title}'
        if files:
            entry += '\n' + '\n'.join(f'  - {f}' for f in files)
        with open(self._res('wiki/log.md'), 'a', encoding='utf8') as f:
            f.write(entry + '\n')
        return {'appended': action}

    def tool_ingest_summary(self, source_file, source_slug, pages_created, pages_updated, contradictions):
        """汇总一次 ingest 的交付摘要（IngestSummary）。"""
        return IngestSummary(
            source_file=source_file, source_slug=source_slug,
            pages_created=pages_created or [], pages_updated=pages_updated or [],
            contradictions=contradictions or [],
        )

    # ============================================================
    # 五、Query
    # ============================================================
    def tool_query(self, question, pages):
        """基于给定页面内容回答查询（QueryResult，按问答缓存）。"""
        cache = self._res('.discoveries/q-' + gen_objs_md5(question, pages) + '.yaml')
        r = read_yaml_model(cache, QueryResult)
        if not r:
            user = QUERY_PMT.format(pages=pages, question=question)
            r = self._json(QueryResult, QUERY_SYSTEM, user)
            r.question = question
            write_yaml_model(cache, r)
        return r

    # ============================================================
    # 六、Lint
    # ============================================================
    def tool_lint(self):
        """运行健康检查：确定性 + 语义分析，输出 LintReport 并写入 outputs/lint-YYYY-MM-DD.md。"""
        pages = {}
        for sect in ['sources', 'entities', 'concepts']:
            for f in self._list_md(f'wiki/{sect}'):
                pages[f'wiki/{sect}/{f}'] = read_text(self._res(f'wiki/{sect}/{f}'))
        issues: List[LintIssue] = []
        # 1. 断链 + 孤儿
        page_names = {path.splitext(k.split('/')[-1])[0]: k for k in pages}
        referenced = set()
        for k, content in pages.items():
            for m in WIKILINK_RE.findall(content):
                ref = path.splitext(m.split('/')[-1])[0]
                referenced.add(ref)
                if ref not in page_names:
                    issues.append(LintIssue(
                        category='broken_link', page=k, severity='medium',
                        title=f'断链 [[{m}]]', detail='指向不存在的页面'))
        orphaned = [k for k in pages if path.splitext(k.split('/')[-1])[0] not in referenced]
        for k in orphaned:
            issues.append(LintIssue(category='orphan', page=k, severity='low',
                                    title='孤儿页', detail='无任何页面链接到它'))
        # 2. index 一致性
        idx = path.isfile(self._res('wiki/index.md')) and read_text(self._res('wiki/index.md')) or ''
        for k in pages:
            if path.splitext(k)[0] not in idx and path.splitext(k.split('/')[-1])[0] not in idx:
                issues.append(LintIssue(category='index_inconsistency', page=k, severity='medium',
                                        title='未登记到 index', detail='index.md 缺少该页'))
        # 3. 语义分析（抽样，LLM，缓存）
        sample = '\n\n'.join(f'===== {k} =====\n{v}' for k, v in list(pages.items())[:20])
        cache = self._res('.discoveries/lint-' + gen_objs_md5(sample[:20000]) + '.yaml')
        semantic = read_yaml_model(cache, None)
        if not semantic:
            try:
                user = LINT_SEMANTIC_PMT.format(pages=sample)
                semantic = self._json(dict, LINT_SEMANTIC_SYSTEM, user)
                write_yaml_model(cache, semantic)
            except Exception as e:
                semantic = {'issues': [], 'suggestions': [f'语义分析失败：{e}']}
        issues += [LintIssue(**it) for it in (semantic or {}).get('issues', []) if isinstance(it, dict)]
        suggestions = (semantic or {}).get('suggestions', [])
        # 健康分
        score = max(0.0, 100.0 - 12 * len(issues))
        report = LintReport(
            issues=issues,
            orphaned_pages=orphaned,
            broken_links=[i.page for i in issues if i.category == 'broken_link'],
            index_inconsistent=[i.page for i in issues if i.category == 'index_inconsistency'],
            missing_entities=[i.title for i in issues if i.category == 'missing_entity'],
            suggestions=suggestions,
            health_score=score,
        )
        fname = f'outputs/lint-{basename_today()}.md'
        write_text(self._res(fname), _render_lint_report(report))
        report.saved_path = fname
        return report

    # ============================================================
    # 七、Graph
    # ============================================================
    def tool_build_graph(self, skip_infer=False):
        """构建知识图谱 graph.json + graph.html（自包含 vis.js）。"""
        pages = {}
        for sect in ['sources', 'entities', 'concepts', 'syntheses']:
            for f in self._list_md(f'wiki/{sect}'):
                pages[f'wiki/{sect}/{f}'] = read_text(self._res(f'wiki/{sect}/{f}'))
        nodes = []
        edges = []
        name_to_id = {}
        for k in pages:
            label = path.splitext(k.split('/')[-1])[0]
            name_to_id[label.lower()] = k
            nodes.append(GraphNode(id=k, label=label, type=k.split('/')[1]))
        # 显式 wikilink → EXTRACTED
        for src, content in pages.items():
            for m in WIKILINK_RE.findall(content):
                target_label = path.splitext(m.split('/')[-1])[0].lower()
                tgt = name_to_id.get(target_label)
                if tgt and tgt != src:
                    edges.append(GraphEdge(source=src, target=tgt, type='EXTRACTED'))
        # 语义推断 → INFERRED（可选，LLM，缓存）
        if not skip_infer:
            sample = '\n'.join(f'===== {k} =====\n{p[:400]}' for k, p in pages.items())
            cache = self._res('.discoveries/graph-' + gen_objs_md5(sample[:30000]) + '.yaml')
            inferred = read_yaml_model(cache, None)
            if not inferred:
                try:
                    inferred = self._json(List[GraphEdge], GRAPH_INFER_SYSTEM,
                                          GRAPH_INFER_PMT.format(pages=sample))
                    write_yaml_model(cache, inferred)
                except Exception:
                    inferred = []
            known_ids = set(pages)
            for e in inferred:
                if e.source in known_ids and e.target in known_ids and e.source != e.target:
                    if e.confidence >= 0.5:
                        edges.append(e)
        # 去重边（同一 source→target 只留一条，EXTRACTED 优先）
        seen = {}
        for e in edges:
            key = (e.source, e.target)
            if key not in seen or seen[key].type == 'INFERRED':
                seen[key] = e
        edges = list(seen.values())
        # degree
        deg = {}
        for e in edges:
            deg[e.source] = deg.get(e.source, 0) + 1
            deg[e.target] = deg.get(e.target, 0) + 1
        for n in nodes:
            n.degree = deg.get(n.id, 0)
        result = GraphResult(
            nodes=nodes, edges=edges,
            node_count=len(nodes), edge_count=len(edges),
        )
        # GRAPH_TEMPLATE 内嵌，避免外部文件
        write_text(self._res('graph/graph.json'),
                   json.dumps({'build_date': result.build_date,
                               'nodes': [n.dict() for n in nodes],
                               'edges': [e.dict() for e in edges]},
                              ensure_ascii=False, indent=2))
        result.graph_json_path = 'graph/graph.json'
        try:
            html = _render_graph_html(nodes, edges, result.build_date)
            write_text(self._res('graph/graph.html'), html)
            result.graph_html_path = 'graph/graph.html'
        except Exception as e:
            result.graph_html_path = f'(html 生成失败: {e})'
        return result

    # ============================================================
    # 八、Discover
    # ============================================================
    def tool_discover(self, config, gaps, history):
        """根据 topics + gaps 规划要抓取的来源（URL 计划），LLM + 缓存。"""
        cfg = config.dict() if isinstance(config, WikiConfig) else config
        topics = [{'name': t.get('name'), 'keywords': t.get('keywords', [])}
                  for t in cfg.get('topics', [])]
        plan_in = {
            'topics': topics,
            'gaps': list(getattr(gaps, 'gaps', []) or []) if not isinstance(gaps, list) else gaps,
            'history_count': len(history) if isinstance(history, list) else 0,
        }
        cache = self._res('.discoveries/dis-' + gen_objs_md5(json.dumps(plan_in, ensure_ascii=False)) + '.yaml')
        r = read_yaml_model(cache, List[Dict[str, str]])
        if not r:
            user = DISCOVER_PMT.format(config=json.dumps(plan_in, ensure_ascii=False))
            r = self._json(List[Dict[str, str]], DISCOVER_SYSTEM, user)
            write_yaml_model(cache, r)
        # 去重：跳过 history 中已有 URL
        if isinstance(history, list):
            r = [s for s in r if s.get('url') not in history]
        return r

    # ============================================================
    # 九、特殊命令：book-summary / competitive-brief / interview-prep
    # ============================================================
    def _wiki_content_for_analysis(self, limit=12000):
        idx = path.isfile(self._res('wiki/index.md')) and read_text(self._res('wiki/index.md')) or ''
        pages = []
        for sect in ['sources', 'entities', 'concepts']:
            for f in self._list_md(f'wiki/{sect}')[:25]:
                pages.append(f'===== wiki/{sect}/{f} =====\n' + read_text(self._res(f'wiki/{sect}/{f}'))[:3000])
        return idx + '\n\n' + '\n\n'.join(pages)[:limit]

    def tool_book_summary(self):
        """生成书籍结构总结（BookSummary），保存到 outputs/。"""
        content = self._wiki_content_for_analysis()
        cache = self._res('.discoveries/book-' + gen_objs_md5(content) + '.yaml')
        r = read_yaml_model(cache, BookSummary)
        if not r:
            r = self._json(BookSummary, BOOK_SUMMARY_SYSTEM, BOOK_SUMMARY_PMT.format(wiki_content=content))
            write_yaml_model(cache, r)
        fname = f'outputs/book-summary-{basename_today()}.md'
        write_text(self._res(fname), _render_book_summary(r))
        r.saved_path = fname
        return r

    def tool_competitive_brief(self, name):
        """生成竞品 battlecard（CompetitiveBrief），保存到 outputs/。"""
        content = self._wiki_content_for_analysis()
        cache = self._res('.discoveries/brief-' + gen_objs_md5(name, content) + '.yaml')
        r = read_yaml_model(cache, CompetitiveBrief)
        if not r:
            r = self._json(CompetitiveBrief, BRIEF_SYSTEM, BRIEF_PMT.format(name=name, context=content))
            r.competitor = name
            write_yaml_model(cache, r)
        slug = to_kebab(name)
        fname = f'outputs/battlecard-{slug}-{basename_today()}.md'
        write_text(self._res(fname), _render_brief(r))
        r.saved_path = fname
        return r

    def tool_interview_prep(self, company):
        """生成面试准备清单（InterviewPrep），保存到 outputs/。"""
        content = self._wiki_content_for_analysis()
        cache = self._res('.discoveries/interview-' + gen_objs_md5(company, content) + '.yaml')
        r = read_yaml_model(cache, InterviewPrep)
        if not r:
            r = self._json(InterviewPrep, INTERVIEW_SYSTEM,
                           INTERVIEW_PMT.format(company=company, context=content))
            r.company = company
            write_yaml_model(cache, r)
        slug = to_kebab(company)
        fname = f'outputs/interview-prep-{slug}-{basename_today()}.md'
        write_text(self._res(fname), _render_interview(r))
        r.saved_path = fname
        return r

    # 保留旧 book 工作流（文档 → 词条草稿）
    def tool_build_chunks(self, text, fname=''):
        """（旧工作流）将 Markdown 文本切分为文本块。"""
        cache_fname = path.join(self.pj_dir, 'chunks_' + gen_objs_md5(text, fname) + '.yaml')
        r = read_yaml_model(cache_fname, ChunkList)
        if r: return r
        from .md2skill_chunker import chunk_markdown
        cres = chunk_markdown(text, path.basename(fname))
        r = ChunkList(chunks=[
            WikiChunk(id=f"chunk_{i + 1:03d}", chunk=c.content, title=' > '.join(c.heading_path))
            for i, c in enumerate(cres.chunks)
        ])
        write_yaml_model(cache_fname, r)
        return r

    def tool_extract_candidates(self, chunk):
        """（旧工作流）从单个文本块抽取候选词条。"""
        cache_fname = path.join(self.pj_dir, 'cand_' + gen_objs_md5(chunk) + '.yaml')
        r = read_yaml_model(cache_fname, CandidateItems)
        if r: return r
        from .md2wiki_pmt import EXT_SYSTEM_PROMPT, EXT_PMT
        user = EXT_PMT.format(text=chunk.chunk)
        parse = lambda s: CandidateItems.model_validate_json(ext_code_block(s))
        r = self._call(EXT_SYSTEM_PROMPT, user, parse_output=parse)
        for it in r.items:
            it.title = it.title or chunk.title
            it.chunks = list(dict.fromkeys((it.chunks or []) + [chunk.chunk]))
        write_yaml_model(cache_fname, r)
        return r

    def tool_make_draft(self, item):
        """（旧工作流）为单个词条写 Wiki 初稿。"""
        cache_fname = path.join(self.pj_dir, 'draft_' + gen_objs_md5(item) + '.yaml')
        r = read_yaml_model(cache_fname, WikiItem)
        if r: return r
        from .md2wiki_pmt import ITEM_TMPL_MAP, WIKI_DRAFT_SYSTEM_PROMPT, DRAFT_USER_PMT, TERM_TMPL
        origin = '\n\n'.join(f'{i + 1}.  {l}' for i, l in enumerate(item.chunks))
        tmpl = ITEM_TMPL_MAP.get(item.type, TERM_TMPL)
        user = DRAFT_USER_PMT.format(origin=origin, name=item.name, tmpl=tmpl)
        draft = self._call(WIKI_DRAFT_SYSTEM_PROMPT, user)
        draft = draft.replace('[content]', '').replace('[/content]', '').strip()
        r = item.model_copy(update={'draft': draft})
        write_yaml_model(cache_fname, r)
        return r

    # ── 工具参数 Schema ─────────────────────────────────────
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # ── Workspace / Config ──────────────────────────────
        "tool_workspace_show": params_schema(),
        "tool_workspace_set": params_schema(required=['path'],
            path=base_schema('string', '新 wiki 工作区绝对路径')),
        "tool_read_config": params_schema(),
        "tool_write_config": params_schema(required=['config'],
            config=model_schema(WikiConfig, 'WikiConfig 配置')),
        # ── raw / 上下文 ────────────────────────────────────
        "tool_list_raw_sources": params_schema(),
        "tool_read_raw": params_schema(required=['fname'],
            fname=base_schema('string', 'raw/ 下相对路径')),
        "tool_read_gaps": params_schema(),
        "tool_write_gaps": params_schema(required=['gaps'],
            gaps=model_schema(DiscoveryGaps, '知识缺口')),
        "tool_list_pages": params_schema(),
        "tool_read_wiki_context": params_schema(),
        "tool_read_page": params_schema(required=['fname'],
            fname=base_schema('string', 'wiki/ 下页面路径')),
        "tool_read_sources_bulk": params_schema(required=['fnames'],
            fnames=str_list_schema('页面路径列表')),
        "tool_input_source": params_schema(required=['source_path'],
            source_path=base_schema('string', '要归档到 raw/ 的文件路径'),
            topic=base_schema('string', 'topic slug，默认 inbox')),
        "tool_extract_content": params_schema(required=['source_file'],
            source_file=base_schema('string', 'raw/ 下文件路径')),
        "tool_check_ingested": params_schema(required=['slug'],
            slug=base_schema('string', '文件名/slug')),
        # ── Ingest ──────────────────────────────────────────
        "tool_summarize_source": params_schema(required=['content'],
            content=base_schema('string', '原始文档内容'),
            title=base_schema('string', '标题'),
            source_file=base_schema('string', 'raw/ 下路径'),
            source_type=base_schema('string', 'markdown|pdf|docx|pptx|xlsx|image'),
            date=base_schema('string', '发布日期')),
        "tool_extract_entities": params_schema(required=['content'],
            content=base_schema('string', '文档内容'),
            context=base_schema('string', '已知 wiki 上下文')),
        "tool_extract_concepts": params_schema(required=['content'],
            content=base_schema('string', '文档内容'),
            context=base_schema('string', '已知 wiki 上下文')),
        "tool_detect_contradictions": params_schema(required=['page_content', 'new_content'],
            page_content=base_schema('string', '已有页面内容'),
            new_content=base_schema('string', '新文档内容')),
        "tool_update_overview": params_schema(required=['new_summary'],
            new_summary=base_schema('string', '新来源摘要')),
        # ── 写页 / index / log ──────────────────────────────
        "tool_write_page": params_schema(required=['page'],
            page=base_schema('object', 'wiki 页面对象（含 type 字段，结构见各模型）')),
        "tool_update_index": params_schema(),
        "tool_append_log": params_schema(required=['action', 'title'],
            action=base_schema('string', '操作名 ingest|query|lint|graph|discover'),
            title=base_schema('string', '标题'),
            files=str_list_schema('涉及文件')),
        "tool_ingest_summary": params_schema(required=['source_file'],
            source_file=base_schema('string', '原始文件'),
            source_slug=base_schema('string', '来源 slug'),
            pages_created=str_list_schema('新建页面'),
            pages_updated=str_list_schema('更新页面'),
            contradictions=str_list_schema('矛盾')),
        # ── Query / Lint / Graph / Discover ─────────────────
        "tool_query": params_schema(required=['question'],
            question=base_schema('string', '问题'),
            pages=base_schema('string', '相关页面内容')),
        "tool_lint": params_schema(),
        "tool_build_graph": params_schema(
            skip_infer=base_schema('boolean', '跳过语义推断（快速模式）')),
        "tool_discover": params_schema(required=['config'],
            config=model_schema(WikiConfig, 'WikiConfig'),
            gaps=base_schema('string', '知识缺口 JSON'),
            history=str_list_schema('已处理来源')),
        # ── 特殊命令 ────────────────────────────────────────
        "tool_book_summary": params_schema(),
        "tool_competitive_brief": params_schema(required=['name'],
            name=base_schema('string', '竞品名称')),
        "tool_interview_prep": params_schema(required=['company'],
            company=base_schema('string', '公司名称')),
        # 旧 book 工作流
        "tool_build_chunks": params_schema(required=['text'],
            text=base_schema('string', '待切分文本'), fname=base_schema('string', '源文件名')),
        "tool_extract_candidates": params_schema(required=['chunk'],
            chunk=model_schema(WikiChunk, '文本块')),
        "tool_make_draft": params_schema(required=['item'],
            item=model_schema(WikiItem, '候选词条')),
    }


# ============================================================================
# 页面渲染辅助
# ============================================================================
def basename_today() -> str:
    import datetime
    return datetime.datetime.now().strftime('%Y-%m-%d')


def _model_from_page(d: dict) -> BaseModel:
    """把页面 dict 反序列化为对应类型模型。"""
    pt = d.get('type')
    if pt == 'entity':
        return WikiEntity(**d)
    if pt == 'concept':
        return WikiConcept(**d)
    if pt == 'synthesis':
        return WikiSynthesis(**d)
    if pt == 'change':
        return WikiChange(**d)
    return WikiSourceSummary(**d)


def _render_body(page) -> str:
    """按类型渲染页面正文 Markdown。"""
    if isinstance(page, WikiSourceSummary):
        parts = [f'# {page.title}', '', '## Summary', str(page.summary), '',
                 '## Key Claims', *_bullets(page.key_takeaways), '',
                 '## Key Quotes / Key Data']
        for q in page.notable_quotes:
            parts += [f'> {q}', '']
        parts += ['## Connections'] + _bullets(page.entities_mentioned + page.concepts_mentioned)
        if getattr(page, 'raw_path', ''):
            parts += ['', f'## Source', f'- [{page.raw_path}]({page.raw_path})']
        return '\n'.join(parts)
    if isinstance(page, WikiEntity):
        return '\n'.join([f'# {page.title}', '', page.description, '', '## Overview',
                          str(page.overview), '', '## Key Points'] + _bullets(page.key_points) +
                         ['', '## Links'] + _bullets(page.links) +
                         ['', '## Sources'] + _bullets(page.sources))
    if isinstance(page, WikiConcept):
        return '\n'.join([f'# {page.title}', '', page.definition, '', '## How It Works',
                          str(page.how_it_works), '', '## Examples'] + _bullets(page.examples) +
                         ['', '## Links'] + _bullets(page.links) +
                         ['', '## Sources'] + _bullets(page.sources))
    if isinstance(page, WikiSynthesis):
        return '\n'.join([f'# {page.title}', '', f'**Question:** {page.question}', '',
                          '## Analysis', str(page.analysis), '', '## Conclusion',
                          str(page.conclusion), '', '## Sources Used'] + _bullets(page.sources_used))
    return str(getattr(page, 'description', '') or '')


def _bullets(items) -> List[str]:
    return ['- ' + str(i) for i in (items or []) if i]


def _render_lint_report(r: LintReport) -> str:
    lines = [f'# Lint Report · {basename_today()}', '', f'**Health score:** {r.health_score:.0f}/100', '']
    cats = {'orphan': '孤儿页', 'broken_link': '断链', 'index_inconsistency': 'Index 不一致',
            'missing_entity': '缺失实体页', 'contradiction': '内容矛盾', 'outdated': '过期摘要',
            'underdeveloped': '单薄概念', 'gap': '知识缺口'}
    for c in ['orphan', 'broken_link', 'index_inconsistency', 'missing_entity',
              'contradiction', 'outdated', 'underdeveloped', 'gap']:
        sub = [i for i in r.issues if i.category == c]
        if sub:
            lines += [f'## {cats.get(c, c)} ({len(sub)})', '']
            for i in sub:
                lines.append(f'- **[{i.severity}]** {i.page}: {i.title} — {i.detail}')
            lines.append('')
    if r.suggestions:
        lines += ['## Suggestions', ''] + _bullets(r.suggestions)
    return '\n'.join(lines)


def _render_book_summary(r: BookSummary) -> str:
    lines = [f'# Book Summary · {basename_today()}', '']
    if r.characters:
        lines += ['## Characters', '', '| Name | Role | Faction | Status |', '|---|---|---|---|']
        for c in r.characters:
            lines += [f"| {c.get('name', '')} | {c.get('role', '')} | {c.get('faction', '')} | {c.get('status', '')} |"]
        lines.append('')
    if r.timeline:
        lines += ['## Timeline', ''] + _bullets([f"{e.get('date', '')}: {e.get('event', '')}" for e in r.timeline]) + ['']
    for sec, key in [('Factions', 'factions'), ('Locations', 'locations')]:
        items = getattr(r, key) or []
        if items:
            lines += [f'## {sec}', '']
            for i in items:
                lines += [f"- **{i.get('name', i.get('description', ''))}**: {i.get('goals', i.get('description', ''))}"]
            lines.append('')
    for sec in ['themes', 'mysteries', 'quotes']:
        items = getattr(r, sec) or []
        if items:
            lines += [f'## {sec.capitalize()}', ''] + _bullets(items) + ['']
    return '\n'.join(lines)


def _render_brief(r: CompetitiveBrief) -> str:
    lines = [f'# Battlecard · {r.competitor}', '', f'**Positioning:** {r.positioning}', '']
    if r.pricing:
        lines += ['## Pricing', '', '| Tier | Price | Notes |', '|---|---|---|']
        for p in r.pricing:
            lines += [f"| {p.get('tier', '')} | {p.get('price', '')} | {p.get('notes', '')} |"]
        lines.append('')
    for sec, key in [('Top Features', 'features'), ('Recent Moves', 'recent_moves'),
                     ('Known Weaknesses', 'weaknesses'), ('Job Signals', 'job_signals')]:
        items = getattr(r, key) or []
        if items:
            lines += [f'## {sec}', ''] + _bullets(items) + ['']
    if r.differentiation:
        lines += ['## How We Differ', '', r.differentiation, '']
    return '\n'.join(lines)


def _render_interview(r: InterviewPrep) -> str:
    lines = [f'# Interview Prep · {r.company}', '', '## Overview', r.overview, '']
    for sec, key in [('Tech Stack', 'tech_stack'), ('Culture Signals', 'culture_signals'),
                     ('Recent News', 'recent_news'), ('Interview Process', 'interview_process'),
                     ('Known Questions', 'known_questions'), ('Green Flags', 'green_flags'),
                     ('Red Flags', 'red_flags'), ('Questions to Ask', 'questions_to_ask')]:
        items = getattr(r, key) or []
        if items:
            lines += [f'## {sec}', ''] + _bullets(items) + ['']
    if r.compensation:
        lines += ['## Compensation', r.compensation, '']
    return '\n'.join(lines)


def _render_graph_html(nodes: List[GraphNode], edges: List[GraphEdge], build_date: str) -> str:
    """生成自包含的 vis.js 知识图谱 HTML。"""
    color = {'source': '#4A90D9', 'entity': '#E8A838', 'concept': '#5BA85A', 'synthesis': '#9B59B6'}
    node_data = [
        {'id': n.id, 'label': n.label, 'color': color.get(n.type, '#888'), 'group': n.type}
        for n in nodes
    ]
    edge_data = [
        {'from': e.source, 'to': e.target, 'label': e.label or e.type,
         'color': '#F50057' if e.type == 'INFERRED' else '#444', 'dashes': e.type == 'INFERRED'}
        for e in edges
    ]
    import html as html_mod
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Wiki Graph · {build_date}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>html,body{{margin:0;height:100%}}#net{{width:100%;height:100%}}</style></head>
<body><div id="net"></div>
<script>
const DATA = {json.dumps({'nodes': node_data, 'edges': edge_data}, ensure_ascii=False)};
const container = document.getElementById('net');
const nodes = new vis.DataSet(DATA.nodes);
const edges = new vis.DataSet(DATA.edges);
new vis.Network(container, {{nodes, edges}}, {{
  physics: {{barnesHut: {{gravitationalConstant: -4000}}}}, groups: {{
    source: {{borderWidth: 0}}, entity: {{borderWidth: 0}}, concept: {{borderWidth: 0}}, synthesis: {{borderWidth: 0}}
  }}
}});
</script></body></html>"""


# ============================================================================
# System Prompts（中文）
# ============================================================================
SOURCE_SUMMARY_SYSTEM = '你是一位 wiki 编辑，负责为原始文档生成结构化来源摘要页（JSON 输出）。'
ENTITY_EXT_SYSTEM = '你是一位 wiki 编辑，负责从文档抽取值得独立成页的实体（JSON 数组输出）。'
CONCEPT_EXT_SYSTEM = '你是一位 wiki 编辑，负责从文档抽取值得独立成页的概念（JSON 数组输出）。'
CONTRADICTION_SYSTEM = '你是一位 wiki 编辑，负责比较已有页面与新内容并指出观点矛盾（JSON 数组输出）。'
OVERVIEW_UPDATE_SYSTEM = '你是一位 wiki 编辑，负责综合新来源更新 overview.md（输出完整 Markdown，用 [content] 包裹）。'
QUERY_SYSTEM = '你是一位 wiki 检索助手，仅基于给定页面内容回答问题（JSON 输出）。'
LINT_SEMANTIC_SYSTEM = '你是一位 wiki 质量审查员，对页面样本做语义健康检查（JSON 输出）。'
GRAPH_INFER_SYSTEM = '你是一位知识图谱构建器，基于页面内容推断隐含语义关联边（JSON 数组输出）。'
DISCOVER_SYSTEM = '你是一位信息搜集员，根据 topics 与 gaps 规划要抓取的来源（JSON 数组输出）。'
BOOK_SUMMARY_SYSTEM = '你是一位书籍/主题分析员，基于 wiki 内容生成结构化总结（JSON 输出）。'
BRIEF_SYSTEM = '你是一位竞争情报分析师，为竞品生成 battlecard（JSON 输出）。'
INTERVIEW_SYSTEM = '你是一位招聘情报分析师，为公司生成面试准备清单（JSON 输出）。'


DISCOVER_PMT = '''
根据以下配置规划要抓取的新来源（2-6 个）。

## 配置（topics + gaps + 已处理数）

[content]
{config}
[/content]

输出 JSON 数组（三个反引号包裹），每项：
- url：来源 URL
- title：建议标题
- topic：对应 topic slug

```
[
  {{"url":"https://...","title":"...","topic":"..."}}
]
```
只输出与 topics/gaps 相关的来源。
'''
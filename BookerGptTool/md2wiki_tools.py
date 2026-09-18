import logging
import os
from os import path
from typing import List, Optional, Dict, Any, Callable

from .util import ext_code_block, gen_objs_md5, read_yaml_model, write_yaml_model, read_text
from .openai import *
from .md2wiki_models import *
from .md2wiki_pmt import *
from .md2skill_chunker import chunk_markdown

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class Md2WikiTools(ToolsMixin):
    """统一 Wiki 词条智能体"""

    def __init__(self, args):
        """初始化工具集：保存参数、配置 OpenAI、并创建项目输出目录。"""
        super(ToolsMixin, self).__init__()
        set_openai_props(args)
        self.args = args
        self.model = args.model
        self.temperature = getattr(args, 'temp', 0.0)
        self.max_tokens = getattr(args, 'max_tokens', 2000)
        self.retry = getattr(args, 'retry', 3)
        self.stream = getattr(args, 'stream', False)
        self.pj_dir = (
            path.dirname(args.fname) + '_md2wiki'
            if path.isfile(args.fname) else
            path.abspath(args.fname) + '_md2wiki'
        )
        os.makedirs(self.pj_dir, exist_ok=True)

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

    def tool_list_input_files(self) -> List[str]:
        """获取待处理的 Markdown 文件。"""
        if path.isfile(self.args.fname):
            fnames = [self.args.fname]
        elif path.isdir(self.args.fname):
            fnames = [
                path.join(self.args.fname, fname)
                for fname in os.listdir(self.args.fname)
            ]
        else:
            fnames = []
        return [fname for fname in fnames if fname.endswith('.md')]

    def tool_read_input_file(self, fname) -> str:
        """读取待处理的 Markdown 文件。"""
        return read_text(fname)

    def tool_build_chunks(self, text: str, fname: str = '') -> ChunkList:
        """将 Markdown 文本切分为语义完整的文本块。"""
        cache_fname = path.join(
            self.pj_dir,
            'chunks_' + gen_objs_md5(text, fname) + '.yaml'
        )
        r = read_yaml_model(cache_fname, ChunkList)
        if r: return r
        cres = chunk_markdown(text, path.basename(fname))
        r = ChunkList(chunks=[
            WikiChunk(
                id=f"chunk_{i + 1:03d}",
                chunk=c.content,
                title=' > '.join(c.heading_path),
            )
            for i, c in enumerate(cres.chunks)
        ])
        write_yaml_model(cache_fname, r)
        return r

    def tool_extract_candidates(self, chunk: WikiChunk) -> CandidateItems:
        """从单个文本块中抽取候选词条。"""
        cache_fname = path.join(
            self.pj_dir,
            'cand_' + gen_objs_md5(chunk) + '.yaml'
        )
        r = read_yaml_model(cache_fname, CandidateItems)
        if r: return r
        user_prompt = EXT_PMT.format(text=chunk.chunk)
        parse_output = lambda s: CandidateItems.model_validate_json(ext_code_block(s))
        r = self._call(EXT_SYSTEM_PROMPT, user_prompt, parse_output=parse_output)
        # 回填：无标题时继承文本块标题，并挂接原始素材，供后续起草溯源
        for it in r.items:
            it.title = it.title or chunk.title
            it.chunks = list(dict.fromkeys((it.chunks or []) + [chunk.chunk]))
        write_yaml_model(cache_fname, r)
        return r

    def tool_make_draft(self, item: WikiItem) -> WikiItem:
        """为单个词条编写 Wiki 初稿（返回带 draft 的词条）。"""
        cache_fname = path.join(
            self.pj_dir,
            'draft_' + gen_objs_md5(item) + '.yaml'
        )
        r = read_yaml_model(cache_fname, WikiItem)
        if r: return r
        origin = '\n\n'.join(f'{i + 1}.  {l}' for i, l in enumerate(item.chunks))
        tmpl = ITEM_TMPL_MAP.get(item.type, TERM_TMPL)
        user_prompt = DRAFT_USER_PMT.format(
            origin=origin, name=item.name, tmpl=tmpl,
        )
        draft = self._call(WIKI_DRAFT_SYSTEM_PROMPT, user_prompt)
        draft = draft.replace('[content]', '') \
            .replace('[/content]', '').strip()
        r = item.model_copy(update={'draft': draft})
        write_yaml_model(cache_fname, r)
        return r

    # 工具名 -> OpenAI parameters 结构（type/properties/required）。
    # pydantic 模型参数用 Model.schema() 展开，不写死 {"type":"object"}。
    _TOOL_PARAMS: Dict[str, Dict[str, Any]] = {
        **ToolsMixin._TOOL_PARAMS,
        # ── Wiki 词条工具 ──────────────────────────────────────
        "tool_list_input_files": params_schema(),
        "tool_read_input_file": params_schema(
            required=["fname"],
            fname=base_schema("string", "要读取的文件"),
        ),
        "tool_build_chunks": params_schema(
            required=['text'],
            text=base_schema('string', '待切分的 Markdown 文本'),
            fname=base_schema('string', '源文件名，用于上下文，可选'),
        ),
        "tool_extract_candidates": params_schema(
            required=['chunk'],
            chunk=model_schema(WikiChunk, '单个文本块（WikiChunk）'),
        ),
        "tool_make_draft": params_schema(
            required=['item'],
            item=model_schema(WikiItem, '候选词条（WikiItem）'),
        ),
    }

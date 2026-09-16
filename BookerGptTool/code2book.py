import re
import copy
import openai
import os
from os import path
import yaml
import json_repair
import json
import logging
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from threading import Lock
from typing import List
from pydantic import parse_obj_as
from tqdm import tqdm

from .util import (
    extname,
    ext_code_block,
    ext_cont_block,
    render_prompt,
)
from .openai import logger as oai_logger
from .openai import ask_chatgpt_retry, set_openai_props
from .code2book_pmt import *
from .code2book_models import *

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


from .code2book_agent import Code2BookAgent, expand_stars

class Code2BookMixin:

    @staticmethod
    def _outline_check_problem(
        outline: List[OutlineChapterResult],
        part_fnames: List[str],
    ):
        outline_fnames = [
            f.replace('\\', '/')
            for o in outline
            for n in o.nodes
            for f in n.src
        ]
        rest_fnames = set(part_fnames) - set(outline_fnames)
        false_fnames = set(outline_fnames) - set(part_fnames)
        prob = ''
        if rest_fnames:
            prob += '以下文件在大纲中未出现：\n' + \
                    '\n'.join(rest_fnames) + '\n'
        if false_fnames:
            prob += '以下文件在源码目录中不存在：\n' + \
                    '\n'.join(false_fnames) + '\n'
        return prob

    @staticmethod
    def _part_check_problem(
        parts:List[PartClusResult], 
        total_fnames: List[str]
    ):   
        total_fnames = set(total_fnames)
        exi_fnames = {
            f for p in parts for f in p.files
        }
        false_fnames = exi_fnames - total_fnames
        rest_fnames = total_fnames - exi_fnames
        prob = ''
        if false_fnames:
            prob += f'以下文件在源码目录中不存在：\n' + \
                    '\n'.join(false_fnames) + '\n'
        if rest_fnames:
            prob += '以下文件没有添加到任何部分中：\n' + \
                    '\n'.join(rest_fnames) + '\n'
        return prob

    @staticmethod
    def _detail_check_problem(
        detail: Detail, 
        total_funcs: List[str]
    ):
        total_funcs = set(total_funcs)
        detail_funcs = __class__._detail_funcs(detail)
        detail_funcs = set(expand_stars(detail_funcs, total_funcs))
        rest_funcs = total_funcs - detail_funcs
        false_funcs = detail_funcs - total_funcs
        prob = ''
        if false_funcs:
            prob += f'以下函数或方法在源文件中不存在：\n' + \
                    '\n'.join(false_funcs) + '\n'
        if rest_funcs:
            prob += '以下函数或方法没有添加到任何单元中：\n' + \
                    '\n'.join(rest_funcs) + '\n'
        return prob

    @staticmethod
    def _code_desc_ch(detail: Detail, code_desc: List[CodeDescItemResult]):
        code_fnames = [
            c.file
            for u in detail.units
            for c in u.codes
        ]
        code_fname_set = set(code_fnames)
        code_desc_ch = [
            d for d in code_desc 
            if d.file in code_fname_set
        ]
        return code_desc_ch

    # ── 持久化工具 ──────────────────────────────────────────

    def _load_yaml(self, fname: str, model: type):
        if path.isfile(fname) and \
           path.getsize(fname):
            try:
                obj = yaml.safe_load(
                    open(fname, encoding='utf8').read())
            except yaml.error.YAMLError:
                return None
            return parse_obj_as(model, obj)

    def _write_yaml(self, fname: str, obj: BaseModel | List[BaseModel]):
        """在主线程中将 meta 写回 yaml 文件。"""
        if isinstance(obj, BaseModel):
            obj = obj.dict()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        with open(fname, 'w', encoding='utf8') as f:
            f.write(yaml.safe_dump(obj, allow_unicode=True))
            f.flush()

    def _collect_hdls(self, 
        res_callback: Optional[Callable] = None,
        write_callback: Optional[Callable] = None,
    ) -> None:
        """等待所有已提交任务完成并清空。
        on_done: 每个子线程完成后在主线程中调用的回调。
        """
        save_step = max(min(len(self.hdls) // 5, 100), 1)
        for i, h in enumerate(self.hdls):
            r = h.result()
            if res_callback: res_callback(r)
            if write_callback and \
               (i % save_step == 0 or i == len(self.hdls) - 1):
               write_callback()
        self.hdls = []

    @staticmethod
    def _code_descs_total_funcs(
        code_descs: List[CodeDescItemResult],
    ) -> List[str]:
        total_funcs = [
            d.file + ':' + fn.name
            for d in code_descs
            for fn in d.funcs
        ]
        total_funcs += [
            d.file + ':' + cls_.name + '.' + m.name
            for d in code_descs
            for cls_ in d.classes
            for m in cls_.methods
        ]
        total_funcs = {
            it.replace('\\', '/').replace('()', '')
            for it in total_funcs
        }
        return total_funcs

    @staticmethod
    def _detail_funcs(detail: Detail):
        detail_funcs = [
            c.file + ':' + c.method_or_func
            for u in detail.units
            for c in u.codes
        ]
        detail_funcs = {
            it.replace('\\', '/').replace('()', '')
            for it in detail_funcs
        }
        return detail_funcs


class Code2BookCheckOrchestrator(Code2BookMixin):

    def __init__(self, args):
        self.args = args
        self.agent = Code2BookAgent( args)
        self.pj_dir = args.dir
        self.pool = ThreadPoolExecutor(args.threads)
        self.hdls: List[Future] = []

    def _tr_check_detail(
        self, 
        detail_fname: str, 
        outline_chs: List[OutlineChapterResult],
        code_desc: List[CodeDescItemResult],
    ):
        idx = int(re.search(r'detail_(\d+)\.yaml', detail_fname).group(1))
        logger.warn(f'[2] 校验细纲 {idx+1}')
        detail = self._load_yaml(detail_fname, Detail)
        if detail is None:
            logger.warn(f'[2] 细纲 {idx+1} 加载失败')
            return
        code_desc_ch = self._code_desc_ch(detail, code_desc)
        total_funcs = self._code_descs_total_funcs(code_desc_ch)

        for _ in range(self.args.check):
            prob = self._detail_check_problem(detail, total_funcs)
            if not prob:
                logger.info(f'[2] 细纲 {idx+1} 校验通过')
                break
            logger.warn(f'[2] 细纲 {idx+1} 校验失败：\n{prob}')
            detail = self.agent.fix_detail(idx, detail, outline_chs, code_desc_ch, prob)

        self._write_yaml(detail_fname, detail)

    def _check_detail(
        self,
        outline_chs: List[OutlineChapterResult],
        code_desc: List[CodeDescItemResult],
    ):
        logger.info('[2] 校验细纲')
        detail_fnames = [
            path.join(self.pj_dir, f)
            for f in os.listdir(self.pj_dir)
            if re.search(r'^detail_\d+\.yaml$', f)
        ]
        for f in tqdm(detail_fnames):
            h = self.pool.submit(
                self._tr_check_detail,
                f, outline_chs, code_desc,
            )
            self.hdls.append(h)
            if len(self.hdls) > self.args.threads:
                self._collect_hdls()
        self._collect_hdls()
            

    def _tr_check_body(
        self,
        body_fname: str,
        outline_chs: List[OutlineChapterResult], 
        detail: Detail, 
        code_desc: List[CodeDescItemResult],
    ):
        idx = int(re.search(r'article_(\d+)\.md', body_fname).group(1))
        logger.info(f'[3] 校验正文 {idx+1}')
        body = open(body_fname, encoding='utf8').read()
        if not body:
            logger.warn(f'[3] 正文 {idx+1} 加载失败')
            return
        code_desc_ch = self._code_desc_ch(detail, code_desc)
        body = self.agent.gen_body(idx, detail, outline_chs, code_desc_ch)

        # 校验正文
        for _ in range(self.args.check):
            cmt = self.agent.check_body(body, detail)
            if '[PERFECT/]' in cmt:
                logger.info(f'[3] 正文 {idx + 1} 校验完成')
                break
            logger.info(f'[3] 正文 {idx + 1} 校验未通过')
            logger.info(cmt)
            body = self.agent.fix_body(detail, body, cmt, code_desc_ch)

        open(body_fname, 'w', encoding='utf8').write(body)

    def _check_body(
        self,
        outline_chs: List[OutlineChapterResult], 
        code_desc: List[CodeDescItemResult],
    ):
        logger.info('[3] 校验正文')
        body_fnames = [
            path.join(self.pj_dir, f)
            for f in os.listdir(self.pj_dir)
            if re.search(r'^article_\d+\.md$', f)
        ]
        for f in tqdm(body_fnames):
            idx_str = re.search(r'article_(\d+)\.md$', f).group(1)
            detail_fname = path.join(self.pj_dir, f'detail_{idx_str}.yaml')
            detail = self._load_yaml(detail_fname, Detail)
            if detail is None:
                logger.warn(f'[3] {detail_fname} 不存在')
                continue
            h = self.pool.submit(
                self._tr_check_body,
                f, outline_chs, detail, code_desc,
            )
            self.hdls.append(h)
            if len(self.hdls) > self.args.threads:
                self._collect_hdls()
        self._collect_hdls()

    def _check_outline(self, outline_chs, code_desc):
        pass

    def run(self):
        outline_fname = path.join(self.pj_dir, 'outline.yaml')
        outline = self._load_yaml(outline_fname, List[OutlinePartResult])
        if outline is None:
            logger.fatal(f'[1] 大纲加载失败')
            return
        outline_chs = sum([pt.chapters for pt in outline], [])
        code_desc_fnames = [
            path.join(self.pj_dir, f) 
            for f in os.listdir(self.pj_dir)
            if re.search(r'_desc\.yaml$', f)
        ]
        code_desc = [
            self._load_yaml(f, CodeDescItemResult)
            for f in code_desc_fnames
        ]
        code_desc = list(filter(None, code_desc))
        if not code_desc:
            logger.fatal(f'[1] 源码描述加载失败')
            return
        if self.args.check_outline:
            self._check_outline(outline_chs, code_desc)
        if self.args.check_detail:
            self._check_detail(outline_chs, code_desc)
        if self.args.check_body:
            self._check_body(outline_chs, code_desc)
        logger.info('[*] 校验完成')

class Code2BookOrchestrator(Code2BookMixin):
    """编排器：协调文件探索、LLM 调用和持久化，驱动整个 code2book 流程。"""

    SUPPORTED_EXTS = [
        'c', 'h', 'cpp', 'cxx', 'hpp',
        'java', 'cs', 'php', 'go',
        'js', 'ts', 'jsx', 'tsx', 'vue',
        'py', 'pyx', 'pyi', 'pxd',
    ]

    def __init__(self, args):
        self.args = args
        self.agent = Code2BookAgent( args)
        self.pj_dir = path.abspath(args.dir) + '_code2book'
        self.pool = ThreadPoolExecutor(args.threads)
        self.hdls: List[Future] = []

    

    # ── 文件探索 ────────────────────────────────────────────

    def _discover_files(self) -> List[str]:
        """扫描项目目录，返回所有支持的源码文件路径。"""
        fnames = [
            path.join(path.relpath(rt, self.args.dir), f)
                .replace('\\', '/')
            for rt, _, fnames in os.walk(self.args.dir)
            for f in fnames
            if extname(f) in self.SUPPORTED_EXTS
        ]
        fnames.append('README.md')
        return fnames

    def _read_code(self, fname: str) -> str:
        return open(path.join(self.args.dir, fname), encoding='utf8').read()

    def _read_code_dict(self, fnames: List[str]) -> dict:
        return {f: self._read_code(f) for f in fnames}

    def _code_to_str(self, code_dict: dict) -> str:
        return '\n\n'.join([
            f'`{f}`\n\n```\n{code}\n```'
            for f, code in code_dict.items()
        ])

    # ── 步骤 2：生成源码文件描述 ──────────────────────────

    def _tr_gen_code_desc(self, fname: str, idx: int) -> Tuple[int, CodeDescItemResult]:
        logger.info(f'[2] 生成描述 {fname}')
        desc_fname = path.join(
            self.pj_dir,
            fname.replace('/', '----') + '_desc.yaml'
        )
        desc = self._load_yaml(desc_fname, CodeDescItemResult)
        if desc is None:
            code = self._read_code(fname)
            desc = self.agent.gen_code_desc(fname, code)
            desc = CodeDescItemResult(file=fname, **desc.dict())
            self._write_yaml(desc_fname, desc)
        return idx, desc

    def step_gen_code_desc(self, fnames: List[str]) -> List[CodeDescItemResult]:
        logger.info('[2] 生成源码文件描述')
        code_desc = [None for _ in fnames]

        def res_callback(tpl):
            idx, code_desc_i = tpl
            code_desc[idx] = code_desc_i
        for i, f in enumerate(tqdm(fnames)):
            h = self.pool.submit(
                self._tr_gen_code_desc, f, i)
            self.hdls.append(h)
            if len(self.hdls) > self.args.threads:
                self._collect_hdls(res_callback)
        
        self._collect_hdls(res_callback)
        return code_desc

    # ── 步骤 3a：划分部分 ──────────────────────────────────
  
    def step_clus_part(
        self, fnames: List[str]
    ):
        logger.info('[3] 划分部分')
        part_clus_fname = path.join(self.pj_dir, 'parts.yaml')
        parts = self._load_yaml(part_clus_fname, List[PartClusResult])
        if parts is not None:
            return parts

        if len(fnames) <= self.args.chapter_limit:
            parts = [PartClusResult(no=1, title='全书', files=fnames)]
            self._write_yaml(part_clus_fname, parts)
            return parts
        
        total_fnames = set(fnames)
        parts = self.agent.cluster_parts(fnames)
        for pt in parts:
            if 'README.md' not in pt.files:
                pt.files.append('README.md')
        for _ in range(self.args.check):    
            prob = self._part_check_problem(parts, total_fnames)
            if not prob:
                logger.debug('[3] 部分校验通过')
                break
            logger.warn(f'[3] 部分校验失败：\n{prob}')
            parts = self.agent.fix_parts(fnames, parts, prob)
        self._write_yaml(part_clus_fname, parts)
        return parts

    # ── 步骤 3：生成大纲 ──────────────────────────────────

    def _tr_gen_outline(
        self, idx: int,
        part_fnames: List[str], 
        part_code_desc: List[CodeDescItemResult],
    ) -> Tuple[int, List[OutlineChapterResult]]:
        logger.info(f'[3] 生成大纲 {idx+1}')
        readme = open(path.join(self.args.dir, 'README.md'), encoding='utf8').read()
        outline = self.agent.gen_outline(part_fnames, part_code_desc, readme)

        # 校验源码文件完整覆盖
        for _ in range(self.args.check):
            prob = self._outline_check_problem(outline, part_fnames)
            if not prob:
                logger.info(f'[3] 大纲 {idx+1} 校验通过')
                break
            logger.warn(f'[3] 大纲 {idx+1} 校验未通过：\n{prob}')
            outline = self.agent.fix_outline(
                outline, part_fnames, part_code_desc, readme,
                prob,
            )
        return idx, outline

    def step_gen_outline(
        self, 
        parts: List[PartClusResult],
        code_desc: List[CodeDescItemResult],
    ) -> List[OutlinePartResult]:
        logger.info('[3] 生成大纲')
        outline_fname = path.join(self.pj_dir, 'outline.yaml')
        outline = self._load_yaml(outline_fname, List[OutlinePartResult])
        if outline is None:
            outline = [
                OutlinePartResult(**pt.dict(), chapters=[]) 
                for pt in parts
            ]
            self._write_yaml(outline_fname, outline)
        
        save_step = max(min(len(parts) // 5, 100), 1)
        def res_callback(tpl):
            idx, pt_outline = tpl
            outline[idx].chapters = pt_outline
        for i, pt in enumerate(tqdm(parts)):
            if not outline[i].chapters:
                pt_fnames_set = set(pt.files)
                pt_code_desc = [
                    it for it in code_desc 
                    if it.file in pt_fnames_set
                ]
                h = self.pool.submit(
                    self._tr_gen_outline,
                    i, pt.files, pt_code_desc
                )
                self.hdls.append(h)
                if len(self.hdls) > self.args.threads:
                    self._collect_hdls(res_callback)
            if i % save_step == 0:
                self._write_yaml(outline_fname, outline)

        self._collect_hdls(res_callback)
        # 重排章节序号
        idx = 1
        for pt in outline:
            for ch in pt.chapters:
                ch.no = idx
                idx += 1
        self._write_yaml(outline_fname, outline)
        return outline

    # ── 步骤 4：生成细纲 ──────────────────────────────────

    def _tr_gen_detail(
        self, 
        outline_chs: List[OutlineChapterResult], 
        idx: int,
        code_desc: List[CodeDescItemResult],
    ) -> Tuple[int, Detail]:
        logger.info(f'[4] 编写第{idx+1}章细纲')


        l = len(str(len(outline_chs)))
        detail_fname = f'detail_{str(idx+1).zfill(l)}.yaml'
        detail_fname = path.join(self.pj_dir, detail_fname)
        detail = self._load_yaml(detail_fname, Detail)
        if detail is not None:
            return idx, detail
        
        code_desc_ch = self._code_desc_ch(detail, code_desc)
        total_funcs = self._code_descs_total_funcs(code_desc_ch)
        # 源码解析部分
        src_anls_result = self.agent.gen_src_anls_detail(idx, outline_chs, code_desc_ch)
        # 剩余部分
        rest_result = self.agent.gen_rest_detail(idx, src_anls_result, outline_chs, code_desc_ch)
        detail = Detail(no=idx, **src_anls_result.dict(), **rest_result.dict())

        for _ in range(self.args.check):
            detail_funcs = self._detail_funcs(detail)
            detail_funcs = set(expand_stars(detail_funcs, total_funcs))
            rest_funcs = total_funcs - detail_funcs
            false_funcs = detail_funcs - total_funcs
            if not rest_funcs and not false_funcs:
                logger.info(f'[4] 细纲 {idx+1} 校验通过')
                break
            prob = ''
            if false_funcs:
                prob += f'以下函数或方法在源文件中不存在：\n' + \
                        '\n'.join(false_funcs) + '\n'
            if rest_funcs:
                prob += '以下函数或方法没有添加到任何单元中：\n' + \
                        '\n'.join(rest_funcs) + '\n'
            logger.warn(f'[4] 细纲 {idx+1} 校验失败：\n{prob}')
            detail = self.agent.fix_detail(idx, detail, outline_chs, code_desc_ch, prob)

        self._write_yaml(detail_fname, detail)
        return idx, detail

    def step_gen_details(
        self, outline_chs: List[OutlineChapterResult],
        code_desc: List[CodeDescItemResult],
    ) -> List[Detail]:
        logger.info('[4] 生成细纲')
        l = len(str(len(outline_chs)))
        details = [None for i in range(len(outline_chs)) ]
        
        def res_callback(tpl):
            idx, detail = tpl
            details[idx] = detail

        for i, ch in enumerate(tqdm(outline_chs)):
            h = self.pool.submit(
                self._tr_gen_detail, outline_chs, i, code_desc)
            self.hdls.append(h)
            if len(self.hdls) > self.args.threads:
                self._collect_hdls(res_callback)
        self._collect_hdls(res_callback)

        return details

    # ── 步骤 5：生成正文 ──────────────────────────────────

    def _tr_gen_body(
        self, 
        outline_chs, 
        detail: Detail, 
        code_desc: List[CodeDescItemResult],
        idx: int,
    ) -> Tuple[int, str]:
        logger.info(f'[5] 编写第{idx+1}章正文')
        
        l = len(str(len(outline_chs)))
        body_fname = f'article_{str(idx+1).zfill(l)}.md'
        body_fname = path.join(self.pj_dir, body_fname)
        if path.isfile(body_fname) and \
           path.getsize(body_fname):
            body = open(body_fname, encoding='utf8').read()
            return idx, body
        
        code_desc_ch = self._code_desc_ch(detail, code_desc)
        body = self.agent.gen_body(idx, detail, outline_chs, code_desc_ch)

        # 校验正文
        logger.info(f'[5] 校验正文 {idx + 1}')
        for _ in range(self.args.check):
            cmt = self.agent.check_body(body, detail)
            if '[PERFECT/]' in cmt:
                logger.info(f'[5] 正文 {idx + 1} 校验完成')
                break
            logger.info(f'[5] 正文 {idx + 1} 校验未通过')
            logger.info(cmt)
            body = self.agent.fix_body(detail, body, cmt, code_desc_ch)

        open(body_fname, 'w', encoding='utf8').write(body)

        return idx, body


    def step_gen_bodies(
        self, outline_chs, details: List[Detail],
        code_desc: List[CodeDescItemResult],
    ) -> List[str]:
        logger.info('[5] 生成正文')
        l = len(str(len(outline_chs)))
        bodies = ['' for _ in range(len(details))]
        
        def res_callback(tpl):
            idx, body = tpl
            bodies[idx] = body
        
        for i, detail in enumerate(tqdm(details)):
            h = self.pool.submit(
                self._tr_gen_body, outline_chs,
                detail, code_desc, i,
            )
            self.hdls.append(h)
            if len(self.hdls) > self.args.threads:
                self._collect_hdls(res_callback)
        self._collect_hdls(res_callback)

        return bodies

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        logger.info(self.args)
        if not path.isdir(self.args.dir):
            logger.fatal('请提供项目目录！')
            return
        if not path.isfile(path.join(self.args.dir, 'README.md')):
            logger.fatal('该项目没有 README.md 文件')
            return
        os.makedirs(self.pj_dir, exist_ok=True)

        # 1. 探索项目结构
        logger.info('[1] 探索项目结构')
        fnames = self._discover_files()
        logger.info('\n'.join(fnames))

        # 2. 生成源码文件描述
        code_desc = self.step_gen_code_desc(fnames)
        code_desc_nocode = copy.deepcopy(code_desc)
        for d in code_desc_nocode:
            for c in d.classes:
                for m in c.methods:
                    m.code = []
            for f in d.funcs:
                f.code = []

        # 3a 划分部分
        parts = self.step_clus_part(fnames)

        # 3. 生成大纲
        outline = self.step_gen_outline(parts, code_desc_nocode)

        # 4. 生成细纲
        outline_chs = sum([pt.chapters for pt in outline], [])
        details = self.step_gen_details(outline_chs, code_desc)

        # 5. 生成正文
        self.step_gen_bodies(outline_chs, details, code_desc)

        logger.info('[*] 已完成')

def code2book_check(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    orchestrator = Code2BookCheckOrchestrator(args)
    orchestrator.run()

def code2book(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    orchestrator = Code2BookOrchestrator(args)
    orchestrator.run()


def reg_subparser(subparsers):
    code2book_parser = subparsers.add_parser("code2book", help="code to book")
    code2book_parser.add_argument('dir', help='proj dir name')
    code2book_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    code2book_parser.add_argument("-c", "--check", type=int, default=3, help="check times")
    code2book_parser.add_argument("-l", "--chapter-limit", type=int, default=20, help="chapter limit")
    code2book_parser.add_argument("-D", "--debug", action='store_true', help="debug mode")
    code2book_parser.add_argument("-cl", "--code-limit", type=int, default=35_000, help="max code length in single prompt")
    code2book_parser.add_argument("-co", "--code-overlap", type=int, default=500, help="code overlap in every chunk")
    code2book_parser.set_defaults(func=code2book)

    code2book_parser = subparsers.add_parser("code2book-check", help="code to book checker")
    code2book_parser.add_argument('dir', help='proj dir name ending with _code2book')
    code2book_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    code2book_parser.add_argument("-c", "--check", type=int, default=3, help="check times")
    code2book_parser.add_argument("-D", "--debug", action='store_true', help="debug mode")
    code2book_parser.add_argument("-co", "--check-outline", action='store_true', help="whether to check outline")
    code2book_parser.add_argument("-cd", "--check-detail", action='store_true', help="whether to check detail")
    code2book_parser.add_argument("-cb", "--check-body", action='store_true', help="whether to check body")
    code2book_parser.set_defaults(func=code2book_check)

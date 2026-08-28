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

from .util import (
    ask_chatgpt_retry,
    set_openai_props,
    extname,
    ext_code_block,
    ext_cont_block,
    logger as util_logger
)
from .code2book_pmt import *
from .code2book_models import *

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def expand_dbl_star(ptns, files):
    res = []
    for p in ptns:
        if not p.endswith('**'):
            res.append(p)
        else:
            res += [f for f in files if f.startswith(p[:-2])]
    return res

class Code2BookAgent:
    """封装所有 LLM 调用，每个方法对应一个独立的 prompt 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args
        set_openai_props(self.args)

    def fix_parts(
        self, files: List[str], 
        parts: List[PartClusResult], problem: str
    ) -> List[PartClusResult]:
        parts_str = json.dumps([p.dict() for p in parts])
        ques = PT_FIX_PMT.replace('{files}', '\n'.join(files)) \
            .replace('{problem}', problem) \
            .replace('{parts}', parts_str)
        parse_output = lambda s: parse_obj_as(
            List[PartClusResult],
            json_repair.loads(ext_code_block(s))
        )
        parts =  ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for pt in parts:
            pt.files = expand_dbl_star(pt.files, files)
        return parts

    def cluster_parts(self, files: List[str]) -> List[PartClusResult]:
        ques = PT_CLUS_PMT.replace('{files}', '\n'.join(files))
        parse_output = lambda s: parse_obj_as(
            List[PartClusResult],
            json_repair.loads(ext_code_block(s))
        )
        parts =  ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )
        for pt in parts:
            pt.files = expand_dbl_star(pt.files, files)
        return parts

    def gen_code_desc(self, fname: str, code: str) -> ClsFuncExtResult:
        """根据源码提取类、方法和全局函数描述。"""
        ques = CLS_FUNC_EXT_PMT.replace('{fname}', fname) \
            .replace('{code}', code)
        parse_output = lambda s: ClsFuncExtResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_outline(
        self, fnames: List[str], code_desc: List[CodeDescItemResult], readme: str,
    ) -> List[OutlineChapterResult]:
        """根据项目结构和源码描述生成书籍大纲。"""
        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme)
        parse_output = lambda s: parse_obj_as(
            List[OutlineChapterResult],
            json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def fix_outline(
        self, outline: List[OutlineChapterResult], fnames: List[str],
        code_desc: List[CodeDescItemResult], readme: str, problem: str,
    ) -> List[OutlineChapterResult]:
        """校验大纲未覆盖所有文件时，补充缺少的源码文件重写大纲。"""
        outline_str = json.dumps(
            [o.dict() for o in outline], 
            ensure_ascii=False
        )
        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        ques = OUTLINE_FIX_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{outline}', outline_str) \
            .replace('{problem}', problem)
        parse_output = lambda s: parse_obj_as(
            List[OutlineChapterResult],
            json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_src_anls_detail(
        self, idx: int, outline_chs: List[OutlineChapterResult], code_str: str,
    ) -> SrcAnlsDetailResult:
        """生成第 idx 章细纲的源码解析部分。"""
        outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )
        ques = SRC_ANLS_DETAIL_PMT.replace('{i}', str(idx + 1)) \
            .replace('{outline}', outline_str) \
            .replace('{code}', code_str)
        parse_output = lambda s: SrcAnlsDetailResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_rest_detail(
        self, idx: int, detail: Detail, outline_chs: List[OutlineChapterResult], code_str: str,
    ) -> RestDetailResult:
        """生成第 idx 章细纲的剩余部分（学习目标、类比、练习等）。"""
        outline_str =  outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )
        ques = REST_DETAIL_PMT.replace('{detail}', detail.json()) \
            .replace('{outline}', outline_str) \
            .replace('{i}', str(idx + 1)) \
            .replace('{code}', code_str)
        parse_output = lambda s: RestDetailResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def fix_detail(
        self, idx: int, detail: Detail, outline_chs: List[OutlineChapterResult],
        code_str: str, problem: str,
    ) -> List[Detail]:
        """校验细纲未覆盖所有函数时，补充缺少的函数重写细纲。"""
        outline_str = json.dumps(
            [c.dict() for c in outline_chs], 
            ensure_ascii=False
        )        
        ques = DETAIL_FIX_PMT \
            .replace('{i}', str(idx)) \
            .replace('{outline}', outline_str) \
            .replace('{detail}', detail.json()) \
            .replace('{code}', code_str) \
            .replace('{problem}', problem)
        parse_output = lambda s: parse_obj_as(
            List[Detail], json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_body(
        self, idx: int, detail: Detail, outline_chs: List[OutlineChapterResult], code_str: str,
    ) -> str:
        """根据大纲和细纲生成第 idx 章正文。"""
        outline_str = json.dumps(outline_chs, ensure_ascii=False)
        detail_str = json.dumps(detail.dict(), ensure_ascii=False)
        ques = BODY_PMT.replace('{detail}', detail_str) \
            .replace('{outline}', outline_str) \
            .replace('{code}', code_str) \
            .replace('{i}', str(idx + 1))
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def check_body(self, body: str, detail: Detail) -> str:
        """校验正文是否符合格式规范，返回修改意见或 [PERFECT/]。"""
        detail_str = detail.json()
        ques = BODY_CHK_PMT.replace('{body}', body) \
            .replace('{detail}', detail_str)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def fix_body(
        self, detail_str: str, body: str, comment: str, code_str: str,
    ) -> str:
        """根据修改意见和对应源码修改正文。"""
        ques = BODY_FIX_PMT.replace('{detail}', detail_str) \
            .replace('{body}', body) \
            .replace('{comment}', comment) \
            .replace('{code}', code_str)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )


class Code2BookOrchestrator:
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
    
    # ── 持久化工具 ──────────────────────────────────────────

    def _write_yaml(self, fname, obj):
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
        code = self._read_code(fname)
        descs = self.agent.gen_code_desc(fname, code)
        return idx, CodeDescItemResult(file=fname, **descs.dict())

    def step_gen_code_desc(self, fnames: List[str]) -> List[CodeDescItemResult]:
        logger.info('[2] 生成源码文件描述')
        code_desc_fname = path.join(self.pj_dir, 'code_desc.yaml')
        if path.isfile(code_desc_fname) and \
           path.getsize(code_desc_fname):
            code_desc = yaml.safe_load(
                open(code_desc_fname, encoding='utf8').read())
            code_desc = parse_obj_as(
                List[CodeDescItemResult], code_desc,
            )
        else:
            code_desc = [
                CodeDescItemResult(
                    file=f, desc='', classes=[], funcs=[],
                )
                for f in fnames
            ]
            self._write_yaml(code_desc_fname, code_desc)

        save_step = max(min(len(code_desc) // 5, 100), 1)
        def res_callback(tpl):
            idx, code_desc_i = tpl
            code_desc[idx] = code_desc_i
        for i, it in enumerate(code_desc):
            if not it.desc:
                h = self.pool.submit(
                    self._tr_gen_code_desc, it.file, i)
                self.hdls.append(h)
                if len(self.hdls) > self.args.threads:
                    self._collect_hdls(res_callback)
            if i % save_step == 0:
                self._write_yaml(code_desc_fname, code_desc)
        
        self._collect_hdls(res_callback)
        self._write_yaml(code_desc_fname, code_desc)                
        return code_desc

    # ── 步骤 3a：划分部分 ──────────────────────────────────
  
    def step_clus_part(
        self, fnames: List[str]
    ):
        logger.info('[3] 划分部分')
        part_clus_fname = path.join(self.pj_dir, 'parts.yaml')
        if path.isfile(part_clus_fname):
            parts = yaml.safe_load(
                open(part_clus_fname, encoding='utf8').read())
            return parse_obj_as(List[PartClusResult], parts)

        if len(fnames) <= self.args.chapter_limit:
            parts = [PartClusResult(no=1, title='全书', files=fnames)]
            self._write_yaml(part_clus_fname, parts)
            return parts

        parts = self.agent.cluster_parts(fnames)
        for pt in parts:
            if 'README.md' not in pt.files:
                pt.files.append('README.md')
        for _ in range(self.args.check):
            total_fnames = set(fnames)
            exi_fnames = {
                f for p in parts for f in p.files
            }
            false_fnames = exi_fnames - total_fnames
            rest_fnames = total_fnames - exi_fnames
            if not false_fnames and not rest_fnames:
                logger.debug('[3] 部分校验通过')
                break

            prob = ''
            if false_fnames:
                prob += f'以下文件在源码目录中不存在：\n' + \
                        '\n'.join(false_fnames) + '\n'
            if rest_fnames:
                prob += '以下文件没有添加到任何部分中：\n' + \
                        '\n'.join(rest_fnames) + '\n'
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
            outline_fnames = [
                f.replace('\\', '/')
                for o in outline
                for n in o.nodes
                for f in n.src
            ]
            rest_fnames = set(part_fnames) - set(outline_fnames)
            false_fnames = set(outline_fnames) - set(part_fnames)
            if not rest_fnames and not false_fnames:
                logger.info(f'[3] 大纲 {idx+1} 校验通过')
                break
            prob = ''
            if rest_fnames:
                prob += '以下文件在大纲中未出现：\n' + \
                        '\n'.join(rest_fnames) + '\n'
            if false_fnames:
                prob += '以下文件在源码目录中不存在：\n' + \
                        '\n'.join(false_fnames) + '\n'
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

        if path.isfile(outline_fname):
            outline = yaml.safe_load(
                open(outline_fname, encoding='utf8').read())
            outline = parse_obj_as(List[OutlinePartResult], outline)
        else:
            outline = [
                OutlinePartResult(**pt.dict(), chapters=[]) 
                for pt in parts
            ]
            self._write_yaml(outline_fname, outline)
        
        save_step = max(min(len(parts) // 5, 100), 1)
        def res_callback(tpl):
            idx, pt_outline = tpl
            outline[idx].chapters = pt_outline
            done = sum(1 for ch in outline[idx].chapters if ch)
            logger.warn(f'写回 {tpl}，完成 {done}')
        for i, pt in enumerate(parts):
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
        code_fnames = [
            f for pt in outline_chs[idx].nodes
              for f in pt.src
        ]
        code_fname_set = set(code_fnames)
        code_dict = self._read_code_dict(code_fnames)
        code_str = self._code_to_str(code_dict)
        code_desc_ch = [
            d for d in code_desc 
            if d.file in code_fname_set
        ]
        total_funcs = [
            d.file + ':' + fn.name
            for d in code_desc_ch
            for fn in d.funcs
        ]
        total_funcs += [
            code_desc_ch.file + ':' + cls_.name + '.' + m.name
            for d in code_desc_ch
            for cls_ in d.classes
            for m in cls_.methods
        ]
        total_funcs = {
            it.replace('\\', '/').replace('()', '')
            for it in total_funcs
        }

        # 源码解析部分
        src_anls_result = self.agent.gen_src_anls_detail(idx, outline_chs, code_str)
        # 剩余部分
        rest_result = self.agent.gen_rest_detail(idx, src_anls_result, outline_chs, code_str)
        detail = Detail(**src_anls_result.dict(), **rest_result.dict())

        for _ in range(self.args.check):
            detail_funcs = [
                c.file + ':' + c.method_or_func
                for u in detail.units
                for c in u.codes
            ]
            detail_funcs = {
                it.replace('\\', '/').replace('()', '')
                for it in detail_funcs
            }
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
            detail = self.agent.fix_detail(idx, detail, outline_chs, code_str, prob)


        return idx, detail

    def step_gen_details(
        self, outline_chs: List[OutlineChapterResult],
        code_desc: List[CodeDescItemResult],
    ) -> List[Detail]:
        logger.info('[4] 生成细纲')
        details: List[Detail] = []
        
        def res_callback(tpl):
            idx, detail = tpl
            details[idx] = detail
            # 持久化
            detail_fname = path.join(self.pj_dir, f'detail_{idx+1}.yaml')
            self._write_yaml(detail_fname, details[i])
        for i, ch in enumerate(outline_chs):
            detail_fname = path.join(self.pj_dir, f'detail_{i+1}.yaml')
            if path.isfile(detail_fname):
                detail = yaml.safe_load(
                    open(detail_fname, encoding='utf8').read())
                details.append(Detail(**detail))
            else:
                details.append(Detail())
                h = self.pool.submit(
                    self._tr_gen_detail, outline_chs, i, code_desc)
                self.hdls.append(h)
                if len(self.hdls) > self.args.threads:
                    self._collect_hdls(res_callback)
        self._collect_hdls(res_callback)

        return details

    # ── 步骤 5：生成正文 ──────────────────────────────────

    def _tr_gen_body(
        self, outline_chs, detail: Detail, idx: int,
    ) -> Tuple[int, str]:
        logger.info(f'[5] 编写第{idx+1}章正文')
        code_fnames = [
            c.file
            for u in detail.units
            for c in u.codes
        ]
        code_dict = self._read_code_dict(code_fnames)
        code_str = self._code_to_str(code_dict)

        body = self.agent.gen_body(idx, detail, outline_chs, code_str)

        # 校验正文
        logger.info(f'[5] 校验正文 {idx + 1}')
        for _ in range(self.args.check):
            cmt = self.agent.check_body(body, detail)
            if '[PERFECT/]' in cmt:
                logger.info(f'[5] 正文 {idx + 1} 校验完成')
                break
            logger.info(f'[5] 正文 {idx + 1} 校验未通过')
            logger.info(cmt)
            body = self.agent.fix_body(detail, body, cmt, code_str)

        return idx, body


    def step_gen_bodies(
        self, outline_chs, details: List[Detail],
    ) -> List[str]:
        logger.info('[5] 生成正文')
        bodies: List[str] = []
        
        for i, detail in enumerate(details):
            body_fname = path.join(self.pj_dir, f'article_{i+1}.md')
            if path.isfile(body_fname):
                body = open(body_fname, encoding='utf8').read()
                bodies.append(body)
                continue
            bodies.append('')
            h = self.pool.submit(
                self._tr_gen_body, outline_chs,
                detail, i,
            )
            self.hdls.append(h)
        def res_callback(tpl):
            idx, body = tpl
            bodies[idx] = body
            body_fname = path.join(self.pj_dir, f'body_{idx+1}.md')
            open(body_fname, 'w', encoding='utf8').write(body)
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

        # 3a 划分部分
        parts = self.step_clus_part(fnames)

        # 3. 生成大纲
        outline = self.step_gen_outline(parts, code_desc)

        # 4. 生成细纲
        outline_chs = sum([pt.chapters for pt in outline], [])
        details = self.step_gen_details(outline_chs, code_desc)

        # 5. 生成正文
        self.step_gen_bodies(outline_chs, details)

        logger.info('[*] 已完成')


def code2book(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        util_logger.setLevel(logging.DEBUG)
    orchestrator = Code2BookOrchestrator(args)
    orchestrator.run()

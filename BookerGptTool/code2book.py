import openai
import os
from os import path
import yaml
import json_repair
import json
import functools
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import List
from pydantic import parse_obj_as

from .util import ask_chatgpt_retry, set_openai_props, extname, ext_code_block, ext_cont_block
from .code2book_pmt import *
from .code2book_models import *


class Code2BookAgent:
    """封装所有 LLM 调用，每个方法对应一个独立的 prompt 调用。"""

    def __init__(self, args):
        self.model = args.model
        self.args = args

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
    ) -> OutlineResult:
        """根据项目结构和源码描述生成书籍大纲。"""
        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        ques = OUTLINE_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme)
        parse_output = lambda s: OutlineResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def fix_outline(
        self, outline: OutlineResult, fnames_li: str,
        code_desc_str: str, readme: str, rest_fnames: str,
    ) -> OutlineResult:
        """校验大纲未覆盖所有文件时，补充缺少的源码文件重写大纲。"""
        outline_str = json.dumps(outline.dict(), ensure_ascii=False)
        ques = OUTLINE_FIX_PMT.replace('{struct}', fnames_li) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{outline}', outline_str) \
            .replace('{rest_fnames}', rest_fnames)
        parse_output = lambda s: OutlineResult(
            **json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_src_anls_detail(
        self, idx: int, outline_str: str, code_str: str,
    ) -> SrcAnlsDetailResult:
        """生成第 idx 章细纲的源码解析部分。"""
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
        self, idx: int, detail_str: str, outline_str: str, code_str: str,
    ) -> RestDetailResult:
        """生成第 idx 章细纲的剩余部分（学习目标、类比、练习等）。"""
        ques = REST_DETAIL_PMT.replace('{detail}', detail_str) \
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

    def fix_details(
        self, details_str: str, struct: str,
        code_desc_str: str, readme: str, rest_funcs: str,
    ) -> List[Detail]:
        """校验细纲未覆盖所有函数时，补充缺少的函数重写细纲。"""
        ques = DETAIL_FIX_PMT \
            .replace('{details}', details_str) \
            .replace('{struct}', struct) \
            .replace('{code_desc}', code_desc_str) \
            .replace('{readme}', readme) \
            .replace('{rest_funcs}', rest_funcs)
        parse_output = lambda s: parse_obj_as(
            List[Detail], json_repair.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_body(
        self, idx: int, detail: Detail, outline_chs: List[OutlineResult], code_str: str,
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

    def check_body(self, body: str) -> str:
        """校验正文是否符合格式规范，返回修改意见或 [PERFECT/]。"""
        ques = BODY_CHK_PMT.replace('{body}', body)
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
        self.lock = Lock()

    # ── 持久化工具 ──────────────────────────────────────────

    def _write_yaml(self, fname, obj):
        with self.lock:
            with open(fname, 'w', encoding='utf8') as f:
                data = (
                    [r.dict() for r in obj]
                    if isinstance(obj, list)
                    else obj.dict()
                )
                f.write(yaml.safe_dump(data, allow_unicode=True))

    def _write_detail_yaml(self, fname, details, idx):
        with self.lock:
            with open(fname, 'w', encoding='utf8') as f:
                f.write(yaml.safe_dump(details[idx].dict(), allow_unicode=True))

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
        print(f'[2] 生成描述 {fname}')
        code = self._read_code(fname)
        descs = self.agent.gen_code_desc(fname, code)
        return idx, CodeDescItemResult(file=fname, **descs.dict())

    def step_gen_code_desc(self, fnames: List[str]) -> List[CodeDescItemResult]:
        print('[2] 生成源码文件描述')
        code_desc_fname = path.join(self.pj_dir, 'code_desc.yaml')

        if path.isfile(code_desc_fname):
            code_desc = yaml.safe_load(
                open(code_desc_fname, encoding='utf8').read())
            return parse_obj_as(List[CodeDescItemResult], code_desc)

        code_desc = [
            CodeDescItemResult(
                file=f, desc='', process=[], structure=[],
                classes=[], funcs=[],
            )
            for f in fnames
        ]
        self._write_yaml(code_desc_fname, code_desc)

        hdls = []
        for i, it in enumerate(code_desc):
            if it.desc:
                continue
            h = self.pool.submit(
                self._tr_gen_code_desc, code_desc[i].file, i)
            hdls.append(h)
        for h in as_completed(hdls):
            idx, code_desc_i = h.result()
            code_desc[idx] == code_desc_i
            self._write_yaml(code_desc_fname, code_desc)

        return code_desc

    # ── 步骤 3：生成大纲 ──────────────────────────────────

    def _gen_outline_core(
        self, fnames: List[str], code_desc: List[CodeDescItemResult],
    ) -> OutlineResult:
        readme = open(path.join(self.args.dir, 'README.md'), encoding='utf8').read()
        outline = self.agent.gen_outline(fnames, code_desc, readme)

        # 校验源码文件完整覆盖
        for _ in range(self.args.check):
            outline_fnames = [
                f.replace('\\', '/')
                for pt in outline.parts
                for ch in pt.chapters
                for n in ch.nodes
                for f in n.src
            ]
            rest_fnames = list(set(fnames) - set(outline_fnames))
            if len(rest_fnames) == 0:
                print('[3] 校验通过')
                break
            print('[3] 校验未通过')
            print('\n'.join(rest_fnames))
            outline = self.agent.fix_outline(
                outline, fnames, code_desc, readme,
                '\n'.join(rest_fnames),
            )

        return outline

    def step_gen_outline(
        self, fnames: List[str], code_desc: List[CodeDescItemResult],
    ) -> OutlineResult:
        print('[3] 生成大纲')
        outline_fname = path.join(self.pj_dir, 'outline.yaml')

        if path.isfile(outline_fname):
            outline = yaml.safe_load(
                open(outline_fname, encoding='utf8').read())
            return OutlineResult(**outline)

        outline = self._gen_outline_core(fnames, code_desc)
        open(outline_fname, 'w', encoding='utf8') \
            .write(yaml.safe_dump(outline.dict(), allow_unicode=True))
        return outline

    # ── 步骤 4：生成细纲 ──────────────────────────────────

    def _tr_gen_detail(self, outline_chs, idx: int, details: List[Detail]):
        print(f'[4] 编写第{idx+1}章细纲')
        code_fnames = [
            f for pt in outline_chs[idx].nodes
              for f in pt.src
        ]
        code_dict = self._read_code_dict(code_fnames)
        code_str = self._code_to_str(code_dict)
        outline_str = json.dumps(outline_chs, ensure_ascii=False)

        # 源码解析部分
        detail_result = self.agent.gen_src_anls_detail(idx, outline_str, code_str)
        details[idx] = Detail(**details[idx], **detail_result.dict())

        # 剩余部分
        detail_str = json.dumps(details[idx], ensure_ascii=False)
        rest_result = self.agent.gen_rest_detail(idx, detail_str, outline_str, code_str)
        details[idx] = Detail(**details[idx], **rest_result.dict())

    def step_gen_details(
        self, outline_chs, code_desc: List[CodeDescItemResult],
        fnames: List[str],
    ) -> List[Detail]:
        print('[4] 生成细纲')
        details: List[Detail] = []
        hdls = []
        for i, ch in enumerate(outline_chs):
            detail_fname = path.join(self.pj_dir, f'detail_{i+1}.yaml')
            if path.isfile(detail_fname):
                detail = yaml.safe_load(
                    open(detail_fname, encoding='utf8').read())
                details.append(Detail(**detail))
                continue
            details.append(Detail())
            h = self.pool.submit(
                self._tr_gen_detail, outline_chs, i, details)
            hdls.append(h)

        for h in hdls:
            h.result()

        # 持久化
        for i, d in enumerate(details):
            detail_fname = path.join(self.pj_dir, f'detail_{i+1}.yaml')
            self._write_detail_yaml(detail_fname, details, i)

        return details

    # ── 步骤 4b：校验细纲 ────────────────────────────────

    def step_check_details(
        self, details: List[Detail],
        code_desc: List[CodeDescItemResult],
        fnames: List[str],
    ) -> List[Detail]:
        fixed = all(d.fixed for d in details)
        if fixed:
            print('[4] 细纲校验通过')
            return details
        for i, d in enumerate(details):
            d.no = i + 1

        fnames_li = '\n'.join(fnames)
        code_desc_str = json.dumps([d.dict() for d in code_desc], ensure_ascii=False)
        readme = open(path.join(self.args.dir, 'README.md'), encoding='utf8').read()
        total_funcs = [
            cd.file + ':' + fn.name
            for cd in code_desc
            for fn in cd.funcs
        ]
        total_funcs += [
            cd.file + ':' + cls_.name + '.' + m.name
            for cd in code_desc
            for cls_ in cd.classes
            for m in cls_.methods
        ]
        total_funcs = [
            it.replace('\\', '/').replace('()', '')
            for it in total_funcs
        ]

        for _ in range(self.args.check):
            exi_funcs = [
                cd.file + ':' + cd.class_or_func
                for d in details
                for u in d.units
                for cd in u.code
            ]
            exi_funcs = [
                it.replace('\\', '/').replace('()', '')
                for it in exi_funcs
            ]
            rest_funcs = list(set(total_funcs) - set(exi_funcs))
            if len(rest_funcs) == 0:
                print('[4] 细纲校验通过')
                break
            print('[4] 细纲校验未通过')
            print('\n'.join(rest_funcs))
            details_str = json.dumps(
                [d.dict() for d in details], ensure_ascii=False)
            details = self.agent.fix_details(
                details_str, fnames_li, code_desc_str, readme,
                '\n'.join(rest_funcs),
            )
            sorted(details, key=lambda it: it.no)
            for i, d in enumerate(details):
                d.fixed = True
                detail_fname = path.join(self.pj_dir, f'detail_{i+1}.yaml')
                open(detail_fname, 'w', encoding='utf8') \
                    .write(yaml.safe_dump(d.dict(), allow_unicode=True))

        return details

    # ── 步骤 5：生成正文 ──────────────────────────────────

    def _tr_gen_body(
        self, outline_chs, details: List[Detail], idx: int,
        bodies: List[str], fname: str,
    ):
        print(f'[5] 编写第{idx+1}章正文')
        code_fnames = [
            c.file
            for u in details[idx].units
            for c in u.code
        ]
        code_dict = self._read_code_dict(code_fnames)
        code_str = self._code_to_str(code_dict)

        body = self.agent.gen_body(idx, details[idx], outline_chs, code_str)
        bodies[idx] = body
        open(fname, 'w', encoding='utf8').write(body)

        # 校验正文
        print(f'[5] 校验正文 {idx + 1}')
        for _ in range(self.args.check):
            cmt = self.agent.check_body(body)
            if '[PERFECT/]' in cmt:
                print(f'[5] 正文 {idx + 1} 校验完成')
                break
            print(f'[5] 正文 {idx + 1} 校验未通过')
            print(cmt)
            body = self.agent.fix_body(details[idx], body, cmt, code_str)
            bodies[idx] = body
            open(fname, 'w', encoding='utf8').write(body)

    def step_gen_bodies(
        self, outline_chs, details: List[Detail],
    ) -> List[str]:
        print('[5] 生成正文')
        bodies: List[str] = []
        hdls = []
        for i, detail in enumerate(details):
            body_fname = path.join(self.pj_dir, f'body_{i+1}.md')
            if path.isfile(body_fname):
                body = open(body_fname, encoding='utf8').read()
                bodies.append(body)
                continue
            bodies.append('')
            h = self.pool.submit(
                self._tr_gen_body, outline_chs,
                details, i, bodies, body_fname,
            )
            hdls.append(h)

        for h in hdls:
            h.result()

        return bodies

    # ── 主流程 ──────────────────────────────────────────────

    def run(self):
        print(self.args)
        set_openai_props(self.args)
        if not path.isdir(self.args.dir):
            print('请提供项目目录！')
            return
        os.makedirs(self.pj_dir, exist_ok=True)

        # 1. 探索项目结构
        print('[1] 探索项目结构')
        fnames = self._discover_files()
        print('\n'.join(fnames))

        # 2. 生成源码文件描述
        code_desc = self.step_gen_code_desc(fnames)

        # 3. 生成大纲
        outline = self.step_gen_outline(fnames, code_desc)

        # 4. 生成细纲
        outline_chs = sum([pt.chapters for pt in outline.parts], [])
        details = self.step_gen_details(outline_chs, code_desc, fnames)

        # 4b. 校验细纲
        details = self.step_check_details(details, code_desc, fnames)

        # 5. 生成正文
        self.step_gen_bodies(outline_chs, details)

        print('[*] 已完成')


def code2book(args):
    """入口函数：创建编排器并运行。"""
    orchestrator = Code2BookOrchestrator(args)
    orchestrator.run()

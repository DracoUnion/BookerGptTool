import os
import re
import copy
from concurrent.futures import ThreadPoolExecutor
from os import path
import logging
import json_repair as json

from .util import (
    ask_chatgpt_retry,
    set_openai_props,
    ext_code_block,
    ext_cont_block,
    extname,
)
from .code2doc_pmt import *
from .code2doc_models import *

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    'c', 'h', 'cpp', 'cxx', 'hpp',
    'java', 'cs', 'php', 'go',
    'js', 'ts', 'jsx', 'tsx', 'vue',
    'py', 'pyx', 'pyi', 'pxd',
}


class Code2DocAgent:
    """封装 code2doc 的 LLM 调用，每个方法对应一个独立请求。"""

    def __init__(self, args):
        self.args = args
        self.model = args.model
        set_openai_props(self.args)


    def gen_overview(self, code: str) -> OverviewResult:
        """根据源码生成设计文档大纲。"""
        ques = OVVW_PMT.replace('{code}', code)
        parse_output = lambda s: OverviewResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_vars_fields(
        self, code: str, vars_fields: str,
    ) -> VarFieldExtResult:
        """分析全局变量和类字段的类型及描述。"""
        ques = VAR_FLD_EXT_PMT.replace('{code}', code) \
            .replace('{vars}', vars_fields)
        parse_output = lambda s: VarFieldExtResult(
            **json.loads(ext_code_block(s))
        )
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=parse_output,
        )

    def gen_func_method(self, code: str, func_name: str) -> str:
        """分析单个全局函数或类方法。"""
        ques = FUNC_MTD_EXT_PMT.replace('{code}', code) \
            .replace('{func}', func_name)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_key_components(self, code: str) -> str:
        """分析源码中的关键组件。"""
        ques = KEY_CMPN_PMT.replace('{code}', code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_advice(self, code: str) -> str:
        """分析源码中的问题和优化建议。"""
        ques = ADVC_PMT.replace('{code}', code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )

    def gen_others(self, code: str) -> str:
        """分析详细设计文档中的其它补充项目。"""
        ques = ETC_PMT.replace('{code}', code)
        return ask_chatgpt_retry(
            ques, self.model, self.args,
            parse_output=ext_cont_block,
        )


class Code2DocOrchestrator:
    """编排源码分析、文档生成以及文件/目录处理流程。"""

    def __init__(self, args):
        self.args = args
        self.agent = Code2DocAgent(args)


    def run(self):
        """分析单个源码文件并写出 Markdown 设计文档。"""
        fname = self.args.fname
        if extname(fname) not in SUPPORTED_EXTENSIONS:
            logger.fatal(f'{fname} 代码类型不支持')
            return

        ofname = fname + '.md'
        if path.isfile(ofname):
            logger.warn(f'{fname} 已存在')
            return

        logger.info(fname)
        code = open(fname, encoding='utf8').read()

        logger.info('[1] 处理大纲')
        ovvw = self.agent.gen_overview(code)
        desc = ovvw.desc
        process = '\n'.join(ovvw.process)
        structure = '\n'.join(ovvw.structure)

        logger.info('[2] 分析全局变量和类字段')
        fields = [
            f'{class_info.name}.{field_name}'
            for class_info in ovvw.classes
            for field_name in class_info.fields
        ]
        vars_fields = '\n'.join(ovvw.vars + fields)
        jvars = self.agent.gen_vars_fields(code, vars_fields)
        vars_fields_md = build_vars_flds_md(jvars)

        logger.info('[3] 分析全局函数和类方法')
        methods = [
            f'{class_info.name}.{method_name}'
            for class_info in ovvw.classes
            for method_name in class_info.methods
        ]
        func_md_dict = {}
        for func_name in ovvw.funcs + methods:
            logger.info(f'[3] 分析 {func_name}')
            func_md_dict[func_name] = self.agent.gen_func_method(code, func_name)

        funcs_methods_md = '\n\n'.join(func_md_dict.values())

        logger.info('[4] 分析关键组件')
        key_components = self.agent.gen_key_components(code)

        logger.info('[5] 分析改机建议')
        advice = self.agent.gen_advice(code)

        logger.info('[6] 其它')
        others = self.agent.gen_others(code)

        doc = self._build_document(
            fname=fname,
            desc=desc,
            process=process,
            structure=structure,
            vars_fields_md=vars_fields_md,
            funcs_methods_md=funcs_methods_md,
            key_components=key_components,
            advice=advice,
            others=others,
        )
        open(ofname, 'w', encoding='utf8').write(doc)

    @staticmethod
    def _build_document(
        fname: str,
        desc: str,
        process: str,
        structure: str,
        vars_fields_md: str,
        funcs_methods_md: str,
        key_components: str,
        advice: str,
        others: str,
    ) -> str:
        """组装最终 Markdown 文档，不执行 LLM 调用。"""
        return f'''
# `{fname}` 详细设计文档

{desc}

## 整体流程

```mermaid
{process}
```

## 类结构

```
{structure}
```

## 全局变量及字段

{vars_fields_md}


## 全局函数及方法

{funcs_methods_md}

## 关键组件

{key_components}

## 问题及建议

{advice}

## 其它

{others}
    '''


def build_vars_flds_md(jvars: VarFieldExtResult):
    tmpl = '''
### `{name}`

{desc}

类型：`{type}`
    '''
    vars_md = '\n\n'.join(
        tmpl.replace('{name}', var.name)
            .replace('{desc}', var.desc)
            .replace('{type}', var.type)
        for var in jvars.vars
    )

    flds_md = '\n\n'.join(
        tmpl.replace('{name}', field.class_ + '.' + field.name)
            .replace('{desc}', field.desc)
            .replace('{type}', field.type)
        for field in jvars.fields
    )

    return vars_md + '\n\n' + flds_md


def extname(name):
    m = re.search(r'\.(\w+)$', name)
    return m.group(1) if m else ''


# 兼容旧的模块级调用方式；CLI 主流程使用 Code2DocOrchestrator。
def process_file_safe(args):
    """处理单文件并隔离非中断异常。"""
    try:
        Code2DocOrchestrator(args).run()
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.exception('处理文件失败')


def code2doc_handle(args):
    """入口函数：创建编排器并运行。"""
    if path.isfile(args.fname):
        fnames = [args.fname]
    else:
        fnames = [
            path.join(args.fname, f)
            for f in os.listdir(args.fname)
        ]
    fnames = [f for f in fnames if extname(f) in SUPPORTED_EXTENSIONS]
    if not fnames:
        logger.fatal('请提供源码文件或目录')
        return

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for f in fnames:
        args = copy.deepcopy(args)
        args.fname = f
        h = pool.submit(process_file_safe, args)
        hdls.append(h)
    for h in hdls:
        h.result()

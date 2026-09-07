import os
import re
import shutil
import tarfile
from io import BytesIO
import logging
from os import path

import json_repair as json
import requests

from .paper2code_models import *
from .paper2code_pmt import *
from .util import ext_code_block, extname, render_prompt
from .openai import ask_chatgpt_retry, set_openai_props

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

dft_hdrs = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/122.0.0.0 Safari/537.36',
}


from .paper2code_agent import Paper2CodeAgent



class Paper2CodeOrchestrator:
    """编排论文读取、规划、分析和代码生成流程。"""

    def __init__(self, args):
        self.args = args
        self.agent = Paper2CodeAgent(args)
        if args.out is None:
            args.out = args.fname.replace(':', '_') + '_code'
        self.out = args.out

    def run(self):
        """运行完整的 paper2code 流程。"""
        os.makedirs(self.out, exist_ok=True)
        logger.info(self.args)

        logger.info('[Downloading] download arxiv paper')
        paper = self._load_paper()

        logger.info('[Planning] Overall plan')
        plan = self._gen_plan(paper)

        logger.info('"[Planning] Architecture design')
        flist_str = self._gen_file_list(paper, plan)

        logger.info('"[Planning] Logic design')
        tasks_str = self._gen_tasks(paper, plan, flist_str)

        logger.info('[Planning] Configuration file generation')
        cfg_str = self._gen_config(paper, plan, flist_str, tasks_str)

        logic_anls_dict = self._generate_logic_analysis(
            paper, plan, flist_str, tasks_str, cfg_str,
        )
        self._generate_code(
            paper, plan, flist_str, tasks_str, 
            logic_anls_dict,
        )

        logger.info('[DONE]')

    def _load_paper(self) -> str:
        paper_fname = path.join(self.out, 'paper')
        if path.isfile(paper_fname):
            return open(paper_fname, encoding='utf8').read()
        if re.search(r'arxiv:\d+\.\d+', self.args.fname):
            paper = arxiv_id2text(self.args.fname[6:])
            open(paper_fname, 'w', encoding='utf8').write(paper)
            return paper
        if path.isfile(self.args.fname) \
                and extname(self.args.fname) in ['tex', 'md', 'txt']:
            paper = open(self.args.fname, encoding='utf8').read()
            shutil.copy(self.args.fname, paper_fname)
            return paper
        raise ValueError('请提供 MD/TEX/TXT 文件或 ARXIV ID（arxiv:\\d+\\.\\d+）')

    def _gen_plan(self, paper: str) -> str:
        plan_fname = path.join(self.out, 'plan.md')
        if path.isfile(plan_fname):
            return open(plan_fname, encoding='utf8').read()
        plan = self.agent.gen_plan(paper)
        open(plan_fname, 'w', encoding='utf8').write(plan)
        return plan

    def _gen_file_list(self, paper: str, plan: str) -> str:
        flist_fname = path.join(self.out, 'file_list.json')
        if path.isfile(flist_fname):
            return open(flist_fname, encoding='utf8').read()
        flist = self.agent.gen_file_list(paper, plan)
        flist_str = flist.json()
        open(flist_fname, 'w', encoding='utf8').write(flist_str)
        return flist_str

    def _gen_tasks(self, paper: str, plan: str, flist_str: str) -> str:
        tasks_fname = path.join(self.out, 'tasks.json')
        if path.isfile(tasks_fname):
            return open(tasks_fname, encoding='utf8').read()
        tasks = self.agent.gen_tasks(paper, plan, flist_str)
        tasks_str = tasks.json()
        open(tasks_fname, 'w', encoding='utf8').write(tasks_str)
        return tasks_str

    def _gen_config(
        self,
        paper: str,
        plan: str,
        flist_str: str,
        tasks_str: str,
    ) -> str:
        cfg_fname = path.join(self.out, 'config.yaml')
        if path.isfile(cfg_fname):
            return open(cfg_fname, encoding='utf8').read()
        cfg_str = self.agent.gen_config(
            paper, plan, flist_str, tasks_str,
        )
        open(cfg_fname, 'w', encoding='utf8').write(cfg_str)
        return cfg_str

    def _generate_logic_analysis(
        self,
        paper: str,
        plan: str,
        flist_str: str,
        tasks_str: str,
        cfg_str: str,
    ) -> dict:
        task_data = json.loads(tasks_str)
        tasks = task_data['task_list']
        file_descs = task_data['file_descs']

        logic_anls_dict = {}
        for fname in tasks:
            logger.info(f'[ANALYSIS] {fname}')
            la_fname = fname.replace('.', '_') + '_logic_analysis.md'
            la_fname = path.join(self.out, la_fname)
            if path.isfile(la_fname):
                logic_anls_dict[fname] = open(
                    la_fname, encoding='utf8').read()
                continue

            dir_ = path.dirname(la_fname)
            if dir_:
                os.makedirs(dir_, exist_ok=True)
            fdesc = file_descs.get(fname, '“未指定”')
            logic_anls = self.agent.gen_logic_analysis(
                paper, plan, flist_str, tasks_str, cfg_str,
                fname, fdesc,
            )
            logic_anls_dict[fname] = logic_anls
            open(la_fname, 'w', encoding='utf8').write(logic_anls)
        return logic_anls_dict

    def _generate_code(
        self,
        paper: str,
        plan: str,
        flist_str: str,
        tasks_str: str,
        logic_anls_dict: dict,
    ):
        task_data = json.loads(tasks_str)
        tasks = task_data['task_list']

        code_dict = {}
        for fname in tasks:
            logger.info(f'[CODING] {fname}')
            code_fname = path.join(self.out, fname)
            if path.isfile(code_fname):
                code_dict[fname] = open(
                    code_fname, encoding='utf8').read()
                continue

            dir_ = path.dirname(code_fname)
            if dir_:
                os.makedirs(dir_, exist_ok=True)
            done_files = ','.join(code_dict.keys()) or 'none'
            logic_analysis = logic_anls_dict.get(fname, '“未指定”')
            code = self.agent.gen_code(
                paper, plan, flist_str, tasks_str,
                done_files, fname, logic_analysis,
            )
            code_dict[fname] = code
            open(code_fname, 'w', encoding='utf8').write(code)


def arxiv_id2text(aid):
    url = f'https://arxiv.org/src/{aid}'
    data = requests.get(url, headers=dft_hdrs).content
    tar = tarfile.open(fileobj=BytesIO(data), mode='r:gz')
    tex_fnames = [
        name for name in tar.getnames()
        if name.endswith('.tex')
    ]
    if not tex_fnames:
        raise FileNotFoundError('找不到 TEX 文件')
    tex = '\n'.join([
        tar.extractfile(fname).read().decode('utf8')
        for fname in tex_fnames
    ])
    return tex


def ext_chapters(tex):
    title = re.findall(r'\\title\{(.+?)\}', tex)
    if not title:
        raise ValueError('找不到标题')
    abs_ = re.findall(
        r'\\begin\{abstract\}([\s\S]+?)\\end\{abstract\}', tex,
    )
    if not abs_:
        raise ValueError('找不到摘要')
    chs = re.findall(r'\\section\{(.+?)\}([\s\S]+?)(?=\\section|\Z)', tex)
    return title[0], abs_[0], chs


def paper2code(args):
    """入口函数：创建编排器并运行。"""
    return Paper2CodeOrchestrator(args).run()


def reg_subparser(subparsers):
    paper2code_parser = subparsers.add_parser("paper2code", help="summarize arxiv papers")
    paper2code_parser.add_argument("fname", help="MD/TEX/TXT file or ARXIV ID（arxiv:\d+\.\d+）")
    paper2code_parser.add_argument("-o", "--out", type=str, help="output dir name")
    paper2code_parser.set_defaults(func=paper2code)

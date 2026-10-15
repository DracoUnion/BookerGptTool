import re
import os
import logging
from os import path
from concurrent.futures import ThreadPoolExecutor

from .util import extname, read_text, write_text, group_chunks, split_md_lines
from .openai import logger as oai_logger
from .derep_agent import DerepAgent

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class DerepOrchestrator:
    """编排器：把素材分块 → 生成文案 → n-gram 重复度检测 → 修复，循环直到达标。"""

    SUPPORTED_EXTS = ('md', 'txt')

    def __init__(self, args):
        self.args = args
        self.agent = DerepAgent(args)
        self.pj_dir = self.agent.pj_dir
        self.asset_dir = self.agent.asset_dir

    def step_derep_chunk(self, source: str) -> str:
        """对单个素材块：生成文案，检测重复度，不达标则交给 LLM 修复。"""
        copy = self.agent.gen_copy(source)
        for i in range(self.args.round):
            metrics = self.agent.calc_metrics(
                source, copy, self.args.ngram, self.args.threshold
            )
            if not metrics.is_duplicated:
                logger.info(
                    f'[?] 第 {i + 1} 轮检查通过，'
                    f'相似度 {metrics.ngram_similarity:.2f}'
                )
                break
            logger.warn(
                f'[?] 第 {i + 1} 轮重复度过高'
                f'（相似度 {metrics.ngram_similarity:.2f}，'
                f'覆盖率 {metrics.ngram_coverage:.2f}），开始修复'
            )
            copy = self.agent.fix_copy(source, copy, metrics)
        return copy

    def step_derep_file(self, fname: str):
        """处理单个素材文件：分块去重，写入 `_derep.<ext>` 结果文件。"""
        ext = extname(fname)
        ofname = fname[:-(len(ext) + 1)] + '_derep.' + ext
        if path.isfile(ofname):
            logger.warn(f'{fname} 已去重')
            return
        logger.info(f'处理 {fname}')
        md = read_text(fname)
        chunks = group_chunks(split_md_lines(md), self.args.limit)
        pool = ThreadPoolExecutor(self.args.chunk_threads)
        hdls = [pool.submit(self.step_derep_chunk, c) for c in chunks]
        res = [h.result() for h in hdls]
        write_text(ofname, '\n\n'.join(res))
        logger.info(f'[*] {ofname} 已写入')

    def run(self):
        """主流程：列出素材文件，并行去重。"""
        logger.info(self.args)
        fnames = self._list_input_files()
        if not fnames:
            logger.critical('请提供素材文件或目录')
            return
        threads = self.args.file_threads
        pool = ThreadPoolExecutor(threads)
        hdls = []
        for f in fnames:
            hdls.append(pool.submit(self.step_derep_file, f))
        for h in hdls:
            h.result()
        logger.info('[*] 已完成')

    def _list_input_files(self):
        args = self.args
        if path.isfile(args.fname):
            fnames = [args.fname]
        elif path.isdir(args.fname):
            fnames = [
                path.join(rt, f)
                for rt, _, fs in os.walk(args.fname)
                for f in fs
            ] if args.recur else [
                path.join(args.fname, f)
                for f in os.listdir(args.fname)
            ]
        else:
            fnames = []
        fnames = [
            f for f in fnames
            if extname(f).lower() in self.SUPPORTED_EXTS and
               not re.search(r'_derep\.', f, flags=re.I)
        ]
        if args.excluding_re:
            fnames = [
                f for f in fnames
                if not re.search(args.excluding_re, f)
            ]
        return fnames


def derep_handle(args):
    """入口函数：创建编排器并运行。"""
    if args.debug:
        logger.setLevel(logging.DEBUG)
        oai_logger.setLevel(logging.DEBUG)
    return DerepOrchestrator(args).run()


def reg_subparser(subparsers):
    derep_parser = subparsers.add_parser(
        "derep", help="文案去重（生成与素材表述不同、观点一致的文案）"
    )
    derep_parser.add_argument("fname", help="素材文件名或目录")
    derep_parser.add_argument("-ft", "--file-threads", type=int, default=8, help="并发处理文件的线程数 (step_derep_file)")
    derep_parser.add_argument("--chunk-threads", type=int, default=4, help="单文件内并发处理分块的线程数 (step_derep_chunk)")
    derep_parser.add_argument("-l", "--limit", type=int, default=8000, help="分块大小上限")
    derep_parser.add_argument("-rc", "--recur", action='store_true', help="是否递归")
    derep_parser.add_argument("-x", "--excluding-re", default='', help="排除文件的正则")
    derep_parser.add_argument("-r", "--round", type=int, default=3, help="修复轮次上限")
    derep_parser.add_argument("-th", "--threshold", type=float, default=0.4, help="重复度阈值，超过则视为重复")
    derep_parser.add_argument("-n", "--ngram", type=int, default=3, help="n-gram 窗口大小")
    derep_parser.add_argument("-D", "--debug", action='store_true', help="调试模式")
    derep_parser.set_defaults(func=derep_handle)

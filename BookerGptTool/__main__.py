import argparse
import sys
import os
from . import __version__
from .trans import *
from .code2doc import *
from .shengcai import *
from .call import *
from .arxiv import *
from .infer import *
from .erchuang import *
from .note import *
from .paper2code import *
from .pdf_pcr import *
from .fiction import *
from .md2skill import *

def main():
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')
    openai_model = os.environ.get('OPENAI_CHAT_MODEL', 'gpt-3.5-turbo')
    openai_vmodel = os.environ.get('OPENAI_VIS_MODEL', '')

    parser = argparse.ArgumentParser(prog="BookerGptTool", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--version", action="version", version=f"PYBP version: {__version__}")
    parser.add_argument("-P", "--proxy", help="proxy")
    parser.add_argument("-m", "--model", default=openai_model, help="model name")
    parser.add_argument("-k", "--key", default=openai_key, help="OpenAI API key")
    parser.add_argument("-r", "--retry", type=int, default=1_000_000, help="times of retry")
    parser.add_argument("--temp", type=float, default=1e-2, help="temperature")
    parser.add_argument("-M", "--max-tokens", type=int, default=None, help="max tokens")
    parser.add_argument("-H", "--host", default=openai_url, help="api host")
    parser.add_argument("--emb", default=os.environ.get('EMB_MODEL_PATH', 'moka-ai/m3e-base'), help="emb model path")
    parser.add_argument("-vm", "--vmodel", default=openai_vmodel, help="vision model name")
    parser.set_defaults(func=lambda x: parser.print_help())
    subparsers = parser.add_subparsers()
    
    trans_parser = subparsers.add_parser("trans-yaml", help="translate YAML files")
    trans_parser.add_argument("fname", help="yaml file name of dir")
    trans_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    trans_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    trans_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    trans_parser.set_defaults(func=trans_yaml_handle)


    test_parser = subparsers.add_parser("trans", help="translate one sentence")
    test_parser.add_argument("en", help="en text")
    test_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    test_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    test_parser.set_defaults(func=trans_handle)

    comm_parser = subparsers.add_parser("code2doc", help="comment code")
    comm_parser.add_argument('fname', help='file or dir name')
    comm_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    comm_parser.set_defaults(func=code2doc_handle)

    shengcai_parser = subparsers.add_parser("shengcai", help="parse shengcai fengxiangbiao")
    shengcai_parser.add_argument('fname', help='epub file name')
    shengcai_parser.add_argument('-p', '--prompt', default=DFT_SHENGCAI_PROMPT, help='prompt for code comment')
    shengcai_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    shengcai_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    shengcai_parser.add_argument("-s", "--start", type=int, default=2, help="page to start")
    shengcai_parser.add_argument("--min", type=int, default=200, help="max token limit")
    shengcai_parser.set_defaults(func=parse_shengcai)

    call_parser = subparsers.add_parser("call", help="call chatgpt with custom question")
    call_parser.add_argument("ques", help="question")
    call_parser.set_defaults(func=call_handle)

    arxiv_parser = subparsers.add_parser("arxiv", help="summarize arxiv papers")
    arxiv_parser.add_argument("arxiv", help="arxiv id")
    arxiv_parser.add_argument("-l", "--limit", type=int, default=3000, help="limit")
    arxiv_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    arxiv_parser.set_defaults(func=sum_arxiv)

    arxiv_batch_parser = subparsers.add_parser("arxiv-batch", help="summarize arxiv papers")
    arxiv_batch_parser.add_argument("fname", help="file name of arxiv id ")
    arxiv_batch_parser.add_argument("-l", "--limit", type=int, default=3000, help="limit")
    arxiv_batch_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    arxiv_batch_parser.set_defaults(func=sum_arxiv_batch)

    paper2code_parser = subparsers.add_parser("paper2code", help="summarize arxiv papers")
    paper2code_parser.add_argument("fname", help="MD/TEX/TXT file or ARXIV ID（arxiv:\d+\.\d+）")
    paper2code_parser.add_argument("-o", "--out", type=str, help="output dir name")
    paper2code_parser.set_defaults(func=paper2code)


    infer_parser = subparsers.add_parser("infer", help="free inference")
    infer_parser.add_argument("fname", help="fname")
    infer_parser.add_argument("-p", "--prompt", default="{question}", help="prompt")
    infer_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    infer_parser.add_argument("--ques-col", default="question", help="question column name")
    infer_parser.add_argument("--ans-col", default="answer", help="answer column name")
    infer_parser.set_defaults(func=infer)

    erchuang_parser = subparsers.add_parser("erchuang", help="gen xhs notes")
    erchuang_parser.add_argument("fname", help="fname")
    erchuang_parser.add_argument("-t", "--threads", type=int, default=8, help="threadcount")
    erchuang_parser.add_argument(
        "-s", "--style", 
        type=str, default='xhs', 
        choices=['xhs', 'gzh', 'fmt', 'sum', 'qa'], 
        help="article style"
    )
    erchuang_parser.set_defaults(func=erchuang_handle)

    note_parser = subparsers.add_parser("note", help="make notes")
    note_parser.add_argument("fname", help="fname")
    note_parser.add_argument("-t", "--threads", type=int, default=8, help="threadcount")
    note_parser.set_defaults(func=mknote)

    pdf_ocr_parser = subparsers.add_parser("pdf-ocr", help="pdf ocr")
    pdf_ocr_parser.add_argument("fname", help="PDF file name")
    pdf_ocr_parser.add_argument("--dpi", type=int, default=300, help="dpi")
    pdf_ocr_parser.add_argument("-t", "--threads", type=int, default=4, help="num threads")
    pdf_ocr_parser.set_defaults(func=pdf_ocr)

    fiction_parser = subparsers.add_parser("fiction", help="write fiction")
    fiction_parser.add_argument("idea", help="idea")
    fiction_parser.add_argument("-o", "--out-dir", help="output dir")
    fiction_parser.add_argument("-c", "--chapters", type=int, default=20, help="num chapters")
    fiction_parser.add_argument("-w", "--words", type=int, default=5000, help="num words")
    fiction_parser.add_argument("-wc", "--write_command", default=DFT_WRITE_CMD, help="writing coommand")
    fiction_parser.add_argument("-pc", "--polish_command", default=DFT_POLISH_CMD, help="polishing coommand")
    fiction_parser.add_argument("-se", "--style-example", default='', help="style example")
    fiction_parser.set_defaults(func=write_fiction)

    md2skill_parser = subparsers.add_parser("md2skill", help="md2skill")
    md2skill_parser.add_argument("fname", help="fname")
    md2skill_parser.add_argument("-t", "--threads", type=int, default=8, help="num threads")
    md2skill_parser.set_defaults(func=md2skill)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == '__main__': main()
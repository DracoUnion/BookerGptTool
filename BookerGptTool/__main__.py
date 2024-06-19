import argparse
import sys
import os
from . import __version__
from .trans import *
from .comment import *
from .shengcai import *
from .call import *
from .arxiv import *
from .sum import *
from .infer import *
from .stylish import *

def main():
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')
    openai_model = os.environ.get('OPENAI_CHAT_MODEL', 'gpt-3.5-turbo')

    parser = argparse.ArgumentParser(prog="BookerGptTool", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--version", action="version", version=f"PYBP version: {__version__}")
    parser.add_argument("-P", "--proxy", help="proxy")
    parser.add_argument("-m", "--model", default=openai_model, help="model name")
    parser.add_argument("-k", "--key", default=openai_key, help="OpenAI API key")
    parser.add_argument("-r", "--retry", type=int, default=1_000_000, help="times of retry")
    parser.add_argument("--temp", type=float, default=0.0, help="temperature")
    parser.add_argument("-H", "--host", default=openai_url, help="api host")
    parser.add_argument("--emb", default=os.environ.get('M3E_PATH', 'moka-ai/m3e-base'), help="emb model path")
    parser.set_defaults(func=lambda x: parser.print_help())
    subparsers = parser.add_subparsers()
    
    trans_parser = subparsers.add_parser("trans-yaml", help="translate YAML files")
    trans_parser.add_argument("fname", help="yaml file name of dir")
    trans_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    trans_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    trans_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    trans_parser.set_defaults(func=trans_yaml_handle)

    stylish_parser = subparsers.add_parser("stylish", help="stylish YAML files")
    stylish_parser.add_argument("fname", help="yaml file name of dir")
    stylish_parser.add_argument("-p", "--prompt", default=DFT_STYLE_PROMPT, help="prompt for trans")
    stylish_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    stylish_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    stylish_parser.set_defaults(func=stylish_yaml_handle)

    test_parser = subparsers.add_parser("trans", help="translate one sentence")
    test_parser.add_argument("en", help="en text")
    test_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    test_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    test_parser.set_defaults(func=trans_handle)

    comm_parser = subparsers.add_parser("comment", help="comment code")
    comm_parser.add_argument('fname', help='file or dir name')
    comm_parser.add_argument('-p', '--prompt', default=DFT_COMM_PROMPT, help='prompt for code comment')
    comm_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    comm_parser.add_argument("-l", "--limit", type=int, default=3000, help="max token limit")
    comm_parser.set_defaults(func=comment_handle)

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

    sum_parser = subparsers.add_parser("sum", help="summarize md or srt")
    sum_parser.add_argument("fname", help="fname")
    sum_parser.add_argument("-s", "--para-size", type=int, default=1500, help="paragraph size")
    sum_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    sum_parser.add_argument("--ctx", type=int, default=2, help="context range")
    sum_parser.add_argument("--md", action='store_true', help="whether to write md")
    sum_parser.set_defaults(func=sum_text)

    infer_parser = subparsers.add_parser("infer", help="free inference")
    infer_parser.add_argument("fname", help="fname")
    infer_parser.add_argument("-p", "--prompt", default="{question}", help="prompt")
    infer_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    infer_parser.add_argument("--ques-col", default="question", help="question column name")
    infer_parser.add_argument("--ans-col", default="answer", help="answer column name")
    infer_parser.set_defaults(func=infer)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == '__main__': main()
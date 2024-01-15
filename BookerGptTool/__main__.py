import argparse
import sys
import os
from . import __version__
from .trans import *
from .comment import *
from .shengcai import *

def main():
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')

    parser = argparse.ArgumentParser(prog="BookerGptTool", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--version", action="version", version=f"PYBP version: {__version__}")
    parser.add_argument("-P", "--proxy", help="proxy")
    parser.add_argument("-m", "--model", default='gpt-3.5-turbo', help="model name")
    parser.add_argument("-k", "--key", default=openai_key, help="OpenAI API key")
    parser.add_argument("-r", "--retry", type=int, default=1_000_000, help="times of retry")
    parser.add_argument("-H", "--host", default=openai_url, help="api host")
    parser.set_defaults(func=lambda x: parser.print_help())
    subparsers = parser.add_subparsers()
    
    trans_parser = subparsers.add_parser("trans-yaml", help="translate YAML files")
    trans_parser.add_argument("fname", help="yaml file name of dir")
    trans_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    trans_parser.add_argument("-l", "--limit", type=int, default=4000, help="max token limit")
    trans_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    trans_parser.set_defaults(func=trans_yaml_handle)

    test_parser = subparsers.add_parser("trans", help="translate one sentence")
    test_parser.add_argument("en", help="en text")
    test_parser.add_argument("-p", "--prompt", default=DFT_TRANS_PROMPT, help="prompt for trans")
    test_parser.add_argument("-l", "--limit", type=int, default=4000, help="max token limit")
    test_parser.set_defaults(func=trans_handle)

    comm_parser = subparsers.add_parser("comment", help="comment code")
    comm_parser.add_argument('fname', help='file or dir name')
    comm_parser.add_argument('-p', '--prompt', default=DFT_COMM_PROMPT, help='prompt for code comment')
    comm_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    comm_parser.add_argument("-l", "--limit", type=int, default=2000, help="max token limit")
    comm_parser.set_defaults(func=comment_handle)

    shengcai_parser = subparsers.add_parser("shengcai", help="parse shengcai fengxiangbiao")
    shengcai_parser.add_argument('fname', help='epub file name')
    shengcai_parser.add_argument('-p', '--prompt', default=DFT_COMM_PROMPT, help='prompt for code comment')
    shengcai_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    shengcai_parser.add_argument("-l", "--limit", type=int, default=500, help="max token limit")
    shengcai_parser.set_defaults(func=parse_shengcai)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == '__main__': main()
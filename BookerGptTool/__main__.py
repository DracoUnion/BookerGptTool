import argparse
import sys
import os
from . import __version__
from .trans import *
from .comment import *

def main():
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')

    parser = argparse.ArgumentParser(prog="BookerGptTool", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--version", action="version", version=f"PYBP version: {__version__}")
    parser.set_defaults(func=lambda x: parser.print_help())
    subparsers = parser.add_subparsers()
    
    gh_book_parser = subparsers.add_parser("gh-book", help="download books from github")
    gh_book_parser.add_argument("url", help="SUMMARY.md url")
    gh_book_parser.add_argument("-t", "--threads", type=int, default=5, help="num of threads")
    gh_book_parser.add_argument("-p", "--proxy", help="proxy")
    gh_book_parser.add_argument("-a", "--article", default='article', help="article selector")
    gh_book_parser.set_defaults(func=dl_gh_book)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == '__main__': main()
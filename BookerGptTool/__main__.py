import argparse
import sys
import os
from . import __version__
from . import (
    trans, fin_report, md2kg, code2doc, code2book, shengcai, call,
    arxiv, infer, erchuang, note, paper2code, pdf_ocr, gts_fiction,
    md2skill, trans_epub, fmt_chunk, md2wiki, clean_heading, forward,
    novel_anls, xhs_img,
)

def main():
    openai_key = os.environ.get('OPENAI_API_KEY')
    openai_url = os.environ.get('OPENAI_BASE_URL')
    openai_model = os.environ.get('OPENAI_CHAT_MODEL', 'gpt-3.5-turbo')
    openai_vmodel = os.environ.get('OPENAI_VIS_MODEL', '')
    openai_tti_model = os.environ.get('OPENAI_TTI_MODEL', '')

    parser = argparse.ArgumentParser(prog="BookerGptTool", formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--version", action="version", version=f"PYBP version: {__version__}")
    parser.add_argument("-m", "--model", default=openai_model, help="model name")
    parser.add_argument("-k", "--key", default=openai_key, help="OpenAI API key")
    parser.add_argument("-r", "--retry", type=int, default=1_000_000, help="times of retry")
    parser.add_argument("-tm", "--temp", type=float, default=1, help="temperature")
    parser.add_argument("-tp", "--top-p", type=float, help="top p")
    parser.add_argument("-fp", "--frequency-penalty", type=float, help="frequency penalty")
    parser.add_argument("-pp", "--presence-penalty", type=float, help="presence penalty")
    parser.add_argument("-mt", "--max-tokens", type=int, default=None, help="max tokens")
    parser.add_argument("-H", "--host", default=openai_url, help="api host")
    parser.add_argument("--emb", default=os.environ.get('EMB_MODEL_PATH', 'moka-ai/m3e-base'), help="emb model path")
    parser.add_argument("-vm", "--vmodel", default=openai_vmodel, help="vision model name")
    parser.add_argument("-im", "--tti-model", default=openai_tti_model, help="vision model name")
    parser.add_argument("-ua", "--user-agent", default='claude-cli/2.1.41 (external, cli)', help="HTTP User-Agent Header")
    parser.add_argument("-st", "--stream", action='store_true' , help="stream mode")
    parser.add_argument("-eb", "--extra-body", help="extra body")
    parser.add_argument("-ct", "--conn-timeout", type=int, default=60, help="")
    parser.add_argument("-rt", "--read-timeout", type=int, default=120, help="")
    parser.add_argument("-rr", "--repetition-regex", default='', help="re for repetition detection")
    parser.set_defaults(func=lambda x: parser.print_help())
    subparsers = parser.add_subparsers()

    trans.reg_subparser(subparsers)
    code2doc.reg_subparser(subparsers)
    code2book.reg_subparser(subparsers)
    shengcai.reg_subparser(subparsers)
    call.reg_subparser(subparsers)
    arxiv.reg_subparser(subparsers)
    paper2code.reg_subparser(subparsers)
    clean_heading.reg_subparser(subparsers)
    infer.reg_subparser(subparsers)
    erchuang.reg_subparser(subparsers)
    note.reg_subparser(subparsers)
    pdf_ocr.reg_subparser(subparsers)
    gts_fiction.reg_subparser(subparsers)
    md2skill.reg_subparser(subparsers)
    md2wiki.reg_subparser(subparsers)
    trans_epub.reg_subparser(subparsers)
    fmt_chunk.reg_subparser(subparsers)
    forward.reg_subparser(subparsers)
    fin_report.reg_subparser(subparsers)
    md2kg.reg_subparser(subparsers)
    novel_anls.reg_subparser(subparsers)
    xhs_img.reg_subparser(subparsers)

    args = parser.parse_args()
    args.func(args)
    
if __name__ == '__main__': main()

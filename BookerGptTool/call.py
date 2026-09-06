import yaml
from .util import *

def check_batch_handle(args):
    print(args)
    set_openai_props(args)
    if not args.fname.endswith('.yaml'):
        print('请提供 YAML 文件')
        return
    keys = yaml.safe_load(open(args.fname, encoding='utf8').read())
    for k in keys:
        print(k)
        openai.api_key = k['api_key']
        openai.base_url = k['base_url']
        try:
           ans = ask_chatgpt_retry(args.ques, k['model'], args)
           print(ans)
        except:
            traceback.print_exc()
        print('=' * 60)

def call_handle(args):
    print(args)
    set_openai_props(args)
    ans = ask_chatgpt_retry(args.ques, args.model, args)
    print(ans)


def reg_subparser(subparsers):
    call_parser = subparsers.add_parser("call", help="call chatgpt with custom question")
    call_parser.add_argument("ques", help="question")
    call_parser.set_defaults(func=call_handle)

    call_parser = subparsers.add_parser("check-batch", help="check keys in YAML")
    call_parser.add_argument("fname", help="YAML file name")
    call_parser.add_argument("ques", help="question")
    call_parser.set_defaults(func=check_batch_handle)
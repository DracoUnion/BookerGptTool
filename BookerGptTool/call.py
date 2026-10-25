import yaml
from .util import *
from .openai import OpenAIClient

def check_batch_handle(args):
    print(args)
    llm = OpenAIClient.from_args(args)
    if not args.fname.endswith('.yaml'):
        print('请提供 YAML 文件')
        return
    keys = yaml.safe_load(open(args.fname, encoding='utf8').read())
    for k in keys:
        print(k)
        llm.api_key = k['api_key']
        llm.base_url = k['base_url']
        try:
           ans = llm.ask_chatgpt_retry(args.ques, k['model'], args)
           print(ans)
        except:
            traceback.print_exc()
        print('=' * 60)

def call_handle(args):
    print(args)
    llm = OpenAIClient.from_args(args)
    ans = llm.ask_chatgpt_retry(args.ques, args.model, args)
    print(ans)


def reg_subparser(subparsers):
    call_parser = subparsers.add_parser("call", help="用自定义问题调用模型")
    call_parser.add_argument("ques", help="问题")
    call_parser.set_defaults(func=call_handle)

    call_parser = subparsers.add_parser("check-batch", help="检查 YAML 中的 key")
    call_parser.add_argument("fname", help="YAML 文件名")
    call_parser.add_argument("ques", help="问题")
    call_parser.set_defaults(func=check_batch_handle)
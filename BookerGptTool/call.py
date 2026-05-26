from .util import *

def call_handle(args):
    print(args)
    set_openai_props(args)
    ans = call_chatgpt_retry(args.ques, args.model, args.temp, args.retry, args.max_tokens)
    print(ans)
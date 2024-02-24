from .util import *

def call_handle(args):
    openai.api_key = args.key
    openai.proxy = args.proxy
    openai.host = args.host

    ans = call_openai_retry(args.ques, args.model, args.retry)
    print(ans)
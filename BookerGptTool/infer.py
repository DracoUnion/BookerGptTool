from .util import *
import traceback

def tr_infer(dit, args, write_callback):
    try:
        if args.ans_col in dit: return
        ques = combine_prompt_args(args.prompt, dit)
        dit[args.ques_col] = ques
        ans = call_chatgpt_retry(ques, args.model, args.temp, args.retry)
        ans = fix_lists(ans)
        dit[args.ans_col] = ans
        write_callback()
    except Exception:
        traceback.print_exc()

def infer(args):
    set_openai_props(args.key, args.proxy, args.host)
    print(args)
    ds = read_ds_file(args.fname)

    lock = Lock()
    def write_callback():
        with lock:
            write_ds_file(args.fname, ds)

    pool = ThreadPoolExecutor(args.threads)
    hdls = []
    for dit in ds:
        if dit.get(args.ans_col):
            continue
        h = pool.submit(tr_infer, dit, args, write_callback)
        hdls.append(h)
    for h in hdls: h.result()

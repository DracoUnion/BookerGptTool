from .util import *
from .openai import ask_chatgpt_retry, set_openai_props
import traceback

def tr_infer(dit, args, write_callback):
    try:
        if args.ans_col in dit: return
        ques = render_prompt(args.prompt, **dit)
        dit[args.ques_col] = ques
        ans = ask_chatgpt_retry(ques, args.model, args)
        ans = fix_lists(ans)
        dit[args.ans_col] = ans
        write_callback()
    except KeyboardInterrupt:
        raise
    except Exception:
        traceback.print_exc()

def infer(args):
    if path.isfile(args.prompt):
        args.prompt = open(args.prompt, encoding='utf8').read()
    set_openai_props(args)
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


def reg_subparser(subparsers):
    infer_parser = subparsers.add_parser("infer", help="free inference")
    infer_parser.add_argument("fname", help="fname")
    infer_parser.add_argument("-p", "--prompt", default="{question}", help="prompt")
    infer_parser.add_argument("-t", "--threads", type=int, default=8, help="thread num")
    infer_parser.add_argument("--ques-col", default="question", help="question column name")
    infer_parser.add_argument("--ans-col", default="answer", help="answer column name")
    infer_parser.set_defaults(func=infer)

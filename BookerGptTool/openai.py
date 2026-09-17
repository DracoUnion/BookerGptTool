import copy
import openai
import base64
import re
import json, json_repair
import requests
import logging
import traceback
from argparse import Namespace
from typing import *
from pydantic import BaseModel, parse_obj_as, ValidationError
from .util import render_prompt
from openai.types.chat import *

logging.getLogger("openai._base_client").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

def request_retry(method, url, retry=10, check_status=False, **kw):
    kw.setdefault('timeout', 10)
    for i in range(retry):
        try:
            r = requests.request(method, url, **kw)
            if check_status: r.raise_for_status()
            return r
        except KeyboardInterrupt as e:
            raise e
        except Exception as e:
            logger.debug(f'{url} retry {i}')
            if i == retry - 1: raise e

def call_vlm_retry(
    img, ques, model_name, args,
    parse_output=None,
):
    img_base64 = base64.b64encode(img).decode('ascii')
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": ques},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_base64}"
                }
            },
        ]
    }]
    return call_llm_retry(
        msgs, model_name,
        retry=args.retry,
        temp=args.temp,
        top_p=args.top_p,
        frequency_penalty=args.frequency_penalty,
        presence_penalty=args.presence_penalty,
        max_tokens=args.max_tokens,
        extra_body=args.extra_body,
        parse_output=parse_output,
    )

def get_msgs_text(msgs):
    for m in msgs[::-1]:
        cont = m.get('content')
        if isinstance(cont, str):
            return m['content']
        elif isinstance(cont, list):
            for it in m['content']:
                tp = it.get('type')
                if tp == 'text':
                    return it['text']
    return ''

def repl_ins_token(msgs):
    repl_ins_token_re = lambda s: re.sub(r'<\|([\w\-\.]+)\|>', r'</\1/>', s)
    for m in msgs:
        cont = m.get('content')
        if isinstance(cont, str):
            m['content'] = ensure_utf8(
                repl_ins_token_re(m['content']))
        elif isinstance(cont, list):
            for it in m['content']:
                tp = it.get('type')
                if tp == 'text':
                    it['text'] = ensure_utf8(
                        repl_ins_token_re(it['text']))
    return msgs

def ask_chatgpt_retry(
    ques, model_name, args,
    parse_output=None,
):
    return call_llm_retry(
        ques, model_name,
        retry=args.retry,
        temp=args.temp,
        top_p=args.top_p,
        frequency_penalty=args.frequency_penalty,
        presence_penalty=args.presence_penalty,
        max_tokens=args.max_tokens,
        extra_body=args.extra_body,
        parse_output=parse_output,
    )


def dispatch_tools(
    tool_dict: Dict[str, Callable],
    name: str,
    args: Dict[str, Any],
) -> Tuple[Any, str]:
    try:
        return tool_dict[name](**args), ""
    except KeyboardInterrupt:
        raise
    except Exception as ex:
        return None, traceback.format_exc()

def _chat_cmpl_create_retry(
    client: openai.Client, 
    msgs, model_name,
    tool_defs,
    *, 
    retry=10, temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
):
    for i in range(retry):
        try:
            res = client.chat.completions.create(
                messages=msgs,
                model=model_name,
                temperature=temp,
                top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
                max_tokens=max_tokens,
                extra_body=extra_body,
                stream=openai.stream,
                tools=tool_defs,
                tool_choice='auto',
            )
            if openai.stream:
                res: Iterable[ChatCompletionChunk]
                toolcalls, ans = collect_stream_toolcalls(res)
            else:
                res: ChatCompletion
                res_msg = res.choices[0].message
                toolcalls, ans = res_msg.tool_calls, res_msg.content
            return res, toolcalls, ans
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            logger.debug(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex

def call_llm_with_toolcall_retry(
    msgs, model_name,
    tool_defs, tool_dict, *,
    tool_finish_name='',
    retry=10,
    temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
    parse_output=None,
):
    if isinstance(msgs, str):
        msgs = [{'role': 'user', 'content': msgs}]
    msgs = repl_ins_token(msgs)
    if isinstance(extra_body, str):
        extra_body = json.loads(extra_body)
    client = openai.OpenAI(
        base_url=openai.base_url,
        api_key=openai.api_key,
        default_headers={'User-Agent': openai.user_agent},
        timeout=openai.timeout,
    )
    while True:
        logger.debug(f'ques: %s', _json_dump(get_msgs_text(msgs)))
        res, toolcalls, ans = _chat_cmpl_create_retry(
            client, msgs, model_name,
            tool_defs, 
            retry=retry,
            temp=temp,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        msgs.append({
            'role': 'assistant',
            'content': None,
            "tool_calls": [tc.dict() for tc in toolcalls],
        })
        if not toolcalls:
            if not tool_finish_name: break
            errmsg = \
                f"未找到任何工具调用，如果你想结束整个流程，调用`{tool_finish_name}`。"
            msgs.append({"role": "user", "content": errmsg})
            continue
        logger.info(f'toolcall: %s', _json_dump(toolcalls))
        finish = False
        for tc in toolcalls:
            if tc.function.name == tool_finish_name:
                finish = True
                break
            tc_res, errmsg = dispatch_tools(
                tool_dict, 
                tc.function.name, 
                json_repair.loads(tc.function.arguments),
            )
            if errmsg:
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": errmsg,
                })
                logger.debug(f'toolcall_res: %s', _json_dump(msgs[-1]))
                continue
            msgs.append({
                'role': "tool",
                'tool_call_id': tc.id, 
                'content': _json_dump(tc_res)
            })
            logger.debug(f'toolcall_res: %s', _json_dump(msgs[-1]))
        if finish: break

    if not ans: raise ValueError(f'回复为空：{res}')

    # 还原指令格式
    ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
    ans = re.sub(r' thinking[\s\S]+? response', '', ans)
    logger.debug(f'ans: %s', _json_dump(ans))
    if parse_output:
        ans = parse_output(ans)
    return ans

def call_llm_retry(
    msgs, model_name, *,
    retry=10, temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
    parse_output=None,
):
    for i in range(retry):
        try:
            res =  call_llm(
                msgs, model_name,
                temp=temp,
                top_p=top_p,
                frequency_penalty=frequency_penalty,
                presence_penalty=presence_penalty,
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
            return (
                parse_output(res)
                if parse_output else res
            )
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            logger.debug(f'OpenAI retry {i+1}')
            logger.debug(traceback.format_exc())
            if i == retry - 1: raise ex

def ensure_utf8(text: str) -> str:
    return text.encode('utf8', 'ignore').decode('utf8', 'ignore')

def call_llm(
    msgs, model_name, *,
    temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
):
    if isinstance(msgs, str):
        msgs = [{'role': 'user', 'content': msgs}]
    # 改变指令符号的形式，避免模型出错
    msgs = repl_ins_token(msgs)
    if isinstance(extra_body, str):
        extra_body = json.loads(extra_body)
    logger.debug(f'ques: %s', _json_dump(get_msgs_text(msgs)))
    client = openai.OpenAI(
        base_url=openai.base_url,
        api_key=openai.api_key,
        default_headers={'User-Agent': openai.user_agent},
        timeout=openai.timeout,
    )
    res = client.chat.completions.create(
        messages=msgs,
        model=model_name,
        temperature=temp,
        top_p=top_p,
        frequency_penalty=frequency_penalty,
        presence_penalty=presence_penalty,
        max_tokens=max_tokens,
        extra_body=extra_body,
        stream=openai.stream,
    )
    if openai.stream:
        res: Iterable[ChatCompletionChunk]
        ans = collect_stream_content(res)
    else:
        res: ChatCompletion
        ans = res.choices[0].message.content.strip()
        check_model_repetition(ans)
    if not ans: raise ValueError(f'回复为空：{res}')

    # 还原指令格式
    ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
    ans = re.sub(r' thinking[\s\S]+? response', '', ans)
    logger.debug(f'ans: %s', _json_dump(ans))
    return ans

def set_openai_props(args):
    openai.api_key = args.key
    openai.base_url = args.host
    openai.user_agent = args.user_agent
    openai.stream = args.stream
    openai.timeout = openai.Timeout(
        read=args.read_timeout,
        connect=args.conn_timeout,
        write=None,
        pool=None,
    )
    openai.rpre = args.repetition_regex

def collect_stream_toolcalls(resp: Iterable[ChatCompletionChunk]):
    tool_calls: Dict[int, ChatCompletionMessageToolCall] = {}
    content: List[str] = []

    for ch in resp:  # resp 是 stream=True 的响应
        if not ch.choices:
            continue
        delta = ch.choices[0].delta
        
        # 1. 累积普通文本
        if delta.content:
            logger.debug(f"stream: %s", _json_dump(delta.content))
            content.append(delta.content)
        
        # 2. 累积工具调用片段
        if delta.tool_calls:
            for delta_tc in delta.tool_calls:
                idx = delta_tc.index
                if idx not in tool_calls:
                    tc = copy.deepcopy(delta_tc)
                    tc.id = None
                    tc.function.name = ''
                    tc.function.arguments = ''
                    tool_calls[idx] = tc
                tc = tool_calls[idx]
                if delta_tc.id:
                    tc.id = delta_tc.id
                if delta_tc.function.name:
                    tc.function.name += delta_tc.function.name
                if delta_tc.function.arguments:
                    tc.function.arguments += delta_tc.function.arguments
                logger.debug(f'toolcall_stream: %s', tc.json())
    return list(tool_calls.values()), ''.join(content)

def collect_stream_content(resp: Iterable[ChatCompletionChunk]):
    content = []
    for chunk in resp:
        if not chunk.choices:
            continue
        delta_content = chunk.choices[0].delta.content
        if not delta_content:
            continue
        content.append(delta_content)
        check_model_repetition(''.join(content))
        logger.debug(f'stream: %s', _json_dump(delta_content))
    return ''.join(content)

def check_model_repetition(text):
    if openai.rpre and re.search(openai.rpre, text):
        raise ValueError('检测到模型复读')

def call_tti(
    text, model_name,
    size='1024x1024',
    ref_img: Optional[bytes]=None,
):
    logger.debug(f'tti: %s', _json_dump(text))
    client = openai.OpenAI(
        base_url=openai.base_url,
        api_key=openai.api_key,
        default_headers={'User-Agent': openai.user_agent},
        timeout=openai.timeout,
    )
    if 'gpt-image' in model_name and ref_img:
        ref_img_b64 = base64.b64encode(ref_img).decode('ascii')
        extra_body = {
            "image": f"data:image/png;base64,{ref_img_b64}",
        }
    else:
        extra_body = {}
    img_data = client.images.generate(
        model=model_name,
        size=size,
        prompt=text,
        response_format='b64_json',
        n=1,
        extra_body=extra_body,
    ).data[0]
    if getattr(img_data, 'b64_json', None):
        return base64.b64decode(img_data.b64_json)
    elif getattr(img_data, 'url', None):
        return request_retry('GET', img_data.url).content
    else:
        raise ValueError('API 未返回数据')

def call_tti_retry(
    text, model_name,
    size='1024x1024',
    ref_img: Optional[bytes]=None,
    retry=10, nothrow=True,
):
    for i in range(retry):
        try:
            return call_tti(text, model_name, size, ref_img)
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            logger.debug(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1 and not nothrow: raise ex

def _json_dump(obj) -> str:
    """将对象（含 pydantic 模型/列表）序列化为 JSON 字符串。"""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump()
    elif isinstance(obj, list):
        obj = [
            it.dict() if isinstance(it, BaseModel) else it
            for it in obj
        ]
    return json.dumps(obj, ensure_ascii=False)

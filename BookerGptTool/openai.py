import openai
import base64
import re
import json, json_repair
import requests
import logging
import traceback
from typing import *
from pydantic import BaseModel, parse_obj_as, ValidationError
from .util import render_prompt

logging.getLogger("openai._base_client").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

TOOLCALL_PMT = '''
## 工具调用指南

### 可用工具

在回答任何问题时，你可以调用一次或多次如下工具：

```
{tool_def}
```

### 返回格式

在决定调用工具时，请按照如下格式返回工具调用，确保内容包含在“[tool]...[/tool]”中，任何其它内容将会被忽略。如果不决定调用工具，不要输出任何“[tool]...[/tool]”内容。

[tool]
[{"id": "uuid", "tool": "tool name", "parameters": {"parameter name": "parameter value"}}]
[/tool]

用户调用工具后，将结果以如下格式传回：

[tool-result]
[{"id": "uuid", "result": "result"}]
[/tool-result]

### 示例

这是一个可用工具列表的示例：

```
{"tools": [{"name": "plus_one", "description": "Add one to a number", "parameters": {"type": "object","properties": {"number": {"type": "string","description": "The number that needs to be changed, for example: 1","default": "1",}},"required": ["number"]}},{"name": "minus_one", "description": "Minus one to a number", "parameters": {"type": "object","properties": {"number": {"type": "string","description": "The number that needs to be changed, for example: 1","default": "1",}},"required": ["number"]}}]}
```

如果你想计算`42 + 1`，可以返回：

[tool]
[{"id": "c3d16bba-9216-449e-8d46-d389fbca6cb5", "tool": "plus_one", "parameters": {"number": 42}}]
[/tool]

用户计算后，传回结果：

[tool-result]
[{"id": "c3d16bba-9216-449e-8d46-d389fbca6cb5", "result": 43}]
[/tool-result]

请注意，上述只是个示例，并不代表`plus_one`和`plus_minus`真实存在。
'''

class ToolCallItem(BaseModel):
    id: str
    tool: str
    parameters: Dict[str, Any]

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


def parse_toolcall(res: str) -> Tuple[List[ToolCallItem], str]:
    """Parse the first [tool]...[/tool] block in a response into a list of
    tool-call dicts (id / tool / parameters). Mirrors llm/openai.py."""
    m = re.search(r"\[tool\]([\s\S]+)\[/tool\]", res)
    if not m:
        return [], ""
    try:
        blocks = parse_obj_as(
            List[ToolCallItem],
            json_repair.loads(m.group(1)),
        )
        return blocks, ""
    except json.JSONDecodeError as ex:
        return [], str(ex)
    except ValidationError as ex:
        return [], str(ex)

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
        return None, str(ex)

def call_llm_with_toolcall(
    msgs, model_name,
    tool_defs, tool_dict, *,
    temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
):
    if isinstance(msgs, str):
        msgs = [{'role': 'user', 'content': msgs}]
    tool_defs_str = json.dumps(tool_defs)
    toolcall_pmt = render_prompt(TOOLCALL_PMT, tool_def=tool_defs_str)
    msgs = [{
        'role': 'system',
        'content': toolcall_pmt
    }] + msgs
    while True:
        res = call_llm(
            msgs, model_name,
            temp=temp,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        toolcalls, errmsg = parse_toolcall(res)
        if not errmsg and not toolcalls:
            break
        if errmsg:
            msgs += [
                {"role": "assistant", "content": res},
                {"role": "user", "content": errmsg},
            ]
            continue
        toolcall_res_list = []
        toolcall_errmsgs = []
        for tc in toolcalls:
            tc_res, errmsg = dispatch_tools(tool_dict, tc.tool, tc.parameters)
            if errmsg:
                toolcall_errmsgs.append(errmsg)
                continue
            toolcall_res_list.append({'id': tc.id, 'result': tc_res})
        toolcall_res_str = json.dumps(toolcall_res_list)
        msgs += [
            {'role': 'assistant', 'content': res},
            {'role': 'user', 'content': f'[tool-result]{toolcall_res_str}[/tool-result]'}
        ]
        if toolcall_errmsgs:
            msgs.append({'role': 'user', 'content': '\n'.join(toolcall_errmsgs)})

    return res

def call_llm_with_toolcall_retry(
    msgs, model_name,
    tool_defs, tool_dict, *,
    retry=10, temp=None,
    top_p=None,
    frequency_penalty=None,
    presence_penalty=None,
    max_tokens=None,
    extra_body=None,
    parse_output=None
):
    for i in range(retry):
        try:
            res =  call_llm_with_toolcall(
                msgs, model_name,
                tool_defs, tool_dict,
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
    logger.debug(f'ques: {json.dumps(get_msgs_text(msgs), ensure_ascii=False)}')
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
        ans = collect_stream_content(res)
    else:
        ans = res.choices[0].message.content.strip()
        check_model_repetition(ans)
    if not ans: raise ValueError(f'回复为空：{res}')

    # 还原指令格式
    ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
    ans = re.sub(r' thinking[\s\S]+? response', '', ans)
    logger.debug(f'ans: {json.dumps(ans, ensure_ascii=False)}')
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

def collect_stream_content(resp):
    content = []
    for chunk in resp:
        if chunk.choices and chunk.choices[0].delta.content:
            pt = chunk.choices[0].delta.content
            content.append(pt)
            check_model_repetition(''.join(content))
            logger.debug(f'stream: {json.dumps(pt, ensure_ascii=False)}')
    return ''.join(content)

def check_model_repetition(text):
    if openai.rpre and re.search(openai.rpre, text):
        raise ValueError('检测到模型复读')

def call_tti(
    text, model_name,
    size='1024x1024',
    ref_img: Optional[bytes]=None,
):
    logging.debug(f'tti: {json.dumps(text, ensure_ascii=False)}')
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
            logging.debug(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1 and not nothrow: raise ex
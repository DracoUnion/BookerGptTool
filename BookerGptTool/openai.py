import yaml
import copy
import openai
import base64
import os
from os import path
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
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": errmsg if errmsg else _json_dump(tc_res),
            })
            logger.debug(f'toolcall_res: %s', _json_dump(msgs[-1])[:50])

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



# ── 工具参数 schema 辅助 ──────────────────────────────────────────
# 为 _TOOL_PARAMS 生成 OpenAI 参数结构。
# pydantic 模型参数用 Model.schema() 展开其结构，而非仅写 {"type":"object"}。


def base_schema(typ: str, desc: str, **extra) -> Dict[str, Any]:
    """基础标量参数：{"type": typ, "description": desc, **extra}。"""
    return {'type': typ, 'description': desc, **extra}

def func_schema(name: str, desc: str, params: Dict[str, Any]):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": params,
        }
    }

def params_schema(required: List[str] = [], **props):
    return {
        "type": "object",
        "required": required,
        "properties": props,
    }

def model_schema(model: Type[BaseModel], desc: str) -> Dict[str, Any]:
    """pydantic 模型参数：以 model.schema() 展开字段结构。"""
    return {**model.schema(), 'description': desc}


def model_list_schema(model: Type[BaseModel], desc: str) -> Dict[str, Any]:
    """元素为 pydantic 模型的数组参数：items 用 model.schema()。"""
    return base_schema('array', desc, items=model.schema())


def str_list_schema(desc: str) -> Dict[str, Any]:
    """元素为字符串的数组参数。"""
    return base_schema('array', desc, items={'type': 'string'})


def str_str_map_schema(desc: str) -> Dict[str, Any]:
    """string->string 字典参数。"""
    return base_schema('object', desc, additionalProperties={'type': 'string'})


class ToolsMixin:

    _TOOL_PARAMS = {
        # ── IO：Workspace 读写（继承自 ToolsMixin）─────────────
        "tool_list_workspace": params_schema(),
        "tool_read_workspace_text": params_schema(
            required=['fname'],
            fname=base_schema('string', '项目内相对路径'),
        ),
        "tool_write_workspace_text": params_schema(
            required=['fname', 'text'],
            fname=base_schema('string', '项目内相对路径'),
            text=base_schema('string', '要写入的文本内容'),
        ),
        "tool_read_workspace_json": params_schema(
            required=['fname'],
            fname=base_schema('string', '项目内相对路径'),
        ),
        "tool_write_workspace_json": params_schema(
            required=['fname', 'obj'],
            fname=base_schema('string', '项目内相对路径'),
            obj=base_schema('object', '要序列化为 JSON 的对象'),
        ),
        "tool_read_workspace_yaml": params_schema(
            required=['fname'],
            fname=base_schema('string', '项目内相对路径'),
        ),
        "tool_write_workspace_yaml": params_schema(
            required=['fname', 'obj'],
            fname=base_schema('string', '项目内相对路径'),
            obj=base_schema('object', '要写入的对象'),
        ),
        "tool_print": params_schema(
            required=["text"],
            text=base_schema("string", "要打印的信息"),
        ),
        "tool_finish": params_schema(),
    }

    def __init__(self):
        self.pj_dir = '.'

    def _write_text(self, fname: str, text: str) -> None:
        """将 text 以 UTF-8 写入 fname（自动创建父目录）。"""
        os.makedirs(path.dirname(fname), exist_ok=True)
        open(fname, 'w', encoding='utf8').write(text)

    def _read_text(self, fname: str) -> str:
        """以 UTF-8 读取 fname 的文本内容。"""
        return open(fname, encoding='utf8').read()

    def _write_yaml(self, fname: str, obj) -> None:
        """将对象（含 pydantic 模型/列表）以 YAML 形式写入 fname。"""
        if isinstance(obj, BaseModel):
            obj = obj.dict()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        os.makedirs(path.dirname(fname), exist_ok=True)
        with open(fname, 'w', encoding='utf8') as f:
            yaml.safe_dump(obj, f, allow_unicode=True, sort_keys=False)

    def _read_yaml(self, fname: str, model):
        """从 fname 读取 YAML 并解析为指定 pydantic 模型；文件缺失或损坏时返回 None。"""
        if not path.isfile(fname) or not path.getsize(fname):
            return None
        try:
            data = yaml.safe_load(open(fname, encoding='utf8').read())
        except yaml.error.YAMLError:
            return None
        return parse_obj_as(model, data)

    def tool_list_workspace(self):
        return [
            path.join(root, f)
            for root, _, fnames in os.walk(self.pj_dir)
            for f in fnames
        ]

    def tool_read_workspace_text(self, fname: str):
        """读取项目目录下的文本文件（fname 为项目内相对路径）。"""
        return self._read_text(path.join(self.pj_dir, fname))

    def tool_write_workspace_text(self, fname: str, text: str):
        """向项目目录写入文本文件（fname 为项目内相对路径）。"""
        return self._write_text(path.join(self.pj_dir, fname), text)

    def tool_read_workspace_json(self, fname: str):
        """读取项目目录下的 JSON 文件并解析为对象。"""
        return json.loads(self.tool_read_workspace_text(fname))

    def tool_write_workspace_json(self, fname: str, obj: Any):
        """将对象以 JSON 形式写入项目目录（fname 为项目内相对路径）。"""
        return self.tool_write_workspace_text(
            fname, json.dumps(obj, ensure_ascii=False))

    def tool_read_workspace_yaml(self, fname: str):
        """读取项目目录下的 YAML 文件并解析为对象。"""
        return yaml.safe_load(self.tool_read_workspace_text(fname))

    def tool_write_workspace_yaml(self, fname: str, obj: Any):
        """将对象以 YAML 形式写入项目目录（fname 为项目内相对路径）。"""
        return self.tool_write_workspace_text(
            fname, yaml.safe_dump(obj, allow_unicode=True))

    def _json_dump(self, obj) -> str:
        """将对象（含 pydantic 模型/列表）序列化为 JSON 字符串。"""
        if isinstance(obj, BaseModel):
            obj = obj.model_dump()
        elif isinstance(obj, list):
            obj = [
                it.dict() if isinstance(it, BaseModel) else it
                for it in obj
            ]
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _json_load(self, text: str, model):
        """将 JSON 文本解析为指定 pydantic 模型。"""
        return parse_obj_as(model, json.loads(text))


    def tool_finish(self):
        """结束整个工具调用流程"""
        pass


    def tool_print(self, text: str):
        """打印信息"""
        print(text)

    def get_tool_dict(self) -> Dict[str, Callable]:
        """返回以 tool_ 开头、可调用的成员方法字典（工具名→方法）。"""
        return {
            name: getattr(self, name)
            for name in dir(self)
            if name.startswith('tool_') and
               callable(getattr(self, name))
        }

    @staticmethod
    def _clean_doc(doc: Optional[str]) -> str:
        """将函数 docstring 压缩为单行描述。"""
        if not doc:
            return ''
        return ' '.join(line.strip() for line in doc.splitlines() if line.strip())

    def get_tool_defs(self) -> List:
        """返回所有 tool_* 方法的 OpenAI 函数工具定义（Chat Completions tools 格式）。

        name 取自函数对象的 __name__，description 取自函数对象的 __doc__；
        parameters 结构由 _TOOL_PARAMS 提供。可直接传给 openai 的 tools 参数。
        """
        return [
            func_schema(
                getattr(self, name).__name__,
                self._clean_doc(getattr(self, name).__doc__),
                params
            )
            for name, params in self._TOOL_PARAMS.items()
        ]
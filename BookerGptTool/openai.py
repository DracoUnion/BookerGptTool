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
from .util import *
from openai.types.chat import *
from ctx_compact import compact

logging.getLogger("openai._base_client").setLevel(logging.CRITICAL)
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

NO_THINK_EXTRA_BODY = {
    "reasoning_effort": "none",
    "chat_template_kwargs": {
        "enable_thinking": False,
        "thinking_mode": "off",
        "reasoning_effort": "none",
        "reasoning_strength":"none"
    }
}

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

def ensure_utf8(text: str) -> str:
    return text.encode('utf8', 'ignore').decode('utf8', 'ignore')

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


class OpenAIClient:
    """封装 OpenAI API 调用（对话/视觉/文生图/工具循环），配置保存在实例上。"""

    def __init__(
        self,
        api_key=None,
        base_url=None,
        user_agent='claude-cli/2.1.41 (external, cli)',
        stream=False,
        timeout=None,
        rpre='',
        no_think=False,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.user_agent = user_agent
        self.stream = stream
        self.timeout = timeout
        self.rpre = rpre
        self.no_think = no_think

    @classmethod
    def from_args(cls, args):
        """从命令行参数对象构造 OpenAIClient。"""
        return cls(
            api_key=getattr(args, 'key', None),
            base_url=getattr(args, 'host', None),
            user_agent=getattr(args, 'user_agent', 'claude-cli/2.1.41 (external, cli)'),
            stream=not getattr(args, 'no_stream', False),
            timeout=openai.Timeout(
                read=getattr(args, 'read_timeout', 120),
                connect=getattr(args, 'conn_timeout', 60),
                write=None,
                pool=None,
            ),
            rpre=getattr(args, 'repetition_regex', ''),
            no_think=getattr(args, 'no_think', False),
        )

    def _build_client(self) -> openai.OpenAI:
        return openai.OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            default_headers={'User-Agent': self.user_agent},
            timeout=self.timeout,
        )

    # ── 对话 ──────────────────────────────────────────

    def call_llm(
        self, msgs, model_name, *,
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
        extra_body = extra_body or {}
        if isinstance(extra_body, str):
            extra_body = json.loads(extra_body)
        if self.no_think:
            extra_body.update(NO_THINK_EXTRA_BODY)
        logger.debug(f'ques: %s', json_dump_model(get_msgs_text(msgs)))
        sdk = self._build_client()
        res = sdk.chat.completions.create(
            messages=msgs,
            model=model_name,
            temperature=temp,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            max_tokens=max_tokens,
            extra_body=extra_body,
            stream=self.stream,
        )
        if self.stream:
            res: Iterable[ChatCompletionChunk]
            ans, rc = self.collect_stream_content(res)
            # ans = ans or rc
        else:
            res: ChatCompletion
            ans = res.choices[0].message.content.strip()
            rc = getattr(res.choices[0].message, 'reasoning_content', "").strip()
            # ans = ans or rc
            self.check_model_repetition(ans)
        if not ans:
            raise ValueError(f'回复为空：{res}')

        # 还原指令格式
        ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
        ans = re.sub(r' thinking[\s\S]+? response', '', ans)
        logger.debug(f'ans: %s', json_dump_model(ans))
        return ans

    def call_llm_retry(
        self, msgs, model_name, *,
        retry=10,
        temp=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        extra_body=None,
        parse_output=None,
    ):
        for i in range(retry):
            try:
                res =  self.call_llm(
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

    def ask_chatgpt_retry(
        self, ques, model_name, args,
        parse_output=None,
    ):
        return self.call_llm_retry(
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

    def call_vlm_retry(
        self, img, ques, model_name, args,
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
        return self.call_llm_retry(
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

    # ── 工具调用循环 ──────────────────────────────────

    def _chat_cmpl_create_retry(
        self, msgs, model_name,
        tool_defs,
        *,
        retry=10, temp=None,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        extra_body=None,
    ):
        sdk = self._build_client()
        for i in range(retry):
            try:
                res = sdk.chat.completions.create(
                    messages=msgs,
                    model=model_name,
                    temperature=temp,
                    top_p=top_p,
                    frequency_penalty=frequency_penalty,
                    presence_penalty=presence_penalty,
                    max_tokens=max_tokens,
                    extra_body=extra_body,
                    stream=self.stream,
                    tools=tool_defs,
                    tool_choice='auto',
                )
                if self.stream:
                    res: Iterable[ChatCompletionChunk]
                    toolcalls, ans, rc = self.collect_stream_toolcalls(res)
                    # ans = ans or rc
                    res_msg = None
                else:
                    res: ChatCompletion
                    res_msg = res.choices[0].message
                    toolcalls, ans = res_msg.tool_calls, res_msg.content
                    rc = getattr(res.choices[0].message, 'reasoning_content', '')
                if not ans and not toolcalls:
                    # 模型（如带思考的 glm/qwen 系）可能只返回 reasoning_content 而
                    # content 为空（无论是否调用工具）。把 reasoning_content 兜底为
                    # 助手文本，若仍为空则视为"未调用工具"，交由上层引导继续。
                    logger.debug(f'OpenAI 空回复（无 tool_calls 且无 content），将继续引导模型')
                return res, toolcalls or [], ans or '', rc or ''
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                logger.debug(f'OpenAI retry {i+1}: {str(ex)}')
                if i == retry - 1: raise ex

    def call_llm_with_toolcall_retry(
        self, msgs, model_name,
        tool_defs, tool_dict, *,
        tool_finish_name='',
        history_fname='',
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
        if history_fname:
            history = read_yaml_model(history_fname, None)
            if history: msgs = history
        extra_body = extra_body or {}
        if isinstance(extra_body, str):
            extra_body = json.loads(extra_body)
        if self.no_think:
            extra_body.update(NO_THINK_EXTRA_BODY)
        nudge_cnt = 0
        while True:
            msgs = compact(msgs, max_tokens=100_000).messages
            if history_fname:
                write_yaml_model(history_fname, msgs)
            logger.debug(f'ques: %s', json_dump_model(get_msgs_text(msgs)))
            res, toolcalls, ans, rc = self._chat_cmpl_create_retry(
                msgs, model_name,
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
                'content': ans or '',
                'reasoning_content': rc or '',
                "tool_calls": [tc.dict() for tc in toolcalls],
            })
            if not toolcalls:
                if not tool_finish_name: break
                if not ans:
                    # 模型未给出任何内容也未调用工具：直接提示其调用 tool_finish 结束。
                    errmsg = \
                        f"未找到任何工具调用，如果你想结束整个流程，调用`{tool_finish_name}`。"
                    msgs.append({"role": "user", "content": errmsg})
                else:
                    # 模型以纯文本作答（可能已输出最终 JSON）但未调用 tool_finish。
                    # 视为本轮已给出回复，提示其调用 tool_finish 以结束流程。
                    msgs.append({"role": "user", "content":
                        f"你已给出回复，但尚未调用`{tool_finish_name}`。若已全部完成，"
                        f"请调用`{tool_finish_name}`结束流程。"})
                # 防死循环：连续多轮无工具调用则按当前回复结束，交由 parse_output 兜底
                # （若解析失败会走 needs_approval 人工介入，而不是无限弹窗）。
                nudge_cnt += 1
                if nudge_cnt >= 5:
                    logger.warning('连续 %d 轮未调用工具，按当前回复结束循环', nudge_cnt)
                    break
                continue
            # 有工具调用，重置无工具计数
            nudge_cnt = 0
            logger.info(f'toolcall: %s', json_dump_model(toolcalls))
            logger.debug(f'ans: %s', json_dump_model(ans))
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
                    "content": (errmsg if errmsg else json_dump_model(tc_res))[:50_000],
                })
                logger.info(f'toolcall_res: %s', json_dump_model(msgs[-1]))

            if finish: break

        # 还原指令格式
        ans = re.sub(r'</([\w\-\.]+)/>', r'<|\1|>', ans)
        ans = re.sub(r' thinking[\s\S]+? response', '', ans)
        logger.debug(f'ans: %s', json_dump_model(ans))
        if parse_output:
            ans = parse_output(ans)
        return ans

    # ── 流式/重复检测 ─────────────────────────────────

    def collect_stream_toolcalls(self, resp: Iterable[ChatCompletionChunk]):
        tool_calls: Dict[int, ChatCompletionMessageToolCall] = {}
        content: List[str] = []
        rc: List[str] = []

        for ch in resp:  # resp 是 stream=True 的响应
            if not ch.choices:
                continue
            delta = ch.choices[0].delta

            # 1. 累积普通文本
            if delta.content:
                logger.debug(f"stream: %s", json_dump_model(delta.content))
                content.append(delta.content)

            delta_rc = getattr(delta, 'reasoning_content', "")
            if delta_rc:
                logger.debug(f"reasoning stream: %s", json_dump_model(delta_rc))
                rc.append(delta_rc)

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
        return list(tool_calls.values()), ''.join(content), ''.join(rc)

    def collect_stream_content(self, resp: Iterable[ChatCompletionChunk]):
        content = []
        rc = []
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            delta_content = delta.content
            delta_rc = getattr(delta, 'reasoning_content', "")
            if delta_content:
                content.append(delta_content)
                self.check_model_repetition(''.join(content))
                logger.debug(f'stream: %s', json_dump_model(delta_content))
            if delta_rc:
                rc.append(delta_rc)
                self.check_model_repetition(''.join(rc))
                logger.debug(f'reasoning stream: %s', json_dump_model(delta_rc))
        return ''.join(content), ''.join(rc)

    def check_model_repetition(self, text):
        if self.rpre and re.search(self.rpre, text):
            raise ValueError('检测到模型复读')

    # ── 文生图 ────────────────────────────────────────

    def call_tti(
        self, text, model_name,
        size='1024x1024',
        ref_img: Optional[bytes]=None,
    ):
        logger.debug(f'tti: %s', json_dump_model(text))
        sdk = self._build_client()
        if 'gpt-image' in model_name and ref_img:
            ref_img_b64 = base64.b64encode(ref_img).decode('ascii')
            extra_body = {
                "image": f"data:image/png;base64,{ref_img_b64}",
            }
        else:
            extra_body = {}
        img_data = sdk.images.generate(
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
        self, text, model_name,
        size='1024x1024',
        ref_img: Optional[bytes]=None,
        retry=10, nothrow=True,
    ):
        for i in range(retry):
            try:
                return self.call_tti(text, model_name, size, ref_img)
            except KeyboardInterrupt:
                raise
            except Exception as ex:
                logger.debug(f'OpenAI retry {i+1}: {str(ex)}')
                if i == retry - 1 and not nothrow: raise ex


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
            append=base_schema('boolean', '是否附加'),
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

    def tool_list_workspace(self):
        return [
            path.join(root, f)
            for root, _, fnames in os.walk(self.pj_dir)
            for f in fnames
        ]

    def tool_read_workspace_text(self, fname: str):
        """读取项目目录下的文本文件（fname 为项目内相对路径）。"""
        return read_text(path.join(self.pj_dir, fname))

    def tool_write_workspace_text(self, fname: str, text: str, append: bool = False):
        """向项目目录写入文本文件（fname 为项目内相对路径）。"""
        write_text(path.join(self.pj_dir, fname), text, append)
        return True

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

    def tool_finish(self):
        """结束整个工具调用流程"""
        pass


    def tool_print(self, text: str):
        """打印信息"""
        print(text)
        return True

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
import openai
import httpx
import os
import traceback
import yaml
import argparse
from os import path
import json
import random
import copy
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Lock


def call_openai_retry(ques, model_name, retry=10):
    for i in range(retry):
        try:
            print(f'ques: {json.dumps(ques, ensure_ascii=False)}')
            client = openai.OpenAI(
                base_url=openai.base_url,
                api_key=openai.api_key,
                http_client=httpx.Client(
                    proxies=openai.proxy,
                    transport=httpx.HTTPTransport(local_address="0.0.0.0"),
                )
            )
            ans = client.chat.completions.create(
                messages=[{
                    "role": "user",
                    "content": ques,
                }],
                model=model_name,
                temperature=0,
            ).choices[0].message.content
            print(f'ans: {json.dumps(ans, ensure_ascii=False)}')
            return ans
        except Exception as ex:
            print(f'OpenAI retry {i+1}: {str(ex)}')
            if i == retry - 1: raise ex

def set_openai_props(key=None, proxy=None, host=None):
    openai.api_key = key
    openai.proxy = proxy
    openai.base_url = host

RE_TITLE = r'\A\s*^#+\x20+(.+?)$'

def get_md_title(text):
    m = re.search(RE_TITLE, text, flags=re.M)
    if not m:
        return None, (None, None)
    return m.group(1).strip(), m.span(1)
    
def extname(fname):
    m = re.search(r'\.(\w+)$', fname)
    return m.group(1) if m else ''

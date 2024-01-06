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
                base_url=openai.host,
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
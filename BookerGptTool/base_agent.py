from typing import Optional, Callable
from .util import call_llm_retry


class BaseAgent:
    """所有智能体的基类，封装通用的初始化和 LLM 调用逻辑"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 temperature: float = 0.0, max_tokens: int = 2000,
                 retry: int = 3, stream: bool = False):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.retry = retry
        self.stream = stream

    def _call(self, system_prompt: str, user_prompt: str,
              max_tokens: Optional[int] = None, parse_output: Callable = None) -> str:
        """调用 LLM，返回原始文本响应，失败时自动重试"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return call_llm_retry(
            messages, self.model,
            retry=self.retry,
            temp=self.temperature,
            max_tokens=max_tokens or self.max_tokens,
            parse_output=parse_output,
        )

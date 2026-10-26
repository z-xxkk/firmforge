"""FakeLLM：按预设脚本回复，供单元测试使用，不联网。

一条预设回复可以是：
- 字符串：普通文字回复；
- 字典 {"tool_calls": [{"name": ..., "arguments": {...}}], "text": "", "reasoning": "..."}：模拟模型要调用工具；
- 函数 fn(messages) -> 上面两种之一。
"""

from __future__ import annotations

import json
from typing import Callable

from .client import LLMError, LLMResponse

Reply = str | dict | Callable[[list[dict]], "str | dict"]


def _content(m: dict) -> str:
    return m.get("content") or ""


class FakeLLM:
    """script 可以是回复列表（按顺序用完为止），也可以是 fn(messages) -> 回复。"""

    def __init__(self, script: list[Reply] | Callable[[list[dict]], "str | dict"], latency_s: float = 0.0,
                 sleep: Callable[[float], None] | None = None):
        self._script = script
        self.latency_s = latency_s           # 模拟网络延迟（性能测试用）
        self._sleep = sleep
        self.calls: list[dict] = []          # 每次调用的 messages、model、tools 等，供测试检查

    def request_info(self) -> dict:
        return {"url": "fake://chat/completions", "headers": {"Authorization": "***"}}

    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None,
                 extra_body: dict | None = None) -> LLMResponse:
        snapshot = [dict(m) for m in messages]
        self.calls.append({"messages": snapshot, "model": model, "temperature": temperature,
                           "max_tokens": max_tokens, "extra_body": extra_body, "tools": tools})
        if callable(self._script):
            reply = self._script(snapshot)
        else:
            idx = len(self.calls) - 1
            if idx >= len(self._script):
                raise LLMError("FakeLLM 的预设回复用完了")
            reply = self._script[idx]
            if callable(reply):
                reply = reply(snapshot)
        if self.latency_s:
            (self._sleep or __import__("time").sleep)(self.latency_s)
        text, tool_calls, reasoning = reply, None, ""
        if isinstance(reply, dict):
            text = reply.get("text", "")
            reasoning = reply.get("reasoning", "")
            n = len(self.calls)
            tool_calls = [{"id": f"call_{n}_{i}", "type": "function",
                           "function": {"name": c["name"],
                                        "arguments": c["arguments"] if isinstance(c.get("arguments"), str)
                                        else json.dumps(c.get("arguments", {}), ensure_ascii=False)}}
                          for i, c in enumerate(reply.get("tool_calls", []))] or None
        prompt_tokens = sum(len(_content(m)) for m in snapshot) // 4
        return LLMResponse(text=text, model=model, prompt_tokens=prompt_tokens,
                           completion_tokens=len(text) // 4, latency_s=self.latency_s,
                           tool_calls=tool_calls, reasoning=reasoning)

    def all_text_sent(self) -> str:
        """所有发给 LLM 的消息内容拼在一起，用于检查有没有泄露。"""
        return "\n".join(_content(m) for c in self.calls for m in c["messages"])

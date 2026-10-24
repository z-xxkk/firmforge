"""FakeLLM：按预设脚本回复，供单元测试使用，不联网。"""

from __future__ import annotations

from typing import Callable

from .client import LLMError, LLMResponse

Reply = str | Callable[[list[dict]], str]


class FakeLLM:
    """script 可以是回复列表（按顺序用完为止），也可以是 fn(messages) -> 回复。"""

    def __init__(self, script: list[Reply] | Callable[[list[dict]], str]):
        self._script = script
        self.calls: list[dict] = []     # 每次调用的 messages、model 等，供测试检查

    def request_info(self) -> dict:
        return {"url": "fake://chat/completions", "headers": {"Authorization": "***"}}

    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None,
                 extra_body: dict | None = None) -> LLMResponse:
        snapshot = [dict(m) for m in messages]
        self.calls.append({"messages": snapshot, "model": model, "temperature": temperature,
                           "max_tokens": max_tokens, "extra_body": extra_body})
        if callable(self._script):
            reply = self._script(snapshot)
        else:
            idx = len(self.calls) - 1
            if idx >= len(self._script):
                raise LLMError("FakeLLM 的预设回复用完了")
            reply = self._script[idx]
            if callable(reply):
                reply = reply(snapshot)
        prompt_tokens = sum(len(m.get("content", "")) for m in snapshot) // 4
        return LLMResponse(text=reply, model=model, prompt_tokens=prompt_tokens,
                           completion_tokens=len(reply) // 4, latency_s=0.0)

    def all_text_sent(self) -> str:
        """所有发给 LLM 的消息内容拼在一起，用于检查有没有泄露。"""
        return "\n".join(m.get("content", "") for c in self.calls for m in c["messages"])

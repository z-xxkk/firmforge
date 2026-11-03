"""LLM 调用日志：llm_calls.jsonl（摘要）+ llm_calls/<n>.json（完整请求与回复，请求头脱敏）。"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .client import LLMClient, LLMError, LLMResponse, redact_headers


class CallLogger:
    def __init__(self, run_dir: Path, secrets: list[str] | None = None):
        self.dir = Path(run_dir) / "llm_calls"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.summary = Path(run_dir) / "llm_calls.jsonl"
        self._secrets = [s for s in (secrets or []) if s]
        self._lock = threading.Lock()
        self._n = 0

    def _scrub(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, "***")
        return text

    def log(self, *, agent: str, model: str, request: dict, messages: list[dict], params: dict,
            response: LLMResponse | None, error: str | None) -> int:
        with self._lock:
            self._n += 1
            n = self._n
        req = dict(request)
        req["headers"] = redact_headers(req.get("headers", {}))
        full = {
            "n": n, "ts": time.time(), "agent": agent, "model": model, "request": req,
            "params": params, "messages": messages,
            "response": None if response is None else {
                "text": response.text, "reasoning": response.reasoning, "model": response.model,
                "reasoning_tokens": response.reasoning_tokens,
                "prompt_tokens": response.prompt_tokens, "completion_tokens": response.completion_tokens,
                "latency_s": round(response.latency_s, 3), "tool_calls": response.tool_calls},
            "error": error,
        }
        summary = {
            "n": n, "ts": full["ts"], "agent": agent, "model": model, "ok": error is None,
            "prompt_tokens": response.prompt_tokens if response else 0,
            "completion_tokens": response.completion_tokens if response else 0,
            "latency_s": round(response.latency_s, 3) if response else None, "error": error,
        }
        body = self._scrub(json.dumps(full, ensure_ascii=False, indent=2))
        line = self._scrub(json.dumps(summary, ensure_ascii=False))
        with self._lock:
            (self.dir / f"{n:04d}.json").write_text(body, encoding="utf-8")
            with self.summary.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return n


class LoggedLLM:
    """包装一个 LLMClient：每次调用都写调用日志。"""

    def __init__(self, inner: LLMClient, logger: CallLogger, agent: str):
        self.inner = inner
        self.logger = logger
        self.agent = agent

    def request_info(self) -> dict:
        return self.inner.request_info()

    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None,
                 extra_body: dict | None = None) -> LLMResponse:
        params = {"temperature": temperature, "max_tokens": max_tokens, "tools": bool(tools),
                  "extra_body": extra_body or {}}
        try:
            resp = self.inner.complete(messages, model=model, temperature=temperature,
                                       max_tokens=max_tokens, tools=tools, extra_body=extra_body)
        except LLMError as exc:
            self.logger.log(agent=self.agent, model=model, request=self.inner.request_info(),
                            messages=messages, params=params, response=None, error=str(exc))
            raise
        self.logger.log(agent=self.agent, model=model, request=self.inner.request_info(),
                        messages=messages, params=params, response=resp, error=None)
        return resp


class MeteredLLM:
    """包装一个 LLMClient：累计 token 和费用。cost_of(模型名, 输入 token, 输出 token) → 元。"""

    def __init__(self, inner, cost_of):
        self.inner, self.cost_of = inner, cost_of
        self.prompt_tokens = self.completion_tokens = self.calls = 0
        self.cost = 0.0

    def request_info(self) -> dict:
        return self.inner.request_info()

    def complete(self, messages: list[dict], *, model: str, **kw) -> LLMResponse:
        resp = self.inner.complete(messages, model=model, **kw)
        self.calls += 1
        self.prompt_tokens += resp.prompt_tokens
        self.completion_tokens += resp.completion_tokens
        self.cost += self.cost_of(model, resp.prompt_tokens, resp.completion_tokens)
        return resp

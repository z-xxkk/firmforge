"""OpenAI 兼容接口的 LLM 客户端（ARCHITECTURE §4），只用标准库 urllib。"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..config import DEFAULT_BASE_URL, Settings, SettingsError

RETRY_STATUS = {429, 500, 502, 503, 504}
SECRET_HEADERS = {"authorization", "api-key", "x-api-key"}


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    tool_calls: list | None = None
    raw: dict = field(default_factory=dict)
    reasoning: str = ""          # 思考模式下的思维链（reasoning_content）；只在工具往返时按 DeepSeek 要求回传
    reasoning_tokens: int = 0


class LLMError(RuntimeError):
    """LLM 调用失败（重试用尽或不可重试的错误）。"""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMClient(Protocol):
    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None,
                 extra_body: dict | None = None) -> LLMResponse: ...

    def request_info(self) -> dict: ...


def redact_headers(headers: dict) -> dict:
    """请求头脱敏：Authorization 等替换为 ***。"""
    return {k: ("***" if k.lower() in SECRET_HEADERS else v) for k, v in headers.items()}


# opener(request, timeout) -> (status, body_bytes, headers)
Opener = Callable[[urllib.request.Request, float], tuple[int, bytes, dict]]


def _urllib_opener(req: urllib.request.Request, timeout: float) -> tuple[int, bytes, dict]:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(), dict(resp.headers)


class OpenAICompatClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, *, timeout_s: float = 120,
                 max_retries: int = 3, opener: Opener | None = None,
                 sleep: Callable[[float], None] = time.sleep, backoff_s: float = 1.0):
        if not api_key:
            raise SettingsError("API key 为空")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._open = opener or _urllib_opener
        self._sleep = sleep
        self.backoff_s = backoff_s

    @classmethod
    def from_settings(cls, settings: Settings, env: dict[str, str] | None = None, **kw) -> "OpenAICompatClient":
        env = os.environ if env is None else env
        key = env.get(settings.llm.api_key_env, "").strip()
        if not key:
            raise SettingsError(
                f"没有找到 API key：请在 .env 里填写 {settings.llm.api_key_env}=…（或设置同名环境变量）")
        base = env.get(settings.llm.base_url_env, "").strip() or DEFAULT_BASE_URL
        return cls(key, base, timeout_s=settings.llm.timeout_s, max_retries=settings.llm.max_retries, **kw)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json",
                "Accept": "application/json"}

    def request_info(self) -> dict:
        return {"url": f"{self.base_url}/chat/completions", "headers": redact_headers(self._headers())}

    def _request(self, method: str, path: str, body: dict | None) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        last_error = "未知错误"
        status = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
            retry_after = None
            try:
                status, raw, _hdrs = self._open(req, self.timeout_s)
                if status == 200:
                    return json.loads(raw.decode("utf-8"))
                last_error = f"HTTP {status}"
            except urllib.error.HTTPError as exc:
                status = exc.code
                detail = exc.read().decode("utf-8", errors="replace")[:300] if exc.fp else ""
                last_error = f"HTTP {status}：{detail}"
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if status not in RETRY_STATUS:
                    raise LLMError(f"LLM 请求失败，{last_error}", status) from exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                status = None
                last_error = f"网络错误：{getattr(exc, 'reason', exc)}"
            except json.JSONDecodeError as exc:
                raise LLMError(f"LLM 返回的不是 JSON：{exc}") from exc
            if attempt < self.max_retries:
                delay = self.backoff_s * (2 ** attempt)
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                self._sleep(delay)
        raise LLMError(f"LLM 请求失败（已重试 {self.max_retries} 次）：{last_error}", status)

    def complete(self, messages: list[dict], *, model: str, temperature: float = 0.2,
                 max_tokens: int = 4096, tools: list[dict] | None = None,
                 extra_body: dict | None = None) -> LLMResponse:
        """extra_body：模型专属参数，原样并入请求体，例如 {"thinking": {"type": "enabled"}}。"""
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
        if extra_body:
            body.update(extra_body)
        t0 = time.perf_counter()
        data = self._request("POST", "/chat/completions", body)
        latency = time.perf_counter() - t0
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"LLM 回复格式不对：{str(data)[:200]}") from exc
        usage = data.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        return LLMResponse(text=msg.get("content") or "", model=data.get("model", model),
                           prompt_tokens=int(usage.get("prompt_tokens", 0)),
                           completion_tokens=int(usage.get("completion_tokens", 0)),
                           latency_s=latency, tool_calls=msg.get("tool_calls"), raw=data,
                           reasoning=msg.get("reasoning_content") or "",
                           reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0))

    def list_models(self) -> list[str]:
        data = self._request("GET", "/models", None)
        return sorted(m.get("id", "") for m in data.get("data", []) if m.get("id"))

"""调用循环（tools_mcp_framework.md 第 3 节①）：和模型来回说话，直到它不再调用工具。

- 把 Agent 能用的工具清单随请求发出；模型回"要调用"就交给执行流水线；
- 按 DeepSeek 文档：带了工具的请求，之后每次都把模型这一轮的完整回复（含 reasoning_content）放回对话；
- 一次模型回复里的多个调用：标明能并行的同时执行，其余按顺序（借鉴 DeepSeek Harness）；
- 一次生成最多 max_rounds 轮工具往返，用完后最后一次请求不带工具，要求直接交卷。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from ..llm.client import LLMResponse
from .pipeline import ToolCall, ToolPipeline, ToolResult
from .registry import ToolDef

DEFAULT_MAX_ROUNDS = 5
ROUNDS_USED_UP = "工具调用次数已经用完。请不要再调用工具，直接按要求的格式给出最终答案。"

Complete = Callable[..., LLMResponse]            # complete(messages, tools=...) -> LLMResponse


@dataclass
class ToolLoopResult:
    response: LLMResponse                         # 最后一次回复（交卷的那一次）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    rounds: int = 0                               # 工具往返轮数
    results: list[ToolResult] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)   # 完整对话（含工具往返）


def parse_tool_calls(raw: list | None) -> list[ToolCall]:
    calls = []
    for i, c in enumerate(raw or []):
        fn = (c or {}).get("function") or {}
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            import json
            args = json.dumps(args, ensure_ascii=False)
        calls.append(ToolCall(id=str(c.get("id") or f"call_{i}"), name=str(fn.get("name", "")), arguments=args))
    return calls


def assistant_message(resp: LLMResponse) -> dict:
    """把模型的回复原样放回对话：内容、思考内容、工具调用都带上。"""
    msg = {"role": "assistant", "content": resp.text or ""}
    if resp.reasoning:
        msg["reasoning_content"] = resp.reasoning
    if resp.tool_calls:
        msg["tool_calls"] = resp.tool_calls
    return msg


def _execute_batch(pipeline: ToolPipeline, calls: list[ToolCall]) -> list[ToolResult]:
    results: list[ToolResult | None] = [None] * len(calls)
    parallel = [i for i, c in enumerate(calls)
                if (t := pipeline.registry.get(c.name)) is not None and t.concurrency_safe]
    threads = []
    for i in parallel:
        def run(i=i):
            results[i] = pipeline.execute(calls[i])
        th = threading.Thread(target=run, daemon=True)
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    for i, c in enumerate(calls):
        if results[i] is None:
            results[i] = pipeline.execute(c)
    return results  # type: ignore[return-value]


def complete_with_tools(complete: Complete, messages: list[dict], tools: list[ToolDef], pipeline: ToolPipeline,
                        *, max_rounds: int = DEFAULT_MAX_ROUNDS) -> ToolLoopResult:
    msgs = list(messages)
    schemas = [t.schema() for t in tools]
    out = ToolLoopResult(response=None)  # type: ignore[arg-type]
    while True:
        offer = schemas if (schemas and out.rounds < max_rounds) else None
        if schemas and offer is None:
            msgs.append({"role": "user", "content": ROUNDS_USED_UP})
        resp = complete(msgs, tools=offer)
        out.prompt_tokens += resp.prompt_tokens
        out.completion_tokens += resp.completion_tokens
        out.reasoning_tokens += resp.reasoning_tokens
        out.latency_s += resp.latency_s
        calls = parse_tool_calls(resp.tool_calls) if offer else []
        if not calls:
            out.response = resp
            out.messages = msgs + [assistant_message(resp)]
            return out
        out.rounds += 1
        msgs.append(assistant_message(resp))
        for r in _execute_batch(pipeline, calls):
            out.results.append(r)
            msgs.append({"role": "tool", "tool_call_id": r.call_id, "content": r.content})


def call_with_tools(complete: Complete, messages: list[dict], tools: list[ToolDef], pipeline: ToolPipeline,
                    *, max_rounds: int = DEFAULT_MAX_ROUNDS) -> LLMResponse:
    """跑完调用循环，返回一个合并后的回复：文字是最后一次的，token 和耗时是全部往返的总和；
    完整对话放在 raw["conversation"]，接着追问时要用它（DeepSeek 要求回传思考内容）。"""
    import dataclasses
    r = complete_with_tools(complete, messages, tools, pipeline, max_rounds=max_rounds)
    raw = dict(r.response.raw or {})
    raw["conversation"] = r.messages
    raw["tool_rounds"] = r.rounds
    raw["tool_calls_made"] = len(r.results)
    return dataclasses.replace(r.response, prompt_tokens=r.prompt_tokens, completion_tokens=r.completion_tokens,
                               reasoning_tokens=r.reasoning_tokens, latency_s=r.latency_s, raw=raw)


def followup(messages: list[dict], resp: LLMResponse) -> list[dict]:
    """接着这次回复继续对话时用的消息列表：用过工具就用完整对话，否则在原消息后加上这次的回复。"""
    conv = (resp.raw or {}).get("conversation") if isinstance(resp.raw, dict) else None
    return list(conv) if conv else list(messages) + [{"role": "assistant", "content": resp.text}]

"""执行流水线（tools_mcp_framework.md 第 3 节③）：每次工具调用都走这 6 步，所有规矩集中在这里。

1 记录调用 → 2 调用前检查（能不能用、参数对不对、要不要问人）→ 3 守卫（只拒不放：不许碰隐藏测试）
→ 4 执行（套超时）→ 5 整理结果（截断、出错如实标失败）→ 6 记录结果。

借鉴：DeepSeek Harness 的工具执行流水线（先记录再执行、守卫只能拒绝、失败不伪装成功、模型看到的都要记录）；
Claude Code 的输出长度上限；OpenAI Agents SDK 的按工具审批。
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .registry import ToolContext, ToolDef, ToolRegistry

DEFAULT_MAX_CHARS = 8000
GUARDED = ("hidden_tests", "reference/", "mutants/")       # 守卫：参数里出现这些就拒绝

Emit = Callable[..., None]                                  # emit(type, node_id, **data)
Approve = Callable[[str, str, dict], bool]                  # (Agent, 工具名, 参数) -> 是否允许


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str                       # 模型给的原始 JSON 字符串


@dataclass
class ToolResult:
    call_id: str
    name: str
    ok: bool
    content: str                         # 返回给模型的文字
    latency_s: float = 0.0
    truncated: bool = False
    summary: str = ""                    # 给界面的一句话


def _type_ok(value, schema: dict) -> bool:
    t = schema.get("type")
    return {"string": isinstance(value, str), "object": isinstance(value, dict),
            "array": isinstance(value, list), "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)}.get(t, True)


def param_usage(tool: ToolDef) -> str:
    """参数说明，放进出错提示里，例如 'query（必填，string）、doc（可选，string）；例：{"query": "...", "doc": "..."}'"""
    props = tool.parameters.get("properties", {})
    req = set(tool.parameters.get("required", []))
    parts = [f"{k}（{'必填' if k in req else '可选'}，{v.get('type', '')}）" for k, v in props.items()]
    example = json.dumps({k: "..." for k in props}, ensure_ascii=False)
    return "、".join(parts) + f"；例：{example}"


def parse_arguments(raw: str, tool: ToolDef) -> tuple[object, str]:
    """解析模型给的参数。模型偶尔把同一个键写两次（如 {"doc": "关键词", "doc": "资料编号"}，JSON 只留最后一个，
    必填的 query 就丢了；第 123 条：基线里 doc_search 这样丢参数 76 次）：这时把前面重复的那个值补给缺的必填参数。
    返回 (参数, 纠正说明；没纠正是空字符串)。"""
    pairs_seen: list[list[tuple[str, object]]] = []
    args = json.loads(raw, object_pairs_hook=lambda pairs: pairs_seen.append(pairs) or dict(pairs))
    if not isinstance(args, dict) or not pairs_seen:
        return args, ""
    top = pairs_seen[-1]                                   # 最外层对象最后构造
    keys = [k for k, _ in top]
    dups = [(k, v) for i, (k, v) in enumerate(top) if k in keys[i + 1:]]   # 被后面同名键盖掉的值
    missing = [k for k in tool.parameters.get("required", []) if k not in args]
    props = tool.parameters.get("properties", {})
    notes = []
    for key in missing:
        for j, (k, v) in enumerate(dups):
            if _type_ok(v, props.get(key, {})):
                args = {**args, key: v}
                notes.append(f"参数里 {k} 写了两次，前一个当作 {key}={json.dumps(v, ensure_ascii=False)}")
                dups.pop(j)
                break
    return args, ("；".join(notes) + f"。正确写法：{param_usage(tool)}") if notes else ""


def check_arguments(tool: ToolDef, args) -> str | None:
    """按参数格式做最基本的检查：是对象、必填项齐全、类型对、不多给。返回问题描述，没问题返回 None。"""
    if not isinstance(args, dict):
        return "参数必须是 JSON 对象"
    props = tool.parameters.get("properties", {})
    for key in tool.parameters.get("required", []):
        if key not in args:
            return f"缺少参数 {key}。{tool.name} 的参数：{param_usage(tool)}"
    for key, value in args.items():
        if key not in props:
            if tool.parameters.get("additionalProperties") is False:
                return f"不认识的参数 {key}"
            continue
        if not _type_ok(value, props[key]):
            return f"参数 {key} 的类型应该是 {props[key].get('type')}"
    return None


class ToolPipeline:
    def __init__(self, registry: ToolRegistry, *, allowed: list[ToolDef], ctx: ToolContext,
                 emit: Emit | None = None, node_id: str | None = None, max_chars: int = DEFAULT_MAX_CHARS,
                 approve: Approve | None = None, output_dir: Path | None = None):
        self.registry = registry
        self.allowed = {t.name for t in allowed}
        self.ctx = ctx
        self.emit = emit or (lambda *a, **k: None)
        self.node_id = node_id
        self.max_chars = max_chars
        self.approve = approve
        self.output_dir = output_dir
        self.calls = 0
        self._seq = itertools.count(1)

    def _fail(self, call: ToolCall, msg: str, t0: float) -> ToolResult:
        return ToolResult(call.id, call.name, False, f"工具调用失败：{msg}", time.perf_counter() - t0, summary=msg)

    def execute(self, call: ToolCall) -> ToolResult:
        t0 = time.perf_counter()
        self.calls += 1
        # 1 记录调用
        self.emit("tool_called", self.node_id, agent=self.ctx.agent, tool=call.name, call_id=call.id,
                  args=call.arguments[:300])
        result = self._run(call, t0)
        # 6 记录结果
        self.emit("tool_result", self.node_id, agent=self.ctx.agent, tool=call.name, call_id=call.id,
                  ok=result.ok, latency_s=round(result.latency_s, 3), truncated=result.truncated,
                  summary=result.summary)
        return result

    def _run(self, call: ToolCall, t0: float) -> ToolResult:
        # 2 调用前检查
        tool = self.registry.get(call.name)
        if tool is None or call.name not in self.allowed:
            return self._fail(call, f"你不能使用工具 {call.name}", t0)
        try:
            args, fixed = parse_arguments(call.arguments or "{}", tool)
        except json.JSONDecodeError as exc:
            return self._fail(call, f"参数不是合法的 JSON：{exc}", t0)
        problem = check_arguments(tool, args)
        if problem:
            return self._fail(call, problem, t0)
        if tool.require_approval:
            if self.approve is None:
                return self._fail(call, "这个工具需要负责人确认，当前没有人值守，已拒绝", t0)
            if not self.approve(self.ctx.agent, call.name, args):
                return self._fail(call, "负责人拒绝了这次调用", t0)
        # 3 守卫：只能拒绝
        if any(g in call.arguments for g in GUARDED):
            return self._fail(call, "不允许访问隐藏测试、参考实现或变异体", t0)
        # 4 执行（套超时）
        box: dict = {}

        def target():
            try:
                box["value"] = tool.execute(args, self.ctx)
            except Exception as exc:          # 工具里的任何错误都如实返回给模型
                box["error"] = f"{type(exc).__name__}: {exc}" if not isinstance(exc, ValueError) else str(exc)

        th = threading.Thread(target=target, daemon=True)
        th.start()
        th.join(tool.timeout_s)
        if th.is_alive():
            return self._fail(call, f"超时（{tool.timeout_s:g} 秒）", t0)
        if "error" in box:
            return self._fail(call, box["error"], t0)
        text = str(box.get("value", ""))
        if fixed:                                          # 告诉模型参数被纠正了，下次写对
            text = f"（提示：{fixed}）\n\n" + text
        # 5 整理结果
        truncated = len(text) > self.max_chars
        summary = tool.summarize(args, text) if tool.summarize else ""
        if truncated:
            note = ""
            if self.output_dir is not None:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                path = self.output_dir / f"{next(self._seq):04d}_{call.name}.txt"
                path.write_text(text, encoding="utf-8")
                note = f"，完整内容已保存到运行目录 {path.parent.name}/{path.name}"
            text = text[:self.max_chars] + f"\n\n……（结果太长，已截断到前 {self.max_chars} 个字符{note}）"
        return ToolResult(call.id, call.name, True, text, time.perf_counter() - t0, truncated, summary)

"""工具箱（tools_mcp_framework.md 第 3 节②）：登记工具、按工具集分组、按 Agent 过滤、生成发给模型的工具清单。

借鉴：
- DeepSeek Harness：工具定义分两半，发给模型的只有名字、说明、参数格式；超时、能否并行、要不要问人只留在平台内部；
- Hermes：工具按工具集分组开关，每个外部 MCP 服务自动成为一个工具集 mcp-<服务名>；
- DeepSeek Harness / Claude Code：外部工具命名 mcp__<服务名>__<工具名>，有损转换时加哈希防撞名；
- DeepSeek Harness：外部工具列表整批替换，失败保留上一批。
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

BUILTIN_TOOLSETS = ("knowledge", "build", "interface")
_NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ToolError(ValueError):
    """工具定义不合法，或工具集不存在。"""


@dataclass
class ToolContext:
    """执行工具时能用到的东西。由调用方（引擎、单 Agent 基线、MCP 服务端）按场景填写。"""
    task: Any = None                     # tasks.Task
    module: Any = None                   # dag.Module；单 Agent 基线为 None
    plan: Any = None                     # dag.Plan
    dep_files: dict[str, str] = field(default_factory=dict)
    toolchain: Any = None
    kb: Any = None                       # memory.knowledge.KnowledgeBase
    scratch_dir: Path | None = None      # 工具的临时工作目录（运行目录下）
    agent: str = ""
    allowed_src_dirs: tuple[str, ...] = ("src",)


@dataclass(frozen=True)
class ToolDef:
    name: str
    toolset: str
    description: str
    parameters: dict                                     # JSON Schema（object）
    execute: Callable[[dict, ToolContext], str]          # 返回给模型的文字；出错抛异常
    timeout_s: float = 60.0
    concurrency_safe: bool = True
    require_approval: bool = False
    summarize: Callable[[dict, str], str] | None = None  # 给界面的一句话摘要（不发给模型）

    def schema(self) -> dict:
        """发给模型的部分：只有名字、说明、参数格式。"""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


def mcp_tool_name(server: str, raw: str) -> str:
    """外部工具的公开名字：mcp__<服务名>__<工具名>。原名里有非法字符时替换成 _ 并加 12 位哈希防撞名。"""
    if not SERVER_NAME.match(server):
        raise ToolError(f"MCP 服务名 {server!r} 只能包含字母、数字、下划线、横线，最长 32 个字符")
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
    name = f"mcp__{server}__{clean}"
    if clean != raw or len(name) > 64:
        digest = hashlib.sha256(f"{server}\0{raw}".encode("utf-8")).hexdigest()[:12]
        name = f"mcp__{server}__{clean[:64 - len(server) - 20]}_{digest}"
    return name


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDef] = {}
        self._lock = threading.Lock()

    def register(self, tool: ToolDef) -> None:
        if not _NAME_OK.match(tool.name):
            raise ToolError(f"工具名 {tool.name!r} 不合法")
        with self._lock:
            if tool.name in self._tools:
                raise ToolError(f"工具 {tool.name} 已经登记过")
            self._tools[tool.name] = tool

    def replace_toolset(self, toolset: str, tools: list[ToolDef]) -> None:
        """整批替换某个工具集（外部 MCP 服务刷新工具列表时用）。有冲突就整批拒绝，保留旧的。"""
        names = [t.name for t in tools]
        if len(set(names)) != len(names):
            raise ToolError(f"工具集 {toolset} 里有重名工具")
        with self._lock:
            others = {n for n, t in self._tools.items() if t.toolset != toolset}
            clash = others.intersection(names)
            if clash:
                raise ToolError(f"工具集 {toolset} 的工具和已有工具重名：{', '.join(sorted(clash))}")
            for n in [n for n, t in self._tools.items() if t.toolset == toolset]:
                del self._tools[n]
            for t in tools:
                if not _NAME_OK.match(t.name) or t.toolset != toolset:
                    raise ToolError(f"工具 {t.name} 不属于工具集 {toolset} 或名字不合法")
                self._tools[t.name] = t

    def remove_toolset(self, toolset: str) -> None:
        with self._lock:
            for n in [n for n, t in self._tools.items() if t.toolset == toolset]:
                del self._tools[n]

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def toolsets(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for t in self._tools.values():
            out.setdefault(t.toolset, []).append(t.name)
        return out

    def for_agent(self, toolsets: tuple[str, ...] | list[str]) -> list[ToolDef]:
        """Agent 能用的工具：toolsets 里既可以写工具集名，也可以写单个工具名。"""
        wanted = set(toolsets)
        return [t for t in self._tools.values() if t.toolset in wanted or t.name in wanted]

    def all(self) -> list[ToolDef]:
        return list(self._tools.values())


def default_registry() -> ToolRegistry:
    from .builtin import BUILTIN_TOOLS
    reg = ToolRegistry()
    for t in BUILTIN_TOOLS:
        reg.register(t)
    return reg

"""工具框架（DECISIONS 第 63 条，tools_mcp_framework.md 第 2 版）。"""

from .loop import ToolLoopResult, complete_with_tools
from .pipeline import ToolCall, ToolPipeline, ToolResult
from .registry import (BUILTIN_TOOLSETS, ToolContext, ToolDef, ToolError, ToolRegistry, default_registry,
                       mcp_tool_name)

__all__ = ["BUILTIN_TOOLSETS", "ToolCall", "ToolContext", "ToolDef", "ToolError", "ToolLoopResult",
           "ToolPipeline", "ToolRegistry", "ToolResult", "complete_with_tools", "default_registry", "mcp_tool_name"]

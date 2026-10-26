"""MCP（模型上下文协议）：服务端 `firmforge mcp` 与客户端（DECISIONS 第 63 条）。"""

from .client import McpClient, McpManager, ServerConfig
from .protocol import LEGACY, MODERN, SUPPORTED, McpError
from .server import McpServer

__all__ = ["LEGACY", "MODERN", "SUPPORTED", "McpClient", "McpError", "McpManager", "McpServer", "ServerConfig"]

"""MCP 两端共用的部分：JSON-RPC 2.0 消息、版本号、错误码（DECISIONS 第 63 条）。

依据 MCP 规范 2026-07-28（basic、versioning、transports/stdio、server/discover、server/tools）：
- stdio 上一行一条 JSON-RPC 消息，消息里不能有换行；标准输出只能写 MCP 消息，日志写标准错误；
- 2026-07-28（"新版"）没有握手：每个请求在 params._meta 里自带协议版本和客户端能力；
- 2025-11-25 及以前（"旧版"）要先 initialize 握手；
- 同时支持两版的实现叫"双版本"：服务端看对方怎么开口决定用哪一版；客户端先发 server/discover 试探，
  收到看不懂的错误或超时就退回 initialize。
核心库只用标准库，所以这里自己实现，不用官方 SDK。
"""

from __future__ import annotations

import json
from typing import IO

MODERN = "2026-07-28"
LEGACY = "2025-11-25"
LEGACY_ACCEPTED = ("2025-11-25", "2025-06-18", "2025-03-26")   # 旧版客户端说这些版本时照着它说的回
SUPPORTED = (MODERN, LEGACY)

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNSUPPORTED_VERSION = -32022
MODERN_ERRORS = (UNSUPPORTED_VERSION, -32020, -32021)            # 认得这些错误码，说明对方是新版


class McpError(Exception):
    def __init__(self, code: int, message: str, data=None):
        super().__init__(f"{message}（{code}）")
        self.code, self.message, self.data = code, message, data


def encode(msg: dict) -> str:
    """一条消息一行；json.dumps 默认会把字符串里的换行转义，所以输出里不会有裸换行。"""
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))


def write_message(stream: IO[str], msg: dict) -> None:
    stream.write(encode(msg) + "\n")
    stream.flush()


def request(id_, method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def notification(method: str, params: dict | None = None) -> dict:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def result(id_, value: dict) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "result": value}


def error(id_, code: int, message: str, data=None) -> dict:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def text_content(text: str) -> list[dict]:
    return [{"type": "text", "text": text}]


def content_to_text(content: list | None) -> str:
    """把工具结果里的内容块转成文字；图片、音频等给出说明而不是什么都没有（借鉴 DeepSeek Harness）。"""
    parts = []
    for c in content or []:
        kind = (c or {}).get("type")
        if kind == "text":
            parts.append(str(c.get("text", "")))
        elif kind == "resource_link":
            parts.append(f"[资源链接] {c.get('name', '')} {c.get('uri', '')}")
        elif kind == "resource":
            res = c.get("resource") or {}
            parts.append(str(res.get("text", "")) or f"[嵌入资源] {res.get('uri', '')}")
        else:
            parts.append(f"[{kind} 类型的内容，固件工坊不支持显示]")
    return "\n".join(parts)

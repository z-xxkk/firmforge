"""MCP 服务端：`firmforge mcp`，让 Claude Code、Cursor 等别的 AI 软件调用固件工坊的判定器（DECISIONS 第 63 条）。

- stdio，一行一条消息；日志只写标准错误；标准输入关闭就退出；
- 双版本：带 _meta 协议版本的请求按 2026-07-28 无状态处理（必须实现 server/discover）；
  收到 initialize 就按 2025-11-25 处理；版本不支持回 -32022 并列出支持的版本；
- 3 个工具：list_tasks、verify_c_module（跑第 1–5 层，不跑隐藏测试）、kb_search；
- 传入的文件只收 src/、tests/ 下的 .c/.h，路径不能有 ..，总共不超过 512 KB，写临时目录、用完删除。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import IO

from .. import __version__, config
from . import protocol as P

MAX_UPLOAD_BYTES = 512 * 1024
INSTRUCTIONS = ("固件工坊（FirmForge）是面向 STM32F103（Cortex-M3）的嵌入式 C 代码质量闸门。"
                "先用 list_tasks 看有哪些模块任务，再用 verify_c_module 提交 src/ 和 tests/ 下的文件，"
                "得到主机编译、交叉编译、资源预算、静态检查、单元测试五层的检查报告。kb_search 可以查嵌入式知识库。")

TOOLS = [
    {"name": "list_tasks", "title": "列出评测任务",
     "description": "列出固件工坊内置的嵌入式模块任务（id、名称、难度），verify_c_module 要用其中的 id。",
     "inputSchema": {"type": "object", "additionalProperties": False}},
    {"name": "verify_c_module", "title": "检查 C 模块",
     "description": "按某个任务的公共头文件和资源预算，对提交的 C 源文件和 Unity 测试做五层检查"
                    "（主机编译、arm-none-eabi 交叉编译、资源预算、禁用符号与 cppcheck、带 ASan/UBSan 的单元测试），返回报告。",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string", "description": "任务 id，来自 list_tasks"},
         "files": {"type": "object", "description": "文件路径（src/xxx.c、tests/test_xxx.c 等）→ 完整内容",
                   "additionalProperties": {"type": "string"}}},
         "required": ["task_id", "files"], "additionalProperties": False}},
    {"name": "kb_search", "title": "查嵌入式知识库",
     "description": "在嵌入式知识库（编码规则、Cortex-M3 注意事项、CRC 参数、协议要点等）里检索，返回最相关的 3 段。",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}},
                     "required": ["query"], "additionalProperties": False}},
]


class ToolFailure(Exception):
    """工具执行出错：按规范放在结果里（isError: true），而不是协议错误。"""


def _tasks():
    from ..tasks import discover_tasks
    root = Path.cwd() / "tasks"
    return discover_tasks(root if root.is_dir() else config.project_root() / "tasks")


def _list_tasks(args: dict) -> tuple[str, dict]:
    rows = [{"id": t.id, "title": t.title, "difficulty": getattr(t, "difficulty", None)} for t in _tasks()]
    text = "\n".join(f"- {r['id']}：{r['title']}" + (f"（难度 {r['difficulty']}）" if r["difficulty"] else "")
                     for r in rows) or "没有任务"
    return text, {"tasks": rows}


def _verify(args: dict) -> tuple[str, dict]:
    from ..agents.context import report_details
    from ..agents.protocol import check_path, write_files
    from ..verifier import verify
    task = next((t for t in _tasks() if t.id == args["task_id"]), None)
    if task is None:
        raise ToolFailure(f"没有任务 {args['task_id']}，请先调用 list_tasks")
    files = args["files"]
    if not files:
        raise ToolFailure("files 不能为空")
    total, clean = 0, {}
    for raw, content in files.items():
        if not isinstance(content, str):
            raise ToolFailure(f"{raw} 的内容必须是字符串")
        path, why = check_path(str(raw), ("src", "tests"))
        if path is None:
            raise ToolFailure(f"{raw}：{why}")
        total += len(content.encode("utf-8"))
        clean[path] = content if content.endswith("\n") else content + "\n"
    if total > MAX_UPLOAD_BYTES:
        raise ToolFailure(f"文件总共 {total} 字节，超过上限 {MAX_UPLOAD_BYTES}")
    work = Path(tempfile.mkdtemp(prefix="firmforge_mcp_"))
    try:
        write_files(work, clean)
        report = verify(work, task)                      # 不含隐藏测试
        agent_report = report.for_agent()
        text = f"结论：{agent_report.verdict}\n\n{report_details(agent_report, work)}"
        return text, {"verdict": agent_report.verdict, "failed_layer": agent_report.failed_layer}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _kb_search(args: dict) -> tuple[str, dict]:
    from ..memory.knowledge import KnowledgeBase
    chunks = KnowledgeBase.load().search(str(args["query"]), k=3)
    return ("\n\n".join(c.render() for c in chunks) or "没有找到相关内容。"), {"hits": [c.title for c in chunks]}


HANDLERS = {"list_tasks": _list_tasks, "verify_c_module": _verify, "kb_search": _kb_search}


class McpServer:
    def __init__(self, log: IO[str] | None = None):
        self.legacy_version: str | None = None       # 收到 initialize 后进入旧版模式
        self.log = log or sys.stderr

    def _server_info(self) -> dict:
        return {"name": "firmforge", "version": __version__}

    def handle(self, msg) -> dict | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return P.error(msg.get("id") if isinstance(msg, dict) else None, P.INVALID_REQUEST, "不是合法的 JSON-RPC 请求")
        id_, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if id_ is None:
            return None                                   # 通知不回复
        try:
            return P.result(id_, self._dispatch(method, params))
        except P.McpError as exc:
            return P.error(id_, exc.code, exc.message, exc.data)
        except Exception as exc:                          # 服务端自身的错误
            print(f"firmforge mcp：处理 {method} 出错：{exc}", file=self.log)
            return P.error(id_, P.INTERNAL_ERROR, f"服务端错误：{exc}")

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":                        # 旧版握手
            asked = params.get("protocolVersion")
            self.legacy_version = asked if asked in P.LEGACY_ACCEPTED else P.LEGACY
            return {"protocolVersion": self.legacy_version, "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": self._server_info(), "instructions": INSTRUCTIONS}
        meta = params.get("_meta") or {}
        modern = P.META_VERSION in meta
        if modern:
            version = meta[P.META_VERSION]
            if version != P.MODERN:
                raise P.McpError(P.UNSUPPORTED_VERSION, "Unsupported protocol version",
                                 {"supported": list(P.SUPPORTED), "requested": version})
            if P.META_CLIENT_CAPS not in meta:
                raise P.McpError(P.INVALID_PARAMS, f"缺少 _meta.{P.META_CLIENT_CAPS}")
        elif self.legacy_version is None and method != "ping":
            raise P.McpError(P.INVALID_PARAMS,
                             f"请求缺少 _meta.{P.META_VERSION}；旧版客户端请先发 initialize。支持的版本："
                             + "、".join(P.SUPPORTED))
        value = self._method(method, params)
        if modern:
            value = {"resultType": "complete", **value,
                     "_meta": {**value.get("_meta", {}), P.META_SERVER_INFO: self._server_info()}}
        return value

    def _method(self, method: str, params: dict) -> dict:
        if method == "ping":
            return {}
        if method == "server/discover":
            return {"supportedVersions": list(P.SUPPORTED), "capabilities": {"tools": {"listChanged": False}},
                    "instructions": INSTRUCTIONS}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in HANDLERS:
                raise P.McpError(P.INVALID_PARAMS, f"Unknown tool: {name}")
            tool = next(t for t in TOOLS if t["name"] == name)
            from ..tools.pipeline import check_arguments
            from ..tools.registry import ToolDef
            problem = check_arguments(ToolDef(name, "mcp", "", tool["inputSchema"], lambda a, c: ""), args)
            if problem:
                return {"content": P.text_content(f"参数不对：{problem}"), "isError": True}
            try:
                text, structured = HANDLERS[name](args)
            except ToolFailure as exc:
                return {"content": P.text_content(str(exc)), "isError": True}
            return {"content": P.text_content(text), "structuredContent": structured, "isError": False}
        raise P.McpError(P.METHOD_NOT_FOUND, f"不支持的方法 {method}")

    def serve(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        import json
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        print(f"firmforge mcp {__version__}：通过标准输入输出提供服务（支持 {'、'.join(P.SUPPORTED)}）", file=self.log)
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                P.write_message(stdout, P.error(None, P.PARSE_ERROR, f"不是合法的 JSON：{exc}"))
                continue
            reply = self.handle(msg)
            if reply is not None:
                P.write_message(stdout, reply)

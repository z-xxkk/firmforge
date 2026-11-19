"""MCP 客户端：把外部 MCP 服务的工具接进固件工坊的工具箱（DECISIONS 第 63 条）。

- 只支持本机子进程（stdio）；
- 双版本：先发 server/discover 试探（5 秒），回得上来就用 2026-07-28；回了新版才有的错误码说明是新版但版本不合；
  其他错误或超时就退回 initialize 握手（2025-11-25）。依据 MCP 规范 2026-07-28 stdio 一节的 Backward Compatibility；
- 配置格式和 Claude Code 的 mcpServers 一样（command、args、env、timeout），支持 ${变量} 和 ${变量:-默认值}；
- 启动外部服务时清掉名字像密钥的环境变量（借鉴 DeepSeek Harness、Claude Code），env 里明确写的才传；
- 启动超时 10 秒；断线重连 1 秒起翻倍、最多 5 次（借鉴 Claude Code）；重连失败就撤掉它的工具；
- 外部工具在固件工坊里叫 mcp__<服务名>__<工具名>，发给服务的永远是原始名字（借鉴 DeepSeek Harness）。
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .. import __version__
from ..tools.registry import SERVER_NAME, ToolDef, ToolError, ToolRegistry, mcp_tool_name
from . import protocol as P

PROBE_TIMEOUT_S = 5.0
START_TIMEOUT_S = 10.0
RECONNECT_ATTEMPTS = 5
RECONNECT_FIRST_DELAY_S = 1.0
MAX_LIST_PAGES = 20
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.I)
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand(text: str, env: dict[str, str]) -> str:
    """展开 ${变量} 和 ${变量:-默认值}；没有定义又没有默认值的原样保留。"""
    def sub(m):
        if m.group(1) in env:
            return env[m.group(1)]
        return m.group(2) if m.group(2) is not None else m.group(0)
    return _VAR.sub(sub, text)


def child_env(extra: dict[str, str], base: dict[str, str] | None = None) -> dict[str, str]:
    """给外部服务的环境变量：去掉名字像密钥的和 DEEPSEEK_ 开头的，再加上配置里明确写的。"""
    base = dict(os.environ if base is None else base)
    clean = {k: v for k, v in base.items() if not SECRET_NAME.search(k) and not k.upper().startswith("DEEPSEEK")}
    clean.update({k: expand(str(v), base) for k, v in extra.items()})
    return clean


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 60.0
    enabled: bool = False
    ask: bool = True                         # 每次调用前问人
    allow: list[str] | None = None           # 允许名单（借鉴 OpenAI Agents SDK）；None 表示全部
    deny: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "ServerConfig":
        if not SERVER_NAME.match(name):
            raise ToolError(f"MCP 服务名 {name!r} 只能包含字母、数字、下划线、横线，最长 32 个字符")
        if not isinstance(d.get("command"), str) or not d["command"]:
            raise ToolError(f"[mcp.servers.{name}] 缺少 command")
        return cls(name=name, command=d["command"], args=[str(a) for a in d.get("args", [])],
                   env={str(k): str(v) for k, v in (d.get("env") or {}).items()},
                   timeout_s=float(d.get("timeout_s", 60)), enabled=bool(d.get("enabled", False)),
                   ask=bool(d.get("ask", True)), allow=d.get("allow"), deny=list(d.get("deny", [])))


class McpClient:
    def __init__(self, cfg: ServerConfig, *, log: Callable[[str], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.era: str | None = None              # "modern" | "legacy"
        self.version: str | None = None
        self.instructions = ""
        self.dead = False
        self._log = log or (lambda s: None)
        self._sleep = sleep
        self._proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._pending: dict = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------ 进程

    def _argv(self) -> list[str]:
        env = dict(os.environ)
        cmd = expand(self.cfg.command, env)
        exe = shutil.which(cmd) or cmd
        return [exe, *(expand(a, env) for a in self.cfg.args)]

    def _spawn(self) -> None:
        self._proc = subprocess.Popen(self._argv(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=child_env(self.cfg.env), text=True,
                                      encoding="utf-8", bufsize=1)
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._log(f"MCP 服务 {self.cfg.name} 输出了不是 JSON 的内容，已忽略")
                continue
            if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    slot = self._pending.pop(msg["id"], None)
                if slot is not None:
                    slot["msg"] = msg
                    slot["event"].set()
        with self._lock:                         # 进程退出：叫醒所有还在等的请求
            for slot in self._pending.values():
                slot["event"].set()
            self._pending.clear()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            self._log(f"[{self.cfg.name}] {line.rstrip()}")

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------ 收发

    def _send(self, method: str, params: dict | None, timeout: float) -> dict:
        if not self._alive():
            raise ConnectionError(f"MCP 服务 {self.cfg.name} 没有在运行")
        id_ = next(self._ids)
        slot = {"event": threading.Event(), "msg": None}
        with self._lock:
            self._pending[id_] = slot
        try:
            with self._write_lock:
                self._proc.stdin.write(P.encode(P.request(id_, method, params)) + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._lock:
                self._pending.pop(id_, None)
            raise ConnectionError(f"MCP 服务 {self.cfg.name} 断开了：{exc}") from exc
        if not slot["event"].wait(timeout):
            with self._lock:
                self._pending.pop(id_, None)
            raise TimeoutError(f"MCP 服务 {self.cfg.name} 的 {method} 超时（{timeout:g} 秒）")
        msg = slot["msg"]
        if msg is None:
            raise ConnectionError(f"MCP 服务 {self.cfg.name} 退出了")
        if "error" in msg:
            e = msg["error"] or {}
            raise P.McpError(int(e.get("code", P.INTERNAL_ERROR)), str(e.get("message", "")), e.get("data"))
        return msg.get("result") or {}

    def _notify(self, method: str, params: dict | None = None) -> None:
        with self._write_lock:
            self._proc.stdin.write(P.encode(P.notification(method, params)) + "\n")
            self._proc.stdin.flush()

    def _params(self, params: dict | None) -> dict:
        params = dict(params or {})
        if self.era == "modern":
            params["_meta"] = {P.META_VERSION: self.version, P.META_CLIENT_CAPS: {},
                               P.META_CLIENT_INFO: {"name": "firmforge", "version": __version__}}
        return params

    # ------------------------------------------------------------ 连接（双版本）

    def connect(self) -> None:
        self._spawn()
        self.era, self.version = "modern", P.MODERN
        try:
            info = self._send("server/discover", self._params(None), PROBE_TIMEOUT_S)
            supported = info.get("supportedVersions") or [P.MODERN]
            if P.MODERN not in supported:
                raise ToolError(f"MCP 服务 {self.cfg.name} 只支持 {', '.join(supported)}，固件工坊支持 {P.MODERN}")
            self.instructions = str(info.get("instructions") or "")
            return
        except P.McpError as exc:
            if exc.code in P.MODERN_ERRORS:      # 新版服务但版本不合：不退回旧版
                supported = (exc.data or {}).get("supported", [])
                raise ToolError(f"MCP 服务 {self.cfg.name} 不支持 {P.MODERN}（它支持 {', '.join(supported)}）") from exc
        except TimeoutError:
            pass
        if not self._alive():
            raise ConnectionError(f"MCP 服务 {self.cfg.name} 启动后马上退出了")
        # 退回旧版握手
        self.era, self.version = "legacy", P.LEGACY
        info = self._send("initialize", {"protocolVersion": P.LEGACY, "capabilities": {},
                                         "clientInfo": {"name": "firmforge", "version": __version__}},
                          START_TIMEOUT_S)
        self.version = str(info.get("protocolVersion") or P.LEGACY)
        self.instructions = str(info.get("instructions") or "")
        self._notify("notifications/initialized")

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=2)
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
                proc.wait()
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except Exception:
                pass

    def _reconnect(self) -> bool:
        delay = RECONNECT_FIRST_DELAY_S
        for attempt in range(1, RECONNECT_ATTEMPTS + 1):
            self.close()
            self._sleep(delay)
            try:
                self.connect()
                self._log(f"MCP 服务 {self.cfg.name} 第 {attempt} 次重连成功")
                return True
            except Exception as exc:
                self._log(f"MCP 服务 {self.cfg.name} 第 {attempt} 次重连失败：{exc}")
                delay *= 2
        self.dead = True
        return False

    def call(self, method: str, params: dict | None, timeout: float) -> dict:
        """发请求；连接断了就按退避重连后重试一次。"""
        if self.dead:
            raise ConnectionError(f"MCP 服务 {self.cfg.name} 已经停用（重连失败）")
        try:
            return self._send(method, self._params(params), timeout)
        except ConnectionError:
            if not self._reconnect():
                raise
            return self._send(method, self._params(params), timeout)

    # ------------------------------------------------------------ 工具

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(MAX_LIST_PAGES):
            res = self.call("tools/list", {"cursor": cursor} if cursor else {}, START_TIMEOUT_S)
            tools += [t for t in res.get("tools", []) if isinstance(t, dict) and t.get("name")]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        names = [t["name"] for t in tools]
        if len(set(names)) != len(names):
            raise ToolError(f"MCP 服务 {self.cfg.name} 的工具列表里有重名工具")
        return tools

    def call_tool(self, raw_name: str, arguments: dict) -> tuple[str, bool]:
        res = self.call("tools/call", {"name": raw_name, "arguments": arguments}, self.cfg.timeout_s)
        return P.content_to_text(res.get("content")), bool(res.get("isError"))

    def tool_defs(self) -> list[ToolDef]:
        """把外部工具包装成工具箱里的工具：名字加前缀，按允许 / 禁止名单过滤。"""
        out = []
        for t in self.list_tools():
            raw = t["name"]
            if (self.cfg.allow is not None and raw not in self.cfg.allow) or raw in self.cfg.deny:
                continue
            schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object"}

            def execute(args, ctx, raw=raw):
                text, is_error = self.call_tool(raw, args)
                if is_error:
                    raise ValueError(text or "外部工具报告了错误")
                return text

            out.append(ToolDef(name=mcp_tool_name(self.cfg.name, raw), toolset=f"mcp-{self.cfg.name}",
                               description=str(t.get("description") or t.get("title") or raw)[:1000],
                               parameters=schema, execute=execute, timeout_s=self.cfg.timeout_s + 5,
                               concurrency_safe=False, require_approval=self.cfg.ask,
                               summarize=lambda a, r: f"返回 {len(r)} 个字符"))
        return out


class McpManager:
    """按 firmforge.toml 的 [mcp.servers.*] 启动外部服务，把它们的工具登记进工具箱。评测时不要用它。"""

    def __init__(self, servers: dict, *, log: Callable[[str], None] | None = None):
        self.configs = [ServerConfig.from_dict(n, d) for n, d in servers.items()]
        self.clients: dict[str, McpClient] = {}
        self.errors: dict[str, str] = {}
        self._log = log or (lambda s: print(s, file=sys.stderr))

    def start(self, registry: ToolRegistry, only_enabled: bool = True) -> None:
        for cfg in self.configs:
            if only_enabled and not cfg.enabled:
                continue
            client = McpClient(cfg, log=self._log)
            try:
                client.connect()
                registry.replace_toolset(f"mcp-{cfg.name}", client.tool_defs())
                self.clients[cfg.name] = client
            except Exception as exc:              # 一个服务起不来不影响其他
                self.errors[cfg.name] = str(exc)
                self._log(f"MCP 服务 {cfg.name} 没有连上：{exc}")
                client.close()

    def close(self) -> None:
        for c in self.clients.values():
            c.close()
        self.clients.clear()

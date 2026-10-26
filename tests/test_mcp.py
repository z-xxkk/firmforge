"""MCP 服务端与客户端（DECISIONS 第 63 条）：新旧两版协议、错误码、文件限制、断线重连、密钥环境变量。不联网。"""

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import helpers
from firmforge.mcp import client as C
from firmforge.mcp import protocol as P
from firmforge.mcp.server import MAX_UPLOAD_BYTES, McpServer
from firmforge.tools import ToolCall, ToolContext, ToolPipeline, ToolRegistry

META = {P.META_VERSION: P.MODERN, P.META_CLIENT_CAPS: {}}
SRC = str(helpers.SRC)

# 只会旧版握手的假服务：initialize 之前的任何请求都回 -32601
LEGACY_SERVER = textwrap.dedent('''
    import json, sys
    ready = False
    for line in sys.stdin:
        m = json.loads(line)
        if "id" not in m:
            continue
        if m["method"] == "initialize":
            ready = True
            r = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "old"}}
        elif not ready:
            print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32601, "message": "no"}}), flush=True)
            continue
        elif m["method"] == "tools/list":
            r = {"tools": [{"name": "echo.v1", "inputSchema": {"type": "object"}}, {"name": "secret_env"}]}
        elif m["method"] == "tools/call":
            a = m["params"]["arguments"]
            if m["params"]["name"] == "secret_env":
                text = ",".join(sorted(k for k in __import__("os").environ if "KEY" in k or k == "VISIBLE"))
            else:
                text = a.get("text", "")
            r = {"content": [{"type": "text", "text": text}], "isError": a.get("fail", False)}
            if a.get("die"):
                sys.exit(3)
        print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
''')


def req(method, params=None, id_=1):
    return P.request(id_, method, params)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.s = McpServer(log=io.StringIO())

    def test_modern_discover_and_list(self):
        r = self.s.handle(req("server/discover", {"_meta": META}))["result"]
        self.assertEqual(r["resultType"], "complete")
        self.assertEqual(r["supportedVersions"], [P.MODERN, P.LEGACY])
        self.assertIn(P.META_SERVER_INFO, r["_meta"])
        tools = self.s.handle(req("tools/list", {"_meta": META}))["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["list_tasks", "verify_c_module", "kb_search"])

    def test_modern_errors(self):
        e = self.s.handle(req("tools/list", {"_meta": {P.META_VERSION: "1900-01-01", P.META_CLIENT_CAPS: {}}}))
        self.assertEqual(e["error"]["code"], P.UNSUPPORTED_VERSION)
        self.assertEqual(e["error"]["data"], {"supported": [P.MODERN, P.LEGACY], "requested": "1900-01-01"})
        e = self.s.handle(req("tools/list", {"_meta": {P.META_VERSION: P.MODERN}}))
        self.assertEqual(e["error"]["code"], P.INVALID_PARAMS)               # 缺客户端能力
        e = self.s.handle(req("tools/list"))
        self.assertEqual(e["error"]["code"], P.INVALID_PARAMS)               # 既没有版本也没有握手
        self.assertEqual(self.s.handle(req("nope", {"_meta": META}))["error"]["code"], P.METHOD_NOT_FOUND)
        self.assertIsNone(self.s.handle(P.notification("notifications/initialized")))

    def test_legacy_handshake(self):
        r = self.s.handle(req("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}))["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        r = self.s.handle(req("tools/list", {}))["result"]
        self.assertNotIn("resultType", r)                                    # 旧版不加新版字段
        r = McpServer().handle(req("initialize", {"protocolVersion": "2024-01-01"}))["result"]
        self.assertEqual(r["protocolVersion"], P.LEGACY)

    def call(self, name, args):
        return self.s.handle(req("tools/call", {"name": name, "arguments": args, "_meta": META}))

    def test_tool_errors(self):
        self.assertEqual(self.call("nope", {})["error"]["code"], P.INVALID_PARAMS)    # 协议错误
        r = self.call("verify_c_module", {"task_id": "ringbuf"})["result"]
        self.assertTrue(r["isError"])                                                  # 执行错误放结果里
        r = self.call("verify_c_module", {"task_id": "ringbuf", "files": {"../hidden_tests/x.c": "x"}})["result"]
        self.assertTrue(r["isError"])
        self.assertIn("..", r["content"][0]["text"])
        r = self.call("verify_c_module", {"task_id": "ringbuf", "files": {"src/a.c": "x" * (MAX_UPLOAD_BYTES + 1)}})
        self.assertIn("超过上限", r["result"]["content"][0]["text"])
        r = self.call("verify_c_module", {"task_id": "no_such", "files": {"src/a.c": "int a;"}})["result"]
        self.assertTrue(r["isError"])

    def test_list_tasks_and_kb(self):
        r = self.call("list_tasks", {})["result"]
        self.assertEqual(len(r["structuredContent"]["tasks"]), 20)
        r = self.call("kb_search", {"query": "Cortex-M3 栈"})["result"]
        self.assertFalse(r["isError"])

    @unittest.skipUnless(helpers.HAS_CC and helpers.HAS_CROSS, "需要工具链")
    def test_verify_reference(self):
        task_dir = helpers.TASKS / "ringbuf"
        files = {f"src/{p.name}": p.read_text(encoding="utf-8") for p in (task_dir / "reference").rglob("*.[ch]")}
        files["tests/test_smoke.c"] = ('#include "unity.h"\n#include "ringbuf.h"\n'
                                       "static void t(void) { TEST_ASSERT_TRUE(1); }\n"
                                       "int main(void) { UNITY_BEGIN(); RUN_TEST(t); return UNITY_END(); }\n")
        r = self.call("verify_c_module", {"task_id": "ringbuf", "files": files})["result"]
        self.assertEqual(r["structuredContent"]["verdict"], "pass", r["content"][0]["text"])
        self.assertNotIn("hidden", r["content"][0]["text"])


class HelpersTest(unittest.TestCase):
    def test_expand(self):
        env = {"A": "1"}
        self.assertEqual(C.expand("${A}-${B:-2}-${C}", env), "1-2-${C}")

    def test_child_env_drops_secrets(self):
        env = C.child_env({"MY_TOKEN": "${PLAIN}"}, base={"DEEPSEEK_API_KEY": "s", "GITHUB_TOKEN": "t",
                                                          "PLAIN": "p", "PATH": "/bin"})
        self.assertEqual(env, {"PLAIN": "p", "PATH": "/bin", "MY_TOKEN": "p"})


class ClientTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.legacy = Path(self.tmp.name) / "legacy_server.py"
        self.legacy.write_text(LEGACY_SERVER, encoding="utf-8")

    def client(self, cfg, **kw):
        c = C.McpClient(cfg, sleep=lambda s: None, **kw)
        self.addCleanup(c.close)
        return c

    def test_modern_against_own_server(self):
        cfg = C.ServerConfig("ff", sys.executable, ["-m", "firmforge", "mcp"], env={"PYTHONPATH": SRC})
        c = self.client(cfg)
        c.connect()
        self.assertEqual((c.era, c.version), ("modern", P.MODERN))
        self.assertIn("FirmForge", c.instructions)
        text, err = c.call_tool("kb_search", {"query": "CRC"})
        self.assertFalse(err)
        defs = c.tool_defs()
        self.assertEqual(sorted(d.name for d in defs),
                         ["mcp__ff__kb_search", "mcp__ff__list_tasks", "mcp__ff__verify_c_module"])
        self.assertTrue(all(d.toolset == "mcp-ff" and d.require_approval for d in defs))

    def test_legacy_fallback_filter_and_env(self):
        cfg = C.ServerConfig("old", sys.executable, [str(self.legacy)], env={"VISIBLE": "1"}, ask=False,
                             deny=["nothing"])
        with mock.patch.dict(os.environ, {"SOME_API_KEY": "secret"}):
            c = self.client(cfg)
            c.connect()
            self.assertEqual((c.era, c.version), ("legacy", "2025-11-25"))
            text, err = c.call_tool("secret_env", {})
        self.assertEqual(text, "VISIBLE")                                     # 密钥变量没有传给外部服务
        reg = ToolRegistry()
        reg.replace_toolset("mcp-old", c.tool_defs())
        names = sorted(t.name for t in reg.all())
        self.assertEqual(names[1], "mcp__old__secret_env")
        self.assertRegex(names[0], r"^mcp__old__echo_v1_[0-9a-f]{12}$")
        # 外部工具出错时如实告诉模型
        pipe = ToolPipeline(reg, allowed=reg.all(), ctx=ToolContext(agent="a"))
        r = pipe.execute(ToolCall("1", names[0], json.dumps({"text": "坏了", "fail": True})))
        self.assertFalse(r.ok)
        self.assertIn("坏了", r.content)
        r = pipe.execute(ToolCall("2", names[0], json.dumps({"text": "好"})))
        self.assertEqual((r.ok, r.content), (True, "好"))

    def test_allow_list(self):
        cfg = C.ServerConfig("old", sys.executable, [str(self.legacy)], allow=["secret_env"])
        c = self.client(cfg)
        c.connect()
        self.assertEqual([d.name for d in c.tool_defs()], ["mcp__old__secret_env"])

    def test_reconnect_after_crash(self):
        cfg = C.ServerConfig("old", sys.executable, [str(self.legacy)])
        logs = []
        c = self.client(cfg, log=logs.append)
        c.connect()
        with self.assertRaises(Exception):
            c.call_tool("echo.v1", {"die": True})                             # 服务在回答前退出
        text, _ = c.call_tool("echo.v1", {"text": "又活了"})                   # 自动重连
        self.assertEqual(text, "又活了")
        self.assertTrue(any("重连成功" in x for x in logs))

    def test_gives_up_after_attempts(self):
        cfg = C.ServerConfig("gone", sys.executable, ["-c", "import sys; sys.exit(1)"])
        c = self.client(cfg)
        with mock.patch.object(C, "PROBE_TIMEOUT_S", 0.5), mock.patch.object(C, "START_TIMEOUT_S", 0.5):
            with self.assertRaises(Exception):
                c.connect()
            c.era = "legacy"
            with self.assertRaises(ConnectionError):
                c.call("tools/list", {}, 0.5)
        self.assertTrue(c.dead)

    def test_modern_server_with_other_version_does_not_fall_back(self):
        script = Path(self.tmp.name) / "future.py"
        script.write_text(textwrap.dedent('''
            import json, sys
            for line in sys.stdin:
                m = json.loads(line)
                print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -32022, "message": "x",
                      "data": {"supported": ["2099-01-01"], "requested": "2026-07-28"}}}), flush=True)
        '''), encoding="utf-8")
        c = self.client(C.ServerConfig("future", sys.executable, [str(script)]))
        with self.assertRaisesRegex(Exception, "2099-01-01"):
            c.connect()

    def test_manager_skips_broken_and_disabled(self):
        servers = {"old": {"command": sys.executable, "args": [str(self.legacy)], "enabled": True},
                   "off": {"command": sys.executable, "args": [str(self.legacy)], "enabled": False},
                   "bad": {"command": "/no/such/program", "enabled": True}}
        m = C.McpManager(servers, log=lambda s: None)
        self.addCleanup(m.close)
        reg = ToolRegistry()
        m.start(reg)
        self.assertEqual(set(reg.toolsets()), {"mcp-old"})
        self.assertIn("bad", m.errors)


if __name__ == "__main__":
    unittest.main()

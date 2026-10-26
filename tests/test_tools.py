"""工具框架（DECISIONS 第 63 条）：工具箱、执行流水线、调用循环、内置工具、接入单 Agent 与多 Agent。全部用 FakeLLM。"""

import json
import tempfile
import time
import unittest
from pathlib import Path

import helpers
from firmforge.agents.registry import Registry, RegistryError, parse_agent
from firmforge.config import ModelTier, Settings
from firmforge.llm import FakeLLM
from firmforge.orchestrator.baseline import B2, run_baseline
from firmforge.runs import read_events
from firmforge.tasks import discover_tasks
from firmforge.tools import (ToolCall, ToolContext, ToolDef, ToolError, ToolPipeline, ToolRegistry,
                             complete_with_tools, default_registry, mcp_tool_name)
from firmforge.tools.loop import ROUNDS_USED_UP, call_with_tools, followup

TASKS = {t.id: t for t in discover_tasks(helpers.TASKS)}
RINGBUF = TASKS["ringbuf"]


def echo_tool(name="echo", toolset="demo", **kw):
    return ToolDef(name=name, toolset=toolset, description="原样返回",
                   parameters={"type": "object", "properties": {"text": {"type": "string"}},
                               "required": ["text"], "additionalProperties": False},
                   execute=lambda a, c: a["text"], **kw)


def pipeline(reg, tools, **kw):
    events = []
    pipe = ToolPipeline(reg, allowed=tools, ctx=ToolContext(agent="tester-agent"),
                        emit=lambda t, n=None, **d: events.append((t, d)), node_id="m", **kw)
    return pipe, events


class RegistryTest(unittest.TestCase):
    def test_schema_only_has_model_facing_fields(self):
        s = echo_tool(timeout_s=3, require_approval=True).schema()
        self.assertEqual(set(s["function"]), {"name", "description", "parameters"})

    def test_for_agent_by_toolset_or_name(self):
        reg = default_registry()
        self.assertEqual({t.name for t in reg.for_agent(["knowledge", "list_interface"])},
                         {"kb_search", "list_interface"})
        self.assertEqual(set(reg.toolsets()), {"knowledge", "build", "interface"})

    def test_mcp_names(self):
        self.assertEqual(mcp_tool_name("fetch", "fetch"), "mcp__fetch__fetch")
        a, b = mcp_tool_name("s", "get.page"), mcp_tool_name("s", "get page")
        self.assertNotEqual(a, b)                    # 有损转换加哈希，不会撞名
        self.assertRegex(a, r"^mcp__s__get_page_[0-9a-f]{12}$")
        with self.assertRaises(ToolError):
            mcp_tool_name("bad name", "x")

    def test_replace_toolset_is_all_or_nothing(self):
        reg = ToolRegistry()
        reg.register(echo_tool("keep", "a"))
        reg.replace_toolset("mcp-x", [echo_tool("mcp__x__one", "mcp-x")])
        with self.assertRaises(ToolError):
            reg.replace_toolset("mcp-x", [echo_tool("mcp__x__two", "mcp-x"), echo_tool("keep", "mcp-x")])
        self.assertEqual(sorted(t.name for t in reg.all()), ["keep", "mcp__x__one"])   # 失败保留上一批

    def test_agent_fields(self):
        d = parse_agent({"name": "c", "role": "coder", "version": "1", "model_tier": "fast", "prompt": "p.md",
                         "skills": ["写代码"], "toolsets": ["build"], "memory": {"experience": False},
                         "thinking": "max"})
        self.assertEqual(d.toolsets, ("build",))
        self.assertFalse(d.memory_experience)
        self.assertTrue(d.memory_knowledge)
        self.assertEqual(d.thinking, "max")
        for bad in ({"toolsets": "build"}, {"memory": {"x": True}}, {"thinking": "medium"}):
            with self.assertRaises(RegistryError):
                parse_agent({"name": "c", "role": "coder", "version": "1", "model_tier": "fast", "prompt": "p",
                             **bad})
        reg = Registry()
        reg.register(d)
        reg.check_toolsets({"build"})
        with self.assertRaises(RegistryError):
            reg.check_toolsets({"knowledge"})

    def test_default_agents_toolsets(self):
        reg = Registry().load_dir()
        self.assertEqual(reg.get("coder-fast").toolsets, ("knowledge", "build", "interface"))
        self.assertNotIn("build", reg.get("tester").toolsets)          # 测试 Agent 不给 build
        self.assertEqual(reg.get("reviewer").toolsets, ())


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(echo_tool())

    def run_call(self, name="echo", args='{"text": "hi"}', **kw):
        pipe, events = pipeline(self.reg, self.reg.all(), **kw)
        return pipe.execute(ToolCall("c1", name, args)), events

    def test_ok_and_events(self):
        r, events = self.run_call()
        self.assertTrue(r.ok)
        self.assertEqual(r.content, "hi")
        self.assertEqual([e[0] for e in events], ["tool_called", "tool_result"])
        self.assertEqual(events[0][1]["agent"], "tester-agent")

    def test_rejections(self):
        cases = [("nope", '{"text": "x"}', "不能使用"), ("echo", "{bad", "JSON"), ("echo", "{}", "缺少参数"),
                 ("echo", '{"text": 1}', "类型"), ("echo", '{"text": "a", "z": 1}', "不认识"),
                 ("echo", '{"text": "../hidden_tests/test_x.c"}', "隐藏测试")]
        for name, args, why in cases:
            r, _ = self.run_call(name, args)
            self.assertFalse(r.ok, (name, args))
            self.assertIn(why, r.content)
            self.assertTrue(r.content.startswith("工具调用失败"))        # 不伪装成功

    def test_tool_exception_and_timeout(self):
        self.reg.register(ToolDef("boom", "demo", "d", {"type": "object", "properties": {}},
                                  execute=lambda a, c: 1 / 0))
        self.reg.register(ToolDef("slow", "demo", "d", {"type": "object", "properties": {}},
                                  execute=lambda a, c: time.sleep(2) or "late", timeout_s=0.2))
        r, _ = self.run_call("boom", "{}")
        self.assertFalse(r.ok)
        self.assertIn("ZeroDivisionError", r.content)
        r, _ = self.run_call("slow", "{}")
        self.assertFalse(r.ok)
        self.assertIn("超时", r.content)

    def test_truncation_saves_full_output(self):
        with tempfile.TemporaryDirectory() as d:
            r, events = self.run_call(args=json.dumps({"text": "x" * 50}), max_chars=10, output_dir=Path(d))
            self.assertTrue(r.ok and r.truncated)
            self.assertTrue(r.content.startswith("x" * 10))
            self.assertIn("已截断", r.content)
            saved = list(Path(d).glob("*.txt"))
            self.assertEqual(saved[0].read_text(encoding="utf-8"), "x" * 50)
            self.assertTrue(events[1][1]["truncated"])

    def test_approval(self):
        self.reg.register(echo_tool("ask", require_approval=True))
        r, _ = self.run_call("ask")
        self.assertFalse(r.ok)
        self.assertIn("没有人值守", r.content)
        r, _ = self.run_call("ask", approve=lambda agent, tool, args: tool == "ask" and args == {"text": "hi"})
        self.assertTrue(r.ok)
        r, _ = self.run_call("ask", approve=lambda *a: False)
        self.assertIn("拒绝", r.content)


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.reg = ToolRegistry()
        self.reg.register(echo_tool())
        self.pipe, self.events = pipeline(self.reg, self.reg.all())

    def complete(self, llm):
        return lambda msgs, tools: llm.complete(msgs, model="m", tools=tools)

    def test_one_call_then_answer_passes_reasoning_back(self):
        llm = FakeLLM([{"tool_calls": [{"name": "echo", "arguments": {"text": "abc"}}], "reasoning": "先查一下"},
                       "答案"])
        r = complete_with_tools(self.complete(llm), [{"role": "user", "content": "q"}], self.reg.all(), self.pipe)
        self.assertEqual(r.response.text, "答案")
        self.assertEqual(r.rounds, 1)
        second = llm.calls[1]["messages"]
        self.assertEqual(second[1]["reasoning_content"], "先查一下")      # DeepSeek 要求回传思考内容
        self.assertEqual(second[1]["tool_calls"][0]["function"]["name"], "echo")
        self.assertEqual(second[2], {"role": "tool", "tool_call_id": "call_1_0", "content": "abc"})
        self.assertIsNotNone(llm.calls[1]["tools"])

    def test_rounds_limit(self):
        call = {"tool_calls": [{"name": "echo", "arguments": {"text": "x"}}]}
        llm = FakeLLM([call] * 3 + ["最终"])
        r = complete_with_tools(self.complete(llm), [{"role": "user", "content": "q"}], self.reg.all(), self.pipe,
                                max_rounds=3)
        self.assertEqual(r.rounds, 3)
        self.assertIsNone(llm.calls[3]["tools"])                         # 用完后不再给工具
        self.assertEqual(llm.calls[3]["messages"][-1]["content"], ROUNDS_USED_UP)
        self.assertEqual(r.response.text, "最终")

    def test_parallel_safe_tools_run_together(self):
        self.reg.register(ToolDef("nap", "demo", "d", {"type": "object", "properties": {}},
                                  execute=lambda a, c: time.sleep(0.3) or "ok"))
        pipe, _ = pipeline(self.reg, self.reg.all())
        llm = FakeLLM([{"tool_calls": [{"name": "nap", "arguments": {}}] * 4}, "完"])
        t0 = time.perf_counter()
        r = complete_with_tools(self.complete(llm), [{"role": "user", "content": "q"}], self.reg.all(), pipe)
        self.assertLess(time.perf_counter() - t0, 1.0)
        self.assertEqual(len(r.results), 4)

    def test_call_with_tools_sums_tokens_and_keeps_conversation(self):
        llm = FakeLLM([{"tool_calls": [{"name": "echo", "arguments": {"text": "x" * 40}}]}, "y" * 40])
        msgs = [{"role": "user", "content": "q" * 40}]
        resp = call_with_tools(self.complete(llm), msgs, self.reg.all(), self.pipe)
        self.assertEqual(resp.completion_tokens, 10)
        self.assertGreater(resp.prompt_tokens, 10)
        nxt = followup(msgs, resp)
        self.assertEqual([m["role"] for m in nxt], ["user", "assistant", "tool", "assistant"])
        plain = FakeLLM(["z"]).complete(msgs, model="m")
        self.assertEqual(followup(msgs, plain)[-1], {"role": "assistant", "content": "z"})


class BuiltinToolsTest(unittest.TestCase):
    def ctx(self, d):
        return ToolContext(task=RINGBUF, toolchain=helpers.TC, scratch_dir=Path(d), agent="a")

    def call(self, name, args, d):
        reg = default_registry()
        pipe, _ = pipeline(reg, reg.all())
        pipe.ctx = self.ctx(d)
        return pipe.execute(ToolCall("c", name, json.dumps(args)))

    def test_kb_search_and_list_interface(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.call("kb_search", {"query": "CRC 参数 多项式"}, d)
            self.assertTrue(r.ok)
            self.assertIn("### ", r.content)
            r = self.call("list_interface", {}, d)
            self.assertTrue(r.ok)
            self.assertIn("ringbuf.h", r.content)
            self.assertNotIn("hidden", r.content)

    @unittest.skipUnless(helpers.HAS_CC, "需要主机 gcc")
    def test_compile_check(self):
        good = (next((helpers.TASKS / "ringbuf" / "reference").rglob("ringbuf.c"))).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as d:
            r = self.call("compile_check", {"files": {"src/ringbuf.c": good}}, d)
            self.assertTrue(r.ok, r.content)
            self.assertIn("主机编译（host_compile）：通过", r.content)
            r = self.call("compile_check", {"files": {"src/ringbuf.c": good + "\nint broken(void) { return x; }\n",
                                                      "tests/test_a.c": "x"}}, d)
            self.assertIn("失败", r.content)
            self.assertIn("tests/test_a.c", r.content)                     # 只编译 src/，其他的说明原因
            self.assertEqual(list(Path(d).iterdir()), [])                   # 临时目录用完就删


class IntegrationTest(unittest.TestCase):
    def test_baseline_uses_tools_and_records_events(self):
        from test_baseline_eval import good_reply
        reply = good_reply(RINGBUF)
        llm = FakeLLM([{"tool_calls": [{"name": "kb_search", "arguments": {"query": "环形缓冲区"}}]}, reply])
        with tempfile.TemporaryDirectory() as d:
            res = run_baseline(RINGBUF, B2, llm=llm, model=ModelTier("fake", 1, 1), runs_root=d,
                               toolchain=helpers.TC, tool_settings={"max_rounds": 2})
            types = [e["type"] for e in read_events(res.run_dir)]
            self.assertIn("tool_called", types)
            self.assertIn("tool_result", types)
            self.assertEqual({t["function"]["name"] for t in llm.calls[0]["tools"]},
                             {"kb_search", "compile_check", "list_interface"})
            self.assertEqual(res.attempts[0].n, 1)

    def test_no_tools_config(self):
        from firmforge.evaluation.configs import get_config
        self.assertFalse(get_config("full-no-tools").tools)
        self.assertTrue(get_config("full").tools)

    def test_thinking_effort_in_extra_body(self):
        t = ModelTier("m", thinking=True)
        self.assertEqual(t.extra_body(), {"thinking": {"type": "enabled"}})
        self.assertEqual(t.extra_body("max"), {"thinking": {"type": "enabled"}, "reasoning_effort": "max"})
        self.assertEqual(t.extra_body("off"), {"thinking": {"type": "disabled"}})
        self.assertEqual(ModelTier("m", thinking=True, reasoning_effort="low").extra_body(),
                         {"thinking": {"type": "enabled"}, "reasoning_effort": "low"})


if __name__ == "__main__":
    unittest.main()

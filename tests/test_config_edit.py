"""设置页写回配置文件：保留注释、插入新项、密钥只写不读、预算上限。"""

import tempfile
import tomllib
import unittest
from pathlib import Path

import helpers  # noqa: F401  先把 src/ 放进 sys.path
from firmforge.config_edit import has_secret, remove_table, set_secret, set_value, update_file

SAMPLE = """# 顶部注释
[llm]
timeout_s = 600                   # 思考模式回复较慢

[models.fast]                     # 型号
name = "deepseek-flash"
thinking = true                   # 思考模式

[[x.list]]
a = 1
"""


class ConfigEditTest(unittest.TestCase):
    def test_replace_keeps_comment(self):
        out = set_value(SAMPLE, "models.fast", "thinking", False)
        self.assertIn("thinking = false", out)
        self.assertIn("# 思考模式", out)
        self.assertIn("# 顶部注释", out)
        self.assertFalse(tomllib.loads(out)["models"]["fast"]["thinking"])

    def test_insert_and_new_table(self):
        out = set_value(SAMPLE, "models.fast", "reasoning_effort", "max")
        data = tomllib.loads(out)
        self.assertEqual(data["models"]["fast"]["reasoning_effort"], "max")
        self.assertEqual(data["models"]["fast"]["name"], "deepseek-flash")
        out = set_value(out, "budget", "max_cost", 2.5)
        self.assertEqual(tomllib.loads(out)["budget"]["max_cost"], 2.5)
        out = set_value(out, "mcp.servers.fetch", "args", ["mcp-server-fetch"])
        self.assertEqual(tomllib.loads(out)["mcp"]["servers"]["fetch"]["args"], ["mcp-server-fetch"])
        self.assertEqual(tomllib.loads(remove_table(out, "mcp.servers.fetch")).get("mcp", {}).get("servers", {}), {})

    def test_hash_inside_string_is_not_comment(self):
        out = set_value('[a]\nk = "x # y"  # 注释\n', "a", "k", "新#值")
        self.assertEqual(tomllib.loads(out)["a"]["k"], "新#值")
        self.assertIn("# 注释", out)

    def test_real_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.toml"
            p.write_text(SAMPLE, encoding="utf-8")
            update_file(p, [("llm", "timeout_s", 300), ("concurrency", "llm", 8)])
            data = tomllib.loads(p.read_text(encoding="utf-8"))
            self.assertEqual((data["llm"]["timeout_s"], data["concurrency"]["llm"]), (300, 8))

    def test_secret_write_only(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("OTHER=1\nDEEPSEEK_API_KEY=old\n", encoding="utf-8")
            self.assertTrue(has_secret(env, "DEEPSEEK_API_KEY", environ={}))
            set_secret(env, "DEEPSEEK_API_KEY", "")                     # 留空不修改
            self.assertIn("=old", env.read_text(encoding="utf-8"))
            set_secret(env, "DEEPSEEK_API_KEY", "sk-new")
            self.assertEqual(env.read_text(encoding="utf-8"), "OTHER=1\nDEEPSEEK_API_KEY=sk-new\n")
            self.assertFalse(has_secret(env, "NOPE", environ={}))
            with self.assertRaises(ValueError):
                set_secret(env, "K", "a\nb")


class BudgetTest(unittest.TestCase):
    def test_over_budget_stops_run(self):
        import dataclasses
        from test_multi_agent import B3, SETTINGS, _Tmp, rb_team
        from firmforge.llm import FakeLLM
        from firmforge.orchestrator.engine import run_multi
        settings = dataclasses.replace(SETTINGS, budget={"max_tokens": 50})
        with tempfile.TemporaryDirectory() as d:
            res = run_multi(__import__("test_multi_agent").RB, B3, llm=FakeLLM(rb_team()), settings=settings,
                            runs_root=d)
        self.assertEqual(res.verdict, "incomplete")
        self.assertIn("超过设置的上限", res.error)


if __name__ == "__main__":
    unittest.main()

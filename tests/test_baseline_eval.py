"""单 Agent 基线、评测、隐藏测试隔离、命令行 run/eval/replay。全部用 FakeLLM，不联网。"""

import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from firmforge import cli
from firmforge.agents.context import coder_messages, repair_message
from firmforge.config import ModelTier, Settings
from firmforge.evaluation.configs import ConfigError, get_config, parse_configs
from firmforge.evaluation.metrics import ROW_FIELDS, read_csv
from firmforge.evaluation.runner import evaluate, judge
from firmforge.llm import FakeLLM
from firmforge.orchestrator.baseline import B1, B2, RunInterrupted, run_baseline
from firmforge.runs import read_events
from firmforge.store import Store
from firmforge.tasks import adhoc_task, discover_tasks, load_task

MODEL = ModelTier("fake-model", 2.0, 8.0)
SETTINGS = Settings(models={"strong": MODEL, "fast": ModelTier("fake-fast", 1.0, 2.0)})
TASKS = {t.id: t for t in discover_tasks(helpers.TASKS)}


def block(path, content):
    if not content.endswith("\n"):
        content += "\n"
    return f"<<<FILE {path}\n{content}>>>FILE\n"


def smoke_test(task):
    header = next(task.include_dir.glob("*.h")).name
    return (f'#include "unity.h"\n#include "{header}"\n'
            "static void test_smoke(void) { TEST_ASSERT_TRUE(1); }\n"
            "int main(void) { UNITY_BEGIN(); RUN_TEST(test_smoke); return UNITY_END(); }\n")


def good_reply(task):
    parts = ["这是实现和测试：\n"]
    for f in sorted(task.reference_dir.glob("*.c")):
        parts.append(block(f"src/{f.name}", f.read_text(encoding="utf-8")))
    parts.append(block(f"tests/test_{task.id}.c", smoke_test(task)))
    return "".join(parts)


def broken_reply(task):
    f = sorted(task.reference_dir.glob("*.c"))[0]
    return block(f"src/{f.name}", "int broken(void) { return 0 }\n") + block(f"tests/test_{task.id}.c", smoke_test(task))


def hidden_markers(task):
    """隐藏测试里独有的字符串：测试函数名和文件名。"""
    marks = set()
    for f in task.hidden_tests_dir.glob("*.c"):
        marks.add(f.stem)
        marks.update(re.findall(r"static void (test_\w+)\(void\)", f.read_text(encoding="utf-8")))
    return marks


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()


class ContextTest(unittest.TestCase):
    def test_coder_context_has_spec_and_headers_only(self):
        for task in TASKS.values():
            with self.subTest(task=task.id):
                msgs = coder_messages(task)
                text = "\n".join(m["content"] for m in msgs)
                self.assertIn(task.spec_path.read_text(encoding="utf-8").strip()[:40], text)
                for h in task.include_dir.glob("*.h"):
                    self.assertIn(h.read_text(encoding="utf-8").strip(), text)
                for mark in hidden_markers(task):
                    self.assertNotIn(mark, text)
                # 参考实现的代码也不能出现在上下文里
                for ref in task.reference_dir.glob("*.c"):
                    # 只看函数体内的行（缩进的），函数签名本来就会出现在公共头文件里
                    body = [ln for ln in ref.read_text(encoding="utf-8").splitlines()
                            if ln.startswith("    ") and len(ln.strip()) > 20]
                    self.assertTrue(body)
                    for ln in body[:5]:
                        self.assertNotIn(ln.strip(), text)
                self.assertIn("malloc", text)        # 禁用符号
                self.assertIn(str(task.budget.flash_bytes), text)

    def test_unfilled_placeholders(self):
        text = "\n".join(m["content"] for m in coder_messages(TASKS["ringbuf"]))
        self.assertIsNone(re.search(r"\$[a-z_]+", text))


class ConfigsTest(unittest.TestCase):
    def test_configs(self):
        self.assertEqual(get_config("B1").max_repairs, 0)
        self.assertEqual(get_config("b2").max_repairs, 3)
        self.assertEqual([c.name for c in parse_configs("b1, b2")], ["b1", "b2"])
        with self.assertRaises(ConfigError) as cm:
            get_config("full")
        self.assertIn("阶段 3", str(cm.exception))


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class BaselineTest(_Tmp):
    def run_b(self, cfg, replies, task_id="ringbuf", store=None):
        llm = FakeLLM(replies)
        events = []
        res = run_baseline(TASKS[task_id], cfg, llm=llm, model=MODEL, runs_root=self.tmp / "runs",
                           store=store, subscribers=(events.append,))
        return res, llm, events

    def test_b1_good_first_try(self):
        t = TASKS["ringbuf"]
        store = Store(self.tmp / "db.sqlite")
        res, llm, events = self.run_b(B1, [good_reply(t)], store=store)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(res.attempts), 1)
        self.assertIsNone(res.attempts[0].report.failed_layer)
        self.assertEqual(res.best, 1)
        self.assertTrue((res.final_dir / "src" / "ringbuf.c").is_file())
        self.assertTrue((res.final_dir / "tests" / "test_ringbuf.c").is_file())
        self.assertTrue((res.final_dir / "report.md").is_file())
        self.assertGreater(res.tokens, 0)
        self.assertAlmostEqual(res.cost, MODEL.cost(res.prompt_tokens, res.completion_tokens))
        self.assertIsNone(llm.calls[0]["extra_body"] or None)      # MODEL 没配置 thinking，不传
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "run_started")
        self.assertEqual(types[-1], "run_finished")
        self.assertIn("node_done", types)
        self.assertEqual(read_events(res.run_dir), events)
        run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["model"], "fake-model")
        self.assertEqual(run["total_tokens"], res.tokens)
        self.assertEqual(len(store.query("SELECT * FROM node_attempts")), 1)
        store.close()

    def test_thinking_and_max_tokens_from_tier(self):
        t = TASKS["ringbuf"]
        tier = ModelTier("thinker", 2.0, 8.0, thinking=True, max_tokens=65536)
        llm = FakeLLM([good_reply(t)])
        res = run_baseline(t, B1, llm=llm, model=tier, runs_root=self.tmp / "runs")
        self.assertEqual(llm.calls[0]["extra_body"], {"thinking": {"type": "enabled"}})
        self.assertEqual(llm.calls[0]["max_tokens"], 65536)
        run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(run["thinking"])

    def test_b1_does_not_repair(self):
        t = TASKS["ringbuf"]
        res, llm, _ = self.run_b(B1, [broken_reply(t), good_reply(t)])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(res.verdict, "fail")
        self.assertEqual(res.attempts[0].report.failed_layer, "host_compile")

    def test_b2_repairs_with_error_feedback(self):
        t = TASKS["ringbuf"]
        res, llm, events = self.run_b(B2, [broken_reply(t), good_reply(t)])
        self.assertEqual(len(res.attempts), 2)
        self.assertEqual(res.best, 2)
        second_prompt = llm.calls[1]["messages"][-1]["content"]
        self.assertIn("host_compile", second_prompt)
        self.assertIn("ringbuf.c", second_prompt)
        self.assertNotIn(str(self.tmp), second_prompt)     # 绝对路径已经被缩短
        self.assertEqual(llm.calls[1]["messages"][-2]["role"], "assistant")
        self.assertIn("node_done", [e["type"] for e in events])

    def test_b2_stops_after_three_repairs(self):
        t = TASKS["ringbuf"]
        res, llm, events = self.run_b(B2, [broken_reply(t)] * 4)
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(res.verdict, "fail")
        self.assertIn("node_failed", [e["type"] for e in events])
        self.assertIn("第 3 轮修正，最多 3 轮", llm.calls[3]["messages"][-1]["content"])

    def test_unchanged_files_carry_over(self):
        t = TASKS["ringbuf"]
        fix_only_src = block("src/ringbuf.c", (t.reference_dir / "ringbuf.c").read_text(encoding="utf-8"))
        res, _, _ = self.run_b(B2, [broken_reply(t), fix_only_src])
        self.assertTrue((res.attempts[1].dir / "tests" / "test_ringbuf.c").is_file())
        self.assertIsNone(res.attempts[1].report.failed_layer)

    def test_rejected_paths_reported(self):
        t = TASKS["ringbuf"]
        reply = good_reply(t) + block("include/ringbuf.h", "/* 想改接口 */")
        res, _, events = self.run_b(B1, [reply])
        fin = next(e for e in events if e["type"] == "agent_finished")
        self.assertEqual(fin["data"]["rejected"], ["include/ringbuf.h"])
        self.assertFalse((res.attempts[0].dir / "include").exists())

    def test_format_reminder_once(self):
        t = TASKS["ringbuf"]
        res, llm, _ = self.run_b(B1, ["我写好了，但忘了用格式", good_reply(t)])
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("没有在你的回复里找到", llm.calls[1]["messages"][-1]["content"])
        self.assertIsNone(res.attempts[0].report.failed_layer)

    def test_ctrl_c_keeps_records(self):
        t = TASKS["ringbuf"]

        def second_call_interrupted(messages):
            raise KeyboardInterrupt
        llm = FakeLLM([broken_reply(t), second_call_interrupted])
        events = []
        with self.assertRaises(RunInterrupted) as cm:
            run_baseline(t, B2, llm=llm, model=MODEL, runs_root=self.tmp / "runs", subscribers=(events.append,))
        run_dir = cm.exception.run_dir
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["verdict"], "interrupted")
        self.assertEqual(run["attempts"], 1)
        self.assertTrue((run_dir / "final" / "src" / "ringbuf.c").is_file())   # 第 1 次的代码照样交付
        self.assertEqual(events[-1]["type"], "run_finished")
        self.assertEqual(events[-1]["data"]["verdict"], "interrupted")
        self.assertIsInstance(cm.exception, KeyboardInterrupt)

    def test_llm_error_ends_run(self):
        res, _, events = self.run_b(B2, [])
        self.assertEqual(res.verdict, "error")
        self.assertIsNotNone(res.error)
        self.assertEqual(events[-1]["type"], "run_finished")

    def test_adhoc_task(self):
        t = adhoc_task(helpers.TASKS / "ringbuf" / "spec.md", helpers.TASKS / "ringbuf" / "include")
        self.assertEqual(t.id, "spec")
        self.assertIn("ringbuf", t.title)
        llm = FakeLLM([good_reply(TASKS["ringbuf"])])
        res = run_baseline(t, B1, llm=llm, model=MODEL, runs_root=self.tmp / "runs")
        self.assertIsNone(res.attempts[0].report.failed_layer)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class HiddenIsolationStage2Test(_Tmp):
    def test_nothing_hidden_reaches_llm_or_run_dir(self):
        """发给 LLM 的所有消息、运行目录里的所有文件，都不含隐藏测试的内容。"""
        for task in TASKS.values():
            with self.subTest(task=task.id):
                llm = FakeLLM([broken_reply(task), good_reply(task)])
                res = run_baseline(task, B2, llm=llm, model=MODEL, runs_root=self.tmp / "runs")
                self.assertEqual(len(llm.calls), 2)      # 确实走到了修正轮
                sent = llm.all_text_sent()
                run_files = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                                      for p in res.run_dir.rglob("*") if p.is_file())
                for mark in hidden_markers(task):
                    self.assertNotIn(mark, sent, f"{mark} 泄露进了 LLM 消息")
                    self.assertNotIn(mark, run_files, f"{mark} 出现在运行目录里")
                self.assertNotIn("hidden_tests", sent)

    def test_repair_message_never_includes_hidden_layer(self):
        t = TASKS["ringbuf"]
        wd = helpers.copy_fixture("test_failure", self.tmp)
        from firmforge.verifier import verify
        report = verify(wd, t, include_hidden=True, stop_on_fail=False)
        self.assertEqual(report.layer("hidden_tests").status, "fail")
        msg = repair_message(report, 1, 3, workdir=wd)
        for mark in hidden_markers(t):
            self.assertNotIn(mark, msg)
        self.assertNotIn("隐藏测试", msg)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class EvaluateTest(_Tmp):
    def test_eval_writes_csv_and_summary(self):
        t = TASKS["ringbuf"]
        # b1 一次就交对；b2 第一次错、第二次对
        llm = FakeLLM([good_reply(t), broken_reply(t), good_reply(t)])
        out = evaluate([t], [B1, B2], 1, self.tmp / "out", llm=llm, settings=SETTINGS)
        rows = read_csv(out.csv_path)
        self.assertEqual(list(rows[0].keys()), ROW_FIELDS)
        b1, b2 = rows
        self.assertEqual((b1["config"], b2["config"]), ("b1", "b2"))
        self.assertEqual(b2["attempts"], "2")
        self.assertEqual(b2["first_pass"], "False")
        # 本机缺交叉工具链时最终判定是 incomplete，不能算通过
        expected = "True" if helpers.HAS_CROSS and helpers.HAS_CPPCHECK else "False"
        self.assertEqual(b1["final_pass"], expected)
        self.assertEqual(b2["final_pass"], expected)
        summary = out.summary_path.read_text(encoding="utf-8")
        self.assertIn("| b1 |", summary)
        self.assertIn("最终通过率", summary)
        judges = list((self.tmp / "out" / "judges").glob("*.json"))
        self.assertEqual(len(judges), 2)
        self.assertTrue((self.tmp / "out" / "firmforge.db").is_file())

    def test_interrupted_eval_still_writes_summary(self):
        t = TASKS["ringbuf"]

        def interrupt(messages):
            raise KeyboardInterrupt
        llm = FakeLLM([good_reply(t), interrupt])
        with self.assertRaises(KeyboardInterrupt):
            evaluate([t], [B1, B2], 1, self.tmp / "out", llm=llm, settings=SETTINGS)
        summary = (self.tmp / "out" / "summary.md").read_text(encoding="utf-8")
        self.assertIn("中断，只完成 1/2 次", summary)
        self.assertEqual(len(read_csv(self.tmp / "out" / "results.csv")), 1)

    def test_judge_uses_hidden_tests_and_ignores_agent_tests(self):
        t = TASKS["ringbuf"]
        wd = helpers.copy_fixture("test_failure", self.tmp)     # 实现有 bug，Agent 的测试也会失败
        r = judge(wd, t)
        self.assertEqual(r.failed_layer, "hidden_tests")
        self.assertIsNone(r.layer("unit_tests"))


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class CliStage2Test(_Tmp):
    def _cli(self, *argv, llm=None):
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(cli.config, "load_settings", return_value=SETTINGS)]
        if llm is not None:
            patches.append(mock.patch.object(cli, "make_llm", return_value=llm))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_run_then_replay(self):
        t = TASKS["ringbuf"]
        llm = FakeLLM([broken_reply(t), good_reply(t)])
        code, out, _ = self._cli("run", "--task", str(t.root), "--config", "b2",
                                 "--runs-dir", str(self.tmp / "runs"), "--db", str(self.tmp / "db"), llm=llm)
        self.assertIn(code, (0, 2))
        self.assertIn("运行开始", out)
        self.assertIn("首个失败层 host_compile", out)
        run_dir = next((self.tmp / "runs").iterdir())
        code, out2, _ = self._cli("replay", str(run_dir), "--speed", "0")
        self.assertEqual(code, 0)
        lines = [ln for ln in out2.splitlines() if ln.startswith("[")]
        self.assertTrue(lines[0].endswith("运行开始：任务 ringbuf，配置 b2，模型 fake-model"))
        self.assertIn("运行结束", lines[-1])

    def test_eval_cli_refuses_to_overwrite(self):
        t = TASKS["ringbuf"]
        llm = FakeLLM(lambda m: good_reply(t))
        args = ("eval", "--configs", "b1", "--tasks", "ringbuf", "--runs", "1", "--out", str(self.tmp / "o"))
        with mock.patch.object(cli, "_tasks_dir", return_value=helpers.TASKS):
            code, out, _ = self._cli(*args, llm=llm)
            self.assertEqual(code, 0)
            self.assertIn("results.csv", out)
            code, _, err = self._cli(*args, llm=llm)
            self.assertEqual(code, 3)
            self.assertIn("已经有 results.csv", err)

    def test_estimate(self):
        t = TASKS["ringbuf"]
        llm = FakeLLM(lambda m: good_reply(t))
        with mock.patch.object(cli, "_tasks_dir", return_value=helpers.TASKS):
            code, out, _ = self._cli("eval", "--configs", "b1,b2", "--tasks", "all", "--runs", "3",
                                     "--out", str(self.tmp / "e"), "--estimate", llm=llm)
        self.assertEqual(code, 0)
        self.assertIn("全量预计", out)
        self.assertIn("× 9", out)     # 3 个任务 × 3 次

    def test_ctrl_c_exit_code(self):
        t = TASKS["ringbuf"]

        def interrupt(messages):
            raise KeyboardInterrupt
        code, _, err = self._cli("run", "--task", str(t.root), "--runs-dir", str(self.tmp / "runs"),
                                 "--db", str(self.tmp / "db"), llm=FakeLLM([interrupt]))
        self.assertEqual(code, 130)
        self.assertIn("已按 Ctrl+C 中断", err)
        self.assertIn(str(self.tmp / "runs"), err)

    def test_heartbeat_while_waiting(self):
        buf = io.StringIO()
        printer = cli.LivePrinter(heartbeat_s=0.2, out=buf)
        try:
            printer({"ts": 100.0, "type": "agent_started", "node_id": "m",
                     "data": {"agent": "coder-strong", "attempt": 1}})
            import time as _t
            _t.sleep(0.7)
            printer({"ts": 101.0, "type": "verify_started", "node_id": "m", "data": {"attempt": 1}})
            text_before = buf.getvalue()
            _t.sleep(0.5)                    # 不在等待模型时不应再提示
        finally:
            printer.close()
        self.assertIn("模型还在思考", text_before)
        self.assertEqual(buf.getvalue(), text_before)

    def test_usage_errors(self):
        self.assertEqual(self._cli("run")[0], 3)
        self.assertEqual(self._cli("run", "--task", str(helpers.TASKS / "ringbuf"), "--config", "full")[0], 3)
        self.assertEqual(self._cli("eval", "--configs", "b9", "--out", str(self.tmp))[0], 3)
        self.assertEqual(self._cli("replay", str(self.tmp))[0], 3)

    def test_unfilled_model_is_usage_error(self):
        with mock.patch.object(cli.config, "load_settings", return_value=Settings(models={"strong": ModelTier("")})):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["run", "--task", str(helpers.TASKS / "ringbuf")])
        self.assertEqual(code, 3)
        self.assertIn("firmforge models", err.getvalue())


if __name__ == "__main__":
    unittest.main()

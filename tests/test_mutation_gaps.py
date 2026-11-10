"""变异测试（tests/mutation.py）找到的测试缺口：这些地方故意改错，原来的测试发现不了。

分三块：评测汇总的数字（每道题的表、跳过次数、卡在哪一层）、串口和烧录的各种失败、合规检查去注释和字符串的边界。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.embedded.board_eval import evaluate, load_rows, summarize
from firmforge.grounding.compliance import check_code, strip_c
from firmforge.grounding.rules import Citation, Rule, RuleSet
from firmforge.target import board as board_mod
from firmforge.target import harness
from firmforge.target.board import BoardRunner, find_port
from test_board_eval import TASK, FakeTeam, make_ctx
from test_rule_verifier import good_rules


def row(task, cfg, run, verdict, layer="", cp=0, ct=4, cost=0.1):
    return {"task": task, "config": cfg, "run": run, "verdict": verdict, "failed_layer": layer, "cases_passed": cp,
            "cases_total": ct, "failed_cases": [], "flow_verdict": "", "attempts": 2, "tokens": 100, "cost": cost,
            "seconds": 10, "rules_used": 0, "run_dir": "", "note": ""}


class SummaryNumbersTest(unittest.TestCase):
    ROWS = [row("a", "single", 1, "pass", cp=4), row("a", "single", 2, "fail", "build"),
            row("a", "none", 1, "skipped", "skipped"), row("a", "none", 2, "fail", "board_test", cp=3),
            row("b", "single", 1, "fail", "board_test", cp=1), row("b", "none", 1, "pass", cp=4),
            row("b", "none", 2, "fail", ""), row("b", "single", 2, "fail", "board_test")]

    def test_config_rows(self):
        text = summarize(self.ROWS, ["single", "none"])
        # single：3 次有效、1 次通过，隐藏用例 5/12；none：3 次有效（1 次跳过不算）、1 次通过，用例 7/12
        self.assertIn("| single | 1 / 4 | 25% | 31% | 2.0 | 100 | 0.1000 | 10 | 0 |", text)
        self.assertIn("| none | 1 / 3 | 33% | 58% | 2.0 | 100 | 0.1000 | 10 | 1 |", text)

    def test_per_task_table(self):
        text = summarize(self.ROWS, ["single", "none"])
        self.assertIn("| a | 1 / 2 | 0 / 1 |", text)
        self.assertIn("| b | 0 / 2 | 1 / 2 |", text)
        self.assertIn("| a | 1 / 2 | 0 / 1 |\n| b |", text)          # 题目按名字排

    def test_failed_layers(self):
        text = summarize(self.ROWS, ["single", "none"])
        part = text.split("## 没通过的卡在哪一层")[1]
        self.assertIn("| single | board_test | 2 |", part)                 # 同一层的次数累加
        self.assertIn("| single | build | 1 |", part)
        self.assertIn("| none | ? | 1 |", part)                          # 没写层的记成 ?
        self.assertIn("| none | board_test | 1 |", part)
        self.assertLess(part.index("| single | board_test"), part.index("| single | build"))
        self.assertLess(part.index("| single |"), part.index("| none |"))  # 按配置顺序
        self.assertNotIn("skipped", part)

    def test_default_config_order_and_unknown(self):
        rows = [row("a", "verified", 1, "pass"), row("a", "single", 1, "pass"), row("a", "zzz", 1, "pass")]
        text = summarize(rows)
        self.assertLess(text.index("| single |"), text.index("| verified |"))
        self.assertLess(text.index("| verified |"), text.index("| zzz |"))   # 不认识的配置排最后

    def test_all_skipped_and_zero_cases(self):
        text = summarize([row("a", "single", 1, "skipped"), row("a", "none", 1, "fail", ct=0)], ["single", "none"])
        self.assertIn("| single | 0 / 0 | - | - | - | - | - | - | 1 |", text)
        self.assertIn("| none | 0 / 1 | 0% | 0% |", text)


class EvalDetailsTest(unittest.TestCase):
    def test_tools_only_for_rule_configs_and_row_numbers(self):
        # none 配置不给查资料工具；unverified 的查资料 Agent 有工具；每行 token、用时如实
        for cfg, want_tools in (("none", False), ("unverified", True)):
            with self.subTest(cfg=cfg), tempfile.TemporaryDirectory() as d:
                ctx = make_ctx(d, FakeTeam(), RuleSet(), [])
                rows = evaluate([TASK], [cfg], 1, Path(d) / "out", ctx, progress=lambda s: None)
                self.assertEqual(any(c["tools"] for c in ctx.llm.calls), want_tools)
                self.assertTrue(0 <= rows[0]["seconds"] < 120)
                info = json.loads((Path(rows[0]["run_dir"]) / "run.json").read_text(encoding="utf-8"))
                self.assertEqual(rows[0]["tokens"], info["tokens"])
                self.assertGreater(rows[0]["tokens"], 0)

    def test_researcher_gets_tools(self):
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(d, FakeTeam(), RuleSet(), [])
            evaluate([TASK], ["unverified"], 1, Path(d) / "out", ctx, progress=lambda s: None)
            research = [c for c in ctx.llm.calls if "资料核查员" in c["messages"][0]["content"]]
            self.assertTrue(research and research[0]["tools"])

    def test_cost_cap_counts_rule_generation(self):
        # 花费上限要把生成未核对规则的钱也算上：上限在"一次运行"和"一次运行 + 生成规则"之间时，第一次跑完就停
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "probe"
            rows = evaluate([TASK], ["unverified"], 1, out, make_ctx(d, FakeTeam(), RuleSet(), []), progress=lambda s: None)
            run_cost = rows[0]["cost"]
            rules_cost = json.loads((out / "unverified_rules" / f"{TASK.id}.json").read_text(encoding="utf-8"))["cost"]
            self.assertGreater(rules_cost, 0)
        with tempfile.TemporaryDirectory() as d:
            said = []
            rows = evaluate([TASK], ["unverified"], 3, Path(d) / "out", make_ctx(d, FakeTeam(), RuleSet(), []),
                            progress=said.append, max_cost=run_cost + rules_cost / 2)
            self.assertEqual(len(rows), 1)
            self.assertTrue(any("达到上限" in m and "还剩 2 次" in m for m in said), said)

    def test_pause_message_counts_remaining(self):
        with tempfile.TemporaryDirectory() as d:
            said, n = [], []
            evaluate([TASK], ["single"], 3, Path(d) / "out", make_ctx(d, FakeTeam(), RuleSet(), []),
                     progress=said.append, stop=lambda: n.append(1) or len(n) > 1)
            self.assertTrue(any("已暂停" in m and "还剩 2 次" in m for m in said), said)
            self.assertEqual(len(load_rows(Path(d) / "out")), 1)

    def test_duplicate_rule_ids_from_researcher_get_new_ids(self):
        class Dup(FakeTeam):
            def __call__(self, messages):
                if "资料核查员" in messages[0]["content"]:
                    r = list(good_rules())[0].to_dict()
                    return "```json\n" + json.dumps([r, {**r, "text": r["text"] + "（第二条）"}], ensure_ascii=False) + "\n```"
                return super().__call__(messages)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            evaluate([TASK], ["unverified"], 1, out, make_ctx(d, Dup(), RuleSet(), []), progress=lambda s: None)
            rules = json.loads((out / "unverified_rules" / f"{TASK.id}.json").read_text(encoding="utf-8"))["rules"]
            self.assertEqual(len({r["id"] for r in rules}), 2)

    def test_skipped_judgement_gets_note(self):
        from firmforge.embedded.board_task import JudgeResult
        with tempfile.TemporaryDirectory() as d:
            ctx = make_ctx(d, FakeTeam(), RuleSet(), [])
            ctx.judge_fn = lambda *a: JudgeResult("skipped", "skipped", 0, 4, [], "没有板子，只编译了" + "x" * 300)
            r = evaluate([TASK], ["single"], 1, Path(d) / "out", ctx, progress=lambda s: None)[0]
            self.assertEqual(r["verdict"], "skipped")
            self.assertTrue(r["note"].startswith("没有板子"))
            self.assertEqual(len(r["note"]), 200)

    def test_rows_missing_fields_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "runs.jsonl"
            p.write_text('{"task": "a", "config": "none", "run": 1, "verdict": "pass"}\n[1, 2]\n'
                         '{"task": "a", "config": "none"}\n\n{"task": "b", "config": "none", "run": 1, "verdict": "fail"}\n',
                         encoding="utf-8")
            bad = []
            self.assertEqual([r["task"] for r in load_rows(Path(d), bad)], ["a", "b"])
            self.assertEqual(len(bad), 2)

    def test_cache_without_task_is_flagged_in_summary(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            (out / "unverified_rules").mkdir(parents=True)
            (out / "unverified_rules" / "x.json").write_text('{"rules": []}', encoding="utf-8")
            evaluate([TASK], ["single"], 1, out, make_ctx(d, FakeTeam(), RuleSet(), []), progress=lambda s: None)
            self.assertIn("读不了", (out / "summary.md").read_text(encoding="utf-8"))


class _Idf:
    python = sys.executable
    env = None

    def __init__(self, flash):
        self.flash = flash

    def idf(self, args, cwd, timeout):
        if isinstance(self.flash, Exception):
            raise self.flash
        return self.flash


class _Proj:
    root = Path(".")


class BoardFailuresTest(unittest.TestCase):
    def test_find_port_order(self):
        saved = board_mod.glob.glob
        board_mod.glob.glob = lambda pat: {"/dev/ttyACM*": ["/dev/ttyACM1", "/dev/ttyACM0"]}.get(pat, [])
        try:
            self.assertEqual(find_port(env={}), "/dev/ttyACM0")
            self.assertEqual(find_port(env={"FF_SERIAL_PORT": "/dev/x"}), "/dev/x")
            self.assertEqual(find_port("/dev/y", env={"FF_SERIAL_PORT": "/dev/x"}), "/dev/y")
        finally:
            board_mod.glob.glob = saved

    def test_flash_timeout_and_failure_are_skipped(self):
        r = BoardRunner(_Idf(subprocess.TimeoutExpired("idf.py", 180)), port=__file__).run(_Proj())
        self.assertEqual(r.verdict(), "skipped")
        self.assertIn("烧录超过", r.report())
        r = BoardRunner(_Idf(subprocess.CompletedProcess([], 2, "...", "A fatal error occurred: Failed to connect")),
                        port=__file__).run(_Proj())
        self.assertEqual(r.verdict(), "skipped")
        self.assertIn("下载模式", r.report())
        self.assertIn("Failed to connect", r.report())
        r = BoardRunner(_Idf(subprocess.CompletedProcess([], 2, "", "其他错误")), port=__file__).run(_Proj())
        self.assertNotIn("下载模式", r.report())
        r = BoardRunner(_Idf(subprocess.CompletedProcess([], 2, "", "")), port="/dev/没有这个串口").run(_Proj())
        self.assertIn("找不到板子的串口", r.report())

    def test_reader_timeout_keeps_output_and_nested_log(self):
        out = f"{harness.BEGIN}\nmain/t.c:1:a:PASS\n1 Tests 0 Failures 0 Ignored\nOK\n{harness.END}\n"
        saved = board_mod.subprocess.run

        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 90, output=out.encode("utf-8"))
        board_mod.subprocess.run = run
        try:
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / "a" / "b" / "serial.log"
                ok = subprocess.CompletedProcess([], 0, "", "")
                r = BoardRunner(_Idf(ok), port=__file__).run(_Proj(), log)
                self.assertEqual(r.verdict(), "pass")
                self.assertIn("a:PASS", log.read_text(encoding="utf-8"))
        finally:
            board_mod.subprocess.run = saved

    def test_reader_timeout_without_output(self):
        saved = board_mod.subprocess.run

        def run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 90, output=None)
        board_mod.subprocess.run = run
        try:
            r = BoardRunner(_Idf(subprocess.CompletedProcess([], 0, "", "")), port=__file__).run(_Proj())
            self.assertEqual(r.verdict(), "fail")
            self.assertFalse(r.started)
        finally:
            board_mod.subprocess.run = saved

    def test_reader_error_message_and_nested_log(self):
        saved = board_mod.subprocess.run
        board_mod.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "第一行\n最后一行错误")
        try:
            with tempfile.TemporaryDirectory() as d:
                log = Path(d) / "x" / "serial.log"
                r = BoardRunner(_Idf(subprocess.CompletedProcess([], 0, "", "")), port=__file__).run(_Proj(), log)
                self.assertTrue(r.report().endswith("最后一行错误"))
                self.assertTrue(log.is_file())
        finally:
            board_mod.subprocess.run = saved


BAN = Rule("R-ALL-009", "api", ("all",), "不许调用 forbidden_call", Citation("doc", None, "", "q"), "user", "trusted",
           {"type": "api_absent", "symbol": "forbidden_call"})


def hits(src: str, path: str = "main/a.c") -> list[int]:
    return [v.line for v in check_code({path: src}, [BAN]).violations if v.rule == "R-ALL-009"]


class StripEdgeTest(unittest.TestCase):
    def test_code_after_tricky_comments_and_strings_is_checked(self):
        self.assertEqual(hits("/**/forbidden_call();"), [1])
        self.assertEqual(hits("/*/ 还在注释里 */forbidden_call();"), [1])
        self.assertEqual(hits("/* a */forbidden_call();"), [1])
        self.assertEqual(hits('s = "\\\\"; forbidden_call();'), [1])          # 字符串里是转义的反斜杠
        self.assertEqual(hits('s = "a\\"b // 不是注释"; forbidden_call();'), [1])
        self.assertEqual(hits("c = '\\''; forbidden_call();"), [1])

    def test_comments_and_strings_are_not_checked(self):
        self.assertEqual(hits("// forbidden_call();"), [])
        self.assertEqual(hits("/* forbidden_call(); */"), [])
        self.assertEqual(hits('s = "forbidden_call()";'), [])
        self.assertEqual(hits("x = 1; /* 没闭合的注释 forbidden_call();"), [])

    def test_unterminated_string_at_end(self):
        self.assertEqual(strip_c('a = "abc'), 'a = ""')
        self.assertEqual(hits('a = "abc'), [])

    def test_line_numbers(self):
        self.assertEqual(hits("\n\n/* x\ny */ forbidden_call();"), [4])
        self.assertEqual(hits("int a;\nforbidden_call();"), [2])

    def test_only_c_sources_checked(self):
        self.assertEqual(hits("forbidden_call();", "main/notes.txt"), [])
        self.assertEqual(hits("forbidden_call();", "main/x.h"), [1])
        self.assertEqual(hits("forbidden_call();", "main/include/hw_config.h"), [])

    def test_summary_when_clean(self):
        self.assertEqual(check_code({"main/a.c": "int x;"}, [BAN]).summary(), "代码合规检查通过")


if __name__ == "__main__":
    unittest.main()

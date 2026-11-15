"""最后一轮测试用到的东西（DECISIONS 第 119 条）：统计（按题聚类的标准误、pass^k、自检准确率、逐题配对）、
冻结的数据库、full 配置（调度器挑编码 Agent、经验库）、题目划分、前后对比命令。假模型、假判定，不花钱。"""

import json
import math
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.embedded import eval_stats as st
from firmforge.embedded.board_eval import (CONFIGS, coder_usage, evaluate, full_eval_registry, summarize)
from firmforge.grounding.rules import RuleSet
from firmforge.store import FrozenStore, Store
from test_board_eval import TASK, FakeTeam, make_ctx
from test_rule_verifier import good_rules

ROOT = Path(helpers.ROOT)


def row(task, cfg, verdict, flow="pass", cost=0.1, attempts=1, run=1):
    return {"task": task, "config": cfg, "run": run, "verdict": verdict, "flow_verdict": flow, "cost": cost,
            "attempts": attempts}


class StatsTest(unittest.TestCase):
    def test_pass_rate_clustered_se(self):
        rows = [row("a", "x", "pass"), row("a", "x", "pass"), row("b", "x", "fail"), row("b", "x", "fail")]
        p, se, n = st.pass_rate(rows)
        self.assertEqual((p, n), (0.5, 4))
        # 聚类：每题残差和 = ±1，平方和 2，G/(G−1)=2 → sqrt(4)/4 = 0.5（比独立样本算的 0.25 大）
        self.assertAlmostEqual(se, 0.5)
        self.assertGreater(se, math.sqrt(0.25 / 4))
        self.assertTrue(math.isnan(st.pass_rate([row("a", "x", "pass")])[1]))       # 一道题估不了
        self.assertEqual(st.pass_rate([])[2], 0)
        self.assertEqual(st.valid([{**row("a", "x", "pass"), "verdict": "skipped"}]), [])

    def test_pass_hat_k(self):
        rows = [row("a", "x", "pass")] * 3 + [row("b", "x", "pass"), row("b", "x", "pass"), row("b", "x", "fail")]
        v, n = st.pass_hat_k(rows, 3)
        self.assertEqual((v, n), (0.5, 2))                         # a 三次全过 = 1，b = C(2,3)/C(3,3) = 0
        v2, _ = st.pass_hat_k(rows, 2)
        self.assertAlmostEqual(v2, (1 + 1 / 3) / 2)                 # b：C(2,2)/C(3,2) = 1/3
        self.assertEqual(st.pass_hat_k([row("a", "x", "pass")], 3)[1], 0)   # 不够 3 次的题不算

    def test_self_check_agreement(self):
        rows = [row("a", "x", "pass", "pass"), row("a", "x", "pass", "fail"), row("a", "x", "fail", "pass（超时）"),
                row("a", "x", "fail", "fail"), {**row("a", "x", "skipped"), "flow_verdict": "pass"},
                row("a", "x", "pass", "")]
        agree, n, strict, loose = st.self_check_agreement(rows)
        self.assertEqual((n, strict, loose), (4, 1, 1))
        self.assertAlmostEqual(agree, 0.5)

    def test_cost_attempts_and_paired(self):
        rows = [row("a", "x", "pass", cost=0.2, attempts=3), row("a", "x", "fail", cost=0.2)]
        self.assertAlmostEqual(st.cost_per_pass(rows), 0.4)
        self.assertEqual(st.cost_per_pass([row("a", "x", "fail")]), math.inf)
        self.assertEqual(st.mean_attempts_of_passes(rows), 3)
        before = [row("a", "n", "fail"), row("a", "n", "pass"), row("b", "n", "fail"), row("c", "n", "pass")]
        after = [row("a", "n", "pass"), row("a", "n", "pass"), row("b", "n", "pass"), row("d", "n", "pass")]
        m, se, nt, per = st.paired_diff(before, after)
        self.assertEqual(nt, 2)                                    # 只算前后都有的题 a、b
        self.assertAlmostEqual(m, (0.5 + 1.0) / 2)
        self.assertAlmostEqual(se, 0.25)                           # 差值 0.5、1.0：sd=√0.125，/√2
        self.assertEqual([t for t, _, _ in per], ["a", "b"])

    def test_compare_report(self):
        before = [row("a", "single", "pass"), row("a", "none", "fail"), row("b", "none", "fail"),
                  row("a", "verified", "fail", "fail"), row("b", "verified", "pass", "fail")]
        after = [row("a", "none", "pass"), row("b", "none", "pass"), row("a", "full", "pass"), row("b", "full", "pass")]
        text = st.compare_report(before, after, "前", "后")
        self.assertIn("| none → none | 0% ± 0% | 100% ± 0% | +100% ± 0% | 2 |", text)
        self.assertIn("| single → single （同一份数据） | 100% | 100% | +0% | 1 | - → - |", text)
        self.assertIn("verified → full", text)
        self.assertIn("single → single （同一份数据）", text)
        self.assertIn("50% → 100%", text)                           # verified 的自检准确率 50%，full 100%


class FrozenStoreTest(unittest.TestCase):
    def test_reads_but_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "x.db"
            s = Store(db)
            s.add_agent_stat("coder-fast", "1.0", "fw-hardware-first", first_pass=True, success=True,
                             tokens=1, latency_s=1, cost=0.1)
            s.close()
            before = db.read_bytes()
            f = FrozenStore(db)
            self.assertEqual(f.agent_stats("fw-hardware-first")["coder-fast"]["successes"], 1)
            f.add_agent_stat("coder-fast", "1.0", "fw-hardware-first", first_pass=False, success=False,
                             tokens=1, latency_s=1, cost=0.1)
            self.assertEqual(f.execute("INSERT INTO experiences (category) VALUES ('x')"), 0)
            self.assertEqual(f.agent_stats("fw-hardware-first")["coder-fast"]["attempts"], 1)
            self.assertEqual(f.query("SELECT COUNT(*) AS n FROM experiences")[0]["n"], 0)
            f.close()
            self.assertEqual(db.read_bytes(), before)


class FullConfigTest(unittest.TestCase):
    def test_full_registry_keeps_coder_tiers(self):
        reg = full_eval_registry("fast", "low")
        self.assertEqual(reg.get("coder-fast").model_tier, "fast")
        self.assertEqual(reg.get("coder-strong").model_tier, "strong")
        self.assertEqual(reg.get("tester").model_tier, "fast")
        self.assertEqual(reg.get("diagnoser").model_tier, "fast")
        self.assertTrue(all(reg.get(n).thinking == "low" for n in reg.names()))

    def test_coder_usage_counts_events(self):
        with tempfile.TemporaryDirectory() as d:
            ev = [{"type": "node_assigned", "data": {"role": "coder", "agent": "coder-strong"}},
                  {"type": "node_assigned", "data": {"role": "coder", "agent": "coder-fast"}},
                  {"type": "node_assigned", "data": {"role": "tester", "agent": "tester"}},
                  {"type": "experience_used", "data": {}}]
            (Path(d) / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\nbad\n", encoding="utf-8")
            self.assertEqual(coder_usage(Path(d), full_eval_registry("fast", "low")), (2, 1, 1))
            self.assertEqual(coder_usage(Path(d) / "nope", None), (0, 0, 0))

    def test_full_learns_then_frozen_retest_does_not(self):
        """调优：full 的调度成绩和经验记进实验数据库；复测：同一个库冻结，跑完库文件不变。"""
        team, calls = FakeTeam(), []
        approved = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "tune.db"
            ctx = make_ctx(d, team, approved, calls)
            ctx.store, ctx.full_registry = Store(db), full_eval_registry("fast", "low")
            rows = evaluate([TASK], ["full"], 1, Path(d) / "tune", ctx, progress=lambda s: None)
            self.assertEqual(rows[0]["verdict"], "pass")
            self.assertGreaterEqual(rows[0]["coder_turns"], 1)              # 编码交给了调度器挑的 Agent
            # 假环境没有 ESP-IDF 和板子，流程自己的检查是"跳过"：跳过不算成绩（不能把没检查当成没过或过了）
            self.assertNotEqual(rows[0]["flow_verdict"][:4], "pass")
            self.assertEqual(ctx.store.query("SELECT COUNT(*) AS n FROM agent_stats")[0]["n"], 0)
            ctx.store.close()
            events = Path(rows[0]["run_dir"]) / "events.jsonl"
            assigned = [json.loads(x)["data"] for x in events.read_text(encoding="utf-8").splitlines()
                        if json.loads(x)["type"] == "node_assigned"]
            self.assertEqual(assigned[0]["scores"]["strategy"], "smart")     # 是调度器打分选的，不是固定指定
            snapshot = db.read_bytes()
            ctx2 = make_ctx(d, team, approved, calls)
            ctx2.store, ctx2.full_registry = FrozenStore(db), full_eval_registry("fast", "low")
            rows2 = evaluate([TASK], ["full"], 1, Path(d) / "test", ctx2, progress=lambda s: None)
            ctx2.store.close()
            self.assertEqual(rows2[0]["verdict"], "pass")
            self.assertEqual(db.read_bytes(), snapshot)
            summary = (Path(d) / "test" / "summary.md").read_text(encoding="utf-8")
            self.assertIn("## 误差、稳定性和自检", summary)
            self.assertIn("full 的调度：编码一共交出去", summary)

    def test_full_without_rules_is_skipped(self):
        team, calls = FakeTeam(), []
        with tempfile.TemporaryDirectory() as d:
            rows = evaluate([TASK], ["full"], 1, Path(d) / "o", make_ctx(d, team, RuleSet(), calls),
                            progress=lambda s: None)
            self.assertEqual([r["verdict"] for r in rows], ["skipped"])   # 下次同一目录会重跑
            self.assertEqual(calls, [])

    def test_old_rows_without_new_fields_still_summarize(self):
        old = [{"task": "a", "config": "none", "run": 1, "verdict": "pass", "failed_layer": "", "cases_passed": 4,
                "cases_total": 4, "failed_cases": [], "flow_verdict": "fail", "attempts": 4, "tokens": 1, "cost": 0.2,
                "seconds": 3, "rules_used": 0, "run_dir": "", "note": ""}]
        text = summarize(old, ["none"])
        self.assertIn("| none | 100%", text)
        self.assertIn("full", CONFIGS)


class SplitAndCompareCliTest(unittest.TestCase):
    def test_splits_cover_all_tasks_without_overlap(self):
        from firmforge.cli import task_split
        from firmforge.embedded.board_task import discover_board_tasks
        tune, test = task_split(ROOT / "tasks_board", "tune"), task_split(ROOT / "tasks_board", "test")
        hold = task_split(ROOT / "tasks_board", "holdout")
        self.assertEqual((len(tune), len(test), len(hold)), (5, 6, 4))
        self.assertFalse(set(tune) & set(test) or set(tune) & set(hold) or set(test) & set(hold))
        self.assertEqual(set(tune) | set(test) | set(hold), {t.id for t in discover_board_tasks(ROOT / "tasks_board")})
        self.assertEqual(task_split(ROOT / "tasks_board", "ledc_pwm, tune")[0], "ledc_pwm")

    def test_board_compare_command(self):
        from firmforge.cli import main
        with tempfile.TemporaryDirectory() as d:
            for name, rows in (("before", [row("a", "none", "fail"), row("b", "none", "pass")]),
                               ("after", [row("a", "none", "pass"), row("b", "none", "pass")])):
                (Path(d) / name).mkdir()
                (Path(d) / name / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            out = Path(d) / "cmp.md"
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["board-compare", str(Path(d) / "before"), str(Path(d) / "after"), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertIn("none → none | 50%", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

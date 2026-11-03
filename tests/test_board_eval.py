"""上板评测运行器（假模型、假判定，不需要 ESP-IDF 和板子）：四种配置、隐藏测试不泄露、接口锁定、断点续跑。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.board_eval import (CONFIGS, EvalContext, evaluate, missing_scopes, parse_configs, summarize,
                                           task_design)
from firmforge.embedded.board_task import JudgeResult, discover_board_tasks, judge_rules, load_board_task
from firmforge.grounding.profile import HardwareProfile
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from test_multi_agent import SETTINGS
from test_rule_verifier import good_rules, make_library

ROOT = Path(helpers.ROOT)
TASKS = ROOT / "tasks_board"
PROFILE = HardwareProfile.load(ROOT / "hardware" / "esp32c3_supermini" / "profile.json")
TASK = load_board_task(TASKS / "mpu6050_drv_idf")
GOOD = "/* GOOD */\n#include \"mpu6050.h\"\n"


class FakeTeam:
    """按 system prompt 认角色；记下所有发给模型的内容，检查有没有泄露隐藏测试。"""

    def __init__(self):
        self.seen: list[str] = []
        self.coder_systems: list[str] = []

    def __call__(self, messages):
        text = "\n".join(m["content"] or "" for m in messages)
        self.seen.append(text)
        system = messages[0]["content"]
        if "资料核查员" in system:
            rules = [r.to_dict() for r in good_rules()]
            return "```json\n" + json.dumps(rules, ensure_ascii=False) + "\n```"
        if "资深嵌入式 C 工程师" in system:
            self.coder_systems.append(system)
            # 故意顺带输出锁定的公共头文件：应当被拒收
            return (f"<<<FILE mpu6050.c\n{GOOD}>>>FILE\n"
                    "<<<FILE include/mpu6050.h\n/* 改坏的接口 */\n>>>FILE\n")
        if "测试工程师" in system:
            return '<<<FILE test/test_mpu6050.c\n#include "unity.h"\nTEST_CASE("x", "[m]") {}\n>>>FILE\n'
        if "诊断工程师" in system:
            return '```json\n{"blame": "code", "rule": "", "evidence": "", "instruction": "改"}\n```'
        raise AssertionError("没想到的调用：" + system[:60])


def fake_judge(calls):
    def judge(task, profile, judge_rules_, idf, board, files, build_dir, log_path):
        calls.append(files)
        ok = all("GOOD" in c for p, c in files.items() if p.endswith(".c"))
        return JudgeResult("pass" if ok else "fail", "" if ok else "board_test", 4 if ok else 1, 4,
                           [] if ok else ["静止时加速度约 1 g"], "假判定")
    return judge


def make_ctx(d, team, rules, calls):
    return EvalContext(llm=FakeLLM(team), settings=SETTINGS, profile=PROFILE, judge=judge_rules(TASKS, PROFILE),
                       rules=rules, library=make_library(), idf=None, board=None, runs_root=Path(d) / "runs",
                       build_root=Path(d) / "build", max_attempts=2, judge_fn=fake_judge(calls))


class DesignTest(unittest.TestCase):
    def test_fixed_design_matches_public_interface(self):
        for t in discover_board_tasks(TASKS):
            with self.subTest(task=t.id):
                m = task_design(t).modules[0]
                self.assertIn(m.header, t.public_files())
                self.assertEqual(set(m.files), {p for p in t.reference_files() if p.endswith(".c")})
                self.assertEqual(m.layer, "hardware")
                self.assertTrue(m.provides)
                self.assertTrue(t.scopes, "task.toml 要写 scopes")

    def test_parse_configs_and_missing_scopes(self):
        self.assertEqual(parse_configs("single, verified"), ["single", "verified"])
        with self.assertRaises(ValueError):
            parse_configs("single,full")
        approved = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
        self.assertEqual(missing_scopes(approved, ("i2c", "mpu6050")), [])
        self.assertEqual(missing_scopes(approved, ("i2c", "rmt")), ["rmt"])       # scope 为 all 的规则不算覆盖
        self.assertEqual(missing_scopes(RuleSet(good_rules()), ("i2c",)), ["i2c"])   # 草稿不算


class EvaluateTest(unittest.TestCase):
    def test_four_configs_resume_and_no_leak(self):
        team, calls = FakeTeam(), []
        approved = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            rows = evaluate([TASK], list(CONFIGS), 1, out, make_ctx(d, team, approved, calls), progress=lambda s: None)
            by = {r["config"]: r for r in rows}
            self.assertEqual(set(by), set(CONFIGS))
            for c in CONFIGS:
                self.assertEqual(by[c]["verdict"], "pass", c)
                self.assertEqual((by[c]["cases_passed"], by[c]["cases_total"]), (4, 4))
            self.assertEqual(by["none"]["rules_used"], 0)
            self.assertGreater(by["unverified"]["rules_used"], 0)
            self.assertGreater(by["verified"]["rules_used"], 0)
            # 判定时用的是 Agent 写的 .c，公共头文件不在里面（题目自带的才算）
            for files in calls:
                self.assertEqual(set(files), {"mpu6050.c"})
            # 编码 Agent 看到的规则：none 和 single 没有，verified 有
            self.assertNotIn("R-IMU-001", team.coder_systems[0])                      # single
            self.assertTrue(any("R-IMU-001" in s for s in team.coder_systems))
            # 隐藏测试不进任何 Agent 的上下文（CLAUDE.md 硬性规则 5）
            hidden = [c.strip()[:120] for c in TASK.hidden_files().values()]
            for text in team.seen:
                for h in hidden:
                    self.assertNotIn(h, text)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("| verified | 1 / 1 | 100% |", summary)
            self.assertTrue((out / "runs.csv").is_file())
            # 断点续跑：已有的组合不再跑
            n_calls = len(team.seen)
            evaluate([TASK], list(CONFIGS), 1, out, make_ctx(d, team, approved, calls), progress=lambda s: None)
            self.assertEqual(len(team.seen), n_calls)
            self.assertEqual(len((out / "runs.jsonl").read_text(encoding="utf-8").splitlines()), 4)

    def test_verified_without_rule_library_is_skipped_and_retried(self):
        team, calls = FakeTeam(), []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            rows = evaluate([TASK], ["verified"], 1, out, make_ctx(d, team, RuleSet(), calls), progress=lambda s: None)
            self.assertEqual(rows[0]["verdict"], "skipped")
            self.assertIn("rules build", rows[0]["note"])
            self.assertEqual(team.seen, [])                                           # 没花钱
            self.assertIn("| verified | 0 / 0 | - |", (out / "summary.md").read_text(encoding="utf-8"))
            approved = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
            rows = evaluate([TASK], ["verified"], 1, out, make_ctx(d, team, approved, calls), progress=lambda s: None)
            self.assertEqual([r["verdict"] for r in rows], ["pass"])                  # skipped 的会重跑

    def test_max_cost_stops_before_next_run(self):
        team, calls = FakeTeam(), []
        with tempfile.TemporaryDirectory() as d:
            out, msgs = Path(d) / "out", []
            rows = evaluate([TASK], ["single", "none"], 2, out, make_ctx(d, team, RuleSet(), calls),
                            progress=msgs.append, max_cost=1e-9)
            self.assertEqual(len(rows), 1)                                            # 第一次跑完就超了
            self.assertTrue(any("达到上限" in m for m in msgs))
            self.assertTrue((out / "summary.md").is_file())

    def test_failed_judgement_counts_layer(self):
        rows = [{"task": "a", "config": "none", "run": 1, "verdict": "fail", "failed_layer": "board_test",
                 "cases_passed": 1, "cases_total": 4, "attempts": 6, "tokens": 10, "cost": 0.1, "seconds": 5},
                {"task": "a", "config": "none", "run": 2, "verdict": "pass", "failed_layer": "",
                 "cases_passed": 4, "cases_total": 4, "attempts": 2, "tokens": 10, "cost": 0.1, "seconds": 5}]
        s = summarize(rows, ["none"])
        self.assertIn("| none | 1 / 2 | 50% | 62% |", s)
        self.assertIn("| none | board_test | 1 |", s)


if __name__ == "__main__":
    unittest.main()

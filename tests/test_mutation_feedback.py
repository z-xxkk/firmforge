"""变异体补测试（第 125 条）：生成变异体、只给测试 Agent 文字说明、通过后补一次测试并重测、补强后没修好交付原来那版。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.embedded.design import Design, DesignModule
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.embedded.mutate import KINDS, candidates, pick
from firmforge.embedded.roles import TEST_CHECKLIST, tester_messages, tester_mutant_message
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import load_workflow
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS

DRIVER = """#include "imu.h"
#define IMU_REG_CFG   0x37      /* 中断脚配置 */
#define LSB_PER_G     16384.0f
// vTaskDelay(pdMS_TO_TICKS(5)); 注释里的不算

static float s_scale = 16384.0f;

esp_err_t imu_init(int range)
{
    if (range < 0 || range >= 4) {
        return ESP_ERR_INVALID_ARG;
    }
    write_reg(0x6B, 0x01);
    vTaskDelay(pdMS_TO_TICKS(50));
    if (range == 2) {
        s_scale = 4096.0f;
    }
    return ESP_OK;
}
"""


class MutateTest(unittest.TestCase):
    def test_every_kind_found_and_one_line_changed(self):
        cands = candidates("imu.c", DRIVER)
        self.assertEqual({c.kind for c in cands}, set(KINDS))
        for c in cands:
            old, new = DRIVER.split("\n"), c.content.split("\n")
            self.assertEqual(len(old), len(new))
            self.assertEqual(sum(a != b for a, b in zip(old, new)), 1, c.describe)
            self.assertNotIn("注释里的不算", new[c.line - 1] if c.kind == "delay" and c.line == 4 else "")
        self.assertFalse(any(c.line == 4 for c in cands))               # 注释行不改

    def test_descriptions_have_no_code(self):
        for c in candidates("imu.c", DRIVER):
            self.assertNotIn("write_reg", c.describe)
            self.assertNotIn("{", c.describe)
        delay = next(c for c in candidates("imu.c", DRIVER) if c.kind == "delay")
        self.assertIn("在 imu_init 里", delay.describe)
        self.assertIn("vTaskDelay(0)", delay.content.split("\n")[delay.line - 1].replace("pdMS_TO_TICKS(0)", "0"))

    def test_pick_spreads_over_kinds(self):
        ms = pick({"imu.c": DRIVER, "include/imu.h": "int x = 0x55;"}, 3)
        self.assertEqual(len(ms), 3)
        self.assertEqual(len({m.kind for m in ms}), 3)
        self.assertTrue(all(m.file == "imu.c" for m in ms))              # 头文件不改
        self.assertEqual(pick({"a.c": "int main(void) { return 0; }"}, 3), [])

    def test_tester_prompt_and_message(self):
        m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"), provides=("int imu_read(void)",))
        text = tester_messages("读 IMU", profile(), RuleSet(), m, "int imu_read(void);", {})[1]["content"]
        self.assertIn("测试要点（通用检查清单）", text)
        self.assertIn(TEST_CHECKLIST.splitlines()[0], text)
        msg = tester_mutant_message(pick({"imu.c": DRIVER}, 2), 4)
        self.assertIn("故意改坏了 4 处", msg)
        self.assertIn("下面 2 处改动", msg)
        self.assertNotIn("write_reg", msg)


class HardenInFlowTest(unittest.TestCase):
    """模块通过后：存活的变异体交给测试 Agent，补完测试重测一次原代码（算一次尝试）。"""

    def run_flow(self, variant_results, checks, workflow=False, max_attempts=4, mutants=3, tester_changes=True):
        seen = []

        def team(messages):
            system, last = messages[0]["content"], messages[-1]["content"] or ""
            if "资深嵌入式 C 工程师" in system:
                seen.append(("coder", last))
                n = sum(1 for k, _ in seen if k == "coder")
                return "<<<FILE imu.c\n" + DRIVER + f"\n/* 第 {n} 版 */\n>>>FILE\n"
            if "测试工程师" in system:
                seen.append(("tester", last))
                n = sum(1 for k, _ in seen if k == "tester")
                body = f'TEST_CASE("t{n if tester_changes else 1}", "[imu]") {{}}'
                return f'<<<FILE test/test_imu.c\n#include "unity.h"\n{body}\n>>>FILE\n'
            if "诊断工程师" in system:
                seen.append(("diag", last))
                return '```json\n{"blame": "code", "rule": "", "evidence": "e", "instruction": "改"}\n```'
            raise AssertionError(system[:40])
        m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"), provides=("int imu_read(void)",))
        import itertools
        res_iter, var_iter = itertools.chain(checks, itertools.repeat(checks[-1])), iter(variant_results)
        settings = SETTINGS
        from dataclasses import replace
        settings = replace(SETTINGS, loop={**(SETTINGS.loop or {}), "mutants": mutants})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: next(res_iter)), \
                mock.patch.object(EmbeddedFlow, "_run_variant", lambda self, *a, **k: next(var_iter)):
            f = EmbeddedFlow(llm=FakeLLM(team), settings=settings, profile=profile(), spec="x", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                             design=Design([m]), locked={"include/imu.h": "int imu_read(void);\n"}, build_rules=False,
                             max_attempts=max_attempts, workflow=load_workflow("firmware_dev") if workflow else None)
            res = f.run()
            ev = read_events(res.run_dir)
        return res.modules["imu"], seen, ev

    PASS = ("pass", "board_test", "共 2 个用例，通过 2")
    FAIL = ("fail", "board_test", "失败：x Expected 1 Was 0")

    def test_survivors_go_to_tester_then_recheck(self):
        for wf in (False, True):
            with self.subTest(workflow=wf):
                mr, seen, ev = self.run_flow(["killed", "survived", "survived"], [self.PASS, self.PASS], wf)
                self.assertEqual(mr.verdict, "pass")
                self.assertEqual(mr.attempts, 2)                         # 补完测试重测算一次尝试
                tester_msgs = [t for k, t in seen if k == "tester"]
                self.assertEqual(len(tester_msgs), 2)
                self.assertIn("下面 2 处改动你的测试没有发现", tester_msgs[1])
                mt = [e["data"] for e in ev if e["type"] == "mutation_test"]
                self.assertEqual((mt[0]["total"], mt[0]["killed"], mt[0]["survived"]), (3, 1, 2))

    def test_all_killed_no_extra_calls(self):
        mr, seen, ev = self.run_flow(["killed", "killed", "killed"], [self.PASS])
        self.assertEqual(mr.attempts, 1)
        self.assertEqual(sum(1 for k, _ in seen if k == "tester"), 1)

    def test_invalid_mutants_replaced_and_skipped_stops(self):
        mr, seen, ev = self.run_flow(["invalid", "killed", "killed", "killed"], [self.PASS])
        mt = [e["data"] for e in ev if e["type"] == "mutation_test"][0]
        self.assertEqual(mt["total"], 3)                                 # 编译不过的不算，补一个
        mr, seen, ev = self.run_flow(["skipped"], [self.PASS])
        self.assertEqual([e["data"]["total"] for e in ev if e["type"] == "mutation_test"], [0])

    def test_no_hardening_on_last_attempt_or_when_disabled(self):
        mr, seen, ev = self.run_flow([], [self.PASS], max_attempts=1)
        self.assertFalse(any(e["type"] == "mutation_test" for e in ev))
        mr, seen, ev = self.run_flow([], [self.PASS], mutants=0)
        self.assertFalse(any(e["type"] == "mutation_test" for e in ev))

    def test_unchanged_tests_no_recheck(self):
        mr, seen, ev = self.run_flow(["survived", "killed", "killed"], [self.PASS], tester_changes=False)
        self.assertEqual(mr.attempts, 1)

    def test_stronger_tests_fail_then_out_of_attempts_delivers_old_version(self):
        for wf in (False, True):
            with self.subTest(workflow=wf):
                mr, seen, ev = self.run_flow(["survived", "survived", "survived"],
                                             [self.PASS, self.FAIL, self.FAIL], wf, max_attempts=3)
                self.assertEqual(mr.verdict, "fail")                     # 如实记没通过
                self.assertTrue(any(e["type"] == "hardened_reverted" for e in ev))
                self.assertIn("第 1 版", mr.files["imu.c"])              # 交付的是补强前通过的那一版


if __name__ == "__main__":
    unittest.main()

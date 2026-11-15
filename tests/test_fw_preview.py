"""工作流预演（设计器"检查"，2026-11-14）：拿真正的解释器空跑几种情况，记下走过的步骤。"""

import copy
import tomllib
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.fw_preview import SCENARIOS, describe, preview
from firmforge.orchestrator.workflow import WorkflowError, load_workflow, parse_workflow


def by_key(traces):
    return {t.scenario.key: t for t in traces}


class PreviewTest(unittest.TestCase):
    def test_default_workflow(self):
        t = by_key(preview(load_workflow("firmware_dev")))
        self.assertEqual(len(t), len(SCENARIOS))
        self.assertTrue(all(not x.error for x in t.values()))
        self.assertEqual(t["hw_pass"].verdict, "pass")
        self.assertNotIn("repair", t["hw_pass"].step_ids)                 # 一次通过：修正循环没起作用，不点亮
        self.assertIn("repair", t["logic_fix"].step_ids)
        self.assertEqual(t["never"].verdict, "fail")
        self.assertEqual(sum("检查：没通过" in x for x in describe(t["never"])), 4)   # 最多 4 次
        self.assertEqual(t["hw_wiring"].verdict, "hardware")
        self.assertIn("confirm_design", t["human"].step_ids)               # 开了人工确认才停
        self.assertNotIn("confirm_design", t["hw_pass"].step_ids)
        ids = t["hw_pass"].step_ids
        self.assertLess(ids.index("design"), ids.index("write"))           # 按执行顺序
        self.assertTrue({"code", "test"} <= set(ids))                      # 并行的两个分支一起点亮

    def test_review_workflow_conditions(self):
        t = by_key(preview(load_workflow("firmware_review")))
        self.assertIn("review", t["hw_pass"].step_ids)
        self.assertNotIn("review", t["logic_fix"].step_ids)               # 逻辑层不审
        self.assertIn("hw_gate", t["logic_fix"].step_ids)                 # 但走到了条件
        self.assertTrue(any("按审查意见改" in x for x in describe(t["review_fix"])))
        self.assertTrue(any("你写了意见" in x for x in describe(t["human"])))

    def test_structure_problem_raises(self):
        data = tomllib.loads((Path(helpers.ROOT) / "workflows" / "firmware_dev.toml").read_text(encoding="utf-8"))
        data["main"] = [s for s in data["main"] if s["id"] != "design"]
        with self.assertRaises(WorkflowError):
            preview(parse_workflow(data, "x"))

    def test_interpreter_error_is_reported_not_raised(self):
        # 预演用的是解释器本身：解释器出错时这一种情况记成出错，不让整个检查崩掉
        from unittest import mock
        with mock.patch("firmforge.embedded.fw_interpreter.FirmwareInterpreter._review", side_effect=RuntimeError("坏了")):
            t = by_key(preview(load_workflow("firmware_review")))
        self.assertIn("坏了", t["hw_pass"].error)
        self.assertTrue(describe(t["hw_pass"])[-1].startswith("出错"))
        self.assertEqual(t["logic_fix"].error, "")                         # 逻辑层不走审查：照常跑完


class StubInterfaceTest(unittest.TestCase):
    def test_stub_flow_has_every_method_the_interpreter_calls(self):
        """预演的替身要有解释器调用的每个流程方法（2026-11-15：加了 _harden_tests 忘了加替身，预演全部报错）。"""
        import re
        from pathlib import Path
        from firmforge.embedded.fw_preview import _StubFlow
        src = (Path(helpers.ROOT) / "src" / "firmforge" / "embedded" / "fw_interpreter.py").read_text(encoding="utf-8")
        called = set(re.findall(r"\b(?:f|self\.flow|flow)\.(_[a-z]\w*)\(", src))
        self.assertTrue(called)
        self.assertEqual(sorted(x for x in called if not hasattr(_StubFlow, x)), [])


if __name__ == "__main__":
    unittest.main()

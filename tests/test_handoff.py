"""第 134 条第 5 阶段：诊断要删的用例由平台核对；诊断可以判"设计"，交回设计 Agent 改验收条件、测试从头写。
只在产品流程（工作流路径）里有；评测的固定路径照旧（诊断提示和可选的 blame 都不变）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.embedded.design import Design, DesignModule
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.embedded.roles import diagnoser_messages, parse_diagnosis
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import load_workflow
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS

M = DesignModule("adc_reader", "ADC", "hardware", ("adc_reader.c", "include/adc_reader.h"),
                 provides=("int adc_read(void)",), scopes=("adc",),
                 acceptance=("将 FF_PIN_ADC_VIN 接 GND 时读数小于 100 mV",))
PASS = ("pass", "board_test", "共 1 个用例，通过 1")
FAIL = ("fail", "board_test", "失败：接 GND 时读数小于 100 mV")


class ParseTest(unittest.TestCase):
    def test_design_only_in_product_flow(self):
        with self.assertRaises(ValueError):
            parse_diagnosis({"blame": "design", "instruction": "改验收条件"})                 # 评测路径不认
        d = parse_diagnosis({"blame": "test", "instruction": "删掉", "remove_cases": ["a", " ", "b"]}, allow_design=True)
        self.assertEqual(d["remove_cases"], ["a", "b"])
        self.assertEqual(parse_diagnosis({"blame": "design", "instruction": "x"}, allow_design=True)["blame"], "design")
        old = diagnoser_messages(profile(), RuleSet(), M, {}, {}, "r")
        new = diagnoser_messages(profile(), RuleSet(), M, {}, {}, "r", allow_design=True)
        self.assertNotIn("design", old[0]["content"].split("## 输出")[0][-200:])
        self.assertIn("remove_cases", new[0]["content"])
        self.assertIn("接 GND 时读数小于 100 mV", new[1]["content"])                          # 诊断看得到验收条件
        self.assertNotIn("设计给这个模块定的验收条件", old[1]["content"])


class FlowTest(unittest.TestCase):
    def run_flow(self, checks, diag_json, tester_keeps=False, designer_reply=None):
        seen = []

        def team(messages):
            system, last = messages[0]["content"], messages[-1]["content"] or ""
            if "资深嵌入式 C 工程师" in system:
                return "<<<FILE adc_reader.c\nint adc_read(void) { return 1; }\n>>>FILE\n"
            if "测试工程师" in system:
                seen.append(("tester", last))
                n = sum(1 for k, _ in seen if k == "tester")
                keep = tester_keeps or n == 1
                body = ('TEST_CASE("接 GND 时读数小于 100 mV", "[adc][hardware]") {}\n' if keep else "") + \
                    f'TEST_CASE("读数在范围内 {n}", "[adc]") {{}}'
                return f'<<<FILE test/test_adc_reader.c\n#include "unity.h"\n{body}\n>>>FILE\n'
            if "诊断工程师" in system:
                return "```json\n" + diag_json + "\n```"
            if "设计工程师" in system:
                seen.append(("designer", last))
                return designer_reply or '```json\n{"acceptance": ["接到 3V3 后读数在 2800–3100 mV"]}\n```'
            raise AssertionError(system[:40])
        it = iter(checks)
        from dataclasses import replace
        settings = replace(SETTINGS, loop={**(SETTINGS.loop or {}), "mutants": 0})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: next(it, checks[-1])):
            f = EmbeddedFlow(llm=FakeLLM(team), settings=settings, profile=profile(), spec="读 ADC", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                             design=Design([M]), build_rules=False, workflow=load_workflow("firmware_dev"))
            res = f.run()
            ev = read_events(res.run_dir)
        return res, seen, ev

    def test_removal_enforced(self):
        diag = '{"blame": "test", "rule": "", "evidence": "要人接线", "instruction": "删掉接 GND 的用例", ' \
               '"remove_cases": ["接 GND 时读数小于 100 mV"]}'
        res, seen, ev = self.run_flow([FAIL, PASS], diag, tester_keeps=False)
        hc = [e["data"] for e in ev if e["type"] == "handoff_checked"]
        self.assertEqual(hc[0]["ok"], True)
        res, seen, ev = self.run_flow([FAIL, PASS], diag, tester_keeps=True)
        tester_msgs = [t for k, t in seen if k == "tester"]
        self.assertTrue(any("还在" in t and "接 GND 时读数小于 100 mV" in t for t in tester_msgs))   # 没删：再要一次
        hc = [e["data"] for e in ev if e["type"] == "handoff_checked"]
        self.assertEqual((hc[0]["ok"], hc[0]["left"]), (False, ["接 GND 时读数小于 100 mV"]))

    def test_design_blame_revises_acceptance(self):
        diag = '{"blame": "design", "rule": "", "evidence": "要人临时接 GND", "instruction": "改成读接好的 3V3"}'
        res, seen, ev = self.run_flow([FAIL, PASS], diag)
        rev = [e["data"] for e in ev if e["type"] == "design_revised"]
        self.assertEqual(rev[0]["new"], ["接到 3V3 后读数在 2800–3100 mV"])
        self.assertEqual(res.modules["adc_reader"].verdict, "pass")
        tester_msgs = [t for k, t in seen if k == "tester"]
        self.assertIn("2800–3100", tester_msgs[-1])                                          # 测试按新条件从头写
        self.assertNotIn("接 GND 时", tester_msgs[-1])

    def test_designer_must_not_keep_manual_condition(self):
        diag = '{"blame": "design", "rule": "", "evidence": "x", "instruction": "y"}'
        bad = '```json\n{"acceptance": ["按下按键时计数加一"]}\n```'
        res, seen, ev = self.run_flow([FAIL, PASS], diag, designer_reply=bad)
        self.assertEqual(sum(1 for k, _ in seen if k == "designer"), 2)                      # 退回一次
        self.assertFalse(any(e["type"] == "design_revised" for e in ev))                     # 两次都不行：按测试问题处理


if __name__ == "__main__":
    unittest.main()

"""接线自检（第 124 条）：自检项从硬件档案生成、自检程序、结果对应、修正循环里"诊断说硬件 → 先实测"。不需要板子。"""

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

import helpers
from firmforge.grounding.profile import HardwareProfile
from firmforge.target import selfcheck as sc

ROOT = Path(helpers.ROOT)
PROFILE_PATH = ROOT / "hardware" / "esp32c3_supermini" / "profile.json"


def profile():
    return HardwareProfile.load(PROFILE_PATH)


@dataclass
class Case:
    name: str
    status: str
    message: str = ""


@dataclass
class FakeBoardResult:
    cases: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    skipped: str = ""


class ProfileFieldsTest(unittest.TestCase):
    def test_board_profile_has_loopbacks_and_int_probes(self):
        p = profile()
        self.assertEqual(p.problems(), [])
        self.assertEqual({(lb["a"], lb["b"]) for lb in p.loopbacks}, {(6, 7), (0, 1)})
        self.assertTrue(all(d.int_probe is not None for d in p.devices))

    def test_bad_entries_are_reported(self):
        d = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        d["loopbacks"] = [{"a": 6, "b": 6}, {"a": 8, "b": 7}, {"a": "x", "b": 1}]
        d["devices"][0]["int_probe"] = {"value": {"setup": [[1]], "active": "mid", "clear": 1, "wait_ms": 1}}
        probs = HardwareProfile.from_dict(d).problems()
        self.assertEqual(sum("回环" in x for x in probs), 3)
        self.assertTrue(any("int_probe" in x for x in probs))


class ItemsAndSourceTest(unittest.TestCase):
    def test_select_items(self):
        p = profile()
        kinds = [it.kind for it in sc.select_items(p)]
        self.assertEqual(kinds.count("i2c"), 2)
        self.assertEqual(kinds.count("int"), 2)
        self.assertEqual(kinds.count("loop"), 2)
        only = sc.select_items(p, {"imu"}, {4, 5, 3})
        self.assertEqual([it.kind for it in only], ["i2c", "int"])             # 不用 ppg、不碰跳线的 GPIO
        loop = sc.select_items(p, set(), {6})
        self.assertEqual([it.gpios for it in loop], [(6, 7)])

    def test_source_has_one_case_per_item_with_right_levels(self):
        p = profile()
        items = sc.select_items(p)
        src = sc.check_source(p, items)
        for it in items:
            self.assertIn(f'TEST_CASE("{it.name}"', src)
        self.assertIn("i2c_master_probe(bus, 0x68", src)
        self.assertIn("ffw_wr(dev, 0x37, 0x30)", src)                         # MPU6050 INT 高有效
        self.assertIn("before != 1", src)
        self.assertIn("during == 0", src)                                    # MAX30102 INT 低有效
        self.assertIn("ffw_loop(6, 7)", src)
        self.assertIn("ffw_loop(7, 6)", src)
        self.assertNotIn("hidden", src.lower())

    def test_results_mapping(self):
        p = profile()
        items = sc.select_items(p, {"imu"}, {6})
        res = FakeBoardResult(cases=[Case(items[0].name, "pass"), Case(items[1].name, "fail", "INT 线没接通")],
                              logs=["INT 脚 GPIO3：清除后 0，产生中断后 0，再清除后 0（有效电平 1）"])
        chk = sc.results_from(items, res)
        self.assertEqual([i["ok"] for i in chk.items], [True, False, None])     # 跳线那项没跑到
        self.assertFalse(chk.all_ok)
        self.assertEqual(len(chk.failed), 1)
        self.assertIn("不通", chk.evidence())
        self.assertIn("没测出结果", chk.evidence())
        self.assertTrue(sc.results_from(items, FakeBoardResult(skipped="烧录失败")).skipped)
        ok = sc.results_from(items, FakeBoardResult(cases=[Case(it.name, "pass") for it in items]))
        self.assertTrue(ok.all_ok)
        self.assertFalse(sc.WiringCheck().all_ok)                            # 一项都没有不算正常

    def test_run_check_without_board_is_skipped(self):
        chk = sc.run_check(profile(), sc.select_items(profile()), None, None, Path(tempfile.mkdtemp()))
        self.assertTrue(chk.skipped)
        self.assertFalse(chk.all_ok)


class RepairLoopUsesSelfcheckTest(unittest.TestCase):
    """诊断说硬件：接线自检正常就重新诊断、继续修；有不通或测不了才停下。"""

    def run_flow(self, check, diag_seq, workflow=False):
        from firmforge.embedded.design import Design, DesignModule
        from firmforge.embedded.flow import EmbeddedFlow
        from firmforge.grounding.rules import RuleSet
        from firmforge.llm import FakeLLM
        from firmforge.orchestrator.workflow import load_workflow
        from firmforge.runs import read_events
        from test_embedded_design import profile as small_profile
        from test_multi_agent import SETTINGS
        seen, diags = [], list(diag_seq)

        def team(messages):
            system = messages[0]["content"]
            if "诊断工程师" in system:
                seen.append(("diag", messages[-1]["content"]))
                b = diags.pop(0) if diags else "code"
                return '```json\n{"blame": "%s", "rule": "", "evidence": "e", "instruction": "改"}\n```' % b
            if "资深嵌入式 C 工程师" in system:
                seen.append(("coder", messages[-1]["content"]))
                return "<<<FILE imu.c\nint imu_read(void){return 1;}\n>>>FILE\n"
            seen.append(("tester", ""))
            return '<<<FILE test/test_imu.c\n#include "unity.h"\nTEST_CASE("x", "[imu]") {}\n>>>FILE\n'
        m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"), provides=("int imu_read(void)",))
        results = iter([("fail", "board_test", "失败：一直超时 Expected 0 Was 263"), ("pass", "board_test", "")])
        calls = []
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: next(results)), \
                mock.patch("firmforge.target.selfcheck.run_check", lambda *a, **k: calls.append(1) or check):
            f = EmbeddedFlow(llm=FakeLLM(team), settings=SETTINGS, profile=small_profile(), spec="x", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                             design=Design([m]), locked={"include/imu.h": "int imu_read(void);\n"}, build_rules=False,
                             max_attempts=3, workflow=load_workflow("firmware_dev") if workflow else None)
            res = f.run()
            ev = read_events(res.run_dir)
        return res.modules["imu"], seen, ev, calls

    def ok_check(self):
        return sc.WiringCheck(items=[{"name": "接线自检：GPIO6 和 GPIO7 之间的跳线连通", "kind": "loop", "ok": True,
                                      "detail": "GPIO6→GPIO7：通"}])

    def test_wiring_ok_rediagnoses_and_keeps_repairing(self):
        for wf in (False, True):
            with self.subTest(workflow=wf):
                mr, seen, ev, calls = self.run_flow(self.ok_check(), ["hardware", "code"], wf)
                self.assertEqual(mr.verdict, "pass")                         # 没停下，修好了
                diag_msgs = [t for k, t in seen if k == "diag"]
                self.assertEqual(len(diag_msgs), 2)                          # 带着实测结果重新诊断了一次
                self.assertIn("平台接线自检", diag_msgs[1])
                self.assertIn("跳线连通：正常", diag_msgs[1])
                self.assertTrue(any(e["type"] == "hardware_check" and e["data"]["ok"] for e in ev))
                self.assertFalse(any(e["type"] == "human_wait" for e in ev))
                self.assertEqual(len(calls), 1)

    def test_still_says_hardware_after_ok_check_is_treated_as_code(self):
        mr, seen, ev, _ = self.run_flow(self.ok_check(), ["hardware", "hardware"])
        self.assertEqual(mr.verdict, "pass")
        coder_msgs = [t for k, t in seen if k == "coder"]
        self.assertIn("平台已实测接线正常", coder_msgs[-1])

    def test_broken_wiring_or_unmeasurable_still_stops(self):
        bad = sc.WiringCheck(items=[{"name": "接线自检：GPIO6 和 GPIO7 之间的跳线连通", "kind": "loop", "ok": False,
                                     "detail": "跳线不通"}])
        for chk, why in ((bad, "不通"), (sc.WiringCheck(skipped="没有板子"), "没能运行")):
            with self.subTest(why=why):
                mr, seen, ev, _ = self.run_flow(chk, ["hardware"])
                self.assertEqual(mr.verdict, "hardware")
                wait = [e for e in ev if e["type"] == "human_wait"]
                self.assertTrue(wait and why in wait[0]["data"]["evidence"])   # 证据交给人看


if __name__ == "__main__":
    unittest.main()

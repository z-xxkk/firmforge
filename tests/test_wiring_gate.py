"""接线一步（第 134 条第 3 阶段）：设计之后、写代码之前，等人按清单接好线，平台按每根线的 expect 自检，
全部对上才往下；对不上告诉人哪根线、实测多少，等改好再测；项目里每根线的状态跟着改。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from firmforge.embedded.design import parse_design
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.profile import HardwareProfile
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.projects import Project
from firmforge.runs import read_events
from firmforge.target.selfcheck import WiringCheck, check_source, items_for_connections
from test_embedded_flow import DESIGN
from test_multi_agent import SETTINGS

C3 = HardwareProfile.load(Path(helpers.ROOT) / "hardware" / "esp32c3_supermini" / "profile.json")
ADC = {"signal": "adc.vin", "gpio": 1, "to": "3V3", "why": "读已知电压", "unplug": True,
       "expect": {"kind": "adc_mv", "min": 3000, "max": 3400}}
I2C = [{"signal": s, "gpio": g, "to": t, "why": "MPU6050", "expect": {"kind": "i2c_ack", "device": "imu"}}
       for s, g, t in (("i2c0.sda", 4, "imu.SDA"), ("i2c0.scl", 5, "imu.SCL"))]
LOOP = [{"signal": "pwm.out", "gpio": 6, "to": "pwm.in", "why": "回环", "expect": {"kind": "loopback", "with": "pwm.in"}},
        {"signal": "pwm.in", "gpio": 7, "to": "pwm.out", "why": "回环", "expect": {"kind": "loopback", "with": "pwm.out"}}]
LVL = {"signal": "sw.in", "gpio": 0, "to": "GND", "why": "电平", "unplug": True, "expect": {"kind": "level", "value": 0}}


def design(wiring):
    """测试用的设计带 MPU6050 模块，接线里总要有它的 I2C 两根线。"""
    if not any(w["signal"] == "i2c0.sda" for w in wiring):
        wiring = [*wiring, *I2C]
    d, probs = parse_design({**DESIGN, "wiring": wiring}, C3)
    assert not probs, probs
    return d


class ItemsTest(unittest.TestCase):
    def test_items_and_source(self):
        d = design([ADC, LVL, *I2C, *LOOP])
        prof = C3.with_wiring(d.wiring)
        items = items_for_connections(prof, d.connections)
        self.assertEqual([(i.kind, i.signals) for i in items],
                         [("adc", ("adc.vin",)), ("level", ("sw.in",)), ("i2c", ("i2c0.sda", "i2c0.scl")),
                          ("loop", ("pwm.out", "pwm.in"))])                    # SDA、SCL 合成一项，回环一对一项
        src = check_source(prof, items)
        self.assertIn("ffw_adc_mv(1)", src)
        self.assertIn("ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED", src)
        self.assertIn("GPIO_PULLUP_ONLY", src)                              # 期望低电平：反着上拉
        self.assertIn("i2c_master_probe", src)
        looked = items_for_connections(prof, design([{**ADC, "expect": {"kind": "look"}}]).connections)
        self.assertEqual([i.kind for i in looked], ["i2c"])                 # 只能看的线不生成自检项


class GateTest(unittest.TestCase):
    def run_gate(self, results, *, answers=("ok",) * 5, wiring=None, preset=None, confirm=True):
        asked = []

        def confirm_wiring(conns, states, failures):
            asked.append((dict(states), list(failures)))
            return answers[len(asked) - 1]
        it = iter(results)
        with tempfile.TemporaryDirectory() as d:
            rec = Project.create(Path(d) / "p")
            dsg = design(wiring or [ADC])
            rec.set_wiring([c.to_dict() for c in dsg.connections])
            for sig, st in (preset or {}).items():
                rec.set_state(sig, st)
            f = EmbeddedFlow(llm=FakeLLM([]), settings=SETTINGS, profile=C3, spec="x", rules=RuleSet(), library=None,
                             verifier=None, runs_root=Path(d) / "runs", rule_mode="none", project=rec,
                             confirm_wiring=confirm_wiring if confirm else None)
            f._use_wiring(dsg)
            with mock.patch("firmforge.target.selfcheck.run_check", lambda *a, **k: next(it)):
                ok = f._wiring_gate(dsg)
            states = {c["signal"]: Project.load(rec.path).state(c["signal"]) for c in rec.wiring}
            ev = [e for e in read_events(f.rd.path) if e["type"] in ("human_wait", "wiring_selfcheck")]
        return ok, asked, states, ev, f.result.error

    @staticmethod
    def chk(ok, detail="ADC GPIO1：3291 mV"):
        return WiringCheck(items=[{"name": "接线自检：adc.vin", "kind": "adc", "ok": ok, "detail": detail,
                                   "signals": ["adc.vin"]},
                                  {"name": "接线自检：imu 应答", "kind": "i2c", "ok": True, "detail": "",
                                   "signals": ["i2c0.sda", "i2c0.scl"]}])

    def test_pass_first_time(self):
        ok, asked, states, ev, _ = self.run_gate([self.chk(True)])
        self.assertTrue(ok)
        self.assertEqual(len(asked), 1)                                      # 先等接线
        self.assertEqual(states, {"adc.vin": "实测", "i2c0.sda": "实测", "i2c0.scl": "实测"})
        self.assertEqual([e["type"] for e in ev], ["human_wait", "wiring_selfcheck"])

    def test_mismatch_then_fixed(self):
        ok, asked, states, ev, _ = self.run_gate([self.chk(False, "ADC GPIO1：412 mV"), self.chk(True)])
        self.assertTrue(ok)
        self.assertEqual(len(asked), 2)
        self.assertEqual(asked[1][0]["adc.vin"], "不符")                    # 第二次问人时带上没对上的结果
        self.assertIn("412 mV", asked[1][1][0]["detail"])
        self.assertEqual(states["adc.vin"], "实测")

    def test_cancel_and_three_strikes(self):
        ok, _, _, _, err = self.run_gate([self.chk(True)], answers=("cancel",))
        self.assertFalse(ok)
        self.assertIn("停止", err)
        ok, asked, states, _, err = self.run_gate([self.chk(False)] * 3)
        self.assertFalse(ok)
        self.assertEqual(len(asked), 3)
        self.assertIn("3 次", err)
        self.assertEqual(states["adc.vin"], "不符")

    def test_already_measured_skips_asking_but_still_checks(self):
        done = {s: "实测" for s in ("pwm.out", "pwm.in", "i2c0.sda", "i2c0.scl")}
        ok, asked, states, ev, _ = self.run_gate([WiringCheck(items=[{"name": "接线自检：回环", "kind": "loop", "ok": True,
                                                                      "detail": "", "signals": ["pwm.out", "pwm.in"]}])],
                                                 wiring=LOOP, preset=done)
        self.assertTrue(ok)
        self.assertEqual(asked, [])                                          # 上次实测过、没改线：不打扰人
        self.assertEqual([e["type"] for e in ev], ["wiring_selfcheck"])     # 但照样自检，不相信上次的结论

    def test_no_board_does_not_claim_measured(self):
        ok, _, states, ev, _ = self.run_gate([WiringCheck(skipped="没有 ESP-IDF 或没有板子")])
        self.assertTrue(ok)
        self.assertEqual(states["adc.vin"], "已接")                         # 测不了：停在人确认的"已接"
        self.assertIn("没有板子", ev[-1]["data"]["skipped"])

    def test_cli_without_confirm_stops_on_mismatch(self):
        ok, asked, _, _, err = self.run_gate([self.chk(False, "ADC GPIO1：5 mV")], confirm=False)
        self.assertFalse(ok)
        self.assertIn("5 mV", err)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class CardTest(unittest.TestCase):
    def test_checklist_card(self):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui.chat_page import ChatPage
        from firmforge.gui.theme import current
        from firmforge.gui.widgets import set_theme
        QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeWiringTest")
        set_theme(current(False))
        with tempfile.TemporaryDirectory() as d:
            page = ChatPage(Path(d))
            answers = []

            class Backend:
                def answer(self, v):
                    answers.append(v)
            page.backend = Backend()
            page.on_ask("wiring", {"connections": [{**ADC, "unplug": "跳线连着 GPIO0"}], "states": {"adc.vin": "计划"},
                                   "failures": [{"name": "接线自检：adc.vin", "detail": "ADC GPIO1：412 mV"}]})
            text = "\n".join(w.text() for w in page.findChildren(QLabel))
            self.assertIn("GPIO1", text)
            self.assertIn("先拔掉原来的线：跳线连着 GPIO0", text)
            self.assertIn("412 mV", text)
            page._answer_pending("接好了")                                  # 打字不算：只能点按钮
            self.assertEqual(answers, [])
            next(b for b in page.findChildren(QPushButton) if b.text() == "已接好，开始自检").click()
            self.assertEqual(answers, ["ok"])
            page.backend = None
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()

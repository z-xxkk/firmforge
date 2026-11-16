"""接线归项目（DECISIONS 第 108 条）：设计给出这个项目的接线，平台检查后用它生成配置、给编码 Agent；
上板时按"用例前复位外接器件"有没有应答，实测确认 I2C 器件确实接在这条总线上。评测（固定设计、不写接线）照旧。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.design import designer_messages, parse_design
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.profile import HardwareProfile
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.runs import read_events
from firmforge.target.esp_idf import hw_config_header
from firmforge.target.harness import BEGIN, BoardTestResult, parse_board_output, _reset_code
from test_embedded_flow import DESIGN, FakeTeam
from test_embedded_design import profile
from test_multi_agent import SETTINGS

C3 = HardwareProfile.load(Path(helpers.ROOT) / "hardware" / "esp32c3_supermini" / "profile.json")
WIRING = {"i2c0.sda": 6, "i2c0.scl": 7, "imu.int": 3}


def conns(wiring: dict) -> list[dict]:
    """完整连线格式（第 134 条）：每根线写另一端、为什么、自检应测到什么。"""
    to = {"i2c0.sda": "imu.SDA", "i2c0.scl": "imu.SCL", "imu.int": "imu.INT"}
    return [{"signal": s, "gpio": g, "to": to.get(s, "3V3"), "why": "测试",
             "expect": {"kind": "i2c_ack", "device": "imu"}} for s, g in wiring.items()]


class WiringCheckTest(unittest.TestCase):
    def test_problems(self):
        self.assertEqual(C3.wiring_problems(WIRING, {"imu"}), [])
        bad = C3.wiring_problems({"i2c0.sda": 8, "i2c0.scl": 30, "imu.int": 3, "led": 4, "x.y": "5", "a.b": 3}, {"imu"})
        text = "；".join(bad)
        self.assertIn("不能用的 GPIO8", text)                  # 启动配置引脚
        self.assertIn("没有 GPIO30", text)                     # C3 只有 GPIO0–21
        self.assertIn("同时分给了", text)                      # GPIO3 给了两个信号
        self.assertIn("信号名 'led'", text)
        self.assertIn("整数", text)
        self.assertIn("i2c0.sda 和 i2c0.scl", "；".join(C3.wiring_problems({"imu.int": 3}, {"imu"})))
        self.assertEqual(C3.wiring_problems({"imu.int": 3}, set()), [])     # 不用 I2C 器件时不要求总线引脚
        self.assertTrue(C3.wiring_problems([1, 2], None))

    def test_project_profile_drives_config_and_reset(self):
        proj = C3.with_wiring(WIRING)
        self.assertEqual(sorted(proj.pins), sorted(WIRING))
        h = hw_config_header(proj)
        self.assertIn("#define FF_PIN_I2C0_SDA 6", h)
        self.assertNotIn("FF_PIN_UART1_TX", h)                  # 默认接线里的回环引脚不再出现
        decl, call = _reset_code(proj)
        self.assertIn("ff_reset_one", decl)
        decl, call = _reset_code(C3.with_wiring({"led.out": 6}))  # 这个项目没接 I2C：不复位器件（宏也不存在）
        self.assertEqual((decl, call), ("", ""))

    def test_design_with_and_without_wiring(self):
        d, probs = parse_design({**DESIGN, "wiring": conns(WIRING)}, profile())
        self.assertEqual(probs, [])
        self.assertEqual(d.wiring, WIRING)
        self.assertEqual(d.to_dict()["wiring"], conns(WIRING))
        self.assertEqual(d.devices_used(profile()), {"imu"})
        d, probs = parse_design({**DESIGN, "wiring": conns({"imu.int": 3})}, profile())
        self.assertIsNone(d)
        self.assertTrue(any("i2c0.sda" in p for p in probs))
        d, probs = parse_design(DESIGN, profile())                   # 不写接线：沿用默认接线
        self.assertEqual((probs, d.wiring), ([], {}))
        self.assertNotIn("wiring", d.to_dict())

    def test_complete_connections_required(self):
        """第 134 条：每根线要写另一端、为什么、自检应测到什么；旧的 {信号: GPIO} 退回；
        不能占用板上现有的线——对话测试里设计把 ADC 放在 GPIO1，而 GPIO1 和 GPIO0 之间有评测用的跳线。"""
        from firmforge.embedded.wiring import board_wiring_lines, parse_connections
        adc = {"signal": "adc.vin", "gpio": 1, "to": "3V3", "why": "读已知电压",
               "expect": {"kind": "adc_mv", "min": 3000, "max": 3400}}
        _, probs = parse_connections([adc], C3, set())
        self.assertTrue(any("GPIO1 板上现在接着" in p and "跳线" in p for p in probs), probs)
        ok, probs = parse_connections([{**adc, "unplug": True}], C3, set())               # 写明先拔掉原来的线：可以
        self.assertEqual(probs, [])
        self.assertIn("先拔掉原来的线：跳线连着 GPIO0", ok[0].line())
        self.assertIn("unplug", ok[0].to_dict())
        ok, probs = parse_connections([{**adc, "gpio": 11}], C3, set())
        self.assertEqual(probs, [])
        self.assertEqual(ok[0].line(), "adc.vin：GPIO11 → 3V3（读已知电压）")
        self.assertTrue(parse_connections({"adc.vin": 11}, C3, set())[1])                  # 旧格式
        for bad, want in (({"to": ""}, "要写 to"), ({"why": ""}, "要写 why"), ({"expect": {}}, "要写 expect"),
                          ({"to": "某处"}, "不知道是什么"), ({"expect": {"kind": "adc_mv", "min": 5, "max": 1}}, "adc_mv"),
                          ({"expect": {"kind": "i2c_ack", "device": "nope"}}, "i2c_ack")):
            with self.subTest(want):
                _, probs = parse_connections([{**adc, "gpio": 11, **bad}], C3, set())
                self.assertTrue(any(want in p for p in probs), probs)
        same = [{"signal": "uart1.tx", "gpio": 0, "to": "uart1.rx", "why": "回环", "expect": {"kind": "loopback", "with": "uart1.rx"}},
                {"signal": "uart1.rx", "gpio": 1, "to": "uart1.tx", "why": "回环", "expect": {"kind": "loopback", "with": "uart1.tx"}}]
        self.assertEqual(parse_connections(same, C3, set())[1], [])                           # 沿用同一根跳线可以
        self.assertTrue(any("GPIO1：跳线连着 GPIO0" in x for x in board_wiring_lines(C3)))
        msg = designer_messages("x", C3, None)[1]["content"]
        self.assertIn("板上现在已经接着的线", msg)                                            # 设计看得到跳线
        self.assertIn("GPIO0：跳线连着 GPIO1", msg)

    def test_designer_sees_previous_wiring(self):
        msgs = designer_messages("x", profile(), None, prior_wiring=WIRING)
        self.assertIn('"i2c0.sda": 6', msgs[-1]["content"])
        self.assertIn("照抄", msgs[-1]["content"])
        self.assertIn("wiring", msgs[0]["content"])                  # 提示词里讲了怎么写接线


class WiredTeam(FakeTeam):
    def __call__(self, messages):
        if "软件架构师" in messages[0]["content"]:
            return "```json\n" + json.dumps({**DESIGN, "wiring": conns(WIRING)}) + "\n```"
        return super().__call__(messages)


def flow(d, team, prof=None, **kw):
    llm = FakeLLM(team)
    f = EmbeddedFlow(llm=llm, settings=SETTINGS, profile=prof or profile(), spec="# 需求\n读 MPU6050", rules=RuleSet(),
                     library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None, **kw)
    return f, llm


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class FlowWiringTest(unittest.TestCase):
    def test_project_wiring_reaches_coder_and_run_record(self):
        with tempfile.TemporaryDirectory() as d:
            f, llm = flow(d, WiredTeam())
            res = f.run()
            info = json.loads((res.run_dir / "wiring.json").read_text(encoding="utf-8"))
            events = read_events(res.run_dir)
        self.assertEqual((info["source"], info["pins"]), ("design", WIRING))
        self.assertEqual([x["name"] for x in info["devices"]], ["imu"])
        self.assertTrue(any(e["type"] == "wiring_ready" for e in events))
        coder = next(c for c in llm.calls if "资深嵌入式 C 工程师" in c["messages"][0]["content"]
                     and "实现模块 imu" in c["messages"][1]["content"])
        self.assertIn("i2c0.sda=GPIO6", coder["messages"][0]["content"])   # 编码 Agent 看到的是项目接线
        self.assertNotIn("uart1.tx", coder["messages"][0]["content"])

    def test_default_wiring_when_design_has_none(self):
        with tempfile.TemporaryDirectory() as d:
            f, _ = flow(d, FakeTeam())
            res = f.run()
            info = json.loads((res.run_dir / "wiring.json").read_text(encoding="utf-8"))
        self.assertEqual(info["source"], "default")
        self.assertEqual(info["pins"], {s: x.value for s, x in profile().pins.items()})

    def test_device_ack_confirms_wiring(self):
        with tempfile.TemporaryDirectory() as d:
            f, _ = flow(d, WiredTeam(), C3)                 # 真实档案：外接器件带用例前复位
            design, probs = parse_design({**DESIGN, "wiring": conns({"i2c0.sda": 4, "i2c0.scl": 5, "imu.int": 3})}, C3)
            self.assertEqual(probs, [])
            f._use_wiring(design)
            f._check_wiring(parse_board_output(f"{BEGIN}\nmain/t.c:1:a:PASS\n"))             # 没有复位失败的记录：应答了
            f._check_wiring(parse_board_output(f"{BEGIN}\nFF_LOG: 用例前复位 imu 失败：器件没有应答\n"))  # 只确认一次
            events = [e for e in read_events(f.rd.path) if e["type"] == "wiring_checked"]
            self.assertEqual(len(events), 1)
            self.assertEqual([(x["device"], x["ok"]) for x in events[0]["data"]["devices"]], [("imu", True)])
            info = json.loads((f.rd.path / "wiring.json").read_text(encoding="utf-8"))
            self.assertTrue(info["checked"][0]["ok"])
            f2, _ = flow(d, WiredTeam(), C3)
            f2._use_wiring(design)
            f2._check_wiring(BoardTestResult(skipped="没板子"))                               # 没跑起来：不下结论
            f2._check_wiring(parse_board_output(f"{BEGIN}\nFF_LOG: 用例前复位 imu 失败：器件没有应答\n"))
            ev = [e for e in read_events(f2.rd.path) if e["type"] == "wiring_checked"]
            self.assertEqual(len(ev), 1)
            x = ev[0]["data"]["devices"][0]
            self.assertEqual((x["ok"], x["why"]), (False, "器件没有应答"))
            self.assertIn("GPIO4", x["pins"])


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class WiringGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeWiringTest")
        set_theme(th.current(False))

    def test_confirm_shows_wiring_and_bad_ack_goes_main(self):
        from firmforge.gui.chat_page import AgentMessage, ChatPage, RunBlock
        page = ChatPage(Path(helpers.ROOT))
        page.on_ask("plan", {"embedded": True, "modules": [{"id": "imu", "layer": "hardware"}], "wiring": WIRING})
        self.assertIn("GPIO6", page.findChildren(AgentMessage)[-1].body.text())
        page.view.config = "embedded/none"
        page.on_event({"type": "run_started", "node_id": None, "data": {"config": "embedded/none", "board": "x"}})
        page.on_event({"type": "wiring_ready", "node_id": "design", "data": {"source": "design", "pins": WIRING}})
        n_main = page.msgs.count()
        page.on_event({"type": "wiring_checked", "node_id": "design", "data": {"devices": [
            {"device": "imu", "part": "MPU6050", "ok": False, "pins": "i2c0.sda=GPIO6", "why": "器件没有应答"}]}})
        self.assertEqual(page.msgs.count(), n_main + 1)                    # 没应答：放在主线上，不折进过程
        blocks = page.findChildren(RunBlock)
        self.assertTrue(blocks and blocks[0].steps >= 1)                   # 接线定下：在过程里

    def test_last_wiring_from_conversation(self):
        from firmforge.gui.chat_page import ChatPage
        page = ChatPage(Path(helpers.ROOT))
        with tempfile.TemporaryDirectory() as d:
            r1, r2 = Path(d) / "r1", Path(d) / "r2"
            r1.mkdir()
            r2.mkdir()
            (r1 / "wiring.json").write_text(json.dumps({"source": "design", "pins": WIRING}), encoding="utf-8")
            (r2 / "wiring.json").write_text(json.dumps({"source": "default", "pins": {"a.b": 1}}), encoding="utf-8")

            class Conv:
                runs = [str(r1), str(r2)]
            page.conv = Conv()
            self.assertEqual(page._last_wiring(), WIRING)                  # 默认接线不算"这个项目定下的"
            page.conv = None
            self.assertIsNone(page._last_wiring())


if __name__ == "__main__":
    unittest.main()

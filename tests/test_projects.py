"""项目（第 134 条第 2 阶段）：每个上板对话一个项目目录，接线按对话分开存、互不相通；
设计定下的接线存进项目、状态是"计划"，改过的线状态回到"计划"；同一个对话里再发需求沿用项目的接线。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.projects import Project, list_projects

ADC = {"signal": "adc.vin", "gpio": 3, "to": "3V3", "why": "读已知电压", "expect": {"kind": "adc_mv", "min": 3000, "max": 3400}}
LED = {"signal": "led.out", "gpio": 6, "to": "元件：LED 正极", "why": "指示", "expect": {"kind": "look"}}

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


class ProjectTest(unittest.TestCase):
    def test_isolated_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            a = Project.create(d)
            b = Project.create(d)
            a.add_spec("每 2 秒读温度和 ADC 电压")
            a.set_wiring([ADC, LED])
            self.assertEqual(Project.load(b.path).wiring, [])                 # 别的对话看不到
            self.assertEqual(a.title, "每 2 秒读温度和 ADC 电压")
            self.assertEqual(a.state("adc.vin"), "计划")
            a.set_state("adc.vin", "实测", "3.29 V")
            a.set_wiring([ADC, {**LED, "gpio": 7}])                           # 改了 LED，ADC 没动
            again = Project.load(a.path)
            self.assertEqual(again.state("adc.vin"), "实测")
            self.assertEqual(again.state("led.out"), "计划")
            with self.assertRaises(ValueError):
                again.set_state("adc.vin", "猜的")
            with self.assertRaises(KeyError):
                again.set_state("nope", "实测")
            a.add_run(Path(d) / "r1")
            a.add_run(Path(d) / "r1")
            self.assertEqual(len(Project.load(a.path).runs), 1)
            self.assertEqual({p.id for p in list_projects(d)}, {a.id, b.id})

    def test_flow_saves_wiring_and_uses_project_wiring(self):
        from firmforge.embedded.design import parse_design
        from firmforge.embedded.flow import EmbeddedFlow
        from firmforge.grounding.rules import RuleSet
        from firmforge.llm import FakeLLM
        from test_embedded_design import profile
        from test_embedded_flow import DESIGN
        from test_multi_agent import SETTINGS
        with tempfile.TemporaryDirectory() as d:
            rec = Project.create(Path(d) / "projects")
            rec.set_wiring([ADC])
            f = EmbeddedFlow(llm=FakeLLM([]), settings=SETTINGS, profile=profile(), spec="x", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d) / "runs", rule_mode="none", project=rec,
                             prior_wiring={"old.sig": 1})
            self.assertEqual(f.prior_wiring, [ADC])                            # 项目里存的完整连线优先
            self.assertIn(str(f.rd.path.resolve()), Project.load(rec.path).runs)
            i2c = [{"signal": s, "gpio": g, "to": t, "why": "MPU6050", "expect": {"kind": "i2c_ack", "device": "imu"}}
                   for s, g, t in (("i2c0.sda", 4, "imu.SDA"), ("i2c0.scl", 5, "imu.SCL"))]
            conns = [{**ADC, "gpio": 11}, *i2c]
            design, probs = parse_design({**DESIGN, "wiring": conns}, profile())
            self.assertEqual(probs, [])
            f._use_wiring(design)
            saved = Project.load(rec.path)
            self.assertEqual(saved.wiring[0]["gpio"], 11)
            self.assertEqual(saved.state("adc.vin"), "计划")


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class ProjectGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication, QSettings
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeProjectTest")
        QSettings().clear()
        from firmforge.gui.theme import current
        from firmforge.gui.widgets import set_theme
        set_theme(current(False))

    def test_new_chat_is_new_project_and_follow_up_reuses_it(self):
        from PySide6.QtCore import Qt
        from firmforge.gui.chat_page import ChatPage
        from test_chat_history import make_run
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            page = ChatPage(root)
            page.flow_combo.setCurrentIndex(page.flow_combo.findData("embedded"))
            started = []
            page.start_embedded = lambda spec="", **k: started.append(spec)
            page.input.setPlainText("每 2 秒读温度")
            page.send()
            rec = page._project_for("每 2 秒读温度")                          # 发送时建了项目（这里模拟开始运行）
            self.assertEqual(started, ["每 2 秒读温度"])
            rd = make_run(root, "20261116-200000-gui", spec="每 2 秒读温度")
            rec.add_run(rd)
            page.run_dir = rd
            page.input.setPlainText("改成每秒一次")
            page.send()
            self.assertEqual(started[-1], "每 2 秒读温度\n\n补充或修改：改成每秒一次")   # 同一个项目里再跑
            page.refresh_sessions()
            rows = [page.sessions.item(i).data(Qt.UserRole) for i in range(page.sessions.count())
                    if page.sessions.item(i).data(Qt.UserRole)]
            self.assertEqual([k for k, _ in rows], ["project"])               # 项目一条，项目里的运行不重复列
            page.new_chat()
            self.assertIsNone(page.project_rec)                                # 新对话是新项目
            page._open_session(next(page.sessions.item(i) for i in range(page.sessions.count())
                                    if page.sessions.item(i).data(Qt.UserRole)))
            self.assertEqual(page.project_rec.id, rec.id)                     # 打开项目可以接着跑
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()

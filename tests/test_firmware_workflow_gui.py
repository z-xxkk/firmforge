"""界面上的上板工作流（DECISIONS 第 110 条）：设计器打开、编辑、检查、试运行 firmware_dev；
对话页写需求时按工作流跑，设计好后停下等确认，调度分配显示在"调度"标签。"""

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import helpers

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False

sys.path.insert(0, str(Path(__file__).resolve().parent))


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class FirmwareWorkflowGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeFwGuiTest")
        set_theme(th.current(False))

    def setUp(self):
        self.errors = []
        self.saved_hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.errors.append(f"{t.__name__}: {v}")

    def tearDown(self):
        sys.excepthook = self.saved_hook

    def test_designer_opens_and_checks_firmware_workflow(self):
        from PySide6.QtCore import Qt
        from firmforge.gui.designer_page import DesignerPage
        d = DesignerPage(helpers.ROOT)
        self.assertEqual(d.wf.name, "module_dev")
        names = [d.wf_combo.itemData(i) for i in range(d.wf_combo.count())]
        self.assertIn("firmware_dev", names)
        d.open_workflow("firmware_dev")
        self.assertEqual(d.wf.engine, "firmware")
        kinds = [d.palette.item(i).data(Qt.UserRole) for i in range(d.palette.count())]
        self.assertIn("agent:designer", kinds)
        self.assertIn("condition", kinds)                               # 解释器支持条件和审查（第 117 条）
        self.assertIn("agent:reviewer", kinds)
        self.assertEqual(d.check(), [])
        d.selected = "repair"
        d.add_step("agent:reviewer")                                    # 检查之后加审查：能执行
        self.assertEqual(d.check(), [])
        d.do_undo()
        d.selected = "write"
        d.add_step("agent:reviewer")                                    # 审查放在检查前：检查不通过、不能保存
        probs = d.check()
        self.assertTrue(any("审查要放在检查之后" in p for p in probs))
        d.do_undo()
        self.assertEqual(d.check(), [])
        d.selected = "repair"                                           # 改尝试次数：照样能执行
        d._set_field("max", 2)
        from firmforge.embedded.firmware_workflow import firmware_plan
        self.assertEqual(firmware_plan(d.wf).max_attempts, 2)
        from PySide6.QtWidgets import QMessageBox
        saved = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        try:
            d.open_workflow("module_dev")                               # 有没保存的改动：问一声，选否就不换
        finally:
            QMessageBox.question = saved
        self.assertEqual(d.wf.name, "firmware_dev")
        self.assertEqual(self.errors, [])

    def test_check_button_and_preview(self):
        # 点"开始检查"才检查；预演用真正的执行代码空跑；改动后提示过期；点问题跳到步骤（2026-11-14）
        from PySide6.QtWidgets import QGraphicsItem
        from firmforge.gui.designer_page import DesignerPage
        d = DesignerPage(helpers.ROOT)
        d.open_workflow("firmware_review")
        self.assertIn("还没检查", d.problems.status.text())
        d.run_check()
        texts = d.problems.texts()
        self.assertTrue(any("结构检查：没发现问题" in t for t in texts))
        self.assertTrue(any("空跑 6 种情况，都能跑完" in t for t in texts))
        self.assertTrue(any("review：按审查意见改" in t for t in texts))                # 审查要求修改那种情况
        self.assertIn("检查完：没发现问题", d.problems.status.text())
        d.selected = "repair"
        d._set_field("max", 2)                                                      # 改动：结果过期
        self.assertIn("可能过期", d.problems.status.text())
        d._set_sub("board_module")                                                  # 只换视图：不算改动
        d.run_check()
        d._set_sub("board_module")
        self.assertNotIn("可能过期", d.problems.status.text())
        d.selected = "write"
        d.add_step("agent:reviewer")                                                # 审查放到检查前：结构问题
        d.run_check()
        texts = d.problems.texts()
        self.assertTrue(any(t.startswith("✗ 引擎") for t in texts))
        self.assertTrue(any("结构有问题，先改好再预演" in t for t in texts))
        d.problems.step_clicked.emit("check")                                      # 点问题：跳到步骤
        self.assertEqual(d.selected, "check")
        d.do_undo()
        d.run_check()
        from firmforge.embedded.fw_preview import preview
        tr = preview(d.wf)[0]
        d.play_trace(tr)                                                            # 画布上依次点亮
        for _ in range(len(tr.step_ids) + 1):
            d._play_next()
        lit = {sid for sid, it in d.canvas.items_by_id.items() if it.state == "done"}
        self.assertTrue({"write", "check", "review"} <= lit)
        self.assertNotIn("look", lit)                                               # 没开人工确认：不经过
        self.assertTrue(d.canvas.items_by_id["check"].flags() & QGraphicsItem.ItemIsMovable)   # 画布上的卡片照样能拖
        d.open_workflow("module_dev") if d.wf == d.saved else None
        self.assertEqual(self.errors, [])

    def test_panels_are_fixed(self):
        # 设计器的面板（步骤箱、属性、代码、检查……）不能拖走、不能浮出窗口；以前浮出去的，打开时放回原位（2026-11-14）
        from PySide6.QtCore import QSettings, Qt
        from PySide6.QtWidgets import QDockWidget
        from firmforge.gui.designer_page import DesignerPage
        d = DesignerPage(helpers.ROOT)
        docks = {dk.objectName(): dk for dk in d.findChildren(QDockWidget)}
        self.assertEqual(set(docks), {"palette", "tree", "props", "code", "check"})
        for dk in docks.values():
            self.assertEqual(dk.features(), QDockWidget.NoDockWidgetFeatures)
        docks["props"].setFloating(True)                                           # 模拟以前版本拖出去的
        d.addDockWidget(Qt.LeftDockWidgetArea, docks["check"])
        d.save_layout()
        d2 = DesignerPage(helpers.ROOT)
        docks2 = {dk.objectName(): dk for dk in d2.findChildren(QDockWidget)}
        self.assertFalse(docks2["props"].isFloating())
        self.assertEqual(d2.dockWidgetArea(docks2["props"]), Qt.RightDockWidgetArea)
        self.assertEqual(d2.dockWidgetArea(docks2["check"]), Qt.BottomDockWidgetArea)
        QSettings().remove("designer/state")

    def test_chat_asks_to_confirm_design(self):
        from firmforge.gui.chat_page import AgentMessage, ChatPage
        page = ChatPage(helpers.ROOT)
        page.on_ask("plan", {"embedded": True, "modules": [{"id": "imu", "layer": "hardware"},
                                                            {"id": "conv", "layer": "logic", "depends_on": ["imu"]}]})
        msg = page.findChildren(AgentMessage)[-1]
        self.assertIn("设计好了", msg.body.text())
        self.assertIn("conv", msg.body.text())
        self.assertEqual(page.pending[0], "plan")
        self.assertTrue(page.tabs.isTabVisible(page.tabs.indexOf(page.why_box)))   # 上板流程也有"调度"标签
        self.assertEqual(self.errors, [])

    def test_try_run_firmware_workflow_from_designer(self):
        from PySide6.QtWidgets import QInputDialog
        from firmforge.gui.app import MainWindow
        from firmforge.orchestrator.workflow import load_workflow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        started = []
        w.chat.start_embedded = lambda **k: started.append((k, w.chat.workflow))
        w.chat.flow_combo.setCurrentIndex(w.chat.flow_combo.findData("classic"))
        saved = QInputDialog.getMultiLineText
        QInputDialog.getMultiLineText = staticmethod(lambda *a, **k: ("读一下温度", True))
        try:
            w._try_run(load_workflow("firmware_dev"))
        finally:
            QInputDialog.getMultiLineText = saved
        self.assertTrue(w.chat.embedded)                                # 切到了上板流程
        self.assertEqual(started[0][0]["spec"], "读一下温度")
        self.assertEqual(started[0][1].name, "firmware_dev")
        w.chat.new_chat()
        self.assertIsNone(w.chat.workflow)                              # 新对话不沿用试运行的草稿
        w.close()
        self.assertEqual(self.errors, [])


@unittest.skipUnless(HAS_QT and helpers.HAS_CC, "需要 PySide6 和 gcc")
class EmbeddedSpecWorkflowTest(unittest.TestCase):
    """对话页写需求：按 firmware_dev 跑；开了"设计后等我确认"就停下问；调度分配进"调度"标签。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import monkey_pages as mp
        from firmforge import llm as llm_mod
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        self.sandbox = mp.make_sandbox()
        self.saved = (llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port)

        def no_idf(*a, **k):
            raise esp_idf.IdfUnavailable("测试：当作没装 ESP-IDF")
        esp_idf.IdfEnv.load = staticmethod(no_idf)
        board_mod.find_port = lambda *a, **k: None
        from firmforge.llm import FakeLLM
        from test_embedded_flow import FakeTeam
        llm_mod.OpenAICompatClient.from_settings = staticmethod(lambda s: FakeLLM(FakeTeam()))

    def tearDown(self):
        from firmforge import llm as llm_mod
        from firmforge.gui.runner import BOARD_USERS
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port = self.saved
        BOARD_USERS.clear()
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_spec_run_follows_workflow_and_asks_after_design(self):
        from test_gui_monkey import wait_for
        from firmforge.gui.runner import EmbeddedBackend
        b = EmbeddedBackend()
        asked, events = [], []
        b.event.connect(events.append)
        b.ask.connect(lambda kind, payload: (asked.append((kind, payload)), b.answer("")))
        b.start(project=self.sandbox, hw="hardware/esp32c3_supermini", runs_dir=self.sandbox / ".ff_runs",
                spec="读 MPU6050", name="t", rule_mode="none", human_review=True)
        info = wait_for(b, 120000)
        self.assertEqual([k for k, _ in asked], ["plan"])
        self.assertTrue(asked[0][1]["embedded"])
        self.assertEqual([m["id"] for m in asked[0][1]["modules"]], ["conv", "imu"])
        assigned = [e for e in events if e["type"] == "node_assigned"]
        self.assertTrue(assigned and assigned[0]["data"]["scores"]["strategy"] == "smart")
        run = json.loads((Path(info["run_dir"]) / "run.json").read_text(encoding="utf-8"))
        self.assertTrue(run["workflow"].startswith("firmware_dev@"))
        self.assertTrue((self.sandbox / ".ff_data" / "firmforge.db").is_file())   # 成绩、经验记在项目里


if __name__ == "__main__":
    unittest.main()

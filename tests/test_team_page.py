"""团队页（2026-11-14 负责人同意的改进）：保存不丢工具、角色名全是中文、成绩按任务类型和版本分开、
改了没保存切换时问一声。在项目副本里跑，不碰真的 agents/。"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


class SummaryTest(unittest.TestCase):
    @unittest.skipUnless(HAS_QT, "需要 PySide6 和 Qt 运行库")
    def test_names_and_summary(self):
        from firmforge.gui.team_page import perf_summary, task_type_name
        self.assertEqual(task_type_name("fw-hardware-first"), "上板·硬件层·第一次写")
        self.assertEqual(task_type_name("fw-logic-repair"), "上板·逻辑层·修改")
        self.assertEqual(task_type_name("protocol"), "纯逻辑·protocol")
        rows = [{"version": "1.0.0", "task_type": "protocol", "attempts": 3, "successes": 3}]
        self.assertIn("都是旧版本 1.0.0 的", perf_summary(rows, "1.1.0"))         # 旧版本的成绩不算到新版本头上
        self.assertNotIn("旧版本", perf_summary(rows, "1.0.0"))
        self.assertEqual(perf_summary([], "1.0.0"), "还没有记录")


@unittest.skipUnless(HAS_QT, "需要 PySide6 和 Qt 运行库")
class TeamPageTest(unittest.TestCase):
    def setUp(self):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        self.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeTeamTest")
        set_theme(th.current(False))
        self.tmp = Path(tempfile.mkdtemp(prefix="ff-team-"))
        shutil.copytree(Path(helpers.ROOT) / "agents", self.tmp / "agents")
        (self.tmp / ".ff_data").mkdir()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)                                   # config.agents_dir() 优先用当前目录的 agents/
        from firmforge.store import Store
        st = Store(str(self.tmp / ".ff_data" / "firmforge.db"))
        st.add_agent_stat("coder-fast", "1.1.0", "fw-hardware-first", first_pass=True, success=True,
                          tokens=10, latency_s=1.0, cost=0.02)
        st.add_agent_stat("coder-fast", "1.1.0", "fw-hardware-repair", first_pass=False, success=False,
                          tokens=10, latency_s=1.0, cost=0.04)
        st.close()

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def page(self):
        from firmforge.gui.team_page import TeamPage
        return TeamPage(self.tmp)

    def row(self, p, name):
        return next(r for r in range(p.table.rowCount()) if p.table.item(r, 0).text() == name)

    def test_role_names_all_chinese(self):
        p = self.page()
        roles = {p.table.item(r, 0).text(): p.table.item(r, 1).text() for r in range(p.table.rowCount())}
        self.assertEqual(roles["designer"], "设计分层")
        self.assertEqual(roles["researcher"], "查资料提炼规则")
        self.assertFalse([r for r in roles.values() if r.isascii()])

    def test_save_keeps_tools_not_in_checkboxes(self):
        from firmforge.agents.registry import Registry
        p = self.page()
        p.table.selectRow(self.row(p, "researcher"))
        self.assertTrue(p.tool_checks["sources"].isChecked())            # 查手册原文这一项现在列出来了
        p.mem_exp.setChecked(not p.mem_exp.isChecked())
        p.save()
        d = Registry().load_dir(self.tmp / "agents").get("researcher")
        self.assertIn("sources", d.toolsets)                              # 原来保存后会丢掉
        self.assertEqual(d.version, "1.0.1")

    def test_perf_by_task_type(self):
        p = self.page()
        p.table.selectRow(self.row(p, "coder-fast"))
        types = [p.perf.item(r, 0).text() for r in range(p.perf.rowCount())]
        self.assertIn("上板·硬件层·第一次写", types)
        self.assertIn("上板·硬件层·修改", types)
        self.assertIn("（当前）", p.perf.item(0, 1).text())
        self.assertIn("2 次", p.table.item(self.row(p, "coder-fast"), 6).text())
        p.table.selectRow(self.row(p, "designer"))
        self.assertIn("还没有记录", p.perf_note.text())

    def test_unsaved_changes_asked(self):
        from PySide6.QtWidgets import QMessageBox
        from firmforge.agents.registry import Registry
        p = self.page()
        p.table.selectRow(self.row(p, "planner"))
        p.mem_exp.setChecked(not p.mem_exp.isChecked())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Cancel):
            p.table.selectRow(self.row(p, "tester"))
        self.assertEqual(p.current().name, "planner")                     # 取消：留在原来那一行，改动还在
        self.assertTrue(p.dirty())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Save):
            p.table.selectRow(self.row(p, "tester"))
        self.assertEqual(Registry().load_dir(self.tmp / "agents").get("planner").version, "1.1.1")
        self.assertEqual(p.current().name, "tester")
        p.mem_kb.setChecked(not p.mem_kb.isChecked())
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Discard):
            p.table.selectRow(self.row(p, "planner"))
        self.assertEqual(Registry().load_dir(self.tmp / "agents").get("tester").version, "1.1.0")   # 放弃：不存
        with mock.patch.object(QMessageBox, "question", side_effect=AssertionError("没改动不该问")):
            p.table.selectRow(self.row(p, "tester"))


if __name__ == "__main__":
    unittest.main()

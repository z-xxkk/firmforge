"""统一的面板布局（DECISIONS 第 133 条）：角色、窗口变化时谁吸收、吸附收起、开关和快捷键、窄窗口自动收起、记住、
标签放不下收进"更多"。规则照 VS Code、Apple HIG、Android 自适应布局来，出处见 gui/panes.py 开头。"""

import os
import tempfile
import unittest
from pathlib import Path

import helpers

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class PaneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgePaneTest")
        from firmforge.gui.theme import current
        from firmforge.gui.widgets import set_theme
        set_theme(current(False))

    def setUp(self):
        from PySide6.QtCore import QSettings
        QSettings().clear()

    def make(self, key="t", auto=900, width=1200):
        from firmforge.gui.panes import PaneSplitter
        sp = PaneSplitter(key, auto_collapse_below=auto)
        for role, mn, d in (("sidebar", 170, 240), ("main", 300, 500), ("inspector", 300, 400)):
            sp.add_pane(QLabel(role), role, minimum=mn, default=d)
        sp.finish()
        sp.resize(width, 600)
        sp.show()
        QApplication.processEvents()
        return sp

    def resize(self, sp, w):
        sp.resize(w, 600)
        QApplication.processEvents()
        return sp.sizes()

    def test_main_absorbs_window_changes(self):
        sp = self.make()
        self.assertEqual(sp.sizes()[0], 240)
        self.assertEqual(self.resize(sp, 1400)[::2], [240, 400])          # 侧栏、右侧面板宽度不变
        self.assertEqual(self.resize(sp, 1100)[::2], [240, 400])
        self.assertEqual(self.resize(sp, 1300)[::2], [240, 400])

    def test_auto_collapse_and_back(self):
        sp = self.make()
        self.assertEqual(self.resize(sp, 850)[0], 0)                      # 窄了自动收起侧栏
        self.assertFalse(sp.is_open("sidebar"))
        self.assertEqual(self.resize(sp, 1100), [240, 444, 400])          # 变宽回来，宽度也还原

    def test_user_choice_wins_until_crossing(self):
        sp = self.make()
        sp.toggle("sidebar")                                              # 宽窗口里自己收起
        self.resize(sp, 1300)
        self.assertFalse(sp.is_open("sidebar"))                           # 不会被自动打开
        self.resize(sp, 850)
        sp.toggle("sidebar")                                              # 窄窗口里自己打开
        QApplication.processEvents()
        self.assertTrue(sp.is_open("sidebar"))
        self.resize(sp, 870)
        self.assertTrue(sp.is_open("sidebar"))                            # 同一档里照用户的
        self.resize(sp, 1200)
        self.resize(sp, 850)
        self.assertFalse(sp.is_open("sidebar"))                           # 跨过一档后按窗口宽度

    def test_snap_collapse_and_restore_width(self):
        sp = self.make()
        sp.moveSplitter(320, 1)
        QApplication.processEvents()
        self.assertEqual(sp.sizes()[0], 320)
        sp.moveSplitter(60, 1)                                            # 拖到最小宽度一半以下：整块收起
        QApplication.processEvents()
        self.assertEqual(sp.sizes()[0], 0)
        sp.toggle("sidebar")
        QApplication.processEvents()
        self.assertEqual(sp.sizes()[0], 320)                              # 打开时恢复收起前的宽度
        sp.toggle("inspector")
        self.assertEqual(sp.sizes()[2], 0)
        sp.toggle("inspector")
        self.assertEqual(sp.sizes()[2], 400)

    def test_remembered_and_double_click_reset(self):
        sp = self.make(key="mem")
        sp.toggle("inspector")
        sp.moveSplitter(300, 1)
        QApplication.processEvents()
        again = self.make(key="mem")
        self.assertEqual(again.sizes()[0], 300)
        self.assertFalse(again.is_open("inspector"))
        again.reset()
        QApplication.processEvents()
        self.assertTrue(again.is_open("inspector"))
        self.assertEqual(again.sizes()[0], 240)

    def test_toggle_button_follows_state(self):
        from firmforge.gui.panes import PaneToggle
        sp = self.make()
        b = PaneToggle(sp, "sidebar", "会话列表", "Ctrl+B")
        self.assertTrue(b.isChecked())
        self.assertIn("隐藏会话列表", b.toolTip())
        b.click()
        QApplication.processEvents()
        self.assertFalse(sp.is_open("sidebar"))
        self.assertFalse(b.isChecked())
        self.assertIn("显示会话列表（Ctrl+B）", b.toolTip())

    def test_overflow_tabs(self):
        from firmforge.gui.panes import OverflowTabs
        t = OverflowTabs()
        for n in ["过程", "检查", "上板", "代码", "规则", "调度", "发给 AI"]:
            t.addTab(QWidget(), n)
        t.setTabVisible(2, False)                                         # 这个流程没有"上板"
        t.resize(700, 300)
        t.show()
        QApplication.processEvents()
        self.assertEqual(t.overflowed(), [])
        self.assertFalse(t.more.isVisible())
        t.resize(250, 300)
        QApplication.processEvents()
        self.assertTrue(t.overflowed())
        self.assertNotIn(2, t.overflowed())                               # 不该有的标签不进"更多"
        self.assertTrue(t.more.isVisible())
        last = t.overflowed()[-1]
        t.more.menu().actions()[-1].trigger()
        QApplication.processEvents()
        self.assertEqual(t.currentIndex(), last)                          # 从"更多"里选：选中并露出来
        self.assertTrue(t.isTabVisible(last))
        self.assertLessEqual(t.minimumSizeHint().width(), 160)            # 不把右侧面板撑宽

    def test_pages_use_roles_and_shortcuts(self):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui.app import MainWindow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        w.show()
        try:
            w.resize(1440, 860)
            QApplication.processEvents()
            for key, roles in (("chat", ["sidebar", "main", "inspector"]), ("hw", ["sidebar", "main", "inspector"]),
                               ("team", ["main", "inspector"]), ("set", ["sidebar", "main"])):
                w.go(key)
                QApplication.processEvents()
                page = w.stack.currentWidget()
                self.assertEqual(page.split.roles, roles, key)
                has_side = "sidebar" in roles
                before = page.split.is_open("sidebar") if has_side else page.split.is_open("inspector")
                page.toggle_pane("sidebar" if has_side else "inspector")
                QApplication.processEvents()
                after = page.split.is_open("sidebar") if has_side else page.split.is_open("inspector")
                self.assertNotEqual(before, after, key)
            w.go("flow")
            QApplication.processEvents()
            self.assertTrue(w.designer.panes.is_open("inspector"))
            w.designer.toggle_pane("inspector")
            self.assertFalse(w.designer.panes.is_open("inspector"))
            w.resize(960, 700)                                           # 最小宽度降到 960，窄了自动收起侧栏
            QApplication.processEvents()
            self.assertEqual(w.width(), 960)
            w.go("chat")
            QApplication.processEvents()
            self.assertEqual(w.minimumWidth(), 960)
        finally:
            w.close()
            QCoreApplication.processEvents()


if __name__ == "__main__":
    unittest.main()

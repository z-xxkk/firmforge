"""桌面软件：纯计算部分（不需要 Qt）和界面冒烟测试（需要 PySide6，在 firmforge-gui 镜像里以 offscreen 方式跑）。"""

import os
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.gui.layout import LAYERS, RunView, flow_positions, grid_positions, layer_depths
from firmforge.orchestrator.workflow import load_workflow

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


def ev(t, node=None, **data):
    return {"ts": 0.0, "type": t, "node_id": node, "data": data}


class LayoutTest(unittest.TestCase):
    def test_depths_and_cycle_safe(self):
        self.assertEqual(layer_depths(["a", "b", "c"], {"c": ["a", "b"], "b": ["a"]}), {"a": 0, "b": 1, "c": 2})
        d = layer_depths(["x", "y"], {"x": ["y"], "y": ["x"]})          # 有环也不会死循环
        self.assertEqual(set(d), {"x", "y"})

    def test_grid_centered(self):
        pos = grid_positions(["a", "b", "c"], {"c": ["a", "b"]}, w=100, gap_x=20)
        self.assertEqual(pos["a"][1], pos["b"][1])
        self.assertAlmostEqual(pos["a"][0] + pos["b"][0] + 100, 0)        # 第 0 层左右对称
        self.assertGreater(pos["c"][1], pos["a"][1])

    def test_flow_positions_branches_to_the_right(self):
        wf = load_workflow()
        steps = list(wf.subflows["module_impl"])
        pos = flow_positions(steps)
        write = next(s for s in steps if s.kind == "parallel")
        for b in write.branches:
            self.assertEqual(pos[b][1], pos[write.id][1])
            self.assertGreater(pos[b][0], pos[write.id][0])

    def test_run_view_follows_events(self):
        v = RunView()
        for e in [ev("run_started", task="t", config="full"),
                  ev("plan_ready", "plan", modules=[{"id": "a", "title": "A", "depends_on": []},
                                                    {"id": "b", "title": "B", "depends_on": ["a"]}]),
                  ev("node_ready", "a", title="A"), ev("node_assigned", "a", agent="coder-fast", role="coder"),
                  ev("verify_started", "a", attempt=1, scope="module"),
                  ev("verify_finished", "a", attempt=1, scope="module", failed_layer="unit_tests"),
                  ev("escalated", "a", level=1), ev("verify_finished", "a", attempt=2, scope="module", failed_layer=None),
                  ev("node_done", "a"), ev("agent_started", "b", agent="tester"),
                  ev("agent_finished", "b", agent="tester", tokens=100), ev("node_failed", "b", verdict="skipped"),
                  ev("run_finished", verdict="fail", tokens=500, cost=0.3)]:
            v.apply(e)
        a, b = v.modules["a"], v.modules["b"]
        self.assertEqual((a.status, a.agent, a.attempt, a.level), ("done", "coder-fast", 1, 1))
        self.assertEqual(a.layers, {l: "pass" for l in LAYERS})
        self.assertEqual((b.status, b.deps), ("skipped", ["a"]))
        self.assertEqual((v.verdict, v.tokens, v.cost), ("fail", 500, 0.3))
        self.assertEqual(v.agents_busy, {})


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication, QSettings
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeTest")
        QSettings().clear()
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = cls.tmp.name

    def window(self):
        from firmforge.gui.app import MainWindow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        return w

    def test_pages_build_and_switch(self):
        w = self.window()
        for i in range(4):
            w.go(i)
            self.app.processEvents()
        self.assertEqual(w.stack.count(), 4)
        self.assertGreater(w.team.table.rowCount(), 5)
        w.close()

    def test_replay_drives_chat_and_graph(self):
        from test_multi_agent import B3, SETTINGS, rb_team, RB
        from firmforge.llm import FakeLLM
        from firmforge.orchestrator.engine import run_multi
        with tempfile.TemporaryDirectory() as d:
            res = run_multi(RB, B3, llm=FakeLLM(rb_team()), settings=SETTINGS, runs_root=d)
            w = self.window()
            page = w.chat
            page.start_replay(res.run_dir)
            page.replay.seek(len(page.replay.events))
            self.app.processEvents()
            self.assertEqual(set(page.graph.cards), {"a", "b", "c"})
            # 缺交叉工具链或 cppcheck 时对应层是 skipped，结论只能是 incomplete（规则 4）
            expected = "pass" if helpers.HAS_CROSS and helpers.HAS_CPPCHECK else "incomplete"
            self.assertEqual(page.view.verdict, expected)
            self.assertGreater(page.msgs.count(), 5)
            page.select_module("a")
            self.assertIn("ringbuf", page.code_view.toPlainText())
            w.close()

    def test_designer_edits_flow_into_code(self):
        w = self.window()
        dz = w.designer
        dz.select("review_gate")
        dz.add_step("human")
        self.assertIn('kind = "human"', dz.code.toPlainText())
        self.assertIn("confirm_1", dz.new_ids)
        self.assertNotEqual(dz.wf.version, dz.saved.version)               # 改了就是草稿，版本号自动加一
        dz.delete("confirm_1")
        dz.do_undo()
        self.assertIsNotNone(dz._get("confirm_1"))
        bad = dz.code.toPlainText().replace('role = "planner"', 'role = "coder"')
        dz.code.setPlainText(bad)
        dz._apply_code()
        self.assertTrue(any("规划" in dz.problems.item(i).text() for i in range(dz.problems.count())))
        w.close()


if __name__ == "__main__":
    unittest.main()

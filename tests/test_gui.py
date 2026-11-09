"""桌面软件：纯计算部分（不需要 Qt）和界面冒烟测试（需要 PySide6，在 firmforge-gui 镜像里以 offscreen 方式跑）。"""

import json
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


    def test_run_view_embedded_flow(self):
        # 新流程每次尝试三段：合规 → 编译 → 测试（DECISIONS 第 100 条）
        v = RunView()
        for e in [ev("run_started", task="r", config="embedded/verified"),
                  ev("plan_ready", "design", modules=[{"id": "ppg", "layer": "hardware", "depends_on": []},
                                                      {"id": "fmt", "layer": "logic", "depends_on": ["ppg"]}]),
                  ev("node_ready", "ppg", title="", layer="hardware"),
                  ev("verify_finished", "ppg", attempt=1, scope="compliance", ok=True, failed_layer=None),
                  ev("verify_started", "ppg", attempt=1, scope="build"),
                  ev("verify_finished", "ppg", attempt=1, scope="build", ok=True, failed_layer=None)]:
            v.apply(e)
        ppg = v.modules["ppg"]
        self.assertTrue(v.embedded)
        self.assertEqual(ppg.layer_keys, ("compliance", "build", "test"))
        self.assertEqual(ppg.layers, {"compliance": "pass", "build": "pass", "test": "running"})
        for e in [ev("verify_finished", "ppg", attempt=1, scope="board_test", ok=False, failed_layer="board_test"),
                  ev("verify_finished", "ppg", attempt=2, scope="compliance", ok=True, failed_layer=None)]:
            v.apply(e)
        self.assertEqual((ppg.attempt, ppg.layers), (2, {"compliance": "pass", "build": "running"}))   # 新的一次从头算
        for e in [ev("node_ready", "fmt", layer="logic"),
                  ev("verify_finished", "fmt", attempt=1, scope="compliance", ok=True, failed_layer=None),
                  ev("verify_finished", "fmt", attempt=1, scope="host_test", ok=True, failed_layer=None),
                  ev("node_done", "fmt"), ev("run_finished", verdict="fail", tokens=9, cost=0.2)]:
            v.apply(e)
        fmt = v.modules["fmt"]
        self.assertEqual((fmt.unit, fmt.layers), ("logic", {"compliance": "pass", "build": "skipped", "test": "pass"}))
        self.assertEqual(ppg.status, "failed")            # 结束时还在进行中的模块按整次结论收尾
        self.assertEqual(v.cost, 0.2)
        # 单 Agent 基线只有编译，没有 node_ready / node_done
        s = RunView()
        for e in [ev("run_started", config="embedded/single"),
                  ev("verify_finished", "m", attempt=1, scope="build", ok=True, failed_layer=None),
                  ev("run_finished", verdict="pass")]:
            s.apply(e)
        self.assertEqual((s.modules["m"].status, s.modules["m"].layers), ("done", {"build": "pass"}))


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
        for i in range(6):
            w.go(i)
            self.app.processEvents()
        self.assertEqual(w.stack.count(), 6)
        self.assertEqual(w.page_keys, ["chat", "hw", "eval", "flow", "team", "set"])
        w.go("hw")
        self.assertIs(w.stack.currentWidget(), w.hardware)
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

    def test_hardware_page_reviews_rules(self):
        # 在界面上确认规则（DECISIONS 第 100 条）：驳回必须写理由；全部表态后才能提交
        w = self.window()
        hp = w.hardware
        self.assertGreater(hp.list.rowCount(), 10)                       # 读出了规则库
        rule = {"id": "R-T-001", "kind": "register", "scope": ["max30102"], "text": "DIE_TEMP_RDY 在 bit1",
                "cite": {"doc": "max30102-ds", "page": 12, "section": "", "quote": "Interrupt Status 2"},
                "tier": "official_manual", "status": "checked", "check": None, "notes": [],
                "steps": [[0, "ok", "资料已登记"], [2, "ok", "找到原文"]]}
        got = {}
        hp.begin_review({"pending": [rule, dict(rule, id="R-T-002")], "escalated": []}, got.update)
        self.assertFalse(hp.submit_btn.isEnabled())
        hp.current = dict(rule, status="pending")
        hp.decide("reject")                                              # 没写理由：不算
        self.assertNotIn("R-T-001", hp.decisions)
        hp.comment.setText("位置写错了")
        hp.decide("reject")
        hp.current = dict(rule, id="R-T-002", status="pending")
        hp.decide("approve")
        self.assertTrue(hp.submit_btn.isEnabled())
        hp.submit()
        self.assertEqual(got, {"R-T-001": ("reject", "位置写错了"), "R-T-002": ("approve", "")})
        self.assertEqual(hp.pending, [])
        w.close()

    def test_board_is_used_by_one_run_at_a_time(self):
        # 对话页的上板运行和评测页不能同时用板子（两边会同时烧录同一个串口）
        from firmforge.embedded.workspace import WorkspaceError
        from firmforge.gui.runner import BOARD_USERS, claim_board

        class Owner:
            pass
        a, b = Owner(), Owner()
        claim_board(a, "对话页的上板运行")
        try:
            with self.assertRaises(WorkspaceError) as cm:
                claim_board(b, "评测")
            self.assertIn("对话页的上板运行", str(cm.exception))
        finally:
            BOARD_USERS.discard(a)
        claim_board(b, "评测")
        BOARD_USERS.discard(b)

    def test_eval_page_reads_results(self):
        w = self.window()
        ep = w.evalp
        base = Path(helpers.ROOT) / ".ff_data"
        base.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as d:
            rows = [{"task": "ledc_pwm", "config": c, "run": 1, "verdict": v, "failed_layer": fl, "cases_passed": cp,
                     "cases_total": 6, "failed_cases": [], "cost": 0.1, "run_dir": "", "note": ""}
                    for c, v, fl, cp in (("single", "pass", "", 6), ("none", "fail", "board_test", 5))]
            (Path(d) / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            ep.out_combo.setEditText(Path(d).relative_to(helpers.ROOT).as_posix())
            ep.refresh()
            last = ep.table.rowCount() - 1
            texts = {ep.table.horizontalHeaderItem(j).text(): (ep.table.item(last, j).text() if ep.table.item(last, j) else "")
                     for j in range(1, ep.table.columnCount())}
            self.assertEqual((texts["single"], texts["none"]), ("1 / 1", "0 / 1"))
            self.assertEqual(ep.layers.item(1, 3).text(), "1")              # none 有 1 次卡在上板测试
            ep._load_dirs()                                                  # 评测结束会重新列目录：自己填的目录不能被换掉
            self.assertEqual(ep.out_combo.currentText(), Path(d).relative_to(helpers.ROOT).as_posix())
        w.close()

    def test_embedded_replay_shows_board_test(self):
        # 回放一次新流程：模块卡片三段，点模块看到上板测试的用例和代码
        from firmforge.target.harness import BEGIN, END
        with tempfile.TemporaryDirectory() as d:
            rd = Path(d) / "run1"
            att = rd / "nodes" / "ppg" / "attempt_1"
            (att / "main").mkdir(parents=True)
            (att / "main" / "ppg.c").write_text("int ppg_ok;\n", encoding="utf-8")
            (att / "report.txt").write_text("共 2 个用例，通过 1", encoding="utf-8")
            (att / "serial.log").write_text(f"{BEGIN}\nt.c:3:读温度:PASS\nt.c:9:等完成标志:FAIL: Expected 0 Was 263\n"
                                            f"2 Tests 1 Failures 0 Ignored\nFAIL\n{END}\n", encoding="utf-8")
            evs = [ev("run_started", task="run1", config="embedded/verified", board="b"),
                   ev("plan_ready", "design", modules=[{"id": "ppg", "layer": "hardware", "depends_on": []}]),
                   ev("node_ready", "ppg", layer="hardware"),
                   ev("verify_finished", "ppg", attempt=1, scope="compliance", ok=True, failed_layer=None),
                   ev("verify_finished", "ppg", attempt=1, scope="build", ok=True, failed_layer=None),
                   ev("verify_finished", "ppg", attempt=1, scope="board_test", ok=False, failed_layer="board_test"),
                   ev("run_finished", verdict="fail", tokens=10, cost=0.01)]
            for i, e in enumerate(evs):
                e["ts"] = 1000.0 + i
                e["run_id"] = "run1"
            (rd / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
            w = self.window()
            page = w.chat
            page.start_replay(rd)
            page.replay.seek(len(page.replay.events))
            self.app.processEvents()
            self.assertTrue(page.view.embedded)
            self.assertEqual(page.view.modules["ppg"].layers, {"compliance": "pass", "build": "pass", "test": "fail"})
            page.select_module("ppg")
            self.assertIn("2 个用例，通过 1", page.board_view.title.text())
            self.assertIn("int ppg_ok", page.code_view.toPlainText())
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

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

    def test_flow_positions_parallel_forks_below(self):
        # 并行画成"分叉再汇合"：分支在并行的下一行并排、以并行为中线，下一步排在分支下面；条件的分支在右侧同一行
        wf = load_workflow()
        steps = list(wf.subflows["module_impl"])
        pos = flow_positions(steps, w=160, h=42, gap=30)
        write = next(s for s in steps if s.kind == "parallel")
        ys = {pos[b][1] for b in write.branches}
        self.assertEqual(len(ys), 1)
        self.assertGreater(ys.pop(), pos[write.id][1])
        xs = [pos[b][0] for b in write.branches]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, pos[write.id][0])           # 整体居中
        self.assertGreater(pos["check"][1], pos[write.branches[0]][1])
        self.assertEqual(pos["check"][0], pos[write.id][0])
        gate = next(s for s in steps if s.kind == "condition")
        self.assertEqual(pos[gate.then[0]][1], pos[gate.id][1])
        self.assertGreater(pos[gate.then[0]][0], pos[gate.id][0])

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
            from firmforge.gui.chat_page import RunBlock
            blocks = page.findChildren(RunBlock)               # 过程消息折叠进去（DECISIONS 第 107 条）
            self.assertTrue(blocks and sum(b.steps for b in blocks) > 5)
            self.assertTrue(all(b.done for b in blocks))
            self.assertFalse(blocks[0].body.isVisible())          # 默认收起
            blocks[0].head.click()
            self.assertTrue(blocks[0].body.isVisibleTo(page))
            self.assertNotIn("费用", page._run_text())            # 对话页不显示花费和 token
            self.assertNotIn("token", page._run_text())
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
            # 导出代码：每个模块最后一次交的代码复制到 <目标>/<运行编号>/<模块>/
            from firmforge.gui.board_view import export_run, final_attempts
            self.assertEqual(final_attempts(rd), {"ppg": att / "main"})
            out = Path(d) / "out"
            files = export_run(rd, out)
            self.assertEqual(files, [out / "run1" / "ppg" / "ppg.c"])
            self.assertEqual(files[0].read_text(encoding="utf-8"), "int ppg_ok;\n")
            self.assertIn("int ppg_ok", page.code_view.toPlainText())
            w.close()

    def test_designer_drag_snap_and_detach(self):
        # 照 Node-RED、n8n 的拖拽（DECISIONS 第 102 条）：拖到连线上插进去、拖到步骤下面接在后面、空白处没连上；
        # 已连好的步骤拖远连线消失、前后自动接上；断开连线；有没连上的步骤不能保存
        from PySide6.QtCore import QMimeData, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from firmforge.gui.graph import STEP_H, STEP_MIME, STEP_W
        w = self.window()
        d = w.designer
        cv = d.canvas
        ids = lambda: [s.id + ("!" if s.detached else "") for s in d.wf.main]          # noqa: E731

        def drop(kind, pt):
            md = QMimeData()
            md.setData(STEP_MIME, kind.encode())
            cv.dropEvent(QDropEvent(QPointF(cv.mapFromScene(pt)), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        e = next(e for e in cv.edges if (e.a, e.b) == ("plan", "confirm_plan"))
        drop("human", e.mid())
        self.assertEqual(ids()[:3], ["plan", "confirm_1", "confirm_plan"])
        drop("human", cv.items_by_id["integrate"].pos() + QPointF(STEP_W / 2, STEP_H + 30))
        self.assertEqual(ids()[-1], "confirm_2")
        drop("agent", QPointF(-900, -900))
        self.assertEqual(ids()[-1], "agent_1!")
        self.assertTrue(any("还没连上" in p for p in d.check(quiet=True)))
        # 拖远：连线隐藏，松手后断开、前后接上
        it = cv.items_by_id["confirm_plan"]
        it._origin = QPointF(it.pos())
        it.setPos(it.pos() + QPointF(400, 0))
        self.assertTrue(all(not e.isVisible() for e in cv.edges if "confirm_plan" in (e.a, e.b)))
        o, it._origin = it._origin, None
        cv._dropped(it, o)
        self.assertIn("confirm_plan!", ids())
        # 拖到 plan 正下方：吸附接上
        it = cv.items_by_id["confirm_plan"]
        it._origin = QPointF(it.pos())
        it.setPos(cv.items_by_id["plan"].pos() + QPointF(5, STEP_H + 25))
        self.assertIsNotNone(cv._preview)                                  # 松手前先显示会接到哪里
        o, it._origin = it._origin, None
        cv._dropped(it, o)
        self.assertEqual(ids()[:2], ["plan", "confirm_plan"])
        # 断开连线：后一步变成没连上
        cv.disconnect_edge.emit("plan", "confirm_plan")
        self.assertIn("confirm_plan!", ids())
        d.do_undo()
        self.assertNotIn("confirm_plan!", ids())
        w.close()

    def test_desktop_shortcut(self):
        # Linux 应用列表里的快捷方式：桌面项指向虚拟环境里的 firmforge-gui，工作目录是项目目录，图标画得出来
        from unittest import mock
        from firmforge.gui import desktop_entry as de
        with tempfile.TemporaryDirectory() as d, mock.patch("pathlib.Path.home", return_value=Path(d)):
            exe = Path(d) / "bin" / "firmforge-gui"
            exe.parent.mkdir()
            exe.write_text("")
            with mock.patch("sys.executable", str(Path(d) / "bin" / "python3")):
                dp, icon = de.install(helpers.ROOT)
            text = dp.read_text(encoding="utf-8")
            self.assertIn(f"Exec={exe}", text)
            self.assertIn(f"Path={Path(helpers.ROOT).resolve()}", text)
            self.assertIn("StartupWMClass=FirmForge", text)
            self.assertGreater(icon.stat().st_size, 1000)
            self.assertEqual(len(de.uninstall()), 2)
            self.assertFalse(dp.exists())

    def test_designer_line_connects_detached_steps(self):
        # 负责人发现的 bug：拉线连上以后还显示"没连上"。用真实鼠标事件：拖远断开，再从小圆点拉线连回
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtTest import QTest
        from firmforge.gui.graph import STEP_H, STEP_W
        w = self.window()
        w.resize(1440, 900)
        w.show()
        w.go("flow")
        d, cv = w.designer, w.designer.canvas
        vp = cv.viewport()

        def drag(a, b):
            QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
            for k in range(1, 9):
                QTest.mouseMove(vp, a + (b - a) * k / 8)
            QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)
        pt = lambda sid, x, y: cv.mapFromScene(cv.items_by_id[sid].pos() + QPointF(x, y))     # noqa: E731
        detached = lambda sid: d._get(sid).detached                                            # noqa: E731
        a = pt("review", 30, 12)
        drag(a, a + QPoint(0, 260))
        self.assertTrue(detached("review"))
        drag(pt("review_gate", STEP_W / 2, STEP_H + 6), pt("review", STEP_W / 2, STEP_H / 2))
        self.assertFalse(detached("review"))
        self.assertIn("review", d._get("review_gate").then)
        a = pt("check", 30, 12)                                               # 从没连上的步骤往外拉线
        drag(a, a + QPoint(-40, 320))                                         # 拖到下方空白处（靠近别的步骤会被吸附过去）
        self.assertTrue(detached("check"))
        drag(pt("check", STEP_W / 2, STEP_H + 6), pt("repair", STEP_W / 2, STEP_H / 2))
        self.assertFalse(detached("check") or detached("repair"))
        self.assertFalse([p for p in d.check(quiet=True) if "没连上" in p])
        w.close()

    def test_designer_join_parallel_branch(self):
        # 负责人发现的 bug：想把 test 接到 code 旁边（同一个并行的分支），却被吸到主干上。
        # 拖到分支步骤的左右两边，或从分支步骤拉线，都是并列成为同一个并行的分支；拖到汇合线上是并行之后的下一步
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtTest import QTest
        from firmforge.gui.graph import STEP_H, STEP_W
        w = self.window()
        w.resize(1440, 900)
        w.show()
        w.go("flow")
        d, cv = w.designer, w.designer.canvas
        vp = cv.viewport()

        def drag(a, b):
            QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
            for k in range(1, 9):
                QTest.mouseMove(vp, a + (b - a) * k / 8)
            QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)
        pt = lambda sid, x, y: cv.mapFromScene(cv.items_by_id[sid].pos() + QPointF(x, y))     # noqa: E731
        branches = lambda: d._get("write").branches                                            # noqa: E731

        def away():
            a = pt("test", 30, 12)
            drag(a, a + QPoint(-40, 330))
            self.assertEqual(branches(), ("code",))
        away()
        drag(pt("test", 30, 12), pt("code", STEP_W + 55, 12))                  # 右边
        self.assertEqual(branches(), ("code", "test"))
        away()
        drag(pt("test", 30, 12), pt("code", -STEP_W - 25 + 30, 12))            # 左边也一样
        self.assertEqual(set(branches()), {"code", "test"})
        away()
        e = next(e for e in cv.edges if e.logical == ("write", "check"))       # 汇合线上：并行做完以后的下一步
        drag(pt("test", 30, 12), cv.mapFromScene(e.mid()) + QPoint(30 - STEP_W // 2, 12))
        self.assertEqual(branches(), ("code",))
        self.assertEqual([s.id for s in d._steps("module_impl") if s.id in ("write", "test", "check")],
                         ["write", "test", "check"])
        self.assertFalse(d._get("test").detached)
        away()
        drag(pt("code", STEP_W / 2, STEP_H + 6), pt("test", STEP_W / 2, STEP_H / 2))   # 从 code 拉线
        self.assertEqual(branches(), ("code", "test"))
        self.assertFalse(d._get("test").detached)
        self.assertEqual(d.check(quiet=True), [])
        w.close()

    def test_designer_explored_bugs(self):
        # 2026-11-10 自查出的 6 个问题（真实鼠标事件）：
        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
        from PySide6.QtGui import QDropEvent
        from PySide6.QtTest import QTest
        from firmforge.gui.graph import STEP_H, STEP_MIME, STEP_W
        w = self.window()
        w.resize(1440, 900)
        w.show()
        w.go("flow")
        d, cv = w.designer, w.designer.canvas
        vp = cv.viewport()
        base = d.wf

        def reset():
            d.wf = base
            d.undo.clear()
            d.selected = None
            cv.user_view = False
            d.render_all()

        def drag(a, b):
            QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, a)
            for k in range(1, 9):
                QTest.mouseMove(vp, a + (b - a) * k / 8)
            QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, b)

        def drop(kind, pt):
            md = QMimeData()
            md.setData(STEP_MIME, kind.encode())
            cv.dropEvent(QDropEvent(QPointF(cv.mapFromScene(pt)), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))

        def overlaps():
            its = list(cv.items_by_id.values())
            return [(a.step.id, b.step.id) for i, a in enumerate(its) for b in its[i + 1:]
                    if abs(a.pos().x() - b.pos().x()) < STEP_W - 10 and abs(a.pos().y() - b.pos().y()) < STEP_H - 6]
        pt = lambda sid, x=30, y=12: cv.mapFromScene(cv.items_by_id[sid].pos() + QPointF(x, y))     # noqa: E731
        sub = lambda: [s.id for s in d._steps("module_impl")]                                       # noqa: E731
        # ① 小范围挪过的步骤，再往它前面插步骤，不能叠在一起（连着的步骤不记手动位置）
        a = pt("check")
        drag(a, a + QPoint(25, 10))
        self.assertIsNone(d._get("check").pos)
        drop("human", next(e for e in cv.edges if e.logical == ("write", "check")).mid())
        self.assertEqual(overlaps(), [])
        # ② 第一步拖走以后能放回最前面（拖到现在第一步的正上方）
        reset()
        a = pt("plan")
        drag(a, a + QPoint(-260, 60))
        self.assertTrue(d._get("plan").detached)
        drag(pt("plan"), cv.mapFromScene(cv.items_by_id["confirm_plan"].pos() + QPointF(30, -STEP_H - 8)))
        self.assertEqual([s.id for s in d.wf.main][:2], ["plan", "confirm_plan"])
        self.assertFalse(d._get("plan").detached)
        # ③ 流程全拆开以后，放回框里就是第一步
        reset()
        for sid in ("review", "review_gate", "repair", "check", "test", "code", "write"):
            d._step_dropped(sid, *d._parking("module_impl"), None, True)
        self.assertEqual(overlaps(), [])
        self.assertIn("module_impl", cv.empty_flows)
        frame = next(r for n, r in cv.regions if n == "module_impl")
        drag(pt("write", 80, 10), cv.mapFromScene(frame.center()))
        self.assertFalse(d._get("write").detached)
        # ④ 把主流程的步骤拖进子流程的框里：不接上，也不压在别的步骤上
        reset()
        drag(pt("integrate"), cv.mapFromScene(cv.items_by_id["repair"].pos() + QPointF(35, STEP_H + 42)))
        self.assertTrue(d._get("integrate").detached)
        self.assertEqual(overlaps(), [])
        # ⑤ 拖到还没有分支的并行下面：成为它的分支；空并行保存前会被检查出来
        reset()
        drop("parallel", next(e for e in cv.edges if (e.a, e.b) == ("check", "repair")).mid())
        self.assertTrue(any("并行还没有分支" in p for p in d.check(quiet=True)))
        d._apply_code()                                                    # 空并行写进代码再读回来不报错
        self.assertIn("parallel_1", sub())
        drop("agent", cv.items_by_id["parallel_1"].pos() + QPointF(STEP_W / 2, STEP_H + 20))
        self.assertEqual(d._get("parallel_1").branches, ("agent_1",))
        # ⑥ 并行被拖走：分支跟着它，不会画成主干上的步骤
        reset()
        a = pt("write", 80, 10)
        drag(a, a + QPoint(430, 60))
        self.assertTrue(d._get("write").detached)
        self.assertEqual(d._get("write").branches, ("code", "test"))
        self.assertEqual(cv.floating, {"write", "code", "test"})
        self.assertFalse([e for e in cv.edges if not e.branch and {"code", "test"} & {e.a, e.b}])
        self.assertEqual(overlaps(), [])
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

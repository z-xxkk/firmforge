"""界面上的三处亮点展示（DECISIONS 第 105 条）：修正时间线、手册原页框出引用原文、资料与实测对不上的对照卡。

核心部分（定位原文、冲突核对、时间线数据）不需要界面；界面部分在 offscreen 下跑，用假数据或项目里已有的运行记录。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.grounding.conflicts import load_conflicts, measured_ids
from firmforge.grounding.pagepic import Word, page_words, parse_bbox, quote_boxes, raw_pdf, render_page
from firmforge.grounding.sources import Document, SourceLibrary

ROOT = Path(helpers.ROOT)
HW = ROOT / "hardware" / "esp32c3_supermini"
LIB_DIR = ROOT / ".ff_sources" / "library"
RAW = ROOT / ".ff_sources" / "raw"
HAS_POPPLER = shutil.which("pdftoppm") is not None and shutil.which("pdftotext") is not None


def words(*lines):
    """每行一组单词，单词等宽排开。"""
    out = []
    for row, text in enumerate(lines):
        x = 50.0
        for w in text.split():
            out.append(Word(x, 100 + row * 14, x + 6 * len(w), 110 + row * 14, w))
            x += 6 * len(w) + 4
    return out


class QuoteBoxTest(unittest.TestCase):
    def test_quote_on_one_line_and_across_lines(self):
        ws = words("Temperature Enable (TEMP_EN) This is a", "self-clearing bit which, when set, starts")
        one = quote_boxes(ws, "Temperature Enable (TEMP_EN)")
        self.assertEqual(len(one), 1)
        self.assertEqual(one[0].x0, 50.0)
        two = quote_boxes(ws, "This is a self-clearing bit")
        self.assertEqual(len(two), 2)                       # 跨两行：每行一个框
        self.assertLess(two[0].y0, two[1].y0)
        self.assertGreater(two[0].x0, 50.0)                 # 第一行从 This 开始，不是行首

    def test_hyphen_at_line_end_and_ellipsis(self):
        ws = words("the interrupt status regis-", "ter is cleared when read", "other words here", "final part")
        self.assertEqual(len(quote_boxes(ws, "status register is cleared")), 2)
        boxes = quote_boxes(ws, "interrupt status…final part")
        self.assertEqual(len(boxes), 2)
        self.assertEqual(boxes[-1].y0, 100 + 3 * 14)

    def test_prefix_fallback_and_no_match(self):
        ws = words("The default value of the register is 0x68 and more")
        self.assertTrue(quote_boxes(ws, "The default value of the register is 0x68. (see table 3 on next page)"))
        self.assertEqual(quote_boxes(ws, "nothing like this is on the page"), [])
        self.assertEqual(quote_boxes([], "anything"), [])

    def test_parse_bbox(self):
        xml = ('<html><body><doc><page width="612.000000" height="792.000000">'
               '<word xMin="54.0" yMin="606.5" xMax="70.1" yMax="619.5">This</word>'
               '<word xMin="72.0" yMin="606.5" xMax="80.0" yMax="619.5">&amp;</word></page></doc></body></html>')
        pw = parse_bbox(xml)
        self.assertEqual((pw.width, pw.height), (612.0, 792.0))
        self.assertEqual([w.text for w in pw.words], ["This", "&"])
        with self.assertRaises(ValueError):
            parse_bbox("<html></html>")

    def test_missing_tools_give_reason_not_fake_image(self):
        # 没装 poppler：说明原因，不生成图片
        saved = {k: os.environ.get(k) for k in ("FF_PDFTOPPM", "FF_PDFTOTEXT")}
        os.environ["FF_PDFTOPPM"] = os.environ["FF_PDFTOTEXT"] = "no-such-tool-ff"
        try:
            with tempfile.TemporaryDirectory() as d:
                pdf = Path(d) / "x.pdf"
                pdf.write_bytes(b"%PDF-1.4\n")
                out = Path(d) / "p.png"
                self.assertIn("pdftoppm", render_page(pdf, 1, out))
                self.assertFalse(out.exists())
                self.assertIn("pdftotext", page_words(pdf, 1))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_raw_pdf_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            hw, raw = Path(d) / "hw", Path(d) / "raw"
            hw.mkdir()
            raw.mkdir()
            (hw / "sources.toml").write_text('[[doc]]\nid = "a"\nkind = "pdf"\nfile = "a.pdf"\n'
                                             '[[doc]]\nid = "b"\nkind = "text"\nfile = "b.txt"\n', encoding="utf-8")
            self.assertIsNone(raw_pdf(hw, raw, "a"))          # 原件不在
            (raw / "a.pdf").write_bytes(b"%PDF")
            (raw / "b.txt").write_text("x", encoding="utf-8")
            self.assertEqual(raw_pdf(hw, raw, "a"), raw / "a.pdf")
            self.assertIsNone(raw_pdf(hw, raw, "b"))          # 不是 PDF
            self.assertIsNone(raw_pdf(hw, raw, "c"))

    @unittest.skipUnless(HAS_POPPLER and (RAW / "max30102.pdf").is_file(), "需要 poppler 和 MAX30102 手册原件")
    def test_real_datasheet_page(self):
        pdf = raw_pdf(HW, RAW, "max30102-ds")
        pw = page_words(pdf, 22)
        boxes = quote_boxes(pw.words, "This bit clears automatically back to zero")
        self.assertEqual(len(boxes), 2)
        self.assertTrue(all(0 <= b.x0 < b.x1 <= pw.width for b in boxes))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(render_page(pdf, 22, Path(d) / "p.png"), "")
            self.assertTrue((Path(d) / "p.png").stat().st_size > 1000)


class ConflictTest(unittest.TestCase):
    @unittest.skipUnless((LIB_DIR / "sources.json").is_file(), "需要资料缓存")
    def test_project_conflicts_verified(self):
        items = load_conflicts(HW, SourceLibrary.load(LIB_DIR))
        self.assertEqual([c.id for c in items], ["C-001", "C-002"])
        for c in items:
            self.assertEqual(c.problems, (), c.id)            # 原句真的在手册那一页，实测记录存在
            self.assertIn(c.record, measured_ids(HW))

    def test_problems_are_reported(self):
        with tempfile.TemporaryDirectory() as d:
            hw = Path(d)
            lib = SourceLibrary(hw / "lib")
            lib.add(Document(id="d", title="t", kind="text", tier="user", pages=("The default value is 0x68.",)))
            (hw / "measured.md").write_text("# 实测\n\n## M-001 编号（2026-11-03）\n", encoding="utf-8")
            item = {"id": "C-1", "title": "x", "document": {"doc": "d", "page": 1, "quote": "is 0x68", "claim": "0x68"},
                    "measured": {"record": "M-001", "claim": "0x70"}}
            bad = [item, {**item, "id": "C-2", "document": {**item["document"], "quote": "is 0x99"}},
                   {**item, "id": "C-3", "measured": {"record": "M-009", "claim": "?"}},
                   {**item, "id": "C-4", "document": {**item["document"], "doc": "nope"}}]
            (hw / "conflicts.json").write_text(json.dumps(bad), encoding="utf-8")
            got = {c.id: c.problems for c in load_conflicts(hw, lib)}
            self.assertEqual(got["C-1"], ())
            self.assertIn("原句不在", got["C-2"][0])
            self.assertIn("M-009", got["C-3"][0])
            self.assertIn("找不到资料", got["C-4"][0])
            self.assertIn("没有核对原句", load_conflicts(hw, None)[0].problems[-1])
            (hw / "conflicts.json").write_text('[{"id": "C-1"}]', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_conflicts(hw, lib)
            (hw / "conflicts.json").unlink()
            self.assertEqual(load_conflicts(hw, lib), [])


def ev(t, node, **data):
    return {"type": t, "node_id": node, "data": data}


EMB_EVENTS = [
    ev("node_ready", "m", title="x"),
    ev("verify_finished", "m", attempt=1, scope="compliance", ok=True, failed_layer=None),
    ev("verify_started", "m", attempt=1, scope="build"),
    ev("verify_finished", "m", attempt=1, scope="build", ok=True, failed_layer=None),
    ev("verify_finished", "m", attempt=1, scope="board_test", ok=False, failed_layer="board_test"),
    ev("diagnosis", "m", attempt=1, blame="test", instruction="改测试里的读回方式", evidence="", rule=""),
    ev("agent_started", "m", agent="tester", role="tester", attempt=2, purpose="改测试"),
    ev("verify_finished", "other", attempt=1, scope="build", ok=False, failed_layer="build"),
    ev("verify_finished", "m", attempt=2, scope="compliance", ok=True, failed_layer=None),
    ev("verify_finished", "m", attempt=2, scope="build", ok=True, failed_layer=None),
    ev("verify_finished", "m", attempt=2, scope="board_test", ok=True, failed_layer=None),
    ev("node_done", "m", verdict="pass", attempts=2),
]


def make_run(d: Path) -> Path:
    from firmforge.target.harness import BEGIN, END
    rd = d / "run"
    for n, (code, test, log) in enumerate((("int a;\n", "T1\n", "main/t.c:1:case_a:PASS\nmain/t.c:2:case_b:FAIL:bad\n"),
                                           ("int a;\n", "T1\nT2\nT3\n", "main/t.c:1:case_a:PASS\nmain/t.c:2:case_b:PASS\n")), 1):
        a = rd / "nodes" / "m" / f"attempt_{n}"
        (a / "main" / "test").mkdir(parents=True)
        (a / "main" / "m.c").write_text(code, encoding="utf-8")
        (a / "main" / "test" / "test_m.c").write_text(test, encoding="utf-8")
        (a / "serial.log").write_text(f"{BEGIN}\n{log}2 Tests 0 Failures 0 Ignored\nOK\n{END}\n", encoding="utf-8")
    (rd / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in
                                             [{"type": "run_started", "node_id": None, "data": {"config": "embedded/none"}},
                                              {"type": "plan_ready", "node_id": "design",
                                               "data": {"modules": [{"id": "m", "title": "x", "layer": "hardware"}]}}]
                                             + EMB_EVENTS), encoding="utf-8")
    return rd


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class TimelineDataTest(unittest.TestCase):
    def test_embedded_repair(self):
        from firmforge.gui.board_view import repair_timeline
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(Path(d))
            steps = repair_timeline(EMB_EVENTS, "m", rd)
            self.assertEqual([(s.kind, s.attempt, s.status) for s in steps],
                             [("attempt", 1, "fail"), ("diagnosis", 1, "warn"), ("fix", 2, "info"),
                              ("attempt", 2, "pass"), ("end", 2, "pass")])
            a1, diag, fix, a2, end = steps
            self.assertEqual(a1.layers, {"compliance": "pass", "build": "pass", "board_test": "fail"})
            self.assertIn("上板✗", a1.lines[0])
            self.assertIn("上板用例 1 / 2", a1.lines)
            self.assertTrue(any("case_b" in ln for ln in a1.lines))
            self.assertEqual(diag.title, "诊断：错在测试")
            self.assertEqual(fix.title, "交给测试 Agent")
            self.assertIn("比上次改了代码 0 行、测试 2 行", a2.lines)
            self.assertEqual(end.title, "结果：通过")

    def test_old_flow_events_and_special_steps(self):
        from firmforge.gui.board_view import repair_timeline
        events = [ev("verify_finished", "crc", attempt=1, verdict="fail", failed_layer="L5", scope="module"),
                  ev("diagnosis", "crc", attempt=1, blame="hardware", instruction="", evidence="接线可能松了", rule="R-1"),
                  ev("escalated", "crc", level="strong", note="换强档模型"),
                  ev("rollback", "crc", from_attempt=3, to_attempt=1),
                  ev("human_wait", "crc", reason="诊断为硬件或接线问题"),
                  ev("verify_finished", "crc", attempt=2, verdict="skipped", failed_layer="skipped", scope="module"),
                  ev("node_failed", "crc", verdict="hardware")]
        steps = repair_timeline(events, "crc")
        kinds = [(s.kind, s.status) for s in steps]
        self.assertEqual(kinds, [("attempt", "fail"), ("diagnosis", "fail"), ("escalated", "warn"), ("rollback", "warn"),
                                 ("human", "fail"), ("attempt", "skipped"), ("end", "fail")])
        self.assertIn("涉及规则 R-1", steps[1].lines)
        self.assertEqual(steps[1].title, "诊断：错在硬件或接线")
        self.assertEqual(steps[3].title, "回到第 1 次的代码")
        self.assertEqual(steps[-1].title, "结果：等人检查硬件")
        self.assertEqual(repair_timeline(events, "nope"), [])

    def test_soft_wrap(self):
        from firmforge.gui.board_view import soft_wrap
        self.assertEqual(soft_wrap("a_b/c").replace("​", ""), "a_b/c")
        self.assertIn("_​", soft_wrap("gpio_set"))


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class HighlightsGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeHighlightsTest")
        set_theme(th.current(False))

    def setUp(self):
        self.errors = []
        self.saved_hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.errors.append(f"{t.__name__}: {v}")

    def tearDown(self):
        sys.excepthook = self.saved_hook

    def test_chat_timeline_and_pick_attempt(self):
        # 回放一次运行：过程标签画出时间线；点第 1 次，上板标签换成第 1 次的结果，换模块后回到最新一次
        from firmforge.gui.chat_page import ChatPage
        from firmforge.runs import read_events
        with tempfile.TemporaryDirectory() as d:
            rd = make_run(Path(d))
            page = ChatPage(Path(d))
            page.run_dir = rd
            for e in read_events(rd):
                page.on_event(e)
            page.select_module("m")
            self.assertEqual([s.kind for s in page.timeline.steps], ["attempt", "diagnosis", "fix", "attempt", "end"])
            self.assertEqual(page.timeline.selected, 2)
            self.assertIn("第 2 次", page.board_view.title.text())
            page.timeline.attempt_clicked.emit(1)
            self.assertEqual(page.timeline.selected, 1)
            self.assertIn("第 1 次", page.layer_title.text())
            self.assertIn("第 1 次", page.board_view.title.text())
            self.assertIn("通过 1", page.board_view.title.text())
            page.on_event(ev("node_ready", "m", title="x"))     # 运行还在继续：仍然看着点选的那一次
            self.assertEqual(page.timeline.selected, 1)
            page.clear_graph()
            self.assertIsNone(page.picked_attempt)
            self.assertEqual(page.seen_events, [])
        self.assertEqual(self.errors, [])

    def test_open_history_shows_whole_run_at_once(self):
        # 点开历史运行：直接显示完整记录和最终状态（不是回放动画）；想看过程再点"从头回放"（DECISIONS 第 106 条）
        from PySide6.QtCore import Qt
        from firmforge.gui.chat_page import ChatPage
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            rd = make_run(proj)
            runs = proj / ".ff_runs"
            runs.mkdir()
            rd = rd.rename(runs / "20261111-120000-eval-x")
            page = ChatPage(proj)
            page.refresh_sessions()
            item = next(page.sessions.item(i) for i in range(page.sessions.count())
                        if page.sessions.item(i).data(Qt.UserRole))
            page._open_session(item)
            self.assertIsNone(page.replay)
            self.assertFalse(page.replay_bar.isVisibleTo(page))
            self.assertTrue(page.replay_btn.isVisibleTo(page))
            self.assertTrue(page.title.text().startswith("运行 "))
            self.assertEqual(page.view.modules["m"].status, "done")        # 最终状态
            self.assertEqual(len(page.seen_events), len(EMB_EVENTS) + 2)
            self.assertEqual(page.graph.selected, "m")                      # 自动选中模块，直接看到修正过程
            self.assertIs(page.tabs.currentWidget(), page.timeline)
            from firmforge.gui.chat_page import RunBlock
            blocks = page.findChildren(RunBlock)
            self.assertTrue(blocks and blocks[-1].done)                     # 过程折叠，结束后标"过程 · N 步"
            self.assertIn("过程", blocks[-1].head.text())
            (rd / "judge_report.txt").write_text("共 4 个用例，通过 3，失败 1", encoding="utf-8")
            page.open_run(rd)                                               # 评测运行：显示隐藏测试判定
            from firmforge.gui.chat_page import AgentMessage
            texts = [m.body.text() for m in page.findChildren(AgentMessage)]
            self.assertTrue(any("隐藏测试判定" in t and "通过 3" in t for t in texts))
            self.assertFalse(any("元" in t and "费用" in t for t in texts))
            page.replay_btn.click()                                         # 从头回放
            self.assertIsNotNone(page.replay)
            self.assertTrue(page.replay_bar.isVisibleTo(page))
            self.assertFalse(page.replay_btn.isVisibleTo(page))
            page.replay.pause()
            page.open_run(rd)                                               # 回放中再打开：停掉回放，直接显示
            self.assertIsNone(page.replay)
            (rd / "events.jsonl").write_text('{"type": "run_sta\n{"bad": 1}\n', encoding="utf-8")
            page.open_run(rd)                                               # 记录坏了：说明原因，不崩
        self.assertEqual(self.errors, [])

    def test_hardware_page_viewer_and_conflicts(self):
        from PySide6.QtWidgets import QFrame
        from firmforge.gui.hardware_page import HardwarePage
        page = HardwarePage(ROOT)
        cards = [f for f in page.prof_box.findChildren(QFrame) if f.objectName() == "ConflictCard"]
        if (LIB_DIR / "sources.json").is_file():
            self.assertEqual(len(cards), 2)
        rule = next((r for r in page.rules if r.get("cite", {}).get("doc") == "max30102-ds" and r["cite"].get("page")), None)
        if rule is None:
            self.skipTest("规则库里没有引用 MAX30102 手册的规则")
        page.current = rule
        page.open_cited_page()
        v = page.viewers[-1]
        self.assertIn(f"第 {rule['cite']['page']} 页", v.windowTitle())
        if HAS_POPPLER and (RAW / "max30102.pdf").is_file():
            self.assertTrue(v.has_image)
            self.assertIn("绿框", v.note.text())
        v.close()
        self.assertEqual(self.errors, [])

    def test_viewer_without_raw_pdf_shows_text(self):
        # 原件不在（例如评委没下载手册）：不报错，显示按版面排列的文字并说明原因
        from firmforge.gui.page_viewer import PageViewer
        with tempfile.TemporaryDirectory() as d:
            hw = Path(d) / "hw"
            hw.mkdir()
            (hw / "sources.toml").write_text('[[doc]]\nid = "x"\nkind = "pdf"\nfile = "x.pdf"\n', encoding="utf-8")
            v = PageViewer(Path(d), hw, "x", "某手册", 3, "some quote", "版面文字")
            self.assertFalse(v.has_image)
            self.assertIn("原件不在", v.note.text())
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

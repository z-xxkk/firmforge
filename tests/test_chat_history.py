"""对话页左边的历史列表：对话页里写需求跑的上板运行总是列出，标题用需求；评测和命令行的运行勾上才列。

2026-11-16 负责人发现：上板流程发出需求就直接运行、不生成对话记录，而"评测和命令行的运行"默认不列，
所以软件里跑过的上板对话在列表里一条都看不到。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


def make_run(root: Path, name: str, *, spec: str | None = None, designer_spec: str | None = None) -> Path:
    rd = root / ".ff_runs" / name
    rd.mkdir(parents=True)
    (rd / "events.jsonl").write_text(json.dumps({"type": "run_started", "data": {}}) + "\n", encoding="utf-8")
    if spec is not None:
        (rd / "spec.md").write_text(spec, encoding="utf-8")
    if designer_spec is not None:                        # 旧运行没有 spec.md：需求只在设计 Agent 的任务单里
        (rd / "llm_calls").mkdir()
        body = f"## 意图\n\n拆模块\n\n## 附件\n\n### 需求文档\n\n{designer_spec}\n\n### 别的附件\n\nx"
        (rd / "llm_calls" / "0001.json").write_text(json.dumps(
            {"agent": "researcher", "messages": [{"role": "user", "content": "查资料"}]}), encoding="utf-8")
        (rd / "llm_calls" / "0002.json").write_text(json.dumps(
            {"agent": "designer", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": body}]},
            ensure_ascii=False), encoding="utf-8")
    return rd


class TitleTest(unittest.TestCase):
    def test_gui_run_names_and_titles(self):
        from firmforge.gui.chat_page import is_gui_run, run_title
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = make_run(root, "20261116-140631-gui", spec="每 2 秒读一次芯片温度和 ADC 引脚上的电压，记录开机以来的最高温度")
            b = make_run(root, "20261115-222756-gui-2", designer_spec="读 MPU6050 加速度")
            c = make_run(root, "20261116-092943-eval-max30102_fifo-none-3")
            e = make_run(root, "20261116-000000-gui")
            self.assertTrue(is_gui_run(a) and is_gui_run(b) and is_gui_run(e))
            self.assertFalse(is_gui_run(c))
            self.assertTrue(run_title(a).startswith("每 2 秒读一次芯片温度和 ADC"))
            self.assertTrue(run_title(a).endswith("…"))
            self.assertEqual(len(run_title(a)), 25)                            # 24 个字加省略号
            self.assertEqual(run_title(b), "读 MPU6050 加速度")
            self.assertEqual(run_title(e), "运行 20261116-000000-gui")          # 什么都没有：退回编号


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class ListTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication, QSettings
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeHistoryTest")
        QSettings().clear()
        from firmforge.gui.theme import current
        from firmforge.gui.widgets import set_theme
        set_theme(current(False))

    def test_gui_runs_listed_by_default(self):
        from PySide6.QtCore import Qt
        from firmforge.gui.chat_page import ChatPage
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            make_run(root, "20261116-140631-gui", spec="每 2 秒读温度")
            make_run(root, "20261116-092943-eval-max30102_fifo-none-3")
            page = ChatPage(root)
            page.show_cli_runs.setChecked(False)
            page.refresh_sessions()

            def items():
                return [(page.sessions.item(i).text(), page.sessions.item(i).data(Qt.UserRole))
                        for i in range(page.sessions.count()) if page.sessions.item(i).data(Qt.UserRole)]
            got = items()
            self.assertEqual([t for t, _ in got], ["每 2 秒读温度"])
            self.assertEqual(got[0][1][0], "run")                           # 点开照常回放
            page.show_cli_runs.setChecked(True)
            got = items()
            self.assertEqual(len(got), 2)
            self.assertTrue(any(t.startswith("运行 20261116-092943-eval") for t, _ in got))
            page.deleteLater()

    def test_open_run_shows_your_message(self):
        """打开历史运行：标题用需求开头，最上面是你当时发的需求（负责人 2026-11-16 发现看不到自己发的消息）。"""
        from PySide6.QtWidgets import QLabel
        from firmforge.gui.chat_page import ChatPage
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            rd = make_run(root, "20261116-140631-gui", spec="每 2 秒读一次芯片温度，掉电后打印上次保存的最高温度")
            page = ChatPage(root)
            for open_ in (page.open_run, page.start_replay):
                with self.subTest(open_.__name__):
                    open_(rd)
                    if page.replay is not None:
                        page.replay.pause()
                    self.assertTrue(page.title.text().startswith("每 2 秒读一次芯片温度"))
                    texts = [w.text() for w in page.findChildren(QLabel)]
                    self.assertIn("每 2 秒读一次芯片温度，掉电后打印上次保存的最高温度", texts)
            ev = make_run(root, "20261116-092943-eval-x-none-1")
            page.replay = None
            page.open_run(ev)
            self.assertTrue(page.title.text().startswith("运行 20261116-092943-eval"))      # 评测运行照旧
            page.deleteLater()


class FlowSavesSpecTest(unittest.TestCase):
    def test_spec_written_to_run_dir(self):
        import inspect
        from firmforge.embedded import flow
        self.assertIn('"spec.md"', inspect.getsource(flow.EmbeddedFlow.__init__))


if __name__ == "__main__":
    unittest.main()

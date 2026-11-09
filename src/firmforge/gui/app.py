"""FirmForge 桌面软件主窗口（DECISIONS 第 62 条，界面草图第 4 版）。

左边竖栏 6 个工作区：对话、硬件、评测、工作流、团队、设置（硬件、评测见 DECISIONS 第 100 条）。
所有面板之间都能拖动调整大小，关闭时记住布局。
启动：`firmforge gui`，或 `python -m firmforge.gui`。需要先 `pip install -e .[gui]`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from .. import config
from . import theme as th
from .widgets import set_theme


def _icon(kind: str, color: str) -> QIcon:
    """竖栏图标：圆头、圆角的细线条（线宽 1.6），用代码画，不依赖图片文件。"""
    pm = QPixmap(48, 48)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2, 2)
    pen = QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if kind == "chat":
        path = QPainterPath()
        path.addRoundedRect(QRectF(3, 5, 18, 12), 3, 3)
        p.drawPath(path)
        p.drawLine(QPointF(8, 17), QPointF(6, 20))
    elif kind == "hw":                      # 插头：最简单的"硬件"示意，不画电路板
        p.drawRoundedRect(QRectF(4, 7, 16, 11), 3, 3)
        p.drawLine(QPointF(9, 7), QPointF(9, 4.5))
        p.drawLine(QPointF(15, 7), QPointF(15, 4.5))
        p.drawLine(QPointF(8.5, 12.5), QPointF(15.5, 12.5))
    elif kind == "eval":
        for x, y in ((5, 11), (12, 5), (19, 13)):
            p.drawLine(QPointF(x, 19), QPointF(x, y))
    elif kind == "flow":
        p.drawRoundedRect(QRectF(3, 3.5, 7, 5), 2, 2)
        p.drawRoundedRect(QRectF(14, 15.5, 7, 5), 2, 2)
        path = QPainterPath(QPointF(6.5, 8.5))
        path.cubicTo(QPointF(6.5, 14), QPointF(17.5, 10), QPointF(17.5, 15.5))
        p.drawPath(path)
    elif kind == "team":
        p.drawEllipse(QPointF(9, 8), 3.2, 3.2)
        path = QPainterPath(QPointF(3.5, 19))
        path.cubicTo(QPointF(4.2, 15.5), QPointF(13.8, 15.5), QPointF(14.5, 19))
        p.drawPath(path)
        p.drawEllipse(QPointF(17, 9), 2.4, 2.4)
    else:
        p.drawEllipse(QPointF(12, 12), 3, 3)
        for a, b in (((12, 2.8), (12, 5.2)), ((12, 18.8), (12, 21.2)), ((2.8, 12), (5.2, 12)), ((18.8, 12), (21.2, 12)),
                     ((5.5, 5.5), (7.2, 7.2)), ((16.8, 16.8), (18.5, 18.5)), ((5.5, 18.5), (7.2, 16.8)), ((16.8, 7.2), (18.5, 5.5))):
            p.drawLine(QPointF(*a), QPointF(*b))
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    def __init__(self, project: Path):
        super().__init__()
        self.project = project
        self.setWindowTitle("FirmForge")
        self.setMinimumSize(1200, 760)
        self.setObjectName("Root")
        root = QWidget()
        root.setObjectName("Root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 8, 8, 4)
        outer.setSpacing(4)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        self.status = QLabel("")
        self.status.setObjectName("StatusBar")
        self.status.setTextFormat(Qt.RichText)            # 状态栏里有板子连接的小圆点（富文本）
        outer.addWidget(self.status)
        self.setCentralWidget(root)

        rail = QWidget()
        rail.setObjectName("Rail")
        rail.setFixedWidth(68)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(7, 4, 7, 4)
        rl.setSpacing(4)
        logo = QLabel("FF")
        logo.setObjectName("Logo")
        logo.setFixedSize(34, 34)
        logo.setAlignment(Qt.AlignCenter)
        rl.addWidget(logo, 0, Qt.AlignHCenter)
        rl.addSpacing(12)
        t = th.current(self.dark)                    # 页面画图要用主题色，先设好
        set_theme(t)
        QApplication.instance().setStyleSheet(th.stylesheet(t))
        self.group = QButtonGroup(self)
        self.stack = QStackedWidget()
        self.buttons = {}
        from .chat_page import ChatPage
        from .designer_page import DesignerPage
        from .eval_page import EvalPage
        from .hardware_page import HardwarePage
        from .settings_page import SettingsPage
        from .team_page import TeamPage
        self.chat = ChatPage(project)
        self.hardware = HardwarePage(project)
        self.evalp = EvalPage(project)
        self.designer = DesignerPage(project)
        self.team = TeamPage(project)
        self.settings = SettingsPage(project)
        pages = [("chat", "对话", self.chat), ("hw", "硬件", self.hardware), ("eval", "评测", self.evalp),
                 ("flow", "工作流", self.designer), ("team", "团队", self.team), ("set", "设置", self.settings)]
        self.page_keys = [k for k, _, _ in pages]
        for i, (key, text, page) in enumerate(pages):
            b = QToolButton()
            b.setObjectName("RailButton")
            b.setText(text)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setIconSize(QSize(22, 22))
            b.clicked.connect(lambda _=False, n=i: self.go(n))
            self.group.addButton(b)
            self.buttons[key] = b
            if key == "set":
                rl.addStretch(1)
            rl.addWidget(b, 0, Qt.AlignHCenter)
            self.stack.addWidget(page)
        body.addWidget(rail)
        body.addWidget(self.stack, 1)
        self.chat.open_designer.connect(self._designer_ai)
        self.chat.review_rules.connect(self._review_rules)
        self.hardware.rules_changed.connect(lambda: self.chat.refresh_status(self.chat._run_text()))
        self.evalp.open_run.connect(self._open_run)
        self.designer.try_run.connect(self._try_run)
        self.settings.theme_changed.connect(self.apply_theme)
        st = QSettings()
        if st.value("window/geometry"):
            self.restoreGeometry(st.value("window/geometry"))
        else:
            self.resize(1440, 900)
        key = st.value("window/page_key", "chat")          # 存名字不存序号：加了工作区以后不会错位
        self.go(key if key in self.page_keys else "chat")
        self._paint_icons()
        self.chat.refresh_status()

    def _paint_icons(self):
        t = th.current(self.dark)
        for key, b in self.buttons.items():
            b.setIcon(_icon(key, t["ink"] if b.isChecked() else t["ink2"]))

    @property
    def dark(self) -> bool:
        pref = QSettings().value("general/theme", "system")
        if pref in ("light", "dark"):
            return pref == "dark"
        hints = QApplication.styleHints()
        return hasattr(hints, "colorScheme") and hints.colorScheme() == Qt.ColorScheme.Dark

    def apply_theme(self):
        t = th.current(self.dark)
        set_theme(t)
        QApplication.instance().setStyleSheet(th.stylesheet(t))
        self._paint_icons()
        for page in (self.chat, self.designer):
            for view in (getattr(page, "graph", None), getattr(page, "flow", None), getattr(page, "canvas", None)):
                if view is not None:
                    view.viewport().update()

    def go(self, i):
        """i 可以是序号，也可以是工作区的名字（chat、hw、eval、flow、team、set）。"""
        if isinstance(i, str):
            i = self.page_keys.index(i)
        i = i if 0 <= i < len(self.page_keys) else 0
        self.stack.setCurrentIndex(i)
        list(self.buttons.values())[i].setChecked(True)
        if self.page_keys[i] == "chat" and hasattr(self, "chat"):
            self.chat.refresh_status(self.chat._run_text())          # 板子可能刚插上或拔掉
        self._paint_icons()

    def set_status(self, text: str):
        self.status.setText(text)

    def reference_in_chat(self, sid: str):
        self.go("chat")
        self.chat.input.insertPlainText(f"@{sid} ")
        self.chat.input.setFocus()

    def _review_rules(self, payload: dict, done):
        self.go("hw")
        self.hardware.begin_review(payload, done)

    def _open_run(self, run_dir: str):
        self.go("chat")
        self.chat.start_replay(Path(run_dir))

    def _designer_ai(self, instruction: str):
        self.go("flow")
        self.designer.ask_ai(instruction or None)

    def _try_run(self, wf):
        tid = self.chat.task_combo.currentData()
        if not tid:
            items = [self.chat.task_combo.itemData(i) for i in range(1, self.chat.task_combo.count())]
            tid, ok = QInputDialog.getItem(self, "试运行", f"用 {wf.name} {wf.version} 跑哪一道评测题？", items, 0, False)
            if not ok:
                return
        from ..tasks import load_task
        self.go("chat")
        self.chat.workflow = wf
        self.chat.new_chat()
        self.chat.add_user(f"用工作流 {wf.name} {wf.version} 试运行评测题 {tid}")
        self.chat.start_run(load_task(self.project / "tasks" / tid))

    def closeEvent(self, ev):
        st = QSettings()
        st.setValue("window/geometry", self.saveGeometry())
        st.setValue("window/page_key", self.page_keys[self.stack.currentIndex()])
        self.chat.save_layout()
        self.hardware.save_layout()
        self.evalp.save_layout()
        self.designer.save_layout()
        super().closeEvent(ev)


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setOrganizationName("FirmForge")
    app.setApplicationName("FirmForge")
    config.load_default_dotenv()
    project = Path.cwd() if (Path.cwd() / "firmforge.toml").is_file() else config.project_root()
    win = MainWindow(project)
    win.apply_theme()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

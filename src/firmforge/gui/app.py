"""FirmForge 桌面软件主窗口（DECISIONS 第 62 条，界面草图第 4 版）。

左边竖栏 6 个工作区：对话、硬件、评测、工作流、团队、设置（硬件、评测见 DECISIONS 第 100 条）。
所有面板之间都能拖动调整大小，关闭时记住布局。
启动：`firmforge gui`，或 `python -m firmforge.gui`。需要先 `pip install -e .[gui]`。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QHBoxLayout, QInputDialog, QLabel, QMainWindow, QPushButton,
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
    elif kind == "hw":                      # 翻开的书：知识库
        left = QPainterPath(QPointF(12, 7))
        left.cubicTo(QPointF(9.5, 5.2), QPointF(6, 5), QPointF(3.5, 5.5))
        left.lineTo(QPointF(3.5, 18))
        left.cubicTo(QPointF(6, 17.5), QPointF(9.5, 17.7), QPointF(12, 19.5))
        right = QPainterPath(QPointF(12, 7))
        right.cubicTo(QPointF(14.5, 5.2), QPointF(18, 5), QPointF(20.5, 5.5))
        right.lineTo(QPointF(20.5, 18))
        right.cubicTo(QPointF(18, 17.5), QPointF(14.5, 17.7), QPointF(12, 19.5))
        p.drawPath(left)
        p.drawPath(right)
        p.drawLine(QPointF(12, 7), QPointF(12, 19.5))
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
        # 代码改过但窗口没重启时提醒（2026-11-14：设计器面板的修复已提交，开着的旧窗口还是旧代码，面板照样能拖出去）
        self.update_bar = QWidget()
        self.update_bar.setObjectName("UpdateBar")
        ub = QHBoxLayout(self.update_bar)
        ub.setContentsMargins(12, 4, 4, 4)
        self.update_label = QLabel("软件的代码已经更新，这个窗口还在用打开时的旧代码。重启后才生效。")
        self.update_label.setWordWrap(True)
        restart = QPushButton("重启软件")
        restart.setObjectName("Primary")
        restart.clicked.connect(self.restart)
        ub.addWidget(self.update_label, 1)
        ub.addWidget(restart)
        self.update_bar.setVisible(False)
        self.update_bar.setAttribute(Qt.WA_StyledBackground, True)   # 圆角背景
        bar_row = QHBoxLayout()
        bar_row.addSpacing(68)                                         # 和内容区对齐，不压在左边导航栏下面
        bar_row.addWidget(self.update_bar, 1)
        outer.addLayout(bar_row)
        outer.addWidget(self.status)
        self.setCentralWidget(root)
        self._code_stamp = code_stamp()
        self._restart = False
        self.code_changed = False
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self.check_code_update)
        self._update_timer.start(20000)

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
        from .knowledge_page import KnowledgePage
        from .settings_page import SettingsPage
        from .team_page import TeamPage
        self.chat = ChatPage(project)
        self.hardware = KnowledgePage(project)
        self.designer = DesignerPage(project)
        self.team = TeamPage(project)
        self.settings = SettingsPage(project)
        pages = [("chat", "对话", self.chat), ("hw", "知识库", self.hardware),
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
        self.chat.run_done.connect(self.hardware.reload)
        self.hardware.rules_changed.connect(lambda: self.chat.refresh_status(self.chat._run_text()))
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
        if hasattr(self, "hardware"):                    # 知识库里有按主题算好的颜色，换主题时重画
            self.hardware.reload()
            self.chat.refresh_status(self.chat._run_text())
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

    def _designer_ai(self, instruction: str):
        self.go("flow")
        self.designer.ask_ai(instruction or None)

    def _try_run(self, wf):
        """设计器里试运行。上板工作流（DECISIONS 第 110 条）：写一句需求，在对话页按这个工作流跑一次；
        纯逻辑工作流：题目只能从 tasks/ 里选（对话页选着"上板"时，题目下拉框里是上板题）。"""
        from PySide6.QtWidgets import QMessageBox
        if wf.engine == "firmware":
            text, ok = QInputDialog.getMultiLineText(
                self, "试运行上板流程", f"用 {wf.name} {wf.version} 跑什么需求？",
                "读 MAX30102 片内温度，调用方传入 I2C 总线句柄，返回摄氏度")
            if not ok or not text.strip():
                return
            if not self.chat.embedded:
                self.chat.flow_combo.setCurrentIndex(self.chat.flow_combo.findData("embedded"))
            if self.chat.rule_combo.currentData() == "single":         # 单 Agent 只给评测题用
                self.chat.rule_combo.setCurrentIndex(max(0, self.chat.rule_combo.findData("verified")))
            self.go("chat")
            self.chat.new_chat()
            self.chat.workflow = wf
            self.chat.add_user(f"用工作流 {wf.name} {wf.version} 试运行：{text.strip()}")
            self.chat.start_embedded(spec=text.strip())
            return
        from ..tasks import discover_tasks, load_task
        ids = [t.id for t in discover_tasks(self.project / "tasks")] if (self.project / "tasks").is_dir() else []
        tid = self.chat.task_combo.currentData() if not self.chat.embedded else ""
        if tid not in ids:
            if not ids:
                QMessageBox.information(self, "试运行", "tasks/ 里没有评测题，没法试运行。")
                return
            tid, ok = QInputDialog.getItem(self, "试运行", f"用 {wf.name} {wf.version} 跑哪一道评测题？", ids, 0, False)
            if not ok or tid not in ids:
                return
        if self.chat.embedded:                            # 切到纯逻辑流程，对话页的选项和详情标签跟着换
            self.chat.flow_combo.setCurrentIndex(self.chat.flow_combo.findData("classic"))
        self.go("chat")
        self.chat.workflow = wf
        self.chat.new_chat()
        self.chat.add_user(f"用工作流 {wf.name} {wf.version} 试运行评测题 {tid}")
        self.chat.start_run(load_task(self.project / "tasks" / tid))

    def busy(self) -> list[str]:
        """还没结束的运行（关窗口前问一声）。"""
        from .runner import LIVE_WORKERS
        names = [text for owner, text in ((self.chat, "对话页的运行"),)
                 if getattr(owner, "backend", None) is not None]
        if not names and any(w.isRunning() for w in LIVE_WORKERS):
            names.append("一次模型调用")
        return names

    def check_code_update(self) -> bool:
        """代码文件比窗口打开时新：显示"重启软件"提示条。返回是否显示。"""
        if not self.code_changed and code_stamp() > self._code_stamp + 1:
            self.code_changed = True
            self.update_bar.setVisible(True)
        return self.code_changed

    def restart(self) -> None:
        """关掉窗口（照常保存布局；还有运行没结束时先问），关成了再用同样的命令重新打开。"""
        self._restart = True
        if not self.close():
            self._restart = False

    def closeEvent(self, ev):
        from PySide6.QtWidgets import QMessageBox
        from .runner import shutdown_workers
        names = self.busy()
        if names:
            ans = QMessageBox.question(self, "还有运行没结束",
                                       f"{'、'.join(names)}还没结束。现在关闭会中止它（评测已经跑完的部分会保留，"
                                       "下次用同一个目录开始会接着跑）。\n\n确定关闭吗？")
            if ans != QMessageBox.Yes:
                ev.ignore()
                return
            for owner in (self.chat, self.hardware):
                b = getattr(owner, "backend", None)
                if b is not None:
                    try:
                        b.stop()
                    except Exception:                    # noqa: BLE001  停的时候出错也要继续关
                        pass
        left = shutdown_workers()                        # 等后台线程在检查点自己退出（不强杀，第 128 条）
        st = QSettings()
        st.setValue("window/geometry", self.saveGeometry())
        st.setValue("window/page_key", self.page_keys[self.stack.currentIndex()])
        self.chat.save_layout()
        self.hardware.save_layout()
        self.designer.save_layout()
        super().closeEvent(ev)
        if self._restart:
            from PySide6.QtCore import QProcess
            QProcess.startDetached(sys.executable, sys.argv)
        if left:                                         # 还卡在模型调用或烧录上：布局已存好，直接结束进程
            st.sync()
            self.hard_exit()

    def hard_exit(self) -> None:
        """后台线程还没退出时结束整个进程（Qt 在线程还跑着时正常退出会中止程序）。测试里会替换掉。"""
        import os
        os._exit(0)


def code_stamp() -> float:
    """firmforge 包里所有 .py 文件最新的修改时间（判断开着的窗口是不是旧代码）。"""
    pkg = Path(__file__).resolve().parents[1]
    try:
        return max((f.stat().st_mtime for f in pkg.rglob("*.py")), default=0.0)
    except OSError:
        return 0.0


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setOrganizationName("FirmForge")
    app.setApplicationName("FirmForge")
    app.setDesktopFileName("firmforge")             # 和应用列表里的 firmforge.desktop 对上，任务栏显示同一个图标
    from . import desktop_entry
    if desktop_entry.icon_path().is_file():
        app.setWindowIcon(QIcon(str(desktop_entry.icon_path())))
    config.load_default_dotenv()
    project = Path.cwd() if (Path.cwd() / "firmforge.toml").is_file() else config.project_root()
    win = MainWindow(project)
    win.apply_theme()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

"""通用小部件：圆角面板、带"可拖动"提示条的分隔条、开关、分段选择、头像、圆形状态点。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSplitter, QSplitterHandle, QVBoxLayout, QWidget)

_theme = None


def set_theme(theme) -> None:
    global _theme
    _theme = theme


def T():
    return _theme


class Panel(QFrame):
    """白色圆角面板（圆角 16）。"""

    def __init__(self, parent=None, margins=(14, 12, 14, 12)):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*margins)
        self.lay.setSpacing(8)


def label(text: str = "", kind: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if kind:
        lab.setObjectName(kind)
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lab


class _GripHandle(QSplitterHandle):
    """分隔条：中间画一根圆头小短条，提示"这里能拖"；双击恢复默认比例。"""

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T()["grip"] if T() else "#D3D7DC"))
        r = self.rect()
        if self.orientation() == Qt.Horizontal:
            p.drawRoundedRect(QRectF(r.center().x() - 1.5, r.center().y() - 14, 3, 28), 1.5, 1.5)
        else:
            p.drawRoundedRect(QRectF(r.center().x() - 14, r.center().y() - 1.5, 28, 3), 1.5, 1.5)

    def mouseDoubleClickEvent(self, ev):
        sp = self.splitter()
        if getattr(sp, "default_sizes", None):
            sp.setSizes(sp.default_sizes)


class GripSplitter(QSplitter):
    """面板之间 8 像素的缝就是分隔条；记住默认比例，双击恢复；侧栏可以拖到底折叠。"""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setHandleWidth(8)
        self.setChildrenCollapsible(True)
        self.default_sizes: list[int] = []

    def createHandle(self):
        return _GripHandle(self.orientation(), self)

    def set_defaults(self, sizes: list[int]) -> None:
        self.default_sizes = list(sizes)
        self.setSizes(sizes)


class Toggle(QAbstractButton):
    """圆角开关。"""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self):
        return QSize(34, 20)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T()["accent"] if on else "#CFD3D8"))
        p.drawRoundedRect(QRectF(1, 1, 32, 18), 9, 9)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(16 if on else 3, 3, 14, 14))


class Segmented(QFrame):
    """分段选择（如思考强度：关 低 高 最高）。value 是选项的内部值。"""
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Segmented")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for val, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setFlat(True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[val] = b
            b.clicked.connect(lambda _=False, v=val: self.changed.emit(v))
        self.set_value(value or options[0][0])
        self._restyle()

    def _restyle(self):
        c = T().c if T() else {"bg": "#F3F4F6", "surface": "#FFFFFF", "ink": "#1C2026", "ink2": "#6E7580"}
        self.setStyleSheet(
            f"#Segmented {{ background: {c['bg']}; border-radius: 10px; }}"
            f"#Segmented QPushButton {{ border: none; border-radius: 8px; padding: 3px 12px; color: {c['ink2']};"
            f" background: transparent; }}"
            f"#Segmented QPushButton:checked {{ background: {c['surface']}; color: {c['ink']}; }}")

    def value(self) -> str:
        for v, b in self.buttons.items():
            if b.isChecked():
                return v
        return ""

    def set_value(self, v: str) -> None:
        if v in self.buttons:
            self.buttons[v].setChecked(True)


class Dot(QWidget):
    """状态小圆点。"""

    def __init__(self, color: str = "#9AA0A8", size: int = 7, hollow: bool = False, parent=None):
        super().__init__(parent)
        self.color, self.hollow, self._s = color, hollow, size
        self.setFixedSize(size + 2, size + 2)

    def set(self, color: str, hollow: bool = False):
        self.color, self.hollow = color, hollow
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.hollow:
            p.setPen(QPen(QColor(self.color), 1.2))
            p.setBrush(Qt.NoBrush)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(self.color))
        p.drawEllipse(QRectF(1, 1, self._s, self._s))


ROLE_TINTS = {"analyst": "#E9E4F3", "planner": "#E1ECF5", "coder": "#E3F0E8", "tester": "#F2EEDC",
              "diagnoser": "#F6E7E1", "reviewer": "#EAEAEA"}


class Avatar(QLabel):
    """Agent 头像：首字母 + 按角色的浅色底，圆形。"""

    def __init__(self, name: str, role: str, parent=None):
        super().__init__((name[:1] or "?").upper(), parent)
        self.setFixedSize(26, 26)
        self.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setBold(True)
        f.setPointSize(8)
        self.setFont(f)
        tint = ROLE_TINTS.get(role, "#ECEEF1")
        if T() and T().dark:
            tint = "#2E343B"
        self.setStyleSheet(f"background: {tint}; border-radius: 13px; color: {T()['ink'] if T() else '#1C2026'};")


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w == "stretch":
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        else:
            lay.addWidget(w)
    return lay


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w

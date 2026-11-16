"""统一的面板布局（DECISIONS 第 133 条）：每页左右排的面板都用 PaneSplitter，规则照正规软件来。

借鉴（出处见 DECISIONS 第 133 条）：
- 面板分角色（VS Code workbench：侧栏 sidebarPart、编辑区 editorPart、辅助栏 auxiliaryBarPart）：
  侧栏和右侧面板窗口变宽变窄时宽度不变，多出的空间给主区（VS Code 的 LayoutPriority：侧栏 Low、编辑区 High）；
- 吸附收起（VS Code splitview.ts 的 snap）：拖到最小宽度的一半以下就整块收起，收起前记住宽度，再打开时恢复；
- 收起的面板随时能找回来（Apple HIG Split views / Sidebars："Provide multiple ways to reveal hidden panes"）：
  页面上有显示/隐藏按钮，Ctrl+B 切换侧栏，Ctrl+Alt+B 切换右侧面板；
- 窗口变窄自动收起侧栏（Apple HIG Sidebars 里 Mail 的例子；Android 列表-详情布局变窄时保留详情、收起列表）；
  用户自己点过的以用户为准，直到窗口宽度再次跨过这一档；
- 双击分隔条恢复默认宽度；每页的宽度、开关状态都记住（QSettings）。
- 右侧标签放不下时收进"更多"菜单（★ 自定：以前的省略号截断看不清，左右箭头会盖住字）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRectF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QLabel, QMenu, QPushButton, QSizePolicy, QTabWidget, QWidget

from .widgets import GripSplitter, T

ROLES = ("sidebar", "main", "inspector", "fixed")


@dataclass
class PaneSpec:
    role: str               # sidebar：左边的列表或导航；inspector：右边的详情；main：吸收窗口变化的主区；fixed：宽度不变也不收起
    minimum: int
    default: int
    stretch: int
    collapsible: bool


class PaneSplitter(GripSplitter):
    """左右排的面板。add_pane 按角色加面板，finish() 之后恢复上次的宽度和开关。"""
    toggled = Signal(str, bool)            # (角色, 现在是否打开)

    def __init__(self, key: str, *, auto_collapse_below: int = 0, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.key = key
        self.auto_below = auto_collapse_below        # 面板区宽度低于它时自动收起侧栏（0 表示不自动）
        self.specs: list[PaneSpec] = []
        self.cached: dict[str, int] = {}             # 收起前的宽度
        self.pinned: dict[str, bool] = {}            # 用户自己定的开关（自动收起不动它）
        self._narrow: bool | None = None
        self._restoring = False
        self.splitterMoved.connect(self._on_moved)

    # ------------------------------------------------------------ 搭建

    def add_pane(self, w: QWidget, role: str, *, minimum: int, default: int, stretch: int | None = None,
                 collapsible: bool | None = None) -> None:
        if role not in ROLES:
            raise ValueError(f"面板角色只能是 {ROLES}")
        if any(s.role == role for s in self.specs) and role != "fixed":
            raise ValueError(f"一页里只能有一个 {role}")
        stretch = (1 if role == "main" else 0) if stretch is None else stretch
        collapsible = role in ("sidebar", "inspector") if collapsible is None else collapsible
        self.specs.append(PaneSpec(role, minimum, default, stretch, collapsible))
        self.addWidget(w)
        i = self.count() - 1
        w.setMinimumWidth(minimum)
        self.setStretchFactor(i, stretch)
        self.setCollapsible(i, collapsible)

    def finish(self) -> None:
        self.default_sizes = [s.default for s in self.specs]
        data = self._load()
        self._restoring = True
        try:
            sizes = data.get("sizes")
            if isinstance(sizes, list) and len(sizes) == len(self.specs) and all(isinstance(x, int) for x in sizes):
                self.setSizes(sizes)
            else:
                self.setSizes(self.default_sizes)
            self.cached = {k: int(v) for k, v in (data.get("cached") or {}).items() if k in self.roles}
            self.pinned = {k: bool(v) for k, v in (data.get("pinned") or {}).items() if k in self.roles}
        finally:
            self._restoring = False

    @property
    def roles(self) -> list[str]:
        return [s.role for s in self.specs]

    def index(self, role: str) -> int:
        return self.roles.index(role)

    def has(self, role: str) -> bool:
        return role in self.roles

    # ------------------------------------------------------------ 开关

    def is_open(self, role: str) -> bool:
        return self.has(role) and self.sizes()[self.index(role)] > 0

    def toggle(self, role: str) -> None:
        if self.has(role):
            self.set_open(role, not self.is_open(role))

    def set_open(self, role: str, open_: bool, *, user: bool = True) -> None:
        if not self.has(role) or not self.specs[self.index(role)].collapsible:
            return
        i = self.index(role)
        sizes = self.sizes()
        give = self._absorber(exclude=i)
        if open_ and sizes[i] == 0:
            spec = self.specs[i]
            want = max(spec.minimum, self.cached.get(role, spec.default))
            if give is not None:                       # 从主区让出地方，主区不小于它的最小宽度
                room = sizes[give] - self.specs[give].minimum
                want = max(spec.minimum, min(want, room)) if room >= spec.minimum else spec.minimum
                sizes[give] = max(self.specs[give].minimum, sizes[give] - want)
            sizes[i] = want
        elif not open_ and sizes[i] > 0:
            # 记住的是用户拖出来的宽度；窗口变窄时 Qt 先把它挤小了，不能把挤小的宽度当成"原来的宽度"
            self.cached.setdefault(role, max(sizes[i], self.specs[i].default))
            if give is not None:
                sizes[give] += sizes[i]
            sizes[i] = 0
        else:
            if user:
                self.pinned[role] = open_
                self._save()
            return
        self.setSizes(sizes)
        if user:
            self.pinned[role] = open_
        self._save()
        self.toggled.emit(role, open_)

    def reset(self) -> None:
        """双击分隔条：全部回到默认宽度，清掉用户的开关选择（窄窗口下侧栏照样自动收起）。"""
        self.pinned.clear()
        self.cached.clear()
        self.setSizes(self.default_sizes)
        self._narrow = None
        self._adapt()
        self._save()
        for r in self.roles:
            self.toggled.emit(r, self.is_open(r))

    def _absorber(self, exclude: int) -> int | None:
        best = None
        for j, s in enumerate(self.specs):
            if j == exclude or self.sizes()[j] == 0:
                continue
            if best is None or s.stretch > self.specs[best].stretch:
                best = j
        return best

    # ------------------------------------------------------------ 拖动、窗口变化

    def _on_moved(self, _pos: int, _index: int) -> None:
        if self._restoring:
            return
        sizes = self.sizes()
        for spec, sz in zip(self.specs, sizes):
            if sz > 0:
                self.cached[spec.role] = sz
            if spec.collapsible:
                now_open = sz > 0
                if self.pinned.get(spec.role, None) != now_open and (sz == 0 or spec.role in self.pinned
                                                                      or self._auto_closed(spec.role)):
                    self.pinned[spec.role] = now_open      # 拖到收起、或把收起的拖出来：算用户的选择
                    self.toggled.emit(spec.role, now_open)
        self._save()

    def _auto_closed(self, role: str) -> bool:
        return role == "sidebar" and bool(self._narrow)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.isVisible():
            self._adapt()
            self._regrow()

    def _regrow(self) -> None:
        """窗口变窄时 Qt 会把侧栏、右侧面板也挤小；窗口再变宽时，先把它们还原到原来的宽度，剩下的才给主区。"""
        sizes = self.sizes()
        main = next((j for j, s in enumerate(self.specs) if s.stretch > 0 and sizes[j] > 0), None)
        if main is None:
            return
        changed = False
        for j, s in enumerate(self.specs):
            if j == main or sizes[j] == 0 or s.stretch > 0:
                continue
            pref = self.cached.get(s.role, s.default)
            room = sizes[main] - self.specs[main].minimum
            if sizes[j] < pref and room > 0:
                d = min(pref - sizes[j], room)
                sizes[j] += d
                sizes[main] -= d
                changed = True
        if changed:
            self._restoring = True
            try:
                self.setSizes(sizes)
            finally:
                self._restoring = False

    def showEvent(self, ev):
        super().showEvent(ev)
        self._adapt()

    def _adapt(self) -> None:
        if not self.auto_below or not self.has("sidebar") or self.width() <= 0:
            return
        narrow = self.width() < self.auto_below
        if self._narrow is not None and narrow != self._narrow:
            self.pinned.pop("sidebar", None)          # 跨过这一档：之前的手动选择作废，按窗口宽度来
        self._narrow = narrow
        if "sidebar" in self.pinned:
            return
        if self.is_open("sidebar") == narrow:
            self.set_open("sidebar", not narrow, user=False)

    # ------------------------------------------------------------ 记住

    def _load(self) -> dict:
        raw = QSettings().value(f"panes/{self.key}")
        try:
            return json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            return {}

    def _save(self) -> None:
        if self._restoring:
            return
        QSettings().setValue(f"panes/{self.key}", json.dumps(
            {"sizes": self.sizes(), "cached": self.cached, "pinned": self.pinned}))


class PaneToggle(QAbstractButton):
    """显示/隐藏侧栏或右侧面板的小按钮：画一个窗口框，打开时对应一侧涂实（和 macOS、VS Code 的图标同一个意思）。"""

    def __init__(self, splitter: PaneSplitter, role: str, what: str, shortcut: str, parent=None):
        super().__init__(parent)
        self.sp, self.role, self.what, self.shortcut = splitter, role, what, shortcut
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.clicked.connect(lambda: splitter.toggle(role))
        splitter.toggled.connect(lambda r, _o: self.sync() if r == role else None)
        self.sync()

    def sync(self) -> None:
        on = self.sp.is_open(self.role)
        self.setChecked(on)
        self.setToolTip(f"{'隐藏' if on else '显示'}{self.what}（{self.shortcut}）")
        self.setAccessibleName(self.toolTip())
        self.update()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.sync()

    def sizeHint(self):
        return QSize(30, 28)

    def paintEvent(self, ev):
        t = T()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse() or self.hasFocus():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(t["line"] if t else "#E4E6EA"))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        ink = QColor((t["ink"] if self.isChecked() else t["ink2"]) if t else "#1C2026")
        box = QRectF(self.width() / 2 - 8, self.height() / 2 - 6.5, 16, 13)
        p.setPen(QPen(ink, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 3, 3)
        strip = QRectF(box.left(), box.top(), 5.5, box.height()) if self.role == "sidebar" else \
            QRectF(box.right() - 5.5, box.top(), 5.5, box.height())
        p.drawLine(strip.right() if self.role == "sidebar" else strip.left(), box.top(),
                   strip.right() if self.role == "sidebar" else strip.left(), box.bottom())
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(ink)
            p.drawRoundedRect(strip.adjusted(0.7, 0.7, -0.7, -0.7), 2, 2)
        p.end()

    def enterEvent(self, ev):
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self.update()
        super().leaveEvent(ev)


class OverflowTabs(QTabWidget):
    """标签多、地方窄时，放不下的标签收进右上角"更多"菜单；当前标签总是露在外面。
    setTabVisible 表示"这个标签该不该有"（例如纯逻辑流程没有"上板"），放不放得下由这里决定。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wanted: dict[int, bool] = {}
        self.more = QPushButton("更多 ▾")              # 和页面上其他轻按钮一个样子
        self.more.setObjectName("Ghost")
        self.more.setStyleSheet("QPushButton::menu-indicator { image: none; width: 0px; }")
        self.more.setMenu(QMenu(self.more))
        self.more.setVisible(False)
        self.setCornerWidget(self.more, Qt.TopRightCorner)
        self.currentChanged.connect(lambda _i: self._relayout())

    def setTabVisible(self, index: int, visible: bool) -> None:     # noqa: N802  Qt 的名字
        self._wanted[index] = bool(visible)
        self._relayout()

    def wanted(self, index: int) -> bool:
        return self._wanted.get(index, True)

    def tabInserted(self, index: int) -> None:                     # noqa: N802
        super().tabInserted(index)
        self._wanted = {(k + 1 if k >= index else k): v for k, v in self._wanted.items()}
        self._relayout()

    def minimumSizeHint(self):
        h = super().minimumSizeHint().height()
        return QSize(160, h)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()

    def _pick(self, n: int) -> None:
        QTabWidget.setTabVisible(self, n, True)       # 藏着的标签选不中，先露出来再选，_relayout 再挤掉别的
        self.setCurrentIndex(n)

    def overflowed(self) -> list[int]:
        return [i for i in range(self.count()) if self.wanted(i) and not self.isTabVisible(i)]

    def _relayout(self) -> None:
        bar = self.tabBar()
        wanted = [i for i in range(self.count()) if self.wanted(i)]
        widths = {i: bar.fontMetrics().horizontalAdvance(self.tabText(i)) + 28 for i in wanted}
        avail = self.width() - 12
        cur = self.currentIndex()
        show = list(wanted)
        if sum(widths.values()) > avail:
            avail -= self.more.sizeHint().width() + 8
            show = []
            used = widths.get(cur, 0) if cur in wanted else 0
            for i in wanted:
                if i == cur:
                    show.append(i)
                    continue
                if used + widths[i] <= avail:
                    show.append(i)
                    used += widths[i]
            if cur in wanted and cur not in show:
                show.append(cur)
        for i in range(self.count()):
            QTabWidget.setTabVisible(self, i, i in show)
        hidden = [i for i in wanted if i not in show]
        menu = self.more.menu()
        menu.clear()
        for i in hidden:
            act = menu.addAction(self.tabText(i))
            act.setToolTip(self.tabToolTip(i))
            act.triggered.connect(lambda _=False, n=i: self._pick(n))
        self.more.setVisible(bool(hidden))


class ElideLabel(QLabel):
    """单行标题：放不下时末尾显示省略号（完整文字放在提示里），不把所在的面板撑宽。"""

    def __init__(self, text: str = "", kind: str = "", parent=None):
        super().__init__(text, parent)
        if kind:
            self.setObjectName(kind)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def setText(self, text: str) -> None:                          # noqa: N802
        super().setText(text)
        self.setToolTip(text)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.palette().color(self.foregroundRole()))
        r = self.contentsRect()
        p.drawText(r, int(self.alignment() | Qt.AlignVCenter),
                   self.fontMetrics().elidedText(self.text(), Qt.ElideRight, r.width()))
        p.end()


class DockPanes(QObject):
    """设计器用 QDockWidget 排面板（主区是画布，QMainWindow 自己把多出的空间给中间）。这里给停靠面板加上和
    PaneSplitter 一样的开关、记住用户的选择、窗口变窄自动收起左边，PaneToggle 和快捷键照样能用。
    角色：sidebar（左边的步骤箱）、inspector（右边的步骤树和属性）、panel（下面的代码和检查，Ctrl+J，同 VS Code）。"""
    toggled = Signal(str, bool)

    def __init__(self, window, groups: dict, key: str, *, auto_collapse_below: int = 0):
        super().__init__(window)
        self.window, self.groups, self.key, self.auto_below = window, groups, key, auto_collapse_below
        self._narrow: bool | None = None
        raw = QSettings().value(f"panes/{key}")
        try:
            self.pinned = {k: bool(v) for k, v in (json.loads(raw) if isinstance(raw, str) and raw else {}).items()
                           if k in groups}
        except ValueError:
            self.pinned = {}
        for role, docks in groups.items():
            for d in docks:
                d.visibilityChanged.connect(lambda _v, r=role: self.toggled.emit(r, self.is_open(r)))

    def has(self, role: str) -> bool:
        return role in self.groups

    def is_open(self, role: str) -> bool:
        return self.has(role) and any(not d.isHidden() for d in self.groups[role])

    def toggle(self, role: str) -> None:
        if self.has(role):
            self.set_open(role, not self.is_open(role))

    def set_open(self, role: str, open_: bool, *, user: bool = True) -> None:
        if not self.has(role):
            return
        for d in self.groups[role]:
            d.setVisible(open_)
        if user:
            self.pinned[role] = open_
            QSettings().setValue(f"panes/{self.key}", json.dumps(self.pinned))
        self.toggled.emit(role, open_)

    def adapt(self, width: int) -> None:
        if not self.auto_below or not self.has("sidebar") or width <= 0:
            return
        narrow = width < self.auto_below
        if self._narrow is not None and narrow != self._narrow:
            self.pinned.pop("sidebar", None)
        self._narrow = narrow
        if "sidebar" not in self.pinned and self.is_open("sidebar") == narrow:
            self.set_open("sidebar", not narrow, user=False)

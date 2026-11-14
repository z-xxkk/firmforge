"""设计器的"检查"面板（负责人 2026-11-14 要求）：像写代码的软件那样，点"开始检查"才检查，结果标出检查时间，
改动以后提示结果可能过期；点某个问题跳到对应的步骤。

检查分两部分：
- 结构检查：步骤都连上了、循环有上限、Agent 可用、引擎能执行（engine_problems 的结果按类分组）；
- 预演（只有上板流程）：拿真正的解释器把工作流空跑几种情况（embedded/fw_preview.py），列出每种情况按什么顺序
  走了哪些步骤；点一种情况，画布上按顺序依次点亮这些步骤。
"""

from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from .widgets import T, hbox, label

GROUPS = (("连线", "每个步骤都连上了", ("没连上",)),
          ("循环", "每个循环都有次数上限", ("次数上限", "尝试次数", "循环")),
          ("Agent", "每个 Agent 步骤都有可用的 Agent", ("没有注册", "的角色是", "可用的 Agent")),
          ("引擎", "引擎能执行这里的每一种组合", ()))
_STEP = re.compile(r"步骤 ([A-Za-z0-9_\-]+)")


def group_problems(probs: list[str]) -> list[tuple[str, list[str]]]:
    """把检查出的问题按类分组：(这一类的说明, 这一类的问题)。没归进前三类的都算"引擎能不能执行"。"""
    out = {title: [] for _, title, _ in GROUPS}
    for p in probs:
        for _, title, keys in GROUPS:
            if not keys or any(k in p for k in keys):
                out[title].append(p)
                break
    return list(out.items())


class CheckPanel(QWidget):
    run_clicked = Signal()
    step_clicked = Signal(str)
    scenario_clicked = Signal(object)          # fw_preview.Trace

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.run_btn = QPushButton("开始检查")
        self.run_btn.setObjectName("Primary")
        self.run_btn.setToolTip("检查结构，并用真正的执行代码把上板工作流空跑几种情况（不花钱、不编译）")
        self.run_btn.clicked.connect(self.run_clicked)
        self.status = label("还没检查。改好以后点\"开始检查\"。", "Faint", wrap=True)
        row = hbox(self.run_btn, self.status, spacing=10)
        row.setStretch(0, 0)
        row.setStretch(1, 1)                       # 按钮按文字宽，剩下的给状态说明
        self.run_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.itemClicked.connect(self._clicked)
        lay.addWidget(self.tree, 1)
        self.checked_at: datetime | None = None
        self.stale = False

    # ------------------------------------------------------------ 显示

    def clear(self):
        self.tree.clear()

    def message(self, text: str) -> None:
        """一句话占满面板（例如"AI 正在改……"、代码读不出来）。"""
        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem([text]))

    def _item(self, parent, text: str, color: str | None = None, data=None) -> QTreeWidgetItem:
        it = QTreeWidgetItem([text])
        if color:
            it.setForeground(0, self.palette().text() if color == "ink" else _brush(color))
        if data is not None:
            it.setData(0, Qt.UserRole, data)
        (parent.addChild if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem)(it)
        return it

    def show_structure(self, probs: list[str], diff: str = "") -> None:
        self.tree.clear()
        top = self._item(self.tree, "结构检查" + ("：没发现问题" if not probs else f"：{len(probs)} 个问题"))
        for title, items in group_problems(probs):
            g = self._item(top, ("✓ " if not items else "✗ ") + title, T()["pass"] if not items else T()["fail"])
            for p in items:
                m = _STEP.search(p)
                self._item(g, p, T()["fail"], ("step", m.group(1)) if m else None)
            g.setExpanded(bool(items))
        top.setExpanded(bool(probs))               # 没问题时收起来，把地方留给预演；点开能看每一项
        if diff:
            self._item(self.tree, f"和已保存的版本相比：{diff}")

    def show_preview(self, traces, why_not: str = "") -> None:
        """traces：fw_preview.preview 的结果；why_not：不能预演的原因（例如纯逻辑流程、结构有问题）。"""
        from ..embedded.fw_preview import VERDICT_NAMES, describe
        if why_not:
            self._item(self.tree, f"预演：{why_not}", T()["ink3"])
            return
        bad = [t for t in traces if t.error]
        top = self._item(self.tree, f"预演：空跑 {len(traces)} 种情况" + (f"，{len(bad)} 种出错" if bad else "，都能跑完")
                         + "（点一种情况，画布上依次点亮走过的步骤）")
        for t in traces:
            # ✗ 只表示预演跑出错；"没通过"是这种情况本来就设定的结果，不是问题
            head = (f"✗ {t.scenario.title}：预演出错" if t.error else
                    f"{t.scenario.title} → 结果：{VERDICT_NAMES.get(t.verdict, t.verdict)}，经过 {len(t.step_ids)} 个步骤")
            it = self._item(top, head, T()["fail"] if t.error else None, ("trace", t))
            for line in describe(t):
                m = re.match(r"([A-Za-z0-9_\-]+)：", line)
                self._item(it, line, T()["fail"] if line.startswith("出错") else None, ("step", m.group(1)) if m else None)
        top.setExpanded(True)

    def finished(self, n_problems: int, n_errors: int) -> None:
        self.checked_at, self.stale = datetime.now(), False
        what = "没发现问题" if not (n_problems or n_errors) else \
            "；".join(x for x in (f"{n_problems} 个结构问题" if n_problems else "", f"预演 {n_errors} 种情况出错" if n_errors else "") if x)
        self.status.setText(f"{self.checked_at:%H:%M:%S} 检查完：{what}")
        self.status.setStyleSheet(f"color: {T()['pass'] if not (n_problems or n_errors) else T()['fail']};")

    def mark_stale(self) -> None:
        """改动过：之前的结果可能过期。"""
        if self.checked_at is None:
            return
        self.stale = True
        self.status.setText(f"{self.checked_at:%H:%M:%S} 检查过，之后又改动了，结果可能过期。再点\"开始检查\"。")
        self.status.setStyleSheet(f"color: {T()['run']};")

    # ------------------------------------------------------------ 点击

    def _clicked(self, it: QTreeWidgetItem, _col: int) -> None:
        data = it.data(0, Qt.UserRole)
        if not data:
            return
        kind, value = data
        if kind == "step":
            self.step_clicked.emit(value)
        elif kind == "trace":
            self.scenario_clicked.emit(value)

    def texts(self) -> list[str]:
        """全部行的文字（测试用）。"""
        out = []
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            out.append(it.text(0))
            stack = [it.child(i) for i in range(it.childCount())] + stack
        return out


def _brush(color: str):
    from PySide6.QtGui import QBrush, QColor
    return QBrush(QColor(color))

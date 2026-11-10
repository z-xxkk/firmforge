"""工作流设计器（界面草图第 4 版第四节）：照 Qt Designer 的布局——左"步骤箱"、中"画布"、右"步骤树"和"属性"、下"代码"和"检查"。

图和文件是同一份：画布上的改动立刻写进下方的 TOML；在下方改 TOML，画布也跟着变（像 Qt Designer 的 .ui 文件和"查看代码"）。
四块面板都是 QDockWidget，可以拖到别的边、叠成标签页、拉出来变成浮动小窗，布局会记住。
连线的含义：顶层步骤按先后顺序执行；从一个步骤下沿拉线到另一个步骤 = 让后者紧跟在前者后面；
从"并行"拉线 = 加一个分支；从"条件"拉线 = 加到"是"分支（属性里可以改到"否"）。

拖拽（DECISIONS 第 102 条，照 Node-RED、n8n 的做法）：
- 从步骤箱把步骤拖进画布：放到连线中点附近就插进这条线，放到某个步骤正下方就接到它后面，放在空白处就是"没连上"；
- 拖动已连好的步骤：小范围挪动连线跟着走；拖远了连线断开、前后两步自动接上，松手时靠近连线或步骤就吸附接上；
- 鼠标移到连线上，点中间的 × 断开；被断开的步骤变成"没连上"（红色虚线框），连上或删除前不能保存、不能试运行；
- 快捷键：Delete 删除选中的步骤，Ctrl+Z 撤销，Ctrl+S 保存；中键按住拖动画布，滚轮缩放。
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from PySide6.QtCore import QMimeData, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDockWidget, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..orchestrator.workflow import (CONDITIONS, Step, Workflow, dump_workflow, engine_problems, list_versions,
                                     load_workflow, parse_workflow, save_workflow)
from .graph import COND_NAMES, KIND_NAMES, ROLE_NAMES, STEP_MIME, STEP_W, FlowCanvas
from .runner import _Worker
from .widgets import FlowLayout, Panel, T, label

PALETTE = [("干活", [("agent", "Agent 干活"), ("verify", "检查"), ("human", "等人确认")]),
           ("控制", [("parallel", "并行"), ("condition", "条件"), ("loop", "循环")]),
           ("嵌套", [("subflow", "子流程"), ("foreach", "每个模块各跑")])]


class PaletteList(QListWidget):
    """步骤箱：可以把步骤拖进画布（也可以双击加到选中步骤后面）。"""

    def __init__(self):
        super().__init__()
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def mimeData(self, items):
        md = QMimeData()
        kind = items[0].data(Qt.UserRole) if items else None
        if kind:
            md.setData(STEP_MIME, kind.encode("utf-8"))
        return md

    def mimeTypes(self):
        return [STEP_MIME]


def bump(version: str) -> str:
    parts = version.split(".")
    if parts and parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    return version + ".1"


class DesignerPage(QMainWindow):
    try_run = Signal(object)                 # 用这个工作流试运行

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.setObjectName("Root")
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.AllowNestedDocks)
        self.wf: Workflow = load_workflow()
        self.saved: Workflow = self.wf
        self.undo: list[Workflow] = []
        self.current_sub = next(iter(self.wf.subflows), None)
        self.selected: str | None = None
        self.new_ids: set[str] = set()
        self._syncing = False
        self._build()
        self.render_all()

    # ------------------------------------------------------------ 布局

    def _dock(self, title: str, widget: QWidget, area, name: str) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(name)
        holder = Panel(margins=(12, 6, 12, 10))
        holder.lay.addWidget(widget)
        d.setWidget(holder)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(area, d)
        return d

    def _build(self):
        center = Panel(margins=(16, 12, 14, 8))
        bar = QWidget()                                  # 窄的时候按钮折到第二行，不被截断
        top = FlowLayout(bar, spacing=8)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.name_label = label("", "H2")
        self.version_combo = QComboBox()
        self.version_combo.setObjectName("Pill")
        self.version_combo.activated.connect(self._load_version)
        self.based_label = label("", "Faint")
        self.sub_combo = QComboBox()
        self.sub_combo.setObjectName("Pill")
        self.sub_combo.activated.connect(lambda: self._set_sub(self.sub_combo.currentData()))
        top.addWidget(self.name_label)
        top.addWidget(self.version_combo)
        top.addWidget(self.based_label)
        top.addWidget(self.sub_combo)
        for text, fn, kind in (("撤销", self.do_undo, "Ghost"), ("适应画布", self.canvas_fit, "Ghost"),
                               ("检查", self.check, "Ghost"),
                               ("让 AI 改", self.ask_ai, "Ghost"), ("保存为新版本", self.save, ""),
                               ("试运行", self._try_run, "Primary")):
            b = QPushButton(text)
            if kind:
                b.setObjectName(kind)
            b.clicked.connect(fn)
            top.addWidget(b)
        center.lay.addWidget(bar)
        self.canvas = FlowCanvas(editable=True)
        self.canvas.step_clicked.connect(self.select)
        self.canvas.step_moved.connect(self._moved)
        self.canvas.connect_steps.connect(self.connect_steps)
        self.canvas.context_action.connect(self._context)
        self.canvas.step_dropped.connect(self._step_dropped)
        self.canvas.new_dropped.connect(self._new_dropped)
        self.canvas.disconnect_edge.connect(self.disconnect)
        # 删除、撤销只在画布上生效（在属性栏、代码区打字时按退格不会删掉步骤）；保存在整个设计器里生效
        for keys, fn, owner in ((QKeySequence.Delete, lambda: self.selected and self.delete(self.selected), self.canvas),
                                (QKeySequence("Backspace"), lambda: self.selected and self.delete(self.selected), self.canvas),
                                (QKeySequence.Undo, self.do_undo, self.canvas), (QKeySequence("Ctrl+0"), self.canvas_fit, self.canvas),
                                (QKeySequence.Save, self.save, self)):
            sc = QShortcut(keys, owner)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        center.lay.addWidget(self.canvas, 1)
        self.setCentralWidget(center)

        self.palette = PaletteList()
        self.palette.setMinimumWidth(150)
        for group, items in PALETTE:
            hdr = QListWidgetItem(group)
            hdr.setFlags(Qt.NoItemFlags)                 # 分组标题：浅色小字，不能拖、不能选
            f = hdr.font()
            f.setPointSizeF(f.pointSizeF() * 0.85)
            hdr.setFont(f)
            hdr.setForeground(Qt.gray)
            self.palette.addItem(hdr)
            for kind, text in items:
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, kind)
                self.palette.addItem(it)
        self.palette.itemDoubleClicked.connect(lambda it: it.data(Qt.UserRole) and self.add_step(it.data(Qt.UserRole)))
        tip = label("把步骤拖进画布：放到连线上就插进去，放到某个步骤下面就接在它后面；也可以双击加到选中步骤后面。"
                    "鼠标移到连线上点 × 断开。", "Faint", wrap=True)
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.palette, 1)
        bl.addWidget(tip)
        self._dock("步骤箱", box, Qt.LeftDockWidgetArea, "palette")

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(lambda it: it.data(0, Qt.UserRole) and self.select(it.data(0, Qt.UserRole)))
        self._dock("步骤树", self.tree, Qt.RightDockWidgetArea, "tree")
        self.props = QWidget()
        self.form = QFormLayout(self.props)
        self.form.setLabelAlignment(Qt.AlignLeft)
        self._dock("属性", self.props, Qt.RightDockWidgetArea, "props")

        self.code = QPlainTextEdit()
        self.code.setObjectName("Code")
        self.code.textChanged.connect(self._code_edited)
        self._code_timer = QTimer(self)
        self._code_timer.setSingleShot(True)
        self._code_timer.timeout.connect(self._apply_code)
        self._dock("代码（workflows/*.toml，和画布实时同步）", self.code, Qt.BottomDockWidgetArea, "code")
        self.problems = QListWidget()
        self._dock("检查", self.problems, Qt.BottomDockWidgetArea, "check")
        docks = {d.objectName(): d for d in self.findChildren(QDockWidget)}
        self.resizeDocks([docks["palette"], docks["tree"]], [170, 300], Qt.Horizontal)
        self.resizeDocks([docks["code"]], [210], Qt.Vertical)
        st = QSettings()
        if st.value("designer/state"):
            self.restoreState(st.value("designer/state"))

    def save_layout(self):
        QSettings().setValue("designer/state", self.saveState())

    def _try_run(self):
        probs = self.check()
        if probs:
            QMessageBox.warning(self, "还不能试运行", "引擎执行不了这个流程：\n\n" + "\n".join(probs))
            return
        self.try_run.emit(self.wf)

    def canvas_fit(self):
        """整个流程缩放到刚好装下（最多放大到 1 倍；比自动适应缩得更小，保证所有步骤都看得见）。"""
        cv = self.canvas
        cv.user_view = True
        r = cv.scene().itemsBoundingRect().adjusted(-30, -30, 30, 30)
        cv.setSceneRect(r)
        cv.fitInView(r, Qt.KeepAspectRatio)
        if cv.transform().m11() > 1.0:
            cv.resetTransform()
            cv.centerOn(r.center())

    # ------------------------------------------------------------ 显示

    def render_all(self, fit: bool = True):
        self._syncing = True
        self.name_label.setText(self.wf.name)
        self.version_combo.clear()
        versions = list_versions(self.wf.name)
        draft = self.wf.version not in versions or self.wf != self.saved
        if draft:
            self.version_combo.addItem(f"{self.wf.version}（草稿）", self.wf.version)
        for v in versions:
            self.version_combo.addItem(v, v)
        self.based_label.setText(f"改自 {self.wf.based_on}" if self.wf.based_on else "")
        self.sub_combo.clear()
        for name in self.wf.subflows:
            self.sub_combo.addItem(f"子流程 {name}", name)
        if self.current_sub:
            self.sub_combo.setCurrentIndex(max(0, self.sub_combo.findData(self.current_sub)))
        states = {sid: "new" for sid in self.new_ids}
        if self.selected:
            states[self.selected] = "selected"
        self.canvas.show_workflow(self.wf, self.current_sub, states, fit=fit)
        if not fit and self.selected in self.canvas.items_by_id:     # 画面跟到刚操作的那一步（比如断开后停到旁边的空地）
            self.canvas.ensureVisible(self.canvas.items_by_id[self.selected], 60, 60)
        self._fill_tree()
        text = dump_workflow(self.wf)
        if self.code.toPlainText() != text:
            self.code.setPlainText(text)
        self._fill_props()
        self.check(quiet=True)
        self._syncing = False

    def _fill_tree(self):
        self.tree.clear()
        for name, steps in [("主流程 main", self.wf.main), *[(f"子流程 {n}", s) for n, s in self.wf.subflows.items()]]:
            top = QTreeWidgetItem([name])
            for s in steps:
                it = QTreeWidgetItem([f"{s.id}　{KIND_NAMES.get(s.kind, s.kind)}"])
                it.setData(0, Qt.UserRole, s.id)
                top.addChild(it)
                if s.id == self.selected:
                    self.tree.setCurrentItem(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)

    def check(self, quiet: bool = False):
        from ..agents.registry import default_registry
        self.problems.clear()
        probs = engine_problems(self.wf, default_registry())
        if not probs:
            for ok in ("每个步骤都连上了", "每个循环都有次数上限", "每个 Agent 步骤都有可用的 Agent", "引擎能执行这里的每一种组合"):
                self.problems.addItem(f"✓ {ok}")
        for p in probs:
            self.problems.addItem(f"✗ {p}")
        diff = self._diff_text()
        if diff:
            self.problems.addItem(f"和 {self.saved.version} 相比：{diff}")
        return probs

    def _diff_text(self) -> str:
        def ids(wf):
            return {s.id for s in wf.main} | {s.id for ss in wf.subflows.values() for s in ss}
        added, removed = ids(self.wf) - ids(self.saved), ids(self.saved) - ids(self.wf)
        parts = [f"新增 {'、'.join(sorted(added))}"] if added else []
        if removed:
            parts.append(f"删除 {'、'.join(sorted(removed))}")
        if not parts and self.wf != self.saved:
            parts.append("改了设置")
        return "；".join(parts)

    # ------------------------------------------------------------ 定位步骤

    def _where(self, sid: str) -> tuple[str, int]:
        for i, s in enumerate(self.wf.main):
            if s.id == sid:
                return "main", i
        for name, steps in self.wf.subflows.items():
            for i, s in enumerate(steps):
                if s.id == sid:
                    return name, i
        return "", -1

    def _steps(self, where: str) -> list[Step]:
        return list(self.wf.main if where == "main" else self.wf.subflows[where])

    def _set_steps(self, where: str, steps: list[Step]) -> None:
        if where == "main":
            self.wf = dataclasses.replace(self.wf, main=tuple(steps))
        else:
            subs = dict(self.wf.subflows)
            subs[where] = tuple(steps)
            self.wf = dataclasses.replace(self.wf, subflows=subs)

    def _get(self, sid: str) -> Step | None:
        where, i = self._where(sid)
        return self._steps(where)[i] if i >= 0 else None

    def _normalize(self) -> None:
        """被并行、条件引用的步骤就是连上了：清掉它的"没连上"标记和临时位置（不管是怎么连上的）。"""
        for where in ["main", *self.wf.subflows]:
            steps = self._steps(where)
            refs = {r for s in steps for r in (*s.branches, *s.then, *s.otherwise)}
            if any(s.detached and s.id in refs for s in steps):
                self._set_steps(where, [dataclasses.replace(s, detached=False, pos=None) if s.detached and s.id in refs
                                        else s for s in steps])

    def _change(self, fn, fit: bool = False):
        self.undo.append(self.wf)
        fn()
        self._normalize()
        if self.wf.version == self.saved.version and self.wf != self.saved:
            self.wf = dataclasses.replace(self.wf, version=bump(self.saved.version), based_on=self.saved.version)
        self.render_all(fit=fit)

    # ------------------------------------------------------------ 编辑操作

    def select(self, sid: str):
        self.selected = sid
        where, _ = self._where(sid)
        if where and where != "main" and where != self.current_sub:
            self.current_sub = where
            self.render_all()
            return
        states = {s: "new" for s in self.new_ids}
        states[sid] = "selected"
        self.canvas.set_states(states)
        self._fill_props()

    def _unique(self, base: str) -> str:
        n = 1
        while self._get(f"{base}_{n}") is not None:
            n += 1
        return f"{base}_{n}"

    def _new_step(self, kind: str, where: str) -> Step:
        defaults = {"agent": dict(role="coder" if where != "main" else "planner"),
                    "verify": dict(scope="all" if where == "main" else "module"),
                    "loop": dict(body="repair_round", until="verify_passed", max=6),
                    "condition": dict(cond="verify_passed"), "human": dict(enabled_by="human_review", note="等人确认"),
                    "parallel": dict(branches=()), "subflow": dict(subflow=self.current_sub or ""),
                    "foreach": dict(over="plan.modules", subflow=self.current_sub or "")}
        sid = self._unique({"agent": "agent", "human": "confirm"}.get(kind, kind))
        return Step(id=sid, kind=kind, **defaults.get(kind, {}))

    def add_step(self, kind: str):
        where = self._where(self.selected)[0] if self.selected else (self.current_sub or "main")
        if kind == "foreach":
            where = "main"
        step = self._new_step(kind, where)
        sid = step.id

        def op():
            steps = self._steps(where)
            i = next((k for k, s in enumerate(steps) if s.id == self.selected), len(steps) - 1)
            steps.insert(i + 1, step)
            self._set_steps(where, steps)
            self.new_ids.add(sid)
            self.selected = sid
        self._change(op)

    def delete(self, sid: str):
        where, i = self._where(sid)
        if i < 0:
            return

        def op():
            steps = self._steps(where)
            del steps[i]
            steps = [dataclasses.replace(s, branches=tuple(x for x in s.branches if x != sid),
                                         then=tuple(x for x in s.then if x != sid),
                                         otherwise=tuple(x for x in s.otherwise if x != sid)) for s in steps]
            self._set_steps(where, steps)
            self.new_ids.discard(sid)
            self.selected = None
        self._change(op)

    def move(self, sid: str, delta: int):
        where, i = self._where(sid)
        steps = self._steps(where)
        j = i + delta
        if i < 0 or not 0 <= j < len(steps):
            return

        def op():
            steps[i], steps[j] = steps[j], steps[i]
            self._set_steps(where, steps)
        self._change(op)

    def connect_steps(self, src: str, dst: str):
        """从 src 下沿拉线到 dst：dst 紧跟在 src 后面（src 是并行就成为分支，是条件就加到"是"分支）。
        两头都会变成"连上了"：src 原来没连上的话，就把它放到 dst 原来的位置上，dst 接在它后面。"""
        ws, i = self._where(src)
        wd, j = self._where(dst)
        if ws != wd or i < 0 or j < 0:
            QMessageBox.information(self, "不能这样连", "只能在同一个流程（主流程或同一个子流程）里连线。")
            return
        if src == dst:
            return
        s = self._steps(ws)[i]

        def op():
            steps = self._unref(self._steps(ws), dst)
            if s.kind == "parallel":
                k = next(n for n, x in enumerate(steps) if x.id == src)
                steps[k] = dataclasses.replace(steps[k], branches=tuple(dict.fromkeys((*steps[k].branches, dst))))
                steps = [dataclasses.replace(x, detached=False, pos=None) if x.id == dst else x for x in steps]
            elif s.kind == "condition":
                k = next(n for n, x in enumerate(steps) if x.id == src)
                steps[k] = dataclasses.replace(steps[k], then=tuple(dict.fromkeys((*steps[k].then, dst))))
                steps = [dataclasses.replace(x, detached=False, pos=None) if x.id == dst else x for x in steps]
            else:
                orig = [x.id for x in steps if x.id != src]
                before = orig[orig.index(dst) - 1] if orig.index(dst) > 0 else None     # dst 原来前面那一步
                moving = next(x for x in steps if x.id == dst)
                steps = [x for x in steps if x.id != dst]
                moving = dataclasses.replace(moving, detached=False, pos=None)
                if s.detached:                       # 起点原来没连上：它占 dst 原来的位置，dst 接在它后面
                    head = next(x for x in steps if x.id == src)
                    steps = [x for x in steps if x.id != src]
                    head = dataclasses.replace(head, detached=False, pos=None)
                    at = next((n + 1 for n, x in enumerate(steps) if x.id == before), 0)
                    steps[at:at] = [head, moving]
                else:
                    k = next(n for n, x in enumerate(steps) if x.id == src)
                    steps.insert(k + 1, moving)
            self._set_steps(ws, steps)
            self.selected = dst
        self._change(op)

    # ------------------------------------------------------------ 拖拽：吸附、拖离、从步骤箱拖进来（DECISIONS 第 102 条）

    def _unref(self, steps: list[Step], sid: str) -> list[Step]:
        return [dataclasses.replace(s, branches=tuple(x for x in s.branches if x != sid),
                                    then=tuple(x for x in s.then if x != sid),
                                    otherwise=tuple(x for x in s.otherwise if x != sid)) for s in steps]

    def _place(self, step: Step, where: str, target) -> list[Step] | None:
        """把 step 放到 target 处：("edge", a, b) 插进 a→b；("after", a, "") 接到 a 后面
        （a 是并行就成为一个分支，a 是条件就加到"是"分支）。返回新的步骤列表；放不了返回 None。"""
        _, anchor, _ = target
        steps = [s for s in self._steps(where) if s.id != step.id]
        steps = self._unref(steps, step.id)
        k = next((n for n, s in enumerate(steps) if s.id == anchor), -1)
        if k < 0:
            return None
        a = steps[k]
        step = dataclasses.replace(step, detached=False, pos=None)
        if target[0] == "after" and a.kind == "parallel":
            steps[k] = dataclasses.replace(a, branches=tuple(dict.fromkeys((*a.branches, step.id))))
        elif target[0] == "after" and a.kind == "condition":
            steps[k] = dataclasses.replace(a, then=tuple(dict.fromkeys((*a.then, step.id))))
        steps.insert(k + 1, step)
        return steps

    def _step_dropped(self, sid: str, x: float, y: float, target, far: bool):
        where, i = self._where(sid)
        if i < 0:
            return
        step = self._steps(where)[i]
        if target is not None:
            steps = self._place(step, where, target)
            if steps is None:
                self.render_all(fit=False)
                return

            def op():
                self._set_steps(where, steps)
                self.selected = sid
            self._change(op)
        elif far:
            def op():
                steps = self._unref(self._steps(where), sid)
                k = next(n for n, s in enumerate(steps) if s.id == sid)
                steps[k] = dataclasses.replace(steps[k], detached=True, pos=(round(x, 1), round(y, 1)))
                self._set_steps(where, steps)
                self.selected = sid
            self._change(op)
        else:
            self._moved(sid, x, y)

    def _new_dropped(self, kind: str, where: str, x: float, y: float, target):
        if kind == "foreach" and where != "main":
            QMessageBox.information(self, "不能放在这里", "\"每个模块各跑\"只能放在主流程。")
            return
        step = self._new_step(kind, where)
        if target is not None:
            steps = self._place(step, where, target)
            if steps is None:
                return
        else:
            steps = self._steps(where) + [dataclasses.replace(step, detached=True, pos=(round(x, 1), round(y, 1)))]

        def op():
            self._set_steps(where, steps)
            self.new_ids.add(step.id)
            self.selected = step.id
        self._change(op)

    def _parking(self, where: str) -> tuple[float, float]:
        """断开的步骤放到流程旁边的空地：主流程放左边，子流程放在子流程框的右边，依次往下排，不压住别的步骤。"""
        n = sum(1 for s in self._steps(where) if s.detached)
        if where == "main":
            return (-STEP_W - 80.0, 40.0 + 64 * n)
        rect = next((r for name, r in self.canvas.regions if name == where), None)
        x = rect.right() + 40 if rect is not None else 700.0
        return (round(x, 1), 40.0 + 64 * n)

    def disconnect(self, a: str, b: str):
        """断开 a→b：b 变成"没连上"，原来 b 前后的步骤自动接上。"""
        where, j = self._where(b)
        if j < 0:
            return
        pos = self._parking(where)

        def op():
            steps = self._unref(self._steps(where), b)
            k = next(n for n, s in enumerate(steps) if s.id == b)
            steps[k] = dataclasses.replace(steps[k], detached=True, pos=pos)
            self._set_steps(where, steps)
            self.selected = b
        self._change(op)

    def _moved(self, sid, x, y):
        where, i = self._where(sid)
        if i < 0:
            return
        steps = self._steps(where)
        steps[i] = dataclasses.replace(steps[i], pos=(round(x, 1), round(y, 1)))
        self._set_steps(where, steps)
        self._syncing = True
        self.code.setPlainText(dump_workflow(self.wf))
        self._syncing = False

    def _context(self, action: str, sid: str):
        if action == "delete":
            self.delete(sid)
        elif action == "detach":
            x, y = self._parking(self._where(sid)[0])
            self._step_dropped(sid, x, y, None, True)
        elif action in ("up", "down"):
            self.move(sid, -1 if action == "up" else 1)
        elif action == "ask_ai":
            self.selected = sid
            self.ask_ai()
        elif action == "ref":
            win = self.window()
            if hasattr(win, "reference_in_chat"):
                win.reference_in_chat(sid)

    def do_undo(self):
        if self.undo:
            self.wf = self.undo.pop()
            self.render_all()

    def _set_sub(self, name):
        self.current_sub = name
        self.render_all()

    # ------------------------------------------------------------ 属性

    def _fill_props(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        s = self._get(self.selected) if self.selected else None
        if s is None:
            self.form.addRow(label("点画布上的步骤，在这里改它的设置。", "Faint", wrap=True))
            return
        from ..agents.registry import default_registry
        reg = default_registry()

        def combo(items, current, key):
            cb = QComboBox()
            for val, text in items:
                cb.addItem(text, val)
            cb.setCurrentIndex(max(0, cb.findData(current)))
            cb.activated.connect(lambda: self._set_field(key, cb.currentData()))
            return cb

        def line(value, key, conv=str):
            le = QLineEdit(value)
            le.editingFinished.connect(lambda: self._set_field(key, conv(le.text())))
            return le

        def ids(text):
            return tuple(x.strip() for x in text.replace("，", ",").split(",") if x.strip())

        self.form.addRow("名称", line(s.id, "id"))
        self.form.addRow("类型", QLabel(KIND_NAMES.get(s.kind, s.kind)))
        conds = [(None, "（无）")] + [(c, COND_NAMES[c]) for c in CONDITIONS]
        if s.kind == "agent":
            roles = [("planner", "规划")] if self._where(s.id)[0] == "main" else [(r, ROLE_NAMES[r]) for r in ("coder", "tester", "reviewer")]
            self.form.addRow("角色", combo(roles, s.role, "role"))
            agents = [(None, "调度器挑")] + [(a.name, a.name) for a in reg.find(s.role or "coder")]
            self.form.addRow("指定 Agent", combo(agents, s.agent, "agent"))
        elif s.kind == "verify":
            self.form.addRow("检查范围", QLabel("全部模块" if s.scope == "all" else "本模块"))
        elif s.kind == "loop":
            self.form.addRow("每一轮", QLabel("修正一轮：诊断 → 交给编码或测试 Agent 改 → 再检查"))
            self.form.addRow("停止条件", combo([(c, COND_NAMES[c]) for c in CONDITIONS], s.until, "until"))
            sp = QSpinBox()
            sp.setRange(1, 20)
            sp.setValue(int(s.max) if str(s.max).isdigit() or isinstance(s.max, int) else 6)
            sp.editingFinished.connect(lambda: self._set_field("max", sp.value()))
            self.form.addRow("最多几次", sp)
        elif s.kind == "condition":
            self.form.addRow("判断依据", combo([(c, COND_NAMES[c]) for c in CONDITIONS], s.cond, "cond"))
            self.form.addRow("是 → 执行", line(", ".join(s.then), "then", ids))
            self.form.addRow("否 → 执行", line(", ".join(s.otherwise), "otherwise", ids))
        elif s.kind == "parallel":
            self.form.addRow("同时执行", line(", ".join(s.branches), "branches", ids))
        elif s.kind in ("subflow", "foreach"):
            self.form.addRow("子流程", combo([(n, n) for n in self.wf.subflows], s.subflow, "subflow"))
        if s.kind == "human":
            self.form.addRow("什么时候出现", combo([("human_review", "勾选了人工确认时"), (None, "总是")], s.enabled_by, "enabled_by"))
        else:
            self.form.addRow("执行条件", combo(conds, s.when, "when"))
        self.form.addRow("说明", line(s.note or "", "note"))

    def _set_field(self, key, value):
        sid = self.selected
        where, i = self._where(sid)
        if i < 0:
            return
        if key == "id":
            value = value.strip()
            if not value or (value != sid and self._get(value) is not None):
                QMessageBox.information(self, "名称不能用", f"{value or '空名称'} 已经有了或者为空")
                return

        def op():
            steps = self._steps(where)
            steps[i] = dataclasses.replace(steps[i], **{key: value})
            if key == "id":
                steps = [dataclasses.replace(s, branches=tuple(value if x == sid else x for x in s.branches),
                                             then=tuple(value if x == sid else x for x in s.then),
                                             otherwise=tuple(value if x == sid else x for x in s.otherwise))
                         for s in steps]
                self.selected = value
            self._set_steps(where, steps)
        self._change(op)

    # ------------------------------------------------------------ 代码 ↔ 画布

    def _code_edited(self):
        if not self._syncing:
            self._code_timer.start(600)

    def _apply_code(self):
        try:
            wf = parse_workflow(tomllib.loads(self.code.toPlainText()), "代码")
        except Exception as exc:
            self.problems.clear()
            self.problems.addItem(f"✗ 代码读不出来：{exc}")
            return
        if wf != self.wf:
            self.undo.append(self.wf)
            self.wf = wf
            pos = self.code.textCursor().position()
            self.render_all(fit=False)
            cur = self.code.textCursor()
            cur.setPosition(min(pos, len(self.code.toPlainText())))
            self.code.setTextCursor(cur)

    # ------------------------------------------------------------ 版本

    def _load_version(self):
        v = self.version_combo.currentData()
        if v and v != self.wf.version:
            self.wf = self.saved = load_workflow(self.wf.name, v)
            self.new_ids.clear()
            self.render_all()

    def save(self):
        probs = self.check()
        if probs:
            QMessageBox.warning(self, "还不能保存", "引擎执行不了这个流程：\n\n" + "\n".join(probs))
            return
        if self.wf == self.saved:
            QMessageBox.information(self, "没有改动", "和当前版本一样，不需要保存。")
            return
        save_workflow(self.wf)
        self.saved = self.wf
        self.new_ids.clear()
        self.render_all()

    # ------------------------------------------------------------ 让 AI 改

    def ask_ai(self, instruction: str | None = None):
        if instruction is None:
            text, ok = QInputDialog.getMultiLineText(self, "让 AI 改工作流",
                                                     "想怎么改？例如\"审查之后加一步人工验收\"。选中的步骤会一起交给 AI。")
            if not ok or not text.strip():
                return
            instruction = text
        from .. import config
        from ..chat import propose_workflow_edit
        from ..llm import OpenAICompatClient
        try:
            llm = OpenAICompatClient.from_settings(config.load_settings())
        except Exception as exc:
            QMessageBox.warning(self, "没法联系模型", str(exc))
            return
        sel = [self.selected] if self.selected else []
        self._ai = _Worker(lambda: propose_workflow_edit(llm, config.load_settings(), self.wf, instruction, sel))
        self._ai.finished.connect(self._ai_done)
        self._ai.start()
        self.problems.clear()
        self.problems.addItem("AI 正在改……")

    def _ai_done(self):
        if self._ai.error:
            QMessageBox.warning(self, "AI 没有改成", self._ai.error)
            return
        new, probs, _sent, raw = self._ai.result
        if new is None or probs:
            QMessageBox.warning(self, "AI 的修改不能用", "\n".join(probs) or "没有拿到新的工作流")
            return
        old_ids = {s.id for s in self.wf.main} | {s.id for ss in self.wf.subflows.values() for s in ss}
        self.undo.append(self.wf)
        self.wf = new
        self.new_ids = ({s.id for s in new.main} | {s.id for ss in new.subflows.values() for s in ss}) - old_ids
        self.render_all()
        ok = QMessageBox.question(self, "接受 AI 的修改？", f"新增的步骤已用浅绿色标出：{'、'.join(sorted(self.new_ids)) or '（没有新增，改了设置）'}。\n"
                                  "接受后还需要点\"保存为新版本\"才会写进文件。")
        if ok != QMessageBox.Yes:
            self.do_undo()

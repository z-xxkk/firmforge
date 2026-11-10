"""画布：模块图（运行时看各模块进度）和流程图（设计器编辑、运行时高亮当前步骤）。

样式按界面草图第 4 版：淡点阵底、白色圆角卡片、平滑曲线连线；已接通的依赖用强调色实线，没接通的用浅灰点线；
每个模块卡片下沿 5 段圆头小条 = 5 层检查；进行中的模块外面一圈柔光。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu

from .layout import (UNIT_NAMES, ModuleView, RunView, attached_ids, flow_positions, grid_positions, layer_names,
                     moved_far, snap_target)
from .theme import MONO
from .widgets import T

CARD_W, CARD_H = 180, 96


def _mono(size=10, bold=False) -> QFont:
    f = QFont()
    f.setFamilies([x.strip(' "') for x in MONO.split(",")])
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


class DotGridView(QGraphicsView):
    """带淡点阵背景的画布：滚轮缩放、按住空白处拖动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.user_view = False      # 用户自己缩放、拖动过画面以后，窗口大小变化不再自动重新适应（不然缩放会被重置）

    def drawBackground(self, p: QPainter, rect: QRectF):
        p.fillRect(rect, QColor(T()["canvas"]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T()["dot"]))
        step = 22
        x0 = int(rect.left()) - int(rect.left()) % step
        y0 = int(rect.top()) - int(rect.top()) % step
        y = y0
        while y < rect.bottom():
            x = x0
            while x < rect.right():
                p.drawEllipse(QPointF(x, y), 1.0, 1.0)
                x += step
            y += step

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.ControlModifier or True:
            f = 1.12 if ev.angleDelta().y() > 0 else 1 / 1.12
            self.scale(f, f)
            self.user_view = True

    def fit(self):
        """缩放到刚好装下；最多放大到 1 倍，最少缩到 0.85 倍（再小字就看不清，宁可滚动）。"""
        r = self.scene().itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self.setSceneRect(r)
        self.fitInView(r, Qt.KeepAspectRatio)
        k = self.transform().m11()
        if k > 1.0 or k < 0.85:
            self.resetTransform()
            if k < 0.85:
                self.scale(0.85, 0.85)
            self.centerOn(r.center())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.scene() and self.scene().items() and not self.user_view:
            self.fit()


class ModuleCard(QGraphicsObject):
    clicked = Signal(str)

    def __init__(self, mv: ModuleView):
        super().__init__()
        self.mv = mv
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        self.selected = False

    def boundingRect(self):
        return QRectF(-8, -8, CARD_W + 16, CARD_H + 16)

    def paint(self, p: QPainter, opt, widget=None):
        c, mv = T(), self.mv
        p.setRenderHint(QPainter.Antialiasing)
        waiting = mv.status in ("pending", "skipped")
        if mv.status == "running":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["run_soft"]))
            p.drawRoundedRect(QRectF(-5, -5, CARD_W + 10, CARD_H + 10), 18, 18)
        border = c["run"] if mv.status == "running" else (c["accent"] if self.selected else c["line"])
        pen = QPen(QColor(border), 1.2)
        if waiting:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(c["surface"]))
        p.drawRoundedRect(QRectF(0, 0, CARD_W, CARD_H), 14, 14)
        p.setFont(_mono(10.5, True))
        p.setPen(QColor(c["ink3"] if waiting else c["ink"]))
        p.drawText(QRectF(16, 12, 110, 20), Qt.AlignLeft | Qt.AlignVCenter, mv.id)
        if mv.level:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["run_soft"]))
            p.drawRoundedRect(QRectF(70, 14, 46, 18), 9, 9)
            p.setPen(QColor(c["run"]))
            p.setFont(QFont(p.font().family(), 8))
            p.drawText(QRectF(70, 14, 46, 18), Qt.AlignCenter, f"升级 {mv.level}")
        color = {"done": c["pass"], "failed": c["fail"], "running": c["run"]}.get(mv.status)
        if color:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(color))
        else:
            p.setPen(QPen(QColor(c["ink3"]), 1))
            p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(CARD_W - 18, 22), 4.5, 4.5)
        f = QFont()
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QColor(c["ink3"] if waiting else c["ink2"]))
        status = {"pending": "等待", "running": "进行中", "done": "通过", "failed": "失败", "skipped": "跳过"}[mv.status]
        who = UNIT_NAMES.get(mv.unit) or mv.agent           # 新流程写"硬件层，上板"，旧流程写 Agent 名
        sub = (f"{who}，" if who else "") + (f"第 {mv.attempt} 次" if mv.attempt and mv.status == "running" else status)
        if waiting and mv.deps:
            sub = f"等 {'、'.join(mv.deps)} 完成" if mv.status == "pending" else (mv.note or status)
        p.drawText(QRectF(16, 34, CARD_W - 28, 20), Qt.AlignLeft | Qt.AlignVCenter, sub)
        keys = mv.layer_keys
        seg = (5 * 30 - 4 - 4 * (len(keys) - 1)) / len(keys)   # 段数不同时总长一样
        for i, layer in enumerate(keys):
            st = mv.layers.get(layer)
            col = {"pass": c["accent"], "fail": c["fail"], "running": c["run"]}.get(st, c["line"])
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(16 + i * (seg + 4), 70, seg, 6), 3, 3)

    def mousePressEvent(self, ev):
        self.clicked.emit(self.mv.id)

    def hoverEnterEvent(self, ev):
        ln = layer_names(self.mv.layer_keys)
        state = {"pass": "通过", "fail": "失败", "skipped": "跳过", "running": "检查中"}
        names = "、".join(f"{ln[l]} {state.get(self.mv.layers.get(l), '未检查')}" for l in self.mv.layer_keys)
        self.setToolTip(f"{self.mv.id}：{self.mv.title}\n{names}")


def curve(a: QPointF, b: QPointF) -> QPainterPath:
    """平滑连线：竖直对齐时画直线；否则用三次贝塞尔曲线，控制点在两端的正下方 / 正上方，不会绕成 S 形的圈。"""
    path = QPainterPath(a)
    if abs(b.x() - a.x()) < 1:
        path.lineTo(b)
        return path
    dy = abs(b.y() - a.y()) * 0.5
    path.cubicTo(QPointF(a.x(), a.y() + dy), QPointF(b.x(), b.y() - dy), b)
    return path


class ModuleGraph(DotGridView):
    """模块图：由 RunView 画出来，事件来了调用 refresh()。"""
    module_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.cards: dict[str, ModuleCard] = {}
        self.edges: list[tuple[QGraphicsPathItem, str, str]] = []
        self._shape: tuple = ()
        self.selected = ""

    def refresh(self, view: RunView) -> None:
        ids = [m for m in view.modules]
        shape = tuple((m, tuple(view.modules[m].deps)) for m in ids)
        if shape != self._shape:
            self._rebuild(view, ids)
            self._shape = shape
        for mid, card in self.cards.items():
            card.mv = view.modules[mid]
            card.selected = mid == self.selected
            card.update()
        c = T()
        for item, src, dst in self.edges:
            done = view.modules.get(src) and view.modules[src].status == "done"
            pen = QPen(QColor(c["accent"] if done else "#C5CBD3"), 2 if done else 1.6)
            pen.setCapStyle(Qt.RoundCap)
            if not done:
                pen.setStyle(Qt.CustomDashLine)
                pen.setDashPattern([0.5, 4])
            item.setPen(pen)

    def _rebuild(self, view: RunView, ids: list[str]) -> None:
        sc = self.scene()
        sc.clear()
        self.cards.clear()
        self.edges.clear()
        deps = {m: list(view.modules[m].deps) for m in ids}
        pos = grid_positions(ids, deps, w=CARD_W, h=CARD_H)
        for mid in ids:
            card = ModuleCard(view.modules[mid])
            card.setPos(*pos[mid])
            card.clicked.connect(self._click)
            sc.addItem(card)
            self.cards[mid] = card
        for mid in ids:
            for d in deps[mid]:
                if d in pos:
                    a = QPointF(pos[d][0] + CARD_W / 2, pos[d][1] + CARD_H)
                    b = QPointF(pos[mid][0] + CARD_W / 2, pos[mid][1])
                    item = sc.addPath(curve(a, b))
                    item.setZValue(-1)
                    self.edges.append((item, d, mid))
        self.fit()

    def _click(self, mid: str):
        self.selected = mid
        for k, card in self.cards.items():
            card.selected = k == mid
            card.update()
        self.module_clicked.emit(mid)


# ---------------------------------------------------------------- 流程图

KIND_NAMES = {"agent": "Agent", "verify": "检查", "parallel": "并行", "condition": "条件", "loop": "循环",
              "subflow": "子流程", "foreach": "每个模块各跑", "human": "等人确认"}
ROLE_NAMES = {"planner": "规划", "coder": "编码", "tester": "测试", "reviewer": "审查", "diagnoser": "诊断",
              "analyst": "需求分析"}
COND_NAMES = {"verify_passed": "检查通过了", "signature_repeated": "同一错误反复出现", "attempts_exhausted": "次数用完",
              "review_needs_fix": "审查要求修改", "human_review": "开启了人工确认"}
STEP_W, STEP_H = 160, 42


def step_subtitle(s) -> str:
    if s.kind == "agent":
        return f"Agent {ROLE_NAMES.get(s.role, s.role)}" + (f"（{s.agent}）" if s.agent else "")
    if s.kind == "verify":
        return "检查：全部模块" if s.scope == "all" else "检查：本模块"
    if s.kind == "loop":
        return f"循环，最多 {s.max} 次" if isinstance(s.max, int) else "循环，次数上限按设置"
    if s.kind == "condition":
        return COND_NAMES.get(s.cond, s.cond or "") + "？"
    if s.kind in ("subflow", "foreach"):
        return f"{KIND_NAMES[s.kind]} {s.subflow}"
    return KIND_NAMES.get(s.kind, s.kind)


STEP_MIME = "application/x-firmforge-step"      # 从步骤箱拖出来的步骤类型


class StepItem(QGraphicsObject):
    """流程图里的一个步骤。设计器里可以拖动：小范围挪动时连线跟着走；拖远了连线断开，松手时靠近连线或步骤就吸附接上。"""
    moved = Signal(str, float, float)
    clicked = Signal(str)
    connect_to = Signal(str, str)

    def __init__(self, step, editable: bool):
        super().__init__()
        self.step = step
        self.editable = editable
        self.state = ""            # "" | running | done | new | selected
        self.setFlag(QGraphicsItem.ItemIsMovable, editable)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, editable)
        self.setCursor(Qt.PointingHandCursor)
        self._drag_line = None
        self._origin: QPointF | None = None
        self.canvas: "FlowCanvas | None" = None

    def boundingRect(self):
        return QRectF(-6, -6, STEP_W + 12, STEP_H + 18)

    def paint(self, p: QPainter, opt, widget=None):
        c, s = T(), self.step
        p.setRenderHint(QPainter.Antialiasing)
        if self.state in ("running", "selected"):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["run_soft"]))
            p.drawRoundedRect(QRectF(-5, -5, STEP_W + 10, STEP_H + 10), 16, 16)
        fill = c["accent_soft"] if self.state == "new" else c["surface"]
        edge = c["run"] if self.state in ("running", "selected") else (c["accent"] if self.state in ("done", "new") else c["line"])
        if s.detached:
            edge = c["fail"]
        pen = QPen(QColor(edge), 1.3)
        radius = 12
        if s.kind == "human" or s.detached:
            pen.setStyle(Qt.DashLine)
        if s.kind == "human":
            radius = STEP_H / 2
        p.setPen(pen)
        p.setBrush(QColor(fill))
        if s.kind == "condition":
            path = QPainterPath()
            path.moveTo(STEP_W / 2, 0)
            path.lineTo(STEP_W, STEP_H / 2)
            path.lineTo(STEP_W / 2, STEP_H)
            path.lineTo(0, STEP_H / 2)
            path.closeSubpath()
            p.drawPath(path)
        elif s.kind == "parallel":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["fail"] if s.detached else c["ink2"]))
            p.drawRoundedRect(QRectF(0, STEP_H - 12, STEP_W, 6), 3, 3)     # 横条在下、名字在上：分叉线从横条下面出去，不压字
        else:
            if s.kind == "foreach":
                p.drawRoundedRect(QRectF(5, 5, STEP_W, STEP_H), radius, radius)
            p.drawRoundedRect(QRectF(0, 0, STEP_W, STEP_H), radius if s.kind != "loop" else 18,
                              radius if s.kind != "loop" else 18)
        p.setPen(QColor(c["ink"]))
        if s.kind == "parallel":
            p.setFont(_mono(9, True))
            p.drawText(QRectF(0, 8, STEP_W, 18), Qt.AlignCenter, f"{s.id}（并行）" + ("，没连上" if s.detached else ""))
            return
        p.setFont(_mono(9.5, True))
        p.drawText(QRectF(12, 3, STEP_W - 20, 20), Qt.AlignLeft | Qt.AlignVCenter if s.kind != "condition" else Qt.AlignCenter, s.id)
        f = QFont()
        f.setPointSizeF(8.5)
        p.setFont(f)
        p.setPen(QColor(c["fail"] if s.detached else c["ink3"]))
        p.drawText(QRectF(12, 20, STEP_W - 20, 18), Qt.AlignLeft | Qt.AlignVCenter if s.kind != "condition" else Qt.AlignCenter,
                   ("没连上" if s.kind == "condition" else "没连上：拖到连线或步骤下面") if s.detached else step_subtitle(s))
        if self.editable:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(c["ink3"]))
            p.drawEllipse(QPointF(STEP_W / 2, STEP_H + 6), 3, 3)     # 连线把手

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and self.editable and self.canvas is not None \
                and self._origin is not None:
            self.canvas._dragging(self)
        return super().itemChange(change, value)

    def mousePressEvent(self, ev):
        self.clicked.emit(self.step.id)
        if self.editable and ev.pos().y() > STEP_H:
            self._drag_line = self.scene().addLine(0, 0, 0, 0, QPen(QColor(T()["accent"]), 1.5, Qt.DashLine))
            self._drag_from = self.mapToScene(QPointF(STEP_W / 2, STEP_H + 6))
            ev.accept()
            return
        self._origin = QPointF(self.pos())
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._drag_line is not None:
            to = ev.scenePos()
            self._drag_line.setLine(self._drag_from.x(), self._drag_from.y(), to.x(), to.y())
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._drag_line is not None:
            self.scene().removeItem(self._drag_line)
            self._drag_line = None
            for it in self.scene().items(ev.scenePos()):
                if isinstance(it, StepItem) and it is not self:
                    self.connect_to.emit(self.step.id, it.step.id)
                    break
            return
        super().mouseReleaseEvent(ev)
        origin, self._origin = self._origin, None
        if self.editable and self.canvas is not None and origin is not None and origin != self.pos():
            self.canvas._dropped(self, origin)


class EdgeItem(QGraphicsPathItem):
    """连线。设计器里鼠标移上去变色并在中点出现 ×，点 × 断开（n8n 的做法：悬停连线后删除）。"""

    def __init__(self, a: str, b: str, path: QPainterPath, canvas: "FlowCanvas", deletable: bool):
        super().__init__(path)
        self.a, self.b, self.canvas, self.deletable = a, b, canvas, deletable
        self.branch = False                 # 并行、条件到分支的线：可以断开，不能把步骤插进去
        self.logical = (a, b)               # 汇合线（分支 → 下一步）画的是分支到下一步，含义是"并行 → 下一步"
        self.hot = False
        self.setAcceptHoverEvents(deletable)
        self._style()

    def mid(self) -> QPointF:
        return self.path().pointAtPercent(0.5)

    def _style(self):
        c = T()
        pen = QPen(QColor(c["accent"] if self.hot else "#B8C0C9"), 2.4 if self.hot else 1.6)
        pen.setCapStyle(Qt.RoundCap)
        self.setPen(pen)

    def shape(self):                                   # 线周围 8 像素都算"在线上"，好点中
        from PySide6.QtGui import QPainterPathStroker
        st = QPainterPathStroker()
        st.setWidth(16)
        return st.createStroke(self.path())

    def boundingRect(self):
        return super().boundingRect().adjusted(-12, -12, 12, 12)

    def paint(self, p, opt, widget=None):
        super().paint(p, opt, widget)
        if self.hot:
            c, m = T(), self.mid()
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(c["accent"]), 1.2))
            p.setBrush(QColor(c["surface"]))
            p.drawEllipse(m, 9, 9)
            p.setPen(QPen(QColor(c["accent"]), 1.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(m + QPointF(-3.5, -3.5), m + QPointF(3.5, 3.5))
            p.drawLine(m + QPointF(-3.5, 3.5), m + QPointF(3.5, -3.5))

    def hoverEnterEvent(self, ev):
        self.hot = True
        self._style()
        self.setToolTip(f"{self.a} → {self.b}：点中间的 × 断开这条连线")
        self.update()

    def hoverLeaveEvent(self, ev):
        self.hot = False
        self._style()
        self.update()

    def mousePressEvent(self, ev):
        if self.deletable and (ev.scenePos() - self.mid()).manhattanLength() <= 14:
            self.canvas.disconnect_edge.emit(self.a, self.b)
            ev.accept()
            return
        ev.ignore()


class FlowCanvas(DotGridView):
    """流程图：设计器里可编辑（拖动、吸附、从步骤箱拖进来、从下沿拉线、悬停连线断开），对话页里只读并高亮当前步骤。"""
    step_clicked = Signal(str)
    step_moved = Signal(str, float, float)
    connect_steps = Signal(str, str)
    context_action = Signal(str, str)      # (动作, 步骤 id)
    step_dropped = Signal(str, float, float, object, bool)   # (步骤, x, y, 吸附目标或 None, 是否拖离了原位)
    new_dropped = Signal(str, str, float, float, object)    # (类型, 流程, x, y, 吸附目标或 None)
    disconnect_edge = Signal(str, str)

    def __init__(self, editable: bool = False, parent=None):
        super().__init__(parent)
        self.editable = editable
        self.setScene(QGraphicsScene(self))
        self.items_by_id: dict[str, StepItem] = {}
        self.edges: list[EdgeItem] = []
        self.flow_of: dict[str, str] = {}
        self.regions: list[tuple[str, QRectF]] = []
        self._preview = None
        if editable:
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.setAcceptDrops(True)

    def show_workflow(self, wf, subflow: str | None = None, states: dict | None = None, fit: bool = True) -> None:
        """画主流程，右侧再画第一个子流程（或指定的子流程）。没连上的步骤不连线，用红色虚线框标出。"""
        sc = self.scene()
        sc.clear()
        self._preview = None
        self.items_by_id.clear()
        self.edges.clear()
        self.flow_of.clear()
        self.regions.clear()
        c = T()
        groups = [("main", list(wf.main))]
        sub = subflow or next(iter(wf.subflows), None)
        if sub:
            groups.append((sub, list(wf.subflows[sub])))
        right = 0.0                                            # 已经画好的流程的右边界，下一个流程排在它右边
        self.floating: set[str] = set()                        # 没连上的步骤，以及跟着它们的分支
        self.heads: set[str] = set()                           # 各流程的第一步（拖到它上方 = 放到最前面）
        self.empty_flows: set[str] = set()                     # 一个连着的步骤都没有的流程
        for name, steps in groups:
            kw = dict(y0=40, w=STEP_W, h=STEP_H, gap=30)
            att = attached_ids(steps)
            self.floating |= {s.id for s in steps} - att
            x0 = 0.0
            if name != "main":                                 # 先排一遍量出左边界，再整体挪到主流程右边
                draft = flow_positions(steps, x0=0.0, **kw)
                x0 = right + 110 - min([p[0] for sid, p in draft.items() if sid in att] or [0.0])
            pos = flow_positions(steps, x0=x0, **kw)
            ys = [p[1] for sid, p in pos.items() if sid in att] or [40]
            xs = [p[0] for sid, p in pos.items() if sid in att] or [x0]
            frame = QRectF(min(xs) - 24, 0, max(xs) - min(xs) + STEP_W + 48, max(ys) + STEP_H + 40)
            if name != "main":                                 # 没连上的步骤：主流程的放左边，子流程的放框的右边
                pos = flow_positions(steps, x0=x0, park=(frame.right() + 40, 40), **kw)
            right = max(right, frame.right())
            self.regions.append((name, frame))
            if name != "main":
                box = sc.addRect(frame, QPen(QColor(c["line"]), 1), QBrush(QColor(c["surface"])))
                box.setOpacity(0.6)
                box.setZValue(-2)
                t = sc.addText(f"子流程 {name}")
                t.setDefaultTextColor(QColor(c["ink2"]))
                t.setPos(frame.left() + 6, 6)
            prev = None
            refd = {r for s in steps for r in (*s.branches, *s.then, *s.otherwise)}
            by_id = {s.id: s for s in steps}
            for s in steps:
                it = StepItem(s, self.editable)
                it.canvas = self
                it.setPos(*pos[s.id])
                it.state = (states or {}).get(s.id, "")
                it.clicked.connect(self.step_clicked.emit)
                it.moved.connect(self._moved)
                it.connect_to.connect(self.connect_steps.emit)
                sc.addItem(it)
                self.items_by_id[s.id] = it
                self.flow_of[s.id] = name
            for s in steps:                                    # 主干：顶层、连着的步骤按先后连起来
                if s.id in refd or s.detached:
                    continue
                if prev is None:
                    self.heads.add(s.id)
                else:
                    forks = [k for k in by_id[prev].branches if k in self.items_by_id]
                    if forks:                                  # 上一步是并行：各分支汇合到这一步
                        for k in forks:
                            self._edge(k, s.id, join=(prev, s.id))
                    else:
                        self._edge(prev, s.id)
                prev = s.id
            if prev is None:
                self.empty_flows.add(name)
            for s in steps:                                    # 分支线：并行、条件到它的分支（没连上的并行也带着分支）
                for k in (*s.branches, *s.then, *s.otherwise):
                    self._edge(s.id, k, label="是" if k in s.then else ("否" if k in s.otherwise else ""), branch=True)
        if fit:
            self.fit()

    def _edge_path(self, a: StepItem, b: StepItem) -> QPainterPath:
        pa = a.pos() + QPointF(STEP_W / 2, STEP_H)
        pb = b.pos() + QPointF(STEP_W / 2, 0)
        if abs(b.pos().y() - a.pos().y()) < STEP_H / 2:     # 同一行（分支）：从右侧连出去
            pa = a.pos() + QPointF(STEP_W, STEP_H / 2)
            pb = b.pos() + QPointF(0, STEP_H / 2)
            if b.pos().x() - a.pos().x() > STEP_W + 60:     # 中间隔着别的分支：从上方绕过去，不穿过它
                pb = b.pos() + QPointF(STEP_W / 2, 0)
                path = QPainterPath(pa)
                path.cubicTo(QPointF(pa.x() + 30, pa.y() - 46), QPointF(pb.x(), pb.y() - 46), pb)
                return path
            path = QPainterPath(pa)
            path.cubicTo(QPointF(pa.x() + 30, pa.y()), QPointF(pb.x() - 30, pb.y()), pb)
            return path
        return curve(pa, pb)

    def _edge(self, a: str, b: str, label: str = "", branch: bool = False, join: tuple[str, str] | None = None) -> None:
        ia, ib = self.items_by_id.get(a), self.items_by_id.get(b)
        if ia is None or ib is None:
            return
        # 汇合线不能单独断开（它不是一条独立的连接，断开分支要点"并行 → 分支"那条线）
        item = EdgeItem(a, b, self._edge_path(ia, ib), self, self.editable and join is None)
        item.branch = branch
        if join is not None:
            item.logical = join
        item.setZValue(-1)
        self.scene().addItem(item)
        self.edges.append(item)
        if label:
            t = self.scene().addText(label)
            t.setDefaultTextColor(QColor(T()["ink3"]))
            m = item.mid()
            t.setPos(m + QPointF(-6, -20))

    # ------------------------------------------------------------ 拖动与吸附（DECISIONS 第 102 条）

    def _targets(self, flow: str | None, exclude: str = ""):
        """可以吸附的地方：同一个流程里已连上的步骤（接到它后面）和顺序连线（插进去）。"""
        steps = {sid: (it.pos().x(), it.pos().y()) for sid, it in self.items_by_id.items()
                 if sid not in self.floating and (flow is None or self.flow_of[sid] == flow) and sid != exclude}
        edges = [(*e.logical, (e.mid().x(), e.mid().y())) for e in self.edges
                 if not e.branch and e.a in steps and e.b in steps and exclude not in (e.a, e.b, *e.logical)]
        self._branch_of = {r: sid for sid in steps for r in (*self.items_by_id[sid].step.branches,
                                                              *self.items_by_id[sid].step.then,
                                                              *self.items_by_id[sid].step.otherwise) if r in steps}
        self._parents = {sid for sid in steps if self.items_by_id[sid].step.kind in ("parallel", "condition")}
        return steps, edges

    def _snap(self, top: QPointF, flow: str | None, exclude: str = ""):
        steps, edges = self._targets(flow, exclude=exclude)
        target = snap_target((top.x(), top.y()), steps, edges, w=STEP_W, h=STEP_H, exclude=exclude,
                             branch_of=self._branch_of, parents=self._parents, heads=self.heads)
        if target is not None and target[0] == "edge":        # 还没有分支的并行：拖到它下面是成为它的第一个分支
            a = self.items_by_id[target[1]].step
            if a.kind == "parallel" and not a.branches:
                target = ("after", a.id, "")
        if target is None:                                     # 这个流程一个连着的步骤都没有：放下就是第一步
            f = flow or self._flow_at(top)
            if f in self.empty_flows:
                target = ("first", f, "")
        return target

    def _flow_at(self, p: QPointF) -> str:
        for name, rect in reversed(self.regions):
            if name != "main" and rect.contains(p):
                return name
        return "main"

    def _show_preview(self, target, top: QPointF) -> None:
        sc = self.scene()
        if self._preview is not None:
            sc.removeItem(self._preview)
            self._preview = None
        for e in self.edges:
            if e.hot and not e.isUnderMouse():
                e.hot = False
                e._style()
                e.update()
        if target is None:
            return
        kind, a, b = target
        if kind == "first":
            return
        if kind == "before":                                   # 放到最前面：从被拖步骤的下沿连到第一步的上沿
            head = self.items_by_id[a]
            pen = QPen(QColor(T()["accent"]), 2, Qt.DashLine)
            pen.setCapStyle(Qt.RoundCap)
            self._preview = sc.addPath(curve(top + QPointF(0, STEP_H), head.pos() + QPointF(STEP_W / 2, 0)), pen)
            self._preview.setZValue(5)
            return
        anchor = self.items_by_id[b if kind == "branch" else a]
        side = kind == "branch" or anchor.step.kind in ("parallel", "condition")
        if side and top.y() < anchor.pos().y() + STEP_H:          # 并列：从旁边那一步的侧面连过来
            left = top.x() < anchor.pos().x() + STEP_W / 2
            start = anchor.pos() + QPointF(0 if left else STEP_W, STEP_H / 2)
            end = top + QPointF(STEP_W / 2 if left else -STEP_W / 2, STEP_H / 2)
            path = QPainterPath(start)
            path.cubicTo(QPointF(start.x() + 20, start.y()), QPointF(end.x() - 20, end.y()), end)
        else:
            start = anchor.pos() + QPointF(STEP_W / 2, STEP_H)
            path = curve(start, top)
        pen = QPen(QColor(T()["accent"]), 2, Qt.DashLine)
        pen.setCapStyle(Qt.RoundCap)
        self._preview = sc.addPath(path, pen)
        self._preview.setZValue(5)
        if kind == "edge":
            for e in self.edges:
                if e.logical == (a, b) and not e.branch:
                    e.hot = True
                    e._style()
                    e.update()

    def _attached(self, sid: str) -> list[EdgeItem]:
        return [e for e in self.edges if sid in (e.a, e.b)]

    def _dragging(self, item: StepItem) -> None:
        """拖动中：没拖远时连线跟着走；拖远了连线隐藏（断开），并提示松手会吸附到哪里。"""
        sid = item.step.id
        far = sid in self.floating or moved_far((item._origin.x(), item._origin.y()), (item.pos().x(), item.pos().y()))
        for e in self._attached(sid):
            e.setVisible(not far)
            if not far:
                e.setPath(self._edge_path(self.items_by_id[e.a], self.items_by_id[e.b]))
        target = None
        if far:
            target = self._snap(item.pos() + QPointF(STEP_W / 2, 0), self.flow_of.get(sid), exclude=sid)
        self._show_preview(target, item.pos() + QPointF(STEP_W / 2, 0))

    def _dropped(self, item: StepItem, origin: QPointF) -> None:
        sid = item.step.id
        far = sid in self.floating or moved_far((origin.x(), origin.y()), (item.pos().x(), item.pos().y()))
        target = None
        if far:
            target = self._snap(item.pos() + QPointF(STEP_W / 2, 0), self.flow_of.get(sid), exclude=sid)
        self._show_preview(None, QPointF())
        self.step_dropped.emit(sid, item.pos().x(), item.pos().y(), target, far)

    # 从步骤箱拖进来
    def dragEnterEvent(self, ev):
        if self.editable and ev.mimeData().hasFormat(STEP_MIME):
            ev.acceptProposedAction()
        else:
            super().dragEnterEvent(ev)

    def _drop_target(self, ev):
        p = self.mapToScene(ev.position().toPoint())
        flow = self._flow_at(p)
        target = self._snap(p, None)
        if target is not None:
            flow = target[1] if target[0] == "first" else self.flow_of[target[1]]
        return p, flow, target

    def dragMoveEvent(self, ev):
        if self.editable and ev.mimeData().hasFormat(STEP_MIME):
            p, _, target = self._drop_target(ev)
            self._show_preview(target, p)
            ev.acceptProposedAction()
        else:
            super().dragMoveEvent(ev)

    def dragLeaveEvent(self, ev):
        self._show_preview(None, QPointF())
        super().dragLeaveEvent(ev)

    def dropEvent(self, ev):
        if not (self.editable and ev.mimeData().hasFormat(STEP_MIME)):
            return super().dropEvent(ev)
        p, flow, target = self._drop_target(ev)
        self._show_preview(None, QPointF())
        kind = bytes(ev.mimeData().data(STEP_MIME)).decode("utf-8")
        self.new_dropped.emit(kind, flow, p.x() - STEP_W / 2, p.y(), target)
        ev.acceptProposedAction()

    # 中键按住拖动画布（主流画布软件的平移方式）
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton:
            self._pan = ev.position()
            self.user_view = True
            self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if getattr(self, "_pan", None) is not None:
            d = ev.position() - self._pan
            self._pan = ev.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if getattr(self, "_pan", None) is not None and ev.button() == Qt.MiddleButton:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(ev)

    def _moved(self, sid, x, y):
        self.step_moved.emit(sid, x, y)

    def set_states(self, states: dict[str, str]) -> None:
        for sid, it in self.items_by_id.items():
            it.state = states.get(sid, "")
            it.update()

    def contextMenuEvent(self, ev):
        if not self.editable:
            return
        item = self.itemAt(ev.pos())
        if isinstance(item, EdgeItem):
            m = QMenu(self)
            m.addAction(f"断开 {item.a} → {item.b}", lambda: self.disconnect_edge.emit(item.a, item.b))
            m.exec(ev.globalPos())
            return
        while item is not None and not isinstance(item, StepItem):
            item = item.parentItem()
        if item is None:
            return
        m = QMenu(self)
        acts = [("up", "上移一步"), ("down", "下移一步"), ("detach", "从流程里拿出来（断开连线）"),
                ("ask_ai", "让 AI 改这一步…"), ("ref", "引用到对话"), ("delete", "删除")]
        for act, text in acts:
            if act == "detach" and item.step.detached:
                continue
            m.addAction(text, lambda a=act, sid=item.step.id: self.context_action.emit(a, sid))
        m.exec(ev.globalPos())

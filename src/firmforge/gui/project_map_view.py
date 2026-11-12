"""对话页右上的工程图和接线图（DECISIONS 第 109、113 条），数据来自 embedded/project_map.py，只画能保证准确的内容。

工程图：从上到下 逻辑层 → 驱动层 → 芯片外设（带引脚）→ 外接器件。层间灰线是上层代码里字面用到的下层接口，
驱动到外设、外设到器件的绿线来自代码里的引脚宏、器件宏和项目接线。平时积木只写模块名和测试结果；
"展开"后积木里列出接口，连线上写出用到的函数名；平时鼠标停上去也能看到。

接线图：这个项目用到的每个引脚接到哪、哪些引脚保留不能用、哪些空着；器件有没有在上板时应答（实测确认）。
只画运行记录里有的连接，不画电源和地线。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, \
    QGraphicsSimpleTextItem

from ..embedded.project_map import MapModule, ProjectMap
from .graph import DotGridView, _mono
from .widgets import T

W_COMPACT, H_COMPACT = 170, 52
W_EXPANDED = 250
LINE_H = 16
MAX_LINES = 6
ROW_GAP = 70
COL_GAP = 26


def _font(size=9.0, bold=False) -> QFont:
    """画布里的文字也用界面的无衬线字体（有的 Linux 把默认字体映射成衬线体）。"""
    from .theme import SANS
    f = QFont()
    f.setFamilies([x.strip(' "') for x in SANS.split(",")])
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def _status_colors(status: str) -> tuple[str, str, bool]:
    """(边框, 底色, 虚线)。"""
    c = T()
    return {"pass": (c["accent"], c["surface"], False), "fail": (c["fail"], c["fail_soft"], False),
            "running": (c["run"], c["run_soft"], False), "skipped": (c["ink3"], c["surface"], True)}.get(
        status, (c["line"], c["surface"], True))


class BlockCard(QGraphicsObject):
    """一块积木（模块）：名字、测试结果；展开时列出接口，没过的用例用到的接口标红。"""
    clicked = Signal(str)

    def __init__(self, m: MapModule, expanded: bool, status: str, selected: bool):
        super().__init__()
        self.m, self.expanded, self.status, self.selected = m, expanded, status, selected
        self.w = W_EXPANDED if expanded else W_COMPACT
        n = min(len(m.interfaces), MAX_LINES) + (1 if len(m.interfaces) > MAX_LINES else 0)
        self.h = H_COMPACT + (n * LINE_H + 8 if expanded and m.interfaces else 0)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        tip = [f"{m.id}　{m.title}", f"{'硬件层，上板' if m.layer == 'hardware' else '逻辑层，电脑上测'}：{m.result}"]
        if m.interfaces:
            tip.append("接口：\n" + "\n".join(f"  {p}" for p in m.interfaces))
        if m.used_by:
            tip.append(f"被 {'、'.join(m.used_by)} 用到")
        if m.failing_uses:
            tip.append(f"没过的用例用到：{'、'.join(m.failing_uses)}")
        self.setToolTip("\n".join(tip))

    def boundingRect(self):
        return QRectF(-4, -4, self.w + 8, self.h + 8)

    def paint(self, p: QPainter, opt, widget=None):
        c = T()
        border, fill, dashed = _status_colors(self.status)
        pen = QPen(QColor(c["accent"] if self.selected else border), 2.2 if self.selected else 1.3)
        if dashed and not self.selected:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(fill))
        p.drawRoundedRect(QRectF(0, 0, self.w, self.h), 12, 12)
        p.setFont(_mono(10, True))
        p.setPen(QColor(c["ink"]))
        p.drawText(QRectF(12, 8, self.w - 24, 18), Qt.AlignLeft | Qt.AlignVCenter, self.m.id)
        p.setFont(_font(8.5))
        p.setPen(QColor(border if self.status in ("pass", "fail", "running") else c["ink2"]))
        sub = "进行中" if self.status == "running" else self.m.result
        p.drawText(QRectF(12, 28, self.w - 24, 16), Qt.AlignLeft | Qt.AlignVCenter, sub)
        if self.expanded and self.m.interfaces:
            y = H_COMPACT
            p.setFont(_mono(8.5))
            for proto, name in list(zip(self.m.interfaces, self.m.names))[:MAX_LINES]:
                bad = name in self.m.failing_uses
                p.setPen(QColor(c["fail"] if bad else c["ink2"]))
                text = p.fontMetrics().elidedText(("✗ " if bad else "") + proto, Qt.ElideRight, int(self.w - 24))
                p.drawText(QRectF(12, y, self.w - 24, LINE_H), Qt.AlignLeft | Qt.AlignVCenter, text)
                y += LINE_H
            if len(self.m.interfaces) > MAX_LINES:
                p.setPen(QColor(c["ink3"]))
                p.drawText(QRectF(12, y, self.w - 24, LINE_H), Qt.AlignLeft | Qt.AlignVCenter,
                           f"…… 共 {len(self.m.interfaces)} 个接口")

    def mousePressEvent(self, ev):
        self.clicked.emit(self.m.id)
        ev.accept()


class Box(QGraphicsObject):
    """外设或外接器件的灰色方块：两行字。"""
    clicked = Signal(str)

    def __init__(self, key: str, title: str, sub: str, w: float, note_color: str = "", tip: str = ""):
        super().__init__()
        self.key, self.title, self.sub, self.w, self.h = key, title, sub, w, 46
        self.note_color = note_color
        self.setToolTip(tip or f"{title}\n{sub}")

    def boundingRect(self):
        return QRectF(-2, -2, self.w + 4, self.h + 4)

    def paint(self, p: QPainter, opt, widget=None):
        c = T()
        p.setPen(QPen(QColor(c["line"]), 1.2))
        p.setBrush(QColor(c["bg"]))
        p.drawRoundedRect(QRectF(0, 0, self.w, self.h), 10, 10)
        p.setFont(_font(9.5, True))
        p.setPen(QColor(c["ink"]))
        p.drawText(QRectF(10, 5, self.w - 20, 18), Qt.AlignHCenter | Qt.AlignVCenter, self.title)
        p.setFont(_font(8.5))
        p.setPen(QColor(self.note_color or c["ink2"]))
        p.drawText(QRectF(6, 23, self.w - 12, 18), Qt.AlignHCenter | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.sub, Qt.ElideRight, int(self.w - 12)))

    def mousePressEvent(self, ev):
        self.clicked.emit(self.key)
        ev.accept()


def _curve(a: QPointF, b: QPointF) -> QPainterPath:
    path = QPainterPath(a)
    dy = (b.y() - a.y()) * 0.5
    path.cubicTo(QPointF(a.x(), a.y() + dy), QPointF(b.x(), b.y() - dy), b)
    return path


def _order(items: list[str], parents: dict[str, list[float]]) -> list[str]:
    """按上一层连过来的位置的平均值排，减少交叉；没人连的放后面，保持原顺序。"""
    pos = {k: i for i, k in enumerate(items)}
    return sorted(items, key=lambda k: (sum(parents[k]) / len(parents[k]) if parents.get(k) else 1e6, pos[k]))


def _row(keys: list[str], widths: dict[str, float], y: float, center: float) -> dict[str, QRectF]:
    total = sum(widths[k] for k in keys) + COL_GAP * max(0, len(keys) - 1)
    x = center - total / 2
    out = {}
    for k in keys:
        out[k] = QRectF(x, y, widths[k], 0)
        x += widths[k] + COL_GAP
    return out


class ProjectMapView(DotGridView):
    module_clicked = Signal(str)
    device_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pm: ProjectMap | None = None
        self.expanded = False
        self.selected = ""
        self.cards: dict[str, BlockCard] = {}
        self.edge_labels: list[QGraphicsSimpleTextItem] = []
        self._key = None

    def set_map(self, pm: ProjectMap | None, live: dict[str, str] | None = None, selected: str = "",
                expanded: bool | None = None) -> None:
        if expanded is not None:
            self.expanded = expanded
        self.pm, self.selected = pm, selected
        self._draw(live or {})

    def _draw(self, live: dict[str, str]) -> None:
        sc = self.scene()
        sc.clear()
        self.cards, self.edge_labels = {}, []
        c = T()
        pm = self.pm
        if pm is None or not pm.modules:
            t = sc.addSimpleText("设计好以后，这里画出这次的工程：模块、用到的接口、外设和引脚、外接器件")
            t.setBrush(QColor(c["ink3"]))
            t.setFont(_font(9.5))
            self.fit()
            return
        w = W_EXPANDED if self.expanded else W_COMPACT
        logic = [m for m in pm.modules if m.layer == "logic"]
        hard = [m for m in pm.modules if m.layer != "logic"]
        rows: list[tuple[str, list]] = [("逻辑层", logic), ("驱动层", hard)]
        y, center = 0.0, 0.0
        rects: dict[str, QRectF] = {}
        xs: dict[str, float] = {}
        for label, mods in rows:
            if not mods:
                continue
            parents = {m.id: [xs[e.src] for e in pm.edges if e.dst == m.id and e.src in xs] for m in mods}
            keys = _order([m.id for m in mods], parents)
            cards = {m.id: BlockCard(m, self.expanded, "running" if live.get(m.id) == "running" else m.status,
                                     m.id == self.selected) for m in mods}
            placed = _row(keys, {k: w for k in keys}, y, center)
            hmax = max(cd.h for cd in cards.values())
            self._label(label, y)
            for k, r in placed.items():
                cd = cards[k]
                cd.setPos(r.x(), y)
                cd.clicked.connect(self.module_clicked.emit)
                sc.addItem(cd)
                self.cards[k] = cd
                rects[k] = QRectF(r.x(), y, cd.w, cd.h)
                xs[k] = r.center().x()
            y += hmax + ROW_GAP
        # 模块之间：上层用到下层的接口
        for e in pm.edges:
            if e.src in rects and e.dst in rects:
                a, b = rects[e.src], rects[e.dst]
                if a.y() == b.y():
                    pa, pb = QPointF(a.right(), a.center().y()), QPointF(b.left(), b.center().y())
                else:
                    pa, pb = QPointF(a.center().x(), a.bottom()), QPointF(b.center().x(), b.top())
                item = sc.addPath(_curve(pa, pb), QPen(QColor(c["ink3"]), 1.4))
                item.setZValue(-1)
                tip = (f"{e.src} 的代码里用到了 {e.dst} 的：{'、'.join(e.uses)}" if e.uses else
                       f"{e.src} 依赖 {e.dst}（代码里还没用到它的接口）")
                item.setToolTip(tip)
                if self.expanded and e.uses:
                    shown = "、".join(e.uses[:3]) + (f" 等 {len(e.uses)} 个" if len(e.uses) > 3 else "")
                    t = sc.addSimpleText(shown)
                    t.setFont(_mono(8))
                    t.setBrush(QColor(c["ink2"]))
                    br = t.boundingRect()                # 写在被用到的那块积木正上方：几条线的字不会叠在一起
                    t.setPos(pb.x() - br.width() / 2, pb.y() - br.height() - 4)
                    t.setToolTip(tip)
                    self.edge_labels.append(t)
        # 芯片外设（带引脚）和外接器件
        dev_names = {d.name for d in pm.devices}
        bus_of = {d.name: d.bus for d in pm.devices}
        periph_parents = {p.id: [xs[m.id] for m in hard if m.id in xs and (
            p.id in pm.module_links(m.id)[0] or any(bus_of.get(dv) == p.id for dv in pm.module_links(m.id)[1]))]
            for p in pm.peripherals}                     # 驱动直接用到的外设，或者驱动用到的器件挂在这条总线上
        pkeys = _order([p.id for p in pm.peripherals], periph_parents)
        boxes: dict[str, QRectF] = {}
        if pkeys:
            pw = {k: 170.0 for k in pkeys}
            placed = _row(pkeys, pw, y + 22, center)
            left = min(r.x() for r in placed.values()) - 16
            right = max(r.right() for r in placed.values()) + 16
            frame = QGraphicsRectItem(QRectF(left, y, right - left, 46 + 34))
            pen = QPen(QColor(c["grip"]), 1.2, Qt.DashLine)
            frame.setPen(pen)
            frame.setBrush(Qt.NoBrush)
            frame.setZValue(-2)
            frame.setToolTip(f"{pm.board or '板子'}（{pm.chip}）")
            sc.addItem(frame)
            bt = sc.addSimpleText(f"{pm.board or '板子'}")
            bt.setFont(_font(8.5))
            bt.setBrush(QColor(c["ink3"]))
            bt.setPos(left + 8, y + 4)
            self._label("芯片", y)
            byid = {p.id: p for p in pm.peripherals}
            for k, r in placed.items():
                p = byid[k]
                sub = " · ".join(f"GPIO{g} {s.split('.', 1)[1].upper()}" for s, g in p.pins)
                b = Box(k, k.upper(), sub or "没有引脚", r.width(),
                        tip=f"{k.upper()}\n" + "\n".join(f"{s} = GPIO{g}" for s, g in p.pins))
                b.setPos(r.x(), y + 22)
                sc.addItem(b)
                boxes[k] = QRectF(r.x(), y + 22, r.width(), 46)
                xs[k] = r.center().x()
            y += 46 + 34 + ROW_GAP
        if pm.devices:
            dparents = {d.name: [xs[d.bus]] if d.bus in xs else
                        [xs[m.id] for m in hard if d.name in pm.module_links(m.id)[1] and m.id in xs] for d in pm.devices}
            dkeys = _order([d.name for d in pm.devices], dparents)
            placed = _row(dkeys, {k: 160.0 for k in dkeys}, y, center)
            self._label("外接器件", y)
            byname = {d.name: d for d in pm.devices}
            for k, r in placed.items():
                d = byname[k]
                addr = f"0x{d.address:02X}" if d.address is not None else ""
                state = {True: "已实测确认", False: "没应答"}.get(d.checked, "")
                sub = " · ".join(x for x in (addr, state) if x) or d.bus
                col = {True: c["pass"], False: c["fail"]}.get(d.checked, "")
                tip = f"{d.part}（{d.name}），{d.bus}，地址 {addr}" + (f"\n{state}：{d.why}" if state else "\n还没上板实测")
                if d.pins:
                    tip += "\n" + "\n".join(f"{s} = GPIO{g}" for s, g in d.pins)
                b = Box(k, d.part, sub, r.width(), col, tip)
                b.clicked.connect(self.device_clicked.emit)
                b.setPos(r.x(), y)
                sc.addItem(b)
                boxes[k] = QRectF(r.x(), y, r.width(), 46)
        green = QPen(QColor(c["accent"]), 1.8)
        for m in hard:
            if m.id not in rects:
                continue
            periph, devs = pm.module_links(m.id)
            for k in [*periph, *devs]:
                if k in boxes:
                    a, b = rects[m.id], boxes[k]
                    it = sc.addPath(_curve(QPointF(a.center().x(), a.bottom()), QPointF(b.center().x(), b.top())), green)
                    it.setZValue(-1)
                    it.setToolTip(f"{m.id} 的代码里用到了 {k}（引脚宏或器件宏）")
        for d in pm.devices:
            if d.bus in boxes and d.name in boxes and d.bus not in dev_names:
                a, b = boxes[d.bus], boxes[d.name]
                it = sc.addPath(_curve(QPointF(a.center().x(), a.bottom()), QPointF(b.center().x(), b.top())), green)
                it.setZValue(-1)
                it.setToolTip(f"{d.part} 接在 {d.bus} 上")
        self.fit()

    def _label(self, text: str, y: float) -> None:
        t = self.scene().addSimpleText(text)
        t.setFont(_font(8.5))
        t.setBrush(QColor(T()["ink3"]))
        t.setData(0, "row_label")
        t.setPos(-10_000, y)                         # 先放远处，fit 前再统一挪到最左边
        self._labels = getattr(self, "_labels", []) + [t]

    def fit(self):
        labels = [i for i in self.scene().items() if isinstance(i, QGraphicsSimpleTextItem) and i.data(0) == "row_label"]
        others = [i for i in self.scene().items() if i not in labels]
        if labels and others:
            left = min(i.sceneBoundingRect().left() for i in others)
            for t in labels:
                t.setPos(left - t.boundingRect().width() - 18, t.pos().y())
        super().fit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self.user_view:
            self.fit()


class WiringView(DotGridView):
    """接线图：左边是板子上这个项目用到的引脚，右边是接到的外接器件或外设；保留引脚和空闲引脚列在下面。"""
    device_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.pm: ProjectMap | None = None

    def set_map(self, pm: ProjectMap | None) -> None:
        self.pm = pm
        self._draw()

    def _draw(self) -> None:
        sc = self.scene()
        sc.clear()
        c = T()
        pm = self.pm
        if pm is None or not pm.pins:
            t = sc.addSimpleText("设计好以后，这里画出这次的接线：每个引脚接到哪个器件，器件有没有在板子上应答")
            t.setBrush(QColor(c["ink3"]))
            t.setFont(_font(9.5))
            self.fit()
            return
        src = "设计 Agent 按需求定的接线" if pm.wiring_source == "design" else "沿用硬件档案的默认接线"
        head = sc.addSimpleText(src)
        head.setFont(_font(9))
        head.setBrush(QColor(c["ink2"]))
        head.setPos(0, -34)
        pins = sorted(pm.pins.items(), key=lambda x: x[1])
        row_h, bw = 26, 210
        board = QGraphicsRectItem(QRectF(0, 0, bw, row_h * len(pins) + 46))
        board.setPen(QPen(QColor(c["line"]), 1.4))
        board.setBrush(QColor(c["surface"]))
        sc.addItem(board)
        t = sc.addSimpleText(f"{pm.board or '板子'}（{pm.chip}）")
        t.setFont(_font(9.5, True))
        t.setBrush(QColor(c["ink"]))
        t.setPos(12, 10)
        pin_y: dict[str, float] = {}
        for i, (s, g) in enumerate(pins):
            y = 40 + i * row_h
            tx = sc.addSimpleText(f"GPIO{g:<3} {s}")
            tx.setFont(_mono(9))
            tx.setBrush(QColor(c["ink"]))
            tx.setPos(12, y)
            dot = sc.addEllipse(QRectF(bw - 6, y + 4, 10, 10), QPen(QColor(c["accent"]), 1.2), QColor(c["accent_soft"]))
            dot.setToolTip(f"{s} 接 GPIO{g}")
            pin_y[s] = y + 9
        # 右边：外接器件（I2C 总线和器件自己的引脚）和只记了信号名的外设
        targets: list[tuple[str, str, list[str], str, str]] = []      # (键, 标题, 引脚信号, 副标题, 颜色)
        dev_names = {d.name for d in pm.devices}
        for d in pm.devices:
            sigs = [s for s in pm.pins if s.split(".", 1)[0] == d.name or s.split(".", 1)[0] == d.bus]
            state = {True: "已实测确认（在板子上应答了）", False: f"没应答：{d.why}，请检查接线"}.get(
                d.checked, "还没上板实测")
            col = {True: c["pass"], False: c["fail"]}.get(d.checked, c["ink3"])
            addr = f"，地址 0x{d.address:02X}" if d.address is not None else ""
            targets.append((d.name, f"{d.part}{addr}", sigs, state, col))
        on_device = {s for _, _, sigs, _, _ in targets for s in sigs}
        order = {s: g for s, g in pm.pins.items()}
        rest: dict[str, list[str]] = {}
        for s in pm.pins:
            if s not in on_device and s.split(".", 1)[0] not in dev_names:
                rest.setdefault(s.split(".", 1)[0], []).append(s)
        for k, sigs in rest.items():
            targets.append((k, k.upper(), sorted(sigs, key=lambda x: order[x]), "运行记录里没有它接到什么器件", c["ink3"]))
        targets = [(k, t, sorted(sg, key=lambda x: order.get(x, 99)), sub, col) for k, t, sg, sub, col in targets]
        targets.sort(key=lambda x: min((order.get(sg, 99) for sg in x[2]), default=99))   # 右边按引脚顺序排，连线少交叉
        x0, y = bw + 150, 0.0
        for key, title, sigs, sub, col in targets:
            h = 44 + 20 * len(sigs)
            box = QGraphicsRectItem(QRectF(x0, y, 230, h))
            box.setPen(QPen(QColor(c["line"]), 1.2))
            box.setBrush(QColor(c["bg"]))
            box.setToolTip(f"{title}\n{sub}")
            sc.addItem(box)
            tt = sc.addSimpleText(title)
            tt.setFont(_font(9.5, True))
            tt.setBrush(QColor(c["ink"]))
            tt.setPos(x0 + 10, y + 6)
            st = sc.addSimpleText(sub)
            st.setFont(_font(8.5))
            st.setBrush(QColor(col))
            st.setPos(x0 + 10, y + 24)
            for j, s in enumerate(sigs):
                yy = y + 44 + j * 20
                lab = sc.addSimpleText(s.split(".", 1)[1].upper())
                lab.setFont(_mono(8.5))
                lab.setBrush(QColor(c["ink2"]))
                lab.setPos(x0 + 14, yy - 2)
                if s in pin_y:
                    path = QPainterPath(QPointF(bw + 4, pin_y[s]))
                    path.cubicTo(QPointF(bw + 75, pin_y[s]), QPointF(x0 - 75, yy + 7), QPointF(x0, yy + 7))
                    wire = sc.addPath(path, QPen(QColor(c["accent"]), 1.6))
                    wire.setToolTip(f"GPIO{pm.pins[s]}（{s}）→ {title} {s.split('.', 1)[1].upper()}")
            y += h + 18
        bottom = max(row_h * len(pins) + 46, y) + 20
        notes = []
        if pm.reserved:
            notes.append("不能用：" + "、".join(f"GPIO{g}" for g in sorted(pm.reserved)) + "（启动、Flash、USB、串口 0）")
        if pm.unused_gpios:
            notes.append("空着：" + "、".join(f"GPIO{g}" for g in pm.unused_gpios))
        notes.append("实测确认只能证明 I2C 器件的两根数据线和地址接对了，确认不了中断等其他引脚")
        from PySide6.QtWidgets import QGraphicsTextItem
        y = bottom
        for n in notes:                                  # 自动换行，不把整张图撑宽
            tx = QGraphicsTextItem(n)
            tx.setFont(_font(8.5))
            tx.setDefaultTextColor(QColor(c["ink3"]))
            tx.setTextWidth(x0 + 230)
            tx.setPos(0, y)
            sc.addItem(tx)
            y += tx.boundingRect().height() + 2
        self.fit()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if not self.user_view:
            self.fit()

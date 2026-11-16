"""知识库页（DECISIONS 第 114 条，方案 A"只做目录"）：全部知识按固定层级排好，能搜索、看依据、驳回错的规则。

左：目录（芯片 → 开发框架 → 外接器件 → 开发板），后面是条目数；
中：选中这一格的说明（芯片能力读自 ESP-IDF、板子的硬件档案、器件参数）和条目列表；上面的搜索框搜全部；
右：条目的依据——规则的引用原文和手册原页、资料的登记信息和引用它的规则、手册和实测对不上的对照。
新规则的确认在对话里做（规则卡片），这里不审核；这里只能把已经在用、但发现错了的规则驳回。
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QFrame, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget, QHBoxLayout, QLabel)

from ..grounding.catalog import Catalog, Entry, Node, build_catalog, chip_title, peripheral_title, reject_rule
from ..grounding.rules import KIND_NAMES, STATUS_NAMES
from .panes import PaneSplitter, PaneToggle
from .widgets import Dot, Panel, T, hbox, label

TIER_NAMES = {"official_manual": "官方手册", "official_errata": "官方勘误", "official_code": "官方代码",
              "official_example": "官方例程", "official_mirror": "官方原版（非官方网站）", "community": "社区资料",
              "user": "用户提供"}
DOC_KINDS = {"pdf": "PDF", "html": "网页", "text": "文本", "header": "头文件", "svd": "SVD"}
ENTRY_KINDS = {"rule": "规则", "doc": "资料", "conflict": "手册和实测对不上"}


def is_rejected(r: dict) -> bool:
    return r.get("status") not in ("trusted", "approved") and any("驳回" in n for n in r.get("notes", []))


def rule_state(r: dict) -> tuple[str, str]:
    """(说明, 颜色键)。"""
    if is_rejected(r):
        return "已驳回", "fail"
    st = r.get("status", "draft")
    if st in ("trusted", "approved"):
        return STATUS_NAMES.get(st, st), "pass"
    return STATUS_NAMES.get(st, st) + "（不给 Agent 用）", "run"


def _ranges(gs: list[int]) -> str:
    out, i = [], 0
    while i < len(gs):
        j = i
        while j + 1 < len(gs) and gs[j + 1] == gs[j] + 1:
            j += 1
        out.append(f"GPIO{gs[i]}" if i == j else f"GPIO{gs[i]}–{gs[j]}")
        i = j + 1
    return "、".join(out)


class EntryRow(QWidget):
    """列表里的一行：状态点、标题（两行内）、编号和出处。"""

    def __init__(self, e: Entry, show_path: bool = False):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(10)
        c = T()
        if e.kind == "rule":
            _, col = rule_state(e.data)
        elif e.kind == "conflict":
            col = "run"
        else:
            col = "ink3"
        lay.addWidget(Dot(c[col]), 0, Qt.AlignTop)
        col_l = QVBoxLayout()
        col_l.setSpacing(2)
        t = label(e.title, wrap=True)
        t.setMinimumWidth(20)
        col_l.addWidget(t)
        if e.kind == "rule":
            cite = e.data.get("cite") or {}
            meta = f"{e.id}　{KIND_NAMES.get(e.data.get('kind'), '')}　{cite.get('doc', '')}" + \
                   (f" 第 {cite['page']} 页" if cite.get("page") else "")
        elif e.kind == "doc":
            meta = f"{e.id}　{DOC_KINDS.get(e.data.get('kind'), '')}　{TIER_NAMES.get(e.data.get('tier'), '')}"
        else:
            meta = f"{e.id}　手册：{e.data.get('doc_claim', '')}　实测：{e.data.get('measured_claim', '')}"
        if show_path and e.path:
            meta = " / ".join(e.path) + "　" + meta
        m = label(meta, "Faint", wrap=True)
        m.setMinimumWidth(20)
        col_l.addWidget(m)
        lay.addLayout(col_l, 1)


class KnowledgePage(QWidget):
    rules_changed = Signal()

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = Path(project)
        self.catalog: Catalog | None = None
        self.node: Node | None = None
        self.key_path: tuple[str, ...] = ()
        self.current: Entry | None = None
        self.viewers: list = []
        self.backend = None                       # 这一页不发起运行（关窗口时 app 会查这个）
        self._library = None
        self._build()
        self.reload()

    # ------------------------------------------------------------ 布局

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        # 目录是侧栏，条目列表是主区，详情是右侧面板（DECISIONS 第 133 条）；面板区窄于 1000 时自动收起目录
        self.split = PaneSplitter("knowledge", auto_collapse_below=1000)
        root.addWidget(self.split)

        left = Panel(margins=(14, 16, 10, 12))
        left.lay.addWidget(label("知识库", "H2"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setFrameShape(QFrame.NoFrame)
        self.tree.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda cur, _old: cur is not None and self.select_path(cur.data(0, Qt.UserRole)))
        left.lay.addWidget(self.tree, 1)
        self.errors_label = label("", "Muted", wrap=True)
        self.errors_label.setVisible(False)
        left.lay.addWidget(self.errors_label)

        mid = Panel(margins=(16, 14, 16, 12))
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜规则和资料：型号、寄存器、函数名、原文都行，例如 WHO_AM_I")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_search)
        self.sidebar_btn = PaneToggle(self.split, "sidebar", "目录", "Ctrl+B")
        self.inspector_btn = PaneToggle(self.split, "inspector", "右侧的详情", "Ctrl+Alt+B")
        mid.lay.addLayout(hbox(self.sidebar_btn, self.search, self.inspector_btn))
        self.crumb = label("", "H3", wrap=True)
        self.sub = label("", "Faint", wrap=True)
        mid.lay.addWidget(self.crumb)
        mid.lay.addWidget(self.sub)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setObjectName("InfoCard")
        mid.lay.addWidget(self.info)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setSpacing(1)
        self.list.currentItemChanged.connect(lambda cur, _old: cur is not None and cur.data(Qt.UserRole)
                                             and self.show_entry(cur.data(Qt.UserRole)))
        mid.lay.addWidget(self.list, 1)

        right = Panel(margins=(20, 18, 20, 16))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("ScrollInner")
        self.detail = QVBoxLayout(inner)
        self.detail.setContentsMargins(0, 0, 8, 0)
        self.detail.setSpacing(10)
        scroll.setWidget(inner)
        right.lay.addWidget(scroll, 1)
        self.reject_row = QWidget()
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("这条规则哪里错了（必填）")
        self.reject_btn = QPushButton("驳回这条规则")
        self.reject_btn.clicked.connect(self.reject_current)
        self.reject_row.setLayout(hbox(self.comment, self.reject_btn))
        self.reject_row.setVisible(False)
        right.lay.addWidget(self.reject_row)
        self.reject_note = label("驳回后这条规则退回\"待核对\"，不再给 Agent 用；以后提炼规则时会重新核对。", "Faint", wrap=True)
        self.reject_note.setVisible(False)
        right.lay.addWidget(self.reject_note)

        self.split.add_pane(left, "sidebar", minimum=200, default=260)
        self.split.add_pane(mid, "main", minimum=320, default=460)
        self.split.add_pane(right, "inspector", minimum=320, default=560)
        self.split.finish()
        self._detail_empty()

    def toggle_pane(self, role: str) -> None:
        self.split.toggle(role)

    def save_layout(self):
        pass                                             # 面板宽度和开关由 PaneSplitter 随时记住

    def restyle(self):
        c = T()
        self.info.setStyleSheet(f"#InfoCard {{ background: {c['bg']}; border-radius: 12px; padding: 10px 12px; }}")
        # 和其他页一致的选中色；行里放的是自己的控件，所以条目本身不留内边距（不然行高算不准、字被裁掉）
        self.tree.setStyleSheet(
            f"QTreeWidget {{ show-decoration-selected: 0; }}"
            f" QTreeWidget::item {{ padding: 4px 2px; border-radius: 0; }}"
            f" QTreeWidget::item:selected, QTreeWidget::item:selected:active, QTreeWidget::item:selected:!active"
            f" {{ background: {c['accent_soft']}; color: {c['ink']}; }}")
        self.list.setStyleSheet(f"QListWidget::item {{ padding: 0; margin: 1px 0; }}"
                                f" QListWidget::item:selected {{ background: {c['accent_soft']}; }}")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_rows()

    def _fit_rows(self):
        """行高按列表宽度算：标题换行后有几行就多高（不然 Qt 按最窄宽度估，一行占半屏）。"""
        vw = max(120, self.list.viewport().width() - 4)
        from PySide6.QtCore import QSize
        for i in range(self.list.count()):
            it = self.list.item(i)
            w = self.list.itemWidget(it)
            if w is not None:
                w.setFixedWidth(vw)
                it.setSizeHint(QSize(vw, w.layout().totalHeightForWidth(vw)))

    # ------------------------------------------------------------ 数据

    def library(self):
        if self._library is None:
            from ..grounding.sources import SourceLibrary
            try:
                self._library = SourceLibrary.load(self.project / ".ff_sources" / "library")
            except Exception:                                   # noqa: BLE001  缓存坏了就不显示页数和原页文字
                self._library = SourceLibrary(self.project / ".ff_sources" / "library")
        return self._library

    def reload(self):
        self._library = None
        self.restyle()
        self.catalog = build_catalog(self.project)
        self.errors_label.setText("\n".join(self.catalog.errors))
        self.errors_label.setVisible(bool(self.catalog.errors))
        self.tree.blockSignals(True)
        self.tree.clear()

        def add(parent, node: Node, keys: tuple[str, ...]):
            it = QTreeWidgetItem([node.title, str(node.count()) if node.count() else ""])
            it.setData(0, Qt.UserRole, keys)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            it.setForeground(1, self.palette().placeholderText())
            if len(keys) == 1:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            (parent.addChild if isinstance(parent, QTreeWidgetItem) else parent.addTopLevelItem)(it)
            for c in node.children:
                add(it, c, keys + (c.key,))
            it.setExpanded(len(keys) <= 2)
            return it
        for top in self.catalog.root.children:
            add(self.tree, top, (top.key,))
        self.tree.blockSignals(False)
        keep = self.key_path if self.catalog.root.find(self.key_path) is not None and self.key_path else None
        self.select_path(keep or (self.catalog.root.children[0].key,) if self.catalog.root.children else ())
        if self.search.text().strip():
            self._on_search(self.search.text())

    def _tree_item(self, keys: tuple[str, ...]):
        for it in self._all_items():
            if it.data(0, Qt.UserRole) == keys:
                return it
        return None

    def _all_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop(0)
            yield it
            stack += [it.child(i) for i in range(it.childCount())]

    # ------------------------------------------------------------ 中间：一格的说明和条目

    def select_path(self, keys) -> None:
        keys = tuple(keys or ())
        node = self.catalog.root.find(keys) if self.catalog is not None else None
        if node is None:
            return
        self.key_path, self.node = keys, node
        it = self._tree_item(keys)
        if it is not None and self.tree.currentItem() is not it:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(it)
            self.tree.blockSignals(False)
        if self.search.text().strip():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
        path_titles, n = [], self.catalog.root
        for k in keys:
            n = n.find((k,))
            path_titles.append(n.title)
        self.crumb.setText(" / ".join(path_titles))
        own = node.entries
        total = node.count()
        rules = sum(1 for e in own if e.kind == "rule")
        docs = sum(1 for e in own if e.kind == "doc")
        self.sub.setText(f"这一格：规则 {rules} 条，资料 {docs} 份" + (f"；连同下面各格共 {total} 条" if total != len(own) else "")
                         if own or total else "这一格还没有规则和资料。手册拖进对话就能登记；查资料提炼的规则确认后会出现在这里。")
        info = self._node_info(node)
        self.info.setText(info)
        self.info.setVisible(bool(info))
        self._fill(own, show_path=False)

    def _fill(self, entries: list[Entry], show_path: bool):
        self.list.clear()
        last = None
        for e in entries:
            if e.kind != last:
                head = QListWidgetItem(f"{ENTRY_KINDS[e.kind]}　{sum(1 for x in entries if x.kind == e.kind)}")
                head.setFlags(Qt.NoItemFlags)
                f = head.font()
                f.setBold(True)
                head.setFont(f)
                self.list.addItem(head)
                last = e.kind
            it = QListWidgetItem()
            it.setData(Qt.UserRole, e)
            w = EntryRow(e, show_path)
            it.setSizeHint(w.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, w)
        self._fit_rows()
        self._detail_empty()

    def _on_search(self, text: str):
        if self.catalog is None:
            return
        q = text.strip()
        if not q:
            self.select_path(self.key_path)
            return
        hits = self.catalog.search(q)
        self.crumb.setText(f"搜索\"{q}\"")
        self.sub.setText(f"找到 {len(hits)} 条" if hits else "没找到。换个说法试试，比如型号、寄存器名或函数名")
        self.info.setVisible(False)
        self._fill(sorted(hits, key=lambda e: ({"conflict": 0, "rule": 1, "doc": 2}[e.kind], e.path, e.id)), show_path=True)

    def _node_info(self, node: Node) -> str:
        i, c = node.info, T()
        t = i.get("type")
        if t == "chip":
            per = "、".join(peripheral_title(p) for p in i.get("peripherals", []))
            io = f"；只能输入：{_ranges(i['input_only'])}" if i.get("input_only") else ""
            usb = "芯片自带 USB 串口" if i.get("usb_serial_jtag") else "没有自带 USB 串口（控制台走 UART0）"
            src = (f"读自本机 ESP-IDF：{html.escape(i['source'])}" if i.get("source")
                   else "本机没有 ESP-IDF，用的是内置表")
            return (f"<b>{chip_title(i['chip'])}</b>　{len(i['gpios'])} 个 GPIO（{_ranges(i['gpios'])}）{io}；{usb}<br>"
                    f"外设：{per}<br><span style='color:{c['ink3']}'>{src}</span>")
        if t == "framework":
            return (f"<b>{html.escape(i['title'])}</b> 的头文件和接口用法规则。头文件取自本机安装的源码树，"
                    f"规则的引用原文能在头文件里逐字找到。")
        if t == "device":
            rows = []
            for d in i.get("boards", []):
                addr = f"，地址 0x{d['address']:02X}" if d.get("address") is not None else ""
                params = "；".join(f"{k} = {self._fmt(v.get('value'))}（{self._origin(v.get('origin'))}）"
                                  for k, v in (d.get("params") or {}).items())
                rows.append(f"{html.escape(d['board'])}：代码里叫 {d['name']}，接 {d['bus']}{addr}" + (f"；{params}" if params else ""))
            return f"<b>{html.escape(i['part'])}</b><br>" + "<br>".join(rows)
        if t == "board":
            p = i.get("profile") or {}
            g = lambda k: html.escape(str((p.get(k) or {}).get("value", "")))          # noqa: E731
            chip_origin = self._origin((p.get("chip") or {}).get("origin"))
            pins = "，".join(f"{s}=GPIO{f.get('value')}" for s, f in (p.get("pins") or {}).items())
            reserved = sorted(int(k) for k in (p.get("reserved_pins") or {}))
            devs = "、".join(f"{d['part']}（{d['name']}）" for d in p.get("devices", []))
            exposed = (i.get("board") or {}).get("exposed")
            return (f"<b>{html.escape(node.title)}</b>　{i.get('dir')}<br>"
                    f"芯片：{g('chip_variant') or g('chip')}（{chip_origin}）；{g('framework')} {g('framework_version')}<br>"
                    + (f"引出的引脚：{_ranges(sorted(exposed))}<br>" if exposed else "")
                    + f"默认接线：{pins or '没有'}<br>不能用的引脚：{_ranges(reserved) or '没有'}<br>外接器件：{devs or '没有'}")
        return ""

    @staticmethod
    def _fmt(v):
        return f"0x{v:02X}" if isinstance(v, int) and not isinstance(v, bool) else html.escape(str(v))

    @staticmethod
    def _origin(o) -> str:
        return {"user": "用户提供", "document": "资料", "measured": "实测", "default": "默认值"}.get(o, o or "")

    # ------------------------------------------------------------ 右边：依据

    def _clear_detail(self):
        while self.detail.count():
            it = self.detail.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self.reject_row.setVisible(False)
        self.reject_note.setVisible(False)

    def _detail_empty(self):
        self._clear_detail()
        self.current = None
        self.detail.addWidget(label("选一条规则或资料，这里显示它的依据。", "Muted", wrap=True))
        self.detail.addStretch(1)

    def show_entry(self, e: Entry) -> None:
        self._clear_detail()
        self.current = e
        {"rule": self._show_rule, "doc": self._show_doc, "conflict": self._show_conflict}[e.kind](e)
        self.detail.addStretch(1)

    def _add(self, w):
        if isinstance(w, QLabel):
            w.setMinimumWidth(20)
        self.detail.addWidget(w)
        return w

    def _cite_block(self, doc_id: str, page, quote: str, extra: str = "") -> None:
        c = T()
        doc = self.library().docs.get(doc_id)
        title = doc.title if doc is not None else doc_id
        where = f"第 {page} 页" if page else "全文"
        q = QLabel(f"<b>{html.escape(title)}</b>　<span style='color:{c['ink2']}'>{html.escape(doc_id)}　{where}{extra}</span><br>"
                   f"<span style='font-family:monospace; background:#FBEFC9; color:#1C2026'>{html.escape(quote)}</span>")
        q.setWordWrap(True)
        q.setTextFormat(Qt.RichText)
        q.setTextInteractionFlags(Qt.TextSelectableByMouse)
        q.setStyleSheet(f"background: {c['bg']}; border-radius: 12px; padding: 10px 12px;")
        self._add(q)
        if doc is not None and doc.kind == "pdf" and page:
            b = QPushButton("看手册原页")
            b.setObjectName("Ghost")
            b.setToolTip("打开引用的那一页，原文用框标出")
            b.clicked.connect(lambda _=False: self.open_page(doc_id, int(page), quote))
            self.detail.addLayout(hbox(b, "stretch"))

    def _show_rule(self, e: Entry):
        r = e.data
        state, col = rule_state(r)
        head = label(f"{e.id}　{KIND_NAMES.get(r.get('kind'), r.get('kind', ''))}　适用 {'、'.join(r.get('scope', []))}", "Faint")
        self._add(head)
        t = label(r.get("text", ""), "H2", wrap=True)
        t.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._add(t)
        s = label(f"{state}　出处等级：{TIER_NAMES.get(r.get('tier'), r.get('tier', ''))}", wrap=True)
        s.setStyleSheet(f"color: {T()[col]};")
        self._add(s)
        cite = r.get("cite") or {}
        self._cite_block(cite.get("doc", ""), cite.get("page"), cite.get("quote", ""),
                         f"　§{cite['section']}" if cite.get("section") else "")
        doc = self.library().docs.get(cite.get("doc", ""))
        bits = doc.page_bits(int(cite["page"])) if doc is not None and cite.get("page") else ""
        if bits:
            b = label(f"这一页的寄存器位标注（平台按 PDF 坐标算出，不是原文）：\n{bits}", "Faint", wrap=True)
            b.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._add(b)
        if r.get("check"):
            import json
            self._add(label("合规检查程序据此判定：" + json.dumps(r["check"], ensure_ascii=False), "Muted", wrap=True))
        if r.get("notes"):
            self._add(label("经过：" + "；".join(r["notes"]), "Muted", wrap=True))
        self._add(label("记在：" + "、".join(f"hardware/{b}/rules.json" for b in e.boards), "Faint", wrap=True))
        can = not is_rejected(r) and r.get("status") in ("trusted", "approved", "checked")
        self.reject_row.setVisible(can)
        self.reject_note.setVisible(can)
        self.comment.clear()

    def _show_doc(self, e: Entry):
        d, c = e.data, T()
        self._add(label(f"{e.id}　{DOC_KINDS.get(d.get('kind'), d.get('kind', ''))}", "Faint"))
        self._add(label(e.title, "H2", wrap=True))
        rows = [("等级", TIER_NAMES.get(d.get("tier"), d.get("tier", ""))), ("版本", d.get("version", "")),
                ("网址", d.get("url", "")), ("原件", d.get("file", "")), ("校验值", d.get("sha256", ""))]
        doc = self.library().docs.get(e.id)
        if doc is not None and doc.pages:
            rows.append(("文字", f"已提取，{len(doc.pages)} 页" if doc.kind == "pdf" else "已提取"))
        else:
            rows.append(("文字", "还没提取（第一次运行用到它时提取）"))
        rows.append(("登记在", "、".join(f"hardware/{b}/sources.toml" for b in e.boards)))
        txt = "<br>".join(f"<span style='color:{c['ink2']}'>{k}</span>　{html.escape(str(v))}" for k, v in rows if v)
        info = QLabel(txt)
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._add(info)
        if doc is not None and doc.kind == "pdf" and doc.pages:
            b = QPushButton("看第 1 页")
            b.setObjectName("Ghost")
            b.clicked.connect(lambda _=False: self.open_page(e.id, 1, ""))
            self.detail.addLayout(hbox(b, "stretch"))
        cites = self.catalog.rules_citing(e.id) if self.catalog is not None else []
        self._add(label(f"引用它的规则　{len(cites)}" if cites else "还没有规则引用它", "H3"))
        if cites:                                       # 链接会换行，长规则不撑出横向滚动
            links = QLabel("<br>".join(f"<a href='{i}' style='color:{c['accent']}; text-decoration:none'>{html.escape(r.id)}</a>"
                                       f"　{html.escape(r.title)}" for i, r in enumerate(cites)))
            links.setWordWrap(True)
            links.setTextFormat(Qt.RichText)
            links.linkActivated.connect(lambda i: self.reveal(cites[int(i)]))
            self._add(links)

    def _show_conflict(self, e: Entry):
        d, c = e.data, T()
        self._add(label(e.id, "Faint"))
        self._add(label(e.title, "H2", wrap=True))
        old = label(f"手册：{d.get('doc_claim', '')}", wrap=True)
        old.setStyleSheet(f"color: {c['ink2']}; text-decoration: line-through;")
        self._add(old)
        self._cite_block(d.get("doc", ""), d.get("page"), d.get("quote", ""))
        new = label(f"实测（{d.get('record', '')}）：{d.get('measured_claim', '')}", wrap=True)
        new.setStyleSheet(f"color: {c['run']}; font-weight: 600;")
        self._add(new)
        if d.get("resolution"):
            self._add(label(f"怎么处理：{d['resolution']}", "Muted", wrap=True))

    def reveal(self, e: Entry):
        """跳到某条（例如资料详情里点了引用它的规则）：目录选到它所在的格，列表选中它。"""
        keys = self._keys_of(e)
        if keys:
            self.select_path(keys)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) is e:
                self.list.setCurrentRow(i)
                return
        self.show_entry(e)

    def _keys_of(self, e: Entry) -> tuple[str, ...] | None:
        def walk(node: Node, keys):
            if any(x is e for x in node.entries):
                return keys
            for c in node.children:
                got = walk(c, keys + (c.key,))
                if got is not None:
                    return got
            return None
        return walk(self.catalog.root, ()) if self.catalog is not None else None

    # ------------------------------------------------------------ 动作

    def open_page(self, doc_id: str, page: int, quote: str) -> None:
        from .page_viewer import PageViewer
        doc = self.library().docs.get(doc_id)
        title = doc.title if doc is not None else doc_id
        layout = (doc.page_layout(page) or doc.page(page)) if doc is not None and doc.pages else ""
        hw = self.project / "hardware" / (self.current.boards[0] if self.current is not None and self.current.boards else "")
        v = PageViewer(self.project, hw, doc_id, title, page, quote, layout, parent=self)
        v.setAttribute(Qt.WA_DeleteOnClose)
        self.viewers = [w for w in self.viewers if w.isVisible()] + [v]
        v.show()

    def reject_current(self) -> bool:
        e = self.current
        if e is None or e.kind != "rule":
            return False
        reason = self.comment.text().strip()
        if not reason:
            self.comment.setPlaceholderText("驳回要写一句理由：这条规则哪里错了")
            self.comment.setFocus()
            return False
        from ..grounding.rules import RuleSet
        paths = [self.project / "hardware" / b / "rules.json" for b in e.boards]
        try:
            for p in paths:                                    # 先确认每份规则库都读得了，免得只改了一半
                if e.id not in RuleSet.load(p).rules:
                    raise ValueError(f"{p} 里没有 {e.id}")
            for p in paths:
                reject_rule(p, e.id, reason)
        except (ValueError, OSError) as exc:                   # RuleError 也是 ValueError
            self.reject_note.setText(f"没驳回成功：{exc}")
            self.reject_note.setVisible(True)
            return False
        keys = self.key_path
        self.reload()
        self.select_path(keys)
        hit = next((x for x in self.catalog.all_entries() if x.kind == "rule" and x.id == e.id and x.boards == e.boards), None)
        if hit is not None:
            self.reveal(hit)
        self.rules_changed.emit()
        return True

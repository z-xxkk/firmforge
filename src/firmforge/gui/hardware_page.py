"""硬件工作区（DECISIONS 第 100 条，界面草图 gui_sketch_board.md 第二节）：左硬件档案、中规则库、右规则详情。

规则确认放在这里：对话页的流程或本页发起的"查资料提炼规则"需要人确认时，待确认的规则出现在"等你确认"里，
逐条通过或驳回（驳回写一句理由），全部表态后点"提交"，流程继续。终端里的确认照旧保留。
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .runner import RulesBackend
from .widgets import Dot, GripSplitter, Panel, Segmented, T, hbox, label

ORIGIN = {"measured": "实测", "document": "资料", "user": "你填的", "default": "默认"}
STATUS = {"trusted": "已确认", "approved": "已确认", "checked": "已核对", "draft": "草稿", "pending": "等你确认"}
TIER_NAMES = {"official_manual": "官方手册", "official_code": "官方代码", "official_example": "官方例程",
              "official_mirror": "官方原版（镜像）", "community": "社区资料", "user": "实测记录"}
KIND_NAMES = {"api": "接口用法", "pin": "引脚", "peripheral": "外设", "register": "寄存器", "device": "外接器件",
              "sequence": "操作顺序", "timing": "时序"}
CHIP = {"measured": ("accent_soft", "accent"), "document": ("bg", "ink2"), "user": ("bg", "ink2"),
        "default": ("bg", "ink3")}


def origin_chip(origin: str) -> QLabel:
    bg, fg = CHIP.get(origin, ("bg", "ink3"))
    lab = QLabel(ORIGIN.get(origin, origin))
    lab.setStyleSheet(f"background: {T()[bg]}; color: {T()[fg]}; border-radius: 9px; padding: 0 7px; font-size: 11px;")
    return lab


def measured_entries(path: Path) -> list[tuple[str, str]]:
    """实测记录 measured.md 里的条目：(编号, 标题)。"""
    if not path.is_file():
        return []
    return re.findall(r"^## (M-\d+)\s+(.+?)(?:（\d{4}-\d{2}-\d{2}）)?\s*$", path.read_text(encoding="utf-8"), re.M)


def is_rejected(r: dict) -> bool:
    return r.get("status") not in ("trusted", "approved", "pending") and any("驳回" in n for n in r.get("notes", []))


class HardwarePage(QWidget):
    rules_changed = Signal()

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.hw = QSettings().value("embedded/hw", "hardware/esp32c3_supermini")
        self.rules: list[dict] = []
        self.pending: list[dict] = []           # 等你确认的规则（来自正在跑的流程）
        self.decisions: dict[str, tuple[str, str]] = {}
        self.on_submit = None
        self.filter = "all"
        self.current: dict | None = None
        self.backend: RulesBackend | None = None
        self._library = None
        self._build()
        self.reload()

    # ------------------------------------------------------------ 布局

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.split = GripSplitter(Qt.Horizontal)
        root.addWidget(self.split)

        # 左：硬件档案
        self.prof_panel = Panel(margins=(16, 16, 16, 16))
        self.prof_scroll = QScrollArea()
        self.prof_scroll.setWidgetResizable(True)
        self.prof_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.prof_box = QWidget()
        self.prof_box.setObjectName("ScrollInner")
        self.prof = QVBoxLayout(self.prof_box)
        self.prof.setContentsMargins(0, 0, 14, 0)
        self.prof.setSpacing(10)
        self.prof_scroll.setWidget(self.prof_box)
        self.prof_panel.lay.addWidget(self.prof_scroll)
        self.prof_panel.setMinimumWidth(290)              # 器件参数名较长，再窄来源标记就挤不下

        # 中：规则库
        mid = Panel(margins=(16, 14, 16, 12))
        self.rules_title = label("规则库", "H2")
        self.rules_sub = label("", "Faint")
        mid.lay.addLayout(hbox(self.rules_title, self.rules_sub, "stretch"))
        self.seg = Segmented([("pending", "等你确认"), ("trusted", "已确认"), ("rejected", "退回"), ("all", "全部")], "all")
        self.seg.changed.connect(self._set_filter)
        mid.lay.addWidget(self.seg)
        build = QFrame()
        build.setObjectName("CodeBox")
        bl = QHBoxLayout(build)
        bl.setContentsMargins(12, 6, 8, 6)
        self.scope_edit = QLineEdit()
        self.scope_edit.setPlaceholderText("要查的范围，逗号分隔，如 max30102,i2c")
        self.build_note = label("每次约 1 元", "Faint")
        self.build_btn = QPushButton("查资料提炼规则")
        self.build_btn.setObjectName("Primary")
        self.build_btn.clicked.connect(self.start_build)
        bl.addWidget(self.scope_edit, 1)
        bl.addWidget(self.build_note)
        bl.addWidget(self.build_btn)
        mid.lay.addWidget(build)
        self.build_status = label("", "Faint", wrap=True)
        mid.lay.addWidget(self.build_status)
        self.list = QTableWidget(0, 3)                  # 编号、内容、状态；分组标题占一整行
        self.list.horizontalHeader().setVisible(False)
        self.list.verticalHeader().setVisible(False)
        self.list.setShowGrid(False)
        self.list.setWordWrap(False)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        hh = self.list.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.list.currentCellChanged.connect(lambda row, *_: self._show(self.list.item(row, 1)))
        mid.lay.addWidget(self.list, 1)
        self.submit_btn = QPushButton("提交确认结果")
        self.submit_btn.setObjectName("Primary")
        self.submit_btn.clicked.connect(self.submit)
        self.submit_btn.setVisible(False)
        mid.lay.addWidget(self.submit_btn)
        mid.setMinimumWidth(330)

        # 右：规则详情
        right = Panel(margins=(20, 18, 20, 16))
        self.d_head = label("", "Faint")
        self.d_text = label("从中间选一条规则", "H2", wrap=True)
        self.d_text.setStyleSheet("font-weight: 500; line-height: 1.6;")
        self.d_cite = QLabel()
        self.d_cite.setWordWrap(True)
        self.d_cite.setTextFormat(Qt.RichText)
        self.d_cite.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.d_bits = QLabel()
        self.d_bits.setWordWrap(True)
        self.d_bits.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.d_steps = QLabel()
        self.d_steps.setWordWrap(True)
        self.d_steps.setTextFormat(Qt.RichText)
        self.d_notes = label("", "Muted", wrap=True)
        for w in (self.d_head, self.d_text, self.d_cite, self.d_bits, self.d_steps, self.d_notes):
            right.lay.addWidget(w)
        right.lay.addStretch(1)
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("驳回时写一句理由")
        self.reject_btn = QPushButton("驳回")
        self.reject_btn.clicked.connect(lambda: self.decide("reject"))
        self.approve_btn = QPushButton("通过")
        self.approve_btn.setObjectName("Primary")
        self.approve_btn.clicked.connect(lambda: self.decide("approve"))
        self.decide_row = QWidget()
        self.decide_row.setLayout(hbox(self.comment, self.reject_btn, self.approve_btn))
        self.decide_row.setVisible(False)
        right.lay.addWidget(self.decide_row)

        self.split.addWidget(self.prof_panel)
        self.split.addWidget(mid)
        self.split.addWidget(right)
        self.split.set_defaults([300, 470, 600])
        st = QSettings()
        if st.value("hardware/split"):
            self.split.restoreState(st.value("hardware/split"))

    def save_layout(self):
        QSettings().setValue("hardware/split", self.split.saveState())

    # ------------------------------------------------------------ 档案

    def reload(self):
        hwdir = self.project / self.hw
        self.rules_error = ""
        try:
            self.rules = json.loads((hwdir / "rules.json").read_text(encoding="utf-8"))
            if not isinstance(self.rules, list) or not all(isinstance(r, dict) and "id" in r and "status" in r
                                                           for r in self.rules):
                raise ValueError("内容不是规则列表")
        except FileNotFoundError:
            self.rules = []
        except (OSError, ValueError) as exc:                # 文件坏了要明说，不能显示成"0 条"让人以为规则库是空的
            self.rules = []
            self.rules_error = f"规则库文件读不了：{exc}"
        self._fill_profile(hwdir)
        self._fill_list()

    def _clear(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            w, sub = it.widget(), it.layout()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif sub is not None:
                self._clear(sub)

    def _kv(self, key: str, value: str, origin: str = "", mono: bool = False) -> QHBoxLayout:
        k = label(key, "Mono" if mono else "Muted")
        k.setFixedWidth(104 if mono else 60)
        k.setWordWrap(True)
        v = label(value, wrap=True)
        v.setMinimumWidth(20)                           # 窄的时候换行，不把右边的来源标记挤出去
        row = hbox(k, v, spacing=10)
        row.setStretch(1, 1)
        if origin:
            chip = origin_chip(origin)
            chip.setFixedHeight(18)
            row.addWidget(chip, 0, Qt.AlignVCenter)
        return row

    def _fill_profile(self, hwdir: Path):
        from ..target.board import find_port
        self._clear(self.prof)
        try:
            prof = json.loads((hwdir / "profile.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.prof.addWidget(label(f"读不到硬件档案 {hwdir / 'profile.json'}：{exc}", "Muted", wrap=True))
            return
        import os
        port = find_port()
        port = os.path.realpath(port) if port else None
        conn = Dot(T()["pass"] if port else T()["ink3"])
        conn.setToolTip("板子已连接" if port else "没找到板子")
        head = hbox(label("硬件档案", "H2"), conn, "stretch")
        self.prof.addLayout(head)
        for key, name in (("board", "板子"), ("chip_variant", "芯片"), ("framework_version", "ESP-IDF")):
            f = prof.get(key, {})
            self.prof.addLayout(self._kv(name, str(f.get("value", "")), f.get("origin", "")))
        self.prof.addLayout(self._kv("串口", port or "没找到，请插上板子", ""))
        pins = prof.get("pins", {})
        self.prof.addWidget(label(f"引脚　{len(pins)} 个，保留 {len(prof.get('reserved_pins', {}))} 个", "Faint"))
        for sig, f in pins.items():
            row = self._kv(sig, f"GPIO{f.get('value')}", f.get("origin", ""), mono=True)
            self.prof.addLayout(row)
        self.prof.addWidget(label("外接器件", "Faint"))
        for d in prof.get("devices", []):
            box = QFrame()
            box.setStyleSheet(f"QFrame {{ border: 1px solid {T()['line']}; border-radius: 12px; }} QLabel {{ border: none; }}")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(12, 8, 12, 8)
            addr = f"，0x{d['address']:02X}" if d.get("address") is not None else ""
            bl.addLayout(hbox(label(f"<b>{d['name']}</b>"), label(f"{d['part']}，{d['bus']}{addr}", "Muted"), "stretch"))
            for k, f in (d.get("params") or {}).items():
                v = f.get("value")
                v = f"0x{v:02X}" if isinstance(v, int) and not isinstance(v, bool) else v
                bl.addLayout(self._kv(k, str(v), f.get("origin", ""), mono=True))
            if d.get("reset"):
                r = d["reset"]["value"]
                bl.addLayout(self._kv("用例前复位", f"0x{r['reg']:02X} 写 0x{r['value']:02X}", d["reset"].get("origin", "")))
            self.prof.addWidget(box)
        meas = measured_entries(hwdir / "measured.md")
        if meas:
            self.prof.addLayout(hbox(label("实测记录", "Faint"), "stretch", label("规则可以引用", "Faint")))
            for mid, title in meas:
                self.prof.addLayout(self._kv(mid, title, mono=True))
        self.prof.addStretch(1)

    # ------------------------------------------------------------ 规则列表

    def _set_filter(self, v: str):
        self.filter = v
        self._fill_list()

    def _rows(self) -> list[dict]:
        rows = [dict(r, status="pending") for r in self.pending] + self.rules
        if self.filter == "pending":
            return [r for r in rows if r["status"] == "pending"]
        if self.filter == "trusted":
            return [r for r in rows if r["status"] in ("trusted", "approved")]
        if self.filter == "rejected":
            return [r for r in rows if is_rejected(r)]
        return rows

    def _fill_list(self):
        trusted = sum(1 for r in self.rules if r["status"] in ("trusted", "approved"))
        rej = sum(1 for r in self.rules if is_rejected(r))
        self.rules_sub.setText(f"{trusted} 条已确认" + (f"，{len(self.pending)} 条等你确认" if self.pending else "")
                               + (f"，{rej} 条退回" if rej else ""))
        if getattr(self, "rules_error", ""):
            self.rules_sub.setText(f"⚠ {self.rules_error}（rules.json；从 git 或备份恢复）")
        keep = self.current["id"] if self.current else None
        c = T()
        self.list.blockSignals(True)
        self.list.clearContents()
        self.list.clearSpans()
        groups: dict[str, list[dict]] = {}
        for r in self._rows():
            g = "等你确认" if r["status"] == "pending" else ("、".join(s for s in r["scope"] if s != "all") or "通用")
            groups.setdefault(g, []).append(r)
        self.list.setRowCount(sum(len(v) + 1 for v in groups.values()))
        row, select = 0, None
        for g, rows in groups.items():
            hdr = QTableWidgetItem(g)
            hdr.setFlags(Qt.NoItemFlags)
            hdr.setForeground(QColor(c["ink3"]))
            self.list.setItem(row, 0, hdr)
            self.list.setSpan(row, 0, 1, 3)
            row += 1
            for r in rows:
                dec = self.decisions.get(r["id"], ("", ""))[0]
                rid = QTableWidgetItem(r["id"])
                rid.setForeground(QColor(c["ink2"]))
                f = rid.font()
                f.setFamily("monospace")
                rid.setFont(f)
                text = QTableWidgetItem(r["text"])
                text.setData(Qt.UserRole, r)
                text.setToolTip(r["text"])
                st_text, st_col = (("你通过了", c["accent"]) if dec == "approve" else ("你驳回了", c["fail"]) if dec == "reject"
                                   else ("等你确认", c["run"]) if r["status"] == "pending"
                                   else ("已确认", c["accent"]) if r["status"] in ("trusted", "approved") else ("退回", c["fail"]))
                st = QTableWidgetItem(st_text)
                st.setForeground(QColor(st_col))
                for col, it in enumerate((rid, text, st)):
                    self.list.setItem(row, col, it)
                if r["id"] == keep:
                    select = row
                row += 1
        self.list.blockSignals(False)
        if select is not None:
            self.list.setCurrentCell(select, 1)
        n_dec = sum(1 for r in self.pending if r["id"] in self.decisions)
        self.submit_btn.setVisible(bool(self.pending))
        self.submit_btn.setEnabled(bool(self.pending) and n_dec == len(self.pending))
        self.submit_btn.setText(f"提交确认结果（已表态 {n_dec} / {len(self.pending)}）")

    # ------------------------------------------------------------ 规则详情

    def library(self):
        if self._library is None:
            from ..grounding.sources import SourceLibrary
            self._library = SourceLibrary.load(self.project / ".ff_sources" / "library")
        return self._library

    def _show(self, item):
        r = item.data(Qt.UserRole) if item is not None else None
        if not r:
            return
        self.current = r
        c = T()
        self.d_head.setText(f"{r['id']}　{KIND_NAMES.get(r['kind'], r['kind'])}　适用 {'、'.join(r['scope'])}")
        self.d_text.setText(r["text"])
        cite = r.get("cite", {})
        where = f"第 {cite['page']} 页" if cite.get("page") else "全文"
        quote = html.escape(cite.get("quote", ""))
        self.d_cite.setText(
            f"<div style='background:{c['bg']}; padding:10px'>"
            f"<b>{html.escape(cite.get('doc', ''))}</b>　<span style='color:{c['ink2']}'>{where}　"
            f"{TIER_NAMES.get(r.get('tier'), r.get('tier', ''))}</span><br>"
            f"<span style='font-family:monospace; background:#FBEFC9; color:#1C2026'>{quote}</span></div>")
        bits = ""
        try:
            doc = self.library().docs.get(cite.get("doc", ""))
            if doc is not None and cite.get("page"):
                bits = doc.page_bits(int(cite["page"]))
        except Exception:                                   # noqa: BLE001  资料缓存读不到就不显示位标注
            bits = ""
        self.d_bits.setVisible(bool(bits))
        self.d_bits.setText(f"这一页的寄存器位标注（平台按 PDF 坐标算出，不是原文）：\n{bits}")
        self.d_bits.setStyleSheet(f"border: 1px dashed {c['grip']}; border-radius: 12px; padding: 8px 12px; color: {c['ink2']};")
        steps = r.get("steps") or []
        if steps:
            marks = {"ok": ("●", c["pass"]), "wrong": ("●", c["fail"]), "unknown": ("○", c["run"]), "skip": ("○", c["ink3"])}
            rows = []
            for n, st, msg in steps:
                m, col = marks.get(st, ("○", c["ink3"]))
                rows.append(f"<span style='color:{c['ink3']}'>{n}</span>　<span style='color:{col}'>{m}</span>　{html.escape(msg)}")
            self.d_steps.setText("规则判定器的核对：<br>" + "<br>".join(rows))
        else:
            self.d_steps.setText("" if r["status"] != "pending" else "（没有拿到核对明细）")
        notes = list(r.get("notes", [])) + [f"多次被退回：{x}" for x in r.get("reasons", [])]
        self.d_notes.setText("\n".join(notes))
        pend = r["status"] == "pending"
        self.decide_row.setVisible(pend)
        if pend:
            v, note = self.decisions.get(r["id"], ("", ""))
            self.comment.setText(note)

    def _clear_detail(self, text: str = "从中间选一条规则"):
        self.d_head.setText("")
        self.d_text.setText(text)
        for w in (self.d_cite, self.d_bits, self.d_steps, self.d_notes):
            w.setText("")
        self.d_bits.setVisible(False)

    def decide(self, verdict: str):
        r = self.current
        if r is None or r["status"] != "pending":
            return
        note = self.comment.text().strip()
        if verdict == "reject" and not note:
            self.comment.setPlaceholderText("驳回要写一句理由")
            self.comment.setFocus()
            return
        self.decisions[r["id"]] = (verdict, note)
        self.comment.clear()
        nxt = next((x for x in self.pending if x["id"] not in self.decisions), None)
        self.current = nxt or r
        self._fill_list()

    # ------------------------------------------------------------ 确认会话

    def begin_review(self, payload: dict, on_submit):
        """流程等人确认规则：把待确认的规则放进"等你确认"，提交时把决定交给 on_submit。"""
        self.pending = list(payload.get("pending", [])) + list(payload.get("escalated", []))
        self.decisions = {}
        self.on_submit = on_submit
        self.current = self.pending[0] if self.pending else None
        self.seg.set_value("pending")
        self.filter = "pending"
        self._fill_list()

    def submit(self):
        if self.on_submit is None:
            return
        decisions, cb = dict(self.decisions), self.on_submit
        self.pending, self.decisions, self.on_submit = [], {}, None
        self.current = None
        self.comment.clear()
        self.decide_row.setVisible(False)
        self._clear_detail("已提交。流程会把通过的规则入库，跑完后这里会重新读取规则库。")
        cb(decisions)
        self.build_status.setText(f"已提交：通过 {sum(1 for v, _ in decisions.values() if v == 'approve')} 条，"
                                  f"驳回 {sum(1 for v, _ in decisions.values() if v == 'reject')} 条。流程继续。")
        self.seg.set_value("all")
        self.filter = "all"
        self._fill_list()

    # ------------------------------------------------------------ 本页发起提炼规则

    def start_build(self):
        if self.backend is not None:
            return
        scopes = [s.strip() for s in self.scope_edit.text().replace("，", ",").split(",") if s.strip()]
        if not scopes:
            self.build_status.setText("先写要查的范围，例如 max30102 或 i2c,mpu6050。")
            return
        self.backend = RulesBackend(self)
        self.backend.event.connect(self._on_event)
        self.backend.ask.connect(lambda kind, payload: self.begin_review(payload, self.backend.answer))
        self.backend.finished.connect(self._build_done)
        try:
            self.backend.start(project=self.project, hw=self.hw, scopes=scopes, runs_dir=self.project / ".ff_runs")
        except Exception as exc:
            self.build_status.setText(f"没能开始：{exc}")
            self.backend = None
            return
        self.build_btn.setEnabled(False)
        self.build_status.setText(f"正在查资料：{'、'.join(scopes)}。核对完会在这里等你确认。")

    def _on_event(self, e: dict):
        t, d = e.get("type"), e.get("data", {})
        if t == "rules_round_done":
            self.build_status.setText(f"第 {d.get('round')} 轮：已核对 {d.get('checked')} 条，退回 {d.get('rejected')} 条"
                                      + (f"；还缺 {'、'.join(d['missing'])}" if d.get("missing") else ""))
        elif t == "tool_result":
            self.build_status.setText(f"查资料 Agent 正在{d.get('summary') or '查资料'}")

    def _build_done(self, info: dict):
        self.backend = None
        self.build_btn.setEnabled(True)
        if info.get("error"):
            self.build_status.setText(f"出错：{info['error']}")
        else:
            self.build_status.setText(f"结束：新入库 {info.get('trusted')} 条，没表态 {info.get('pending')} 条，"
                                      f"约 {info.get('cost', 0):.2f} 元" + (f"；还缺 {'、'.join(info['missing'])}" if info.get("missing") else ""))
        self.reload()
        self.rules_changed.emit()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.backend is None and not self.pending:
            self.reload()                                  # 流程跑完规则可能入库了，回到本页时重新读

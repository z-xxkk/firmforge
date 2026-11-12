"""规则在对话里就地确认（DECISIONS 第 114 条）：不用切到知识库页。

每条规则一张卡片：左边是规则、出处和引用原文、规则判定器的核对结果、要你看的确认要点和通过/驳回；
右边是手册原页（引用原文用框标出，点一下打开大图）。全部表态后点"提交"，流程才继续。
原页图片一张张在后台慢慢画（pdftoppm 一页约零点几秒），不卡住对话。
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .widgets import T, label

KIND_NAMES = {"api": "接口用法", "pin": "引脚", "peripheral": "外设", "register": "寄存器", "device": "外接器件",
              "timing": "时序", "errata": "勘误"}
TIER_NAMES = {"official_manual": "官方手册", "official_errata": "官方勘误", "official_code": "官方代码",
              "official_example": "官方例程", "official_mirror": "官方原版（非官方网站）", "community": "社区资料",
              "user": "用户提供"}
THUMB_W = 270


def review_points(rule: dict) -> list[str]:
    """确认要点：判定器做不到、要人看的地方。核对里没通过或没下结论的步骤排在前面。"""
    pts = [f"判定器没能确认：{msg}" for _n, st, msg in rule.get("steps") or [] if st in ("wrong", "unknown")]
    pts += [f"多次被退回：{x}" for x in rule.get("reasons", [])]
    pts.append("引用原文说的是不是这条规则的意思（判定器只核对原文在这一页里，意思要人看）")
    if rule.get("scope"):
        pts.append(f"适用范围 {'、'.join(rule['scope'])} 对不对：别把一个器件的规矩套到别的器件上")
    if rule.get("tier") in ("community", "user"):
        pts.append("出处不是官方资料：最好再对一下官方手册")
    return pts


class RuleCard(QFrame):
    decided = Signal(str, str, str)          # 规则编号、approve/reject、意见
    open_page = Signal(str, int, str)        # 资料、页码、引用

    def __init__(self, rule: dict, page_text: str = ""):
        super().__init__()
        self.rule, self.verdict = rule, ""
        c = T()
        self.setObjectName("RuleCard")
        self.setStyleSheet(f"#RuleCard {{ border: 1px solid {c['line']}; border-radius: 14px; background: {c['surface']}; }}")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(6)
        left.addWidget(label(f"{rule['id']}　{KIND_NAMES.get(rule.get('kind'), rule.get('kind', ''))}　"
                             f"适用 {'、'.join(rule.get('scope', []))}", "Faint"))
        text = label(rule.get("text", ""), "H3", wrap=True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.addWidget(text)
        cite = rule.get("cite") or {}
        where = f"第 {cite['page']} 页" if cite.get("page") else "全文"
        q = QLabel(f"<b>{html.escape(cite.get('doc', ''))}</b>　<span style='color:{c['ink2']}'>{where}　"
                   f"{TIER_NAMES.get(rule.get('tier'), rule.get('tier', ''))}</span><br>"
                   f"<span style='font-family:monospace; background:#FBEFC9; color:#1C2026'>"
                   f"{html.escape(cite.get('quote', ''))}</span>")
        q.setWordWrap(True)
        q.setTextFormat(Qt.RichText)
        left.addWidget(q)
        steps = rule.get("steps") or []
        if steps:
            marks = {"ok": ("●", c["pass"]), "wrong": ("●", c["fail"]), "unknown": ("○", c["run"]), "skip": ("○", c["ink3"])}
            rows = [f"<span style='color:{marks.get(st, ('○', c['ink3']))[1]}'>{marks.get(st, ('○',))[0]}</span> "
                    f"{html.escape(str(msg))}" for _n, st, msg in steps]
            s = QLabel("判定器核对：<br>" + "<br>".join(rows))
            s.setWordWrap(True)
            s.setTextFormat(Qt.RichText)
            s.setStyleSheet(f"color: {c['ink2']}; font-size: 12px;")
            left.addWidget(s)
        pts = QLabel("<b>要你看的</b><br>" + "<br>".join(f"· {html.escape(p)}" for p in review_points(rule)))
        pts.setWordWrap(True)
        pts.setTextFormat(Qt.RichText)
        left.addWidget(pts)
        self.comment = QLineEdit()
        self.comment.setPlaceholderText("意见（驳回要写一句理由）")
        left.addWidget(self.comment)
        btns = QHBoxLayout()
        self.state = label("", "Faint")
        btns.addWidget(self.state, 1)
        self.reject_btn = QPushButton("驳回")
        self.approve_btn = QPushButton("通过")
        self.approve_btn.setObjectName("Primary")
        self.reject_btn.clicked.connect(lambda: self.decide("reject"))
        self.approve_btn.clicked.connect(lambda: self.decide("approve"))
        btns.addWidget(self.reject_btn)
        btns.addWidget(self.approve_btn)
        left.addLayout(btns)
        row.addLayout(left, 1)
        # 右边：手册原页
        self.page_box = QVBoxLayout()
        self.pic = QPushButton()
        self.pic.setFlat(True)
        self.pic.setCursor(Qt.PointingHandCursor)
        self.pic.setVisible(False)
        self.pic.clicked.connect(self._open)
        self.page_note = label("原页稍后显示……" if cite.get("page") else "这条规则引用的不是 PDF 某一页", "Faint", wrap=True)
        self.page_note.setFixedWidth(THUMB_W)
        self.page_text = label(page_text[:900], "Faint", wrap=True) if page_text and not cite.get("page") else None
        self.page_box.addWidget(self.pic)
        self.page_box.addWidget(self.page_note)
        if self.page_text is not None:
            self.page_text.setFixedWidth(THUMB_W)
            self.page_box.addWidget(self.page_text)
        self.page_box.addStretch(1)
        row.addLayout(self.page_box)

    def show_page(self, pm, note: str, y: int) -> None:
        """pm：画好框的原页（QPixmap）或 None。缩到卡片宽，从框住的那一段附近截一块。"""
        if pm is None:
            self.page_note.setText(note)
            return
        scaled = pm.scaledToWidth(THUMB_W, Qt.SmoothTransformation)
        k = scaled.width() / max(1, pm.width())
        top = max(0, min(int(y * k) - 90, scaled.height() - 300))
        crop = scaled.copy(0, top, THUMB_W, min(300, scaled.height()))
        self.pic.setIcon(crop)
        self.pic.setIconSize(crop.size())
        self.pic.setFixedSize(crop.size())
        self.pic.setVisible(True)
        self.pic.setToolTip("点开看整页")
        self.page_note.setText(note + "。点图看整页")

    def _open(self):
        cite = self.rule.get("cite") or {}
        if cite.get("doc") and cite.get("page"):
            self.open_page.emit(cite["doc"], int(cite["page"]), cite.get("quote", ""))

    def decide(self, verdict: str) -> bool:
        note = self.comment.text().strip()
        if verdict == "reject" and not note:
            self.comment.setPlaceholderText("驳回要写一句理由")
            self.comment.setFocus()
            return False
        self.verdict = verdict
        self.state.setText("已通过" if verdict == "approve" else f"已驳回：{note}")
        self.state.setStyleSheet(f"color: {T()['pass'] if verdict == 'approve' else T()['fail']};")
        self.decided.emit(self.rule["id"], verdict, note)
        return True


class RuleReview(QWidget):
    """一组待确认的规则 + 提交。submitted 发出 {规则编号: (approve|reject, 意见)}。"""
    submitted = Signal(dict)

    def __init__(self, payload: dict, project: Path, hw: str, library=None):
        super().__init__()
        self.project, self.hw, self.library = Path(project), hw, library
        self.rules = list(payload.get("pending", [])) + list(payload.get("escalated", []))
        self.decisions: dict[str, tuple[str, str]] = {}
        self.viewers: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.cards: list[RuleCard] = []
        for r in self.rules:
            card = RuleCard(r, self._page_text(r))
            card.decided.connect(self._on_decided)
            card.open_page.connect(self.open_page)
            lay.addWidget(card)
            self.cards.append(card)
        bar = QHBoxLayout()
        self.count = label("", "Muted")
        bar.addWidget(self.count, 1)
        self.submit_btn = QPushButton("提交")
        self.submit_btn.setObjectName("Primary")
        self.submit_btn.clicked.connect(self.submit)
        bar.addWidget(self.submit_btn)
        lay.addLayout(bar)
        self._update()
        self._todo = [c for c in self.cards if (c.rule.get("cite") or {}).get("page")]
        QTimer.singleShot(50, self._next_page)

    def _page_text(self, r: dict) -> str:
        cite = r.get("cite") or {}
        try:
            doc = self.library.docs.get(cite.get("doc", "")) if self.library is not None else None
            return doc.page(None)[:900] if doc is not None and not cite.get("page") else ""
        except Exception:                                    # noqa: BLE001
            return ""

    def _next_page(self):
        """一张一张画原页，画完一张再排下一张，对话不卡。"""
        if not self._todo:
            return
        card = self._todo.pop(0)
        cite = card.rule["cite"]
        try:
            from .page_viewer import page_image
            pm, note, y = page_image(self.project, self.project / self.hw, cite["doc"], int(cite["page"]), cite.get("quote", ""))
        except Exception as exc:                              # noqa: BLE001  原页画不出来不影响确认
            pm, note, y = None, f"原页显示不了：{exc}", 0
        card.show_page(pm, note, y)
        QTimer.singleShot(10, self._next_page)

    def open_page(self, doc_id: str, page: int, quote: str):
        from .page_viewer import PageViewer
        doc = self.library.docs.get(doc_id) if self.library is not None else None
        title = doc.title if doc is not None else doc_id
        layout = (doc.page_layout(page) or doc.page(page)) if doc is not None else ""
        v = PageViewer(self.project, self.project / self.hw, doc_id, title, page, quote, layout, parent=self)
        v.setAttribute(Qt.WA_DeleteOnClose)
        self.viewers = [w for w in self.viewers if w.isVisible()] + [v]
        v.show()

    def _on_decided(self, rid: str, verdict: str, note: str):
        self.decisions[rid] = (verdict, note)
        self._update()

    def _update(self):
        n, done = len(self.rules), len(self.decisions)
        ok = sum(1 for v, _ in self.decisions.values() if v == "approve")
        self.count.setText(f"已表态 {done}/{n}（通过 {ok}，驳回 {done - ok}）。全部表态后提交，流程才继续")
        self.submit_btn.setEnabled(done == n and n > 0)

    def submit(self):
        if len(self.decisions) != len(self.rules):
            return
        for c in self.cards:
            for w in (c.approve_btn, c.reject_btn, c.comment):
                w.setEnabled(False)
        self.submit_btn.setEnabled(False)
        self.submitted.emit(dict(self.decisions))

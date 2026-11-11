"""手册原页窗口（DECISIONS 第 105 条）：显示规则引用的那一页，把引用原文框出来。

参考 CoCounsel、Noxtua 的做法：点引用，在旁边打开原文档并高亮那一段，人确认规则时能看到原文的上下文。
窗口不阻塞主窗口，可以一边看原页一边在硬件页点通过或驳回。没装 poppler 时只显示按版面排列的文字，并写明原因。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QScrollArea, QTextEdit, QVBoxLayout

from ..grounding.pagepic import page_words, quote_boxes, raw_pdf, render_page
from .widgets import T, label

PAGES_DIR = Path(".ff_sources") / "pages"
RAW_DIR = Path(".ff_sources") / "raw"


def page_image(project: Path, hwdir: Path, doc: str, page: int, quote: str) -> tuple[QPixmap | None, str, int]:
    """画好框的原页图片。返回 (图片, 说明, 第一个框的纵坐标)；图片为 None 时说明里是原因。"""
    pdf = raw_pdf(hwdir, project / RAW_DIR, doc)
    if pdf is None:
        return None, f"资料 {doc} 不是 PDF，或者原件不在 {RAW_DIR}（按资料清单下载后再看）", 0
    png = project / PAGES_DIR / f"{doc}-p{page}.png"
    why = render_page(pdf, page, png)
    if why:
        return None, why, 0
    pm = QPixmap(str(png))
    if pm.isNull():
        return None, f"读不了渲染出的图片 {png}", 0
    words = page_words(pdf, page)
    if isinstance(words, str):
        return pm, f"{words}；只显示原页，没有框出原文", 0
    boxes = quote_boxes(words.words, quote)
    if not boxes:
        return pm, "在这一页没有定位到引用原文的位置（原文核对是另一回事，以规则判定器的结果为准）", 0
    sx, sy = pm.width() / words.width, pm.height() / words.height
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(T()["accent"] if T() is not None else "#2F6F68"), 3))
    for b in boxes:
        p.drawRoundedRect(QRectF(b.x0 * sx - 4, b.y0 * sy - 3, (b.x1 - b.x0) * sx + 8, (b.y1 - b.y0) * sy + 6), 4, 4)
    p.end()
    return pm, f"绿框是引用原文的位置（共 {len(boxes)} 行）", int(boxes[0].y0 * sy)


class PageViewer(QDialog):
    """一页手册。layout_text：没有图片时退而显示的按版面排列的文字。"""

    def __init__(self, project: Path, hwdir: Path, doc: str, title: str, page: int, quote: str,
                 layout_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} 第 {page} 页")
        self.setModal(False)
        self.resize(760, 900)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.addWidget(label(f"{title}　第 {page} 页", "H3", wrap=True))
        q = label(f"引用原文：{quote}", "Muted", wrap=True)
        q.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(q)
        pm, note, y = page_image(project, hwdir, doc, page, quote)
        self.note = label(note, "Faint", wrap=True)
        lay.addWidget(self.note)
        self.has_image = pm is not None
        if pm is not None:
            scroll = QScrollArea()
            scroll.setWidgetResizable(False)
            pic = QLabel()
            pic.setPixmap(pm)
            scroll.setWidget(pic)
            lay.addWidget(scroll, 1)
            self.resize(min(pm.width() + 60, 1100), 920)            # 窗口按页面宽度，不用左右拖
            scroll.verticalScrollBar().setValue(max(0, y - 200))     # 一打开就看到框住的那一段
        else:
            text = QTextEdit()
            text.setObjectName("Code")
            text.setReadOnly(True)
            text.setPlainText(layout_text or "（没有这一页的文字）")
            lay.addWidget(text, 1)

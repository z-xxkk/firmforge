"""界面样式（界面草图第 4 版，DECISIONS 第 62 条）：浅色简约、圆角分档、松石绿唯一强调色；跟随系统的浅色 / 深色。

圆角按层级分档：面板 16、卡片 12–14、按钮与输入框 10、标签与发送按钮全圆。面板之间留 8 像素的缝，缝就是可拖动的分隔条。
"""

from __future__ import annotations

from dataclasses import dataclass

LIGHT = {
    "bg": "#F3F4F6", "surface": "#FFFFFF", "ink": "#1C2026", "ink2": "#6E7580", "ink3": "#9AA0A8",
    "line": "#E4E6EA", "accent": "#2F6F68", "accent_soft": "#E6F0EE", "run": "#C98A2B", "run_soft": "#F7EEDD",
    "fail": "#C8483D", "fail_soft": "#F8E6E4", "pass": "#3E8E63", "pass_soft": "#E5F2EA", "grip": "#D3D7DC", "canvas": "#FBFBFC",
    "dot": "#E3E6EA", "button": "#1C2026", "button_text": "#FFFFFF",
}
DARK = {
    "bg": "#16191D", "surface": "#1F2328", "ink": "#E8EAED", "ink2": "#A3AAB3", "ink3": "#737B85",
    "line": "#30353C", "accent": "#5FB3A8", "accent_soft": "#22403C", "run": "#E0A54A", "run_soft": "#3B3020",
    "fail": "#E06A5F", "fail_soft": "#402523", "pass": "#5DB585", "pass_soft": "#203A2C", "grip": "#454B53", "canvas": "#1B1E22",
    "dot": "#2B3036", "button": "#E8EAED", "button_text": "#16191D",
}
STATUS = {"pass": "pass", "done": "pass", "fail": "fail", "failed": "fail", "error": "fail", "rejected": "fail",
          "running": "run", "ready": "run", "incomplete": "run", "interrupted": "run"}

# 有的 Linux 把通用 sans-serif 映射成衬线体（FreeSerif），所以列出常见无衬线字体，不依赖系统默认
SANS = ('"Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC", '
        '"Noto Sans", "DejaVu Sans", "Liberation Sans", "Noto Sans CJK JP", "WenQuanYi Micro Hei", sans-serif')
MONO = '"JetBrains Mono", "Cascadia Mono", "Noto Sans Mono", "DejaVu Sans Mono", Consolas, monospace'


@dataclass(frozen=True)
class Theme:
    c: dict
    dark: bool = False

    def __getitem__(self, k: str) -> str:
        return self.c[k]

    def status_color(self, status: str) -> str:
        return self.c[STATUS.get(status, "ink3")] if STATUS.get(status) else self.c["ink3"]


def current(dark: bool) -> Theme:
    return Theme(DARK if dark else LIGHT, dark)


def arrow_icon(color: str) -> str:
    """下拉框的小三角：运行时画成 PNG 放在临时目录，样式表里引用它（QSS 不能直接画三角形）。"""
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
    path = Path(tempfile.gettempdir()) / f"firmforge_arrow_{color.lstrip('#')}.png"
    if not path.is_file():
        pm = QPixmap(20, 12)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        tri = QPainterPath(QPointF(4, 3))
        tri.lineTo(QPointF(16, 3))
        tri.lineTo(QPointF(10, 9.5))
        tri.closeSubpath()
        p.fillPath(tri, QColor(color))
        p.end()
        pm.save(str(path))
    return path.as_posix()


def stylesheet(t: Theme) -> str:
    c = t.c
    arrow = arrow_icon(c["ink3"])
    return f"""
* {{ font-family: {SANS}; font-size: 13px; color: {c['ink']}; }}
QMainWindow, #Root {{ background: {c['bg']}; }}
#Panel {{ background: {c['surface']}; border-radius: 16px; }}
#Rail {{ background: transparent; }}
QToolButton#RailButton {{ border: none; border-radius: 12px; padding: 8px 2px 6px 2px; color: {c['ink2']};
    font-size: 11px; min-width: 54px; }}
QToolButton#RailButton:checked {{ background: {c['surface']}; color: {c['ink']}; }}
QToolButton#RailButton:hover {{ background: {c['surface']}; }}
#Logo {{ background: {c['ink']}; color: {c['bg']}; border-radius: 11px; font-family: {MONO}; font-weight: 700; }}
QLabel#H1 {{ font-size: 20px; font-weight: 600; }}
QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
QLabel#H3 {{ font-size: 13px; font-weight: 600; }}
QLabel#Muted {{ color: {c['ink2']}; }}
QLabel#Faint {{ color: {c['ink3']}; font-size: 12px; }}
QLabel#Mono, QPlainTextEdit#Code, QTextEdit#Code {{ font-family: {MONO}; font-size: 12px; }}
QPushButton {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 5px 14px; }}
QPushButton:hover {{ border-color: {c['ink3']}; }}
QPushButton:disabled {{ color: {c['ink3']}; }}
QPushButton#Primary {{ background: {c['button']}; color: {c['button_text']}; border-color: {c['button']}; font-weight: 500; }}
QPushButton#Ghost {{ background: {c['bg']}; border-color: transparent; }}
QPushButton#Send {{ background: {c['button']}; border: none; border-radius: 16px; min-width: 32px; max-width: 32px;
    min-height: 32px; max-height: 32px; padding: 0; color: {c['button_text']}; font-size: 15px; font-weight: 700; }}
QComboBox#Pill {{ border: 1px solid {c['line']}; border-radius: 13px; padding: 2px 10px; background: {c['surface']};
    color: {c['ink2']}; font-size: 12px; min-height: 22px; }}
QComboBox::drop-down {{ border: none; width: 18px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 6px; }}
QComboBox#Pill::drop-down {{ width: 14px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: none; }}
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{ border: 1px solid {c['line']}; border-radius: 10px; padding: 4px 10px;
    background: {c['surface']}; min-height: 22px; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {c['accent']}; }}
QComboBox QAbstractItemView {{ border: 1px solid {c['line']}; border-radius: 10px; background: {c['surface']};
    selection-background-color: {c['accent_soft']}; selection-color: {c['ink']}; }}
QPlainTextEdit, QTextEdit {{ border: none; background: transparent; }}
QPlainTextEdit#Field {{ border: 1px solid {c['line']}; border-radius: 10px; padding: 6px 8px; background: {c['surface']}; }}
QScrollArea, QScrollArea > QWidget > QWidget#ScrollInner {{ background: transparent; }}
QTableView {{ background: transparent; }}
QHeaderView {{ background: transparent; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
#Composer {{ border: 1px solid {c['line']}; border-radius: 16px; background: {c['surface']}; }}
#Composer QPlainTextEdit {{ font-size: 13px; }}
#CodeBox {{ background: {c['bg']}; border-radius: 12px; }}
QListWidget {{ border: none; background: transparent; outline: none; }}
QListWidget::item {{ border-radius: 10px; padding: 7px 10px; margin: 1px 0; }}
QListWidget::item:selected {{ background: {c['bg']}; color: {c['ink']}; }}
QListWidget::item:hover {{ background: {c['bg']}; }}
QTreeWidget, QTableWidget {{ border: none; background: transparent; outline: none; gridline-color: {c['line']}; }}
QTreeWidget::item {{ padding: 3px 4px; border-radius: 8px; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{ background: {c['run_soft']}; color: {c['ink']}; }}
QHeaderView::section {{ background: transparent; border: none; border-bottom: 1px solid {c['line']}; color: {c['ink3']};
    font-size: 12px; padding: 6px 8px; }}
QTabBar::tab {{ background: transparent; border: none; padding: 8px 2px 8px 2px; margin-right: 14px; color: {c['ink2']}; }}
QTabBar::tab:selected {{ color: {c['ink']}; border-bottom: 2px solid {c['ink']}; }}
QTabWidget::pane {{ border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['grip']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['grip']}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {c['surface']}; padding: 10px 14px 4px 14px; font-weight: 600; }}
QMainWindow::separator {{ background: {c['bg']}; width: 8px; height: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {c['ink3']}; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
QToolTip {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 8px; padding: 4px 8px; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 8px; }}
QMenu::item:selected {{ background: {c['bg']}; }}
#StatusBar {{ color: {c['ink2']}; font-size: 12px; padding-left: 76px; }}
"""

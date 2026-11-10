"""在 Linux 桌面的应用列表里加上 FirmForge（`firmforge gui --install-shortcut`）。

按 freedesktop 的桌面项规范写 ~/.local/share/applications/firmforge.desktop，图标放在
~/.local/share/icons/hicolor/256x256/apps/firmforge.png。图标就是界面左上角的 "FF" 标志，用 Qt 画出来，不依赖图片文件。
窗口和应用列表靠 StartupWMClass 与 QGuiApplication.desktopFileName 对上，任务栏里显示同一个图标。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "firmforge"
ICON_SIZE = 256


def icon_path() -> Path:
    return Path.home() / ".local" / "share" / "icons" / "hicolor" / f"{ICON_SIZE}x{ICON_SIZE}" / "apps" / f"{APP_ID}.png"


def desktop_path() -> Path:
    return Path.home() / ".local" / "share" / "applications" / f"{APP_ID}.desktop"


def render_icon(path: Path, size: int = ICON_SIZE) -> Path:
    """画应用图标：深色圆角方块上写 "FF"（和界面左上角的标志一样）。"""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    _app = QGuiApplication.instance() or QGuiApplication([])          # 画字要有 Qt 应用对象
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1C2026"))
    m = size * 0.06
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.22, size * 0.22)
    f = QFont()
    f.setFamilies(["JetBrains Mono", "DejaVu Sans Mono", "monospace"])
    f.setBold(True)
    f.setPixelSize(int(size * 0.36))
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(0, 0, size, size * 0.96), Qt.AlignCenter, "FF")
    p.setBrush(QColor("#2F6F68"))                                    # 松石绿小圆点：界面唯一的强调色
    p.setPen(Qt.NoPen)
    r = size * 0.055
    p.drawEllipse(QRectF(size * 0.72 - r, size * 0.72 - r, 2 * r, 2 * r))
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path))
    return path


def desktop_text(exe: Path, project: Path, icon: Path) -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=FirmForge",
        "GenericName=嵌入式固件 Agent 平台",
        "Comment=面向嵌入式固件开发的多 Agent 编排与验证平台",
        f"Exec={exe}",
        f"Path={project}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Development;",
        "StartupNotify=true",
        "StartupWMClass=FirmForge",                       # Qt 窗口的 WM_CLASS 是 ("firmforge-gui", "FirmForge")，按类名对上
        "",
    ])


def install(project: Path) -> tuple[Path, Path]:
    """写图标和桌面项；返回 (桌面项, 图标)。用当前 Python 所在虚拟环境里的 firmforge-gui 启动，工作目录是项目目录。"""
    exe = Path(sys.executable).with_name("firmforge-gui")
    if not exe.is_file():
        raise FileNotFoundError(f"没找到 {exe}：请先在项目目录运行 pip install -e \".[gui,sources]\"")
    icon = render_icon(icon_path())
    dp = desktop_path()
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_text(desktop_text(exe, Path(project).resolve(), icon), encoding="utf-8")
    dp.chmod(0o755)
    tool = shutil.which("update-desktop-database")
    if tool:                                               # 让应用列表马上刷新（没有这个工具也不影响，下次登录会刷新）
        subprocess.run([tool, str(dp.parent)], capture_output=True, check=False)
    return dp, icon


def uninstall() -> list[Path]:
    removed = []
    for p in (desktop_path(), icon_path()):
        if p.is_file():
            p.unlink()
            removed.append(p)
    return removed

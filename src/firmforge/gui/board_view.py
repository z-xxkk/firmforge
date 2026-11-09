"""新流程用的小部件和取数（DECISIONS 第 100 条）：上板测试结果、状态栏上的板子信息、一次尝试的文件。

上板测试结果从运行目录里每次尝试的 serial.log 重新解析：每个用例一行，没通过的自动展开原因，下面放串口原文。
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QScrollArea, QTextEdit, QVBoxLayout, QWidget

from ..target.harness import parse_board_output
from .widgets import Dot, GripSplitter, T, hbox, label


def attempt_dir(run_dir: Path | None, module: str, attempt: int) -> Path | None:
    if run_dir is None or not attempt:
        return None
    d = Path(run_dir) / "nodes" / module / f"attempt_{attempt}"
    return d if d.is_dir() else None


def attempt_code(adir: Path | None) -> str:
    """一次尝试交上来的文件（实现和测试），按路径排好。"""
    if adir is None or not (adir / "main").is_dir():
        return ""
    out = []
    for f in sorted((adir / "main").rglob("*")):
        if f.is_file() and f.suffix in (".c", ".h"):
            out.append(f"// ===== {f.relative_to(adir / 'main').as_posix()} =====\n"
                       f"{f.read_text(encoding='utf-8', errors='replace')}")
    return "\n".join(out)


def latest_attempt_with(run_dir: Path | None, module: str, name: str, upto: int) -> Path | None:
    """第 upto 次以前（含）最近一次有某个文件的尝试目录。"""
    for n in range(upto, 0, -1):
        d = attempt_dir(run_dir, module, n)
        if d is not None and (d / name).exists():
            return d
    return None


def board_summary(project: Path, hw: str) -> dict:
    """状态栏用：板子型号、串口、ESP-IDF 版本、规则库里已确认的条数。读不到的项留空，不报错。"""
    import os
    from ..target.board import find_port
    port = find_port()
    out = {"board": "", "port": os.path.realpath(port) if port else "", "idf": "", "rules": 0}   # by-id 长路径换成 /dev/ttyACM0
    hwdir = project / hw
    try:
        prof = json.loads((hwdir / "profile.json").read_text(encoding="utf-8"))
        out["board"] = str(prof.get("board", {}).get("value", ""))
        out["idf"] = str(prof.get("framework_version", {}).get("value", ""))
    except (OSError, ValueError):
        pass
    try:
        rules = json.loads((hwdir / "rules.json").read_text(encoding="utf-8"))
        out["rules"] = sum(1 for r in rules if r.get("status") in ("trusted", "approved"))
    except (OSError, ValueError):
        pass
    return out


GAP = "&nbsp;" * 6                                   # 富文本里普通空格会被合并，用不换行空格拉开各项


def board_status_text(s: dict) -> str:
    """状态栏前半段（富文本）：板子连着没有、ESP-IDF 版本、规则库条数。"""
    c = T()
    dot = c["pass"] if s.get("port") else c["ink3"]
    port = f"{s['port']} 已连接" if s.get("port") else "没找到板子"
    parts = [f"<span style='color:{dot}'>●</span> 板子 <b>{html.escape(s.get('board') or '未知')}</b>，{html.escape(port)}"]
    if s.get("idf"):
        parts.append(f"ESP-IDF <b>{html.escape(s['idf'])}</b>")
    parts.append(f"规则库 <b>{s.get('rules', 0)}</b> 条已确认")
    return GAP.join(parts)


class BoardTestView(QWidget):
    """上板测试：用例列表（没通过的展开原因）+ 串口原文。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        self.title = label("点一个硬件层模块看它的上板测试", "Muted", wrap=True)
        self.title.setMinimumWidth(10)                 # 长标题换行，不撑宽右边的面板
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(self.title, 0, Qt.AlignTop)
        self.cases_box = QWidget()
        self.cases_box.setMinimumWidth(10)
        self.cases = QVBoxLayout(self.cases_box)
        self.cases.setContentsMargins(0, 0, 0, 0)
        self.cases.setSpacing(2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.cases_box)
        self.serial = QTextEdit()
        self.serial.setObjectName("Code")
        self.serial.setReadOnly(True)
        split = self.split = GripSplitter(Qt.Vertical)  # 用例列表和串口原文之间也能拖
        split.addWidget(scroll)
        split.addWidget(self.serial)
        split.set_defaults([220, 160])
        lay.addWidget(split, 1)
        lay.addStretch(0)

    def _clear(self):
        while self.cases.count():
            w = self.cases.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()

    def show_log(self, log: Path | None, title: str) -> None:
        self._clear()
        if log is None or not log.is_file():
            self.title.setText(f"{title}：还没有上板测试（逻辑层在电脑上测，或者还没跑到上板这一步）")
            self.serial.setPlainText("")
            self.split.setVisible(False)
            return
        self.split.setVisible(True)
        text = log.read_text(encoding="utf-8", errors="replace")
        res = parse_board_output(text)
        passed = sum(1 for x in res.cases if x.status == "pass")
        head = f"{title}：{len(res.cases)} 个用例，通过 {passed}"
        if res.crash:
            head += "，芯片崩溃"
        if res.hang:
            head += "，没有看到结束标记（卡死或超时）"
        if res.skipped:
            head = f"{title}：上板测试跳过（{res.skipped}）"
        self.title.setText(head)
        c = T()
        for x in res.cases:
            col = {"pass": c["pass"], "fail": c["fail"]}.get(x.status, c["ink3"])
            row = QWidget()
            if x.status == "fail":
                row.setStyleSheet(f"background: {c['fail_soft']}; border-radius: 10px;")
            name = label(x.name, wrap=True)
            name.setMinimumWidth(10)
            row.setLayout(hbox(Dot(col), name, margins=(10, 4, 10, 4)))
            self.cases.addWidget(row)
            if x.status == "fail":
                why = QLabel(f"{x.message}　{x.where}")
                why.setStyleSheet(f"color: {c['fail']}; font-size: 12px; padding: 0 10px 4px 27px;")
                why.setWordWrap(True)
                self.cases.addWidget(why)
        self.cases.addStretch(1)
        self.serial.setHtml(_serial_html(text))


def _serial_html(text: str) -> str:
    c = T()
    lines = []
    for ln in text.splitlines()[-400:]:
        e = html.escape(ln)
        if ":FAIL" in ln or "Guru Meditation" in ln:
            e = f"<span style='color:{c['fail']}'>{e}</span>"
        elif ":PASS" in ln:
            e = f"<span style='color:{c['pass']}'>{e}</span>"
        elif "FF_LOG:" in ln:
            e = f"<span style='color:{c['ink']}'>{e}</span>"
        else:
            e = f"<span style='color:{c['ink2']}'>{e}</span>"
        lines.append(e)
    return "<pre style='margin:0'>" + "\n".join(lines) + "</pre>"


def rules_used_text(run_dir: Path | None, store_path: Path) -> str:
    """这次运行用到的规则：编号、内容、出处。"""
    if run_dir is None or not (Path(run_dir) / "run.json").is_file():
        return "（运行结束后显示这次用到的规则）"
    info = json.loads((Path(run_dir) / "run.json").read_text(encoding="utf-8"))
    ids = info.get("rules_used") or []
    if not ids:
        mode = info.get("rule_mode", "")
        return "这次没有给规则（配置 none 或 single）。" if mode in ("none", "single") else "（运行记录里没有规则清单）"
    try:
        rules = {r["id"]: r for r in json.loads(Path(store_path).read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        rules = {}
    out = []
    for rid in ids:
        r = rules.get(rid)
        if r is None:
            out.append(f"{rid}（规则库里已经没有这一条，可能是未核对的临时规则）")
            continue
        cite = r.get("cite", {})
        where = cite.get("doc", "") + (f" 第 {cite['page']} 页" if cite.get("page") else "")
        out.append(f"{rid}　{r.get('text', '')}\n　　出处：{where}")
    return "\n".join(out)


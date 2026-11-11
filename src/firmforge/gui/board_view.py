"""新流程用的小部件和取数（DECISIONS 第 100 条）：上板测试结果、状态栏上的板子信息、一次尝试的文件。

上板测试结果从运行目录里每次尝试的 serial.log 重新解析：每个用例一行，没通过的自动展开原因，下面放串口原文。
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from ..target.harness import parse_board_output
from .widgets import Dot, FlowLayout, GripSplitter, T, hbox, label


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
        scroll = self.scroll = QScrollArea()
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
        if res.started and not res.cases and not res.crash:
            head += "：一个用例都没有跑（测试文件里可能没有 TEST_CASE，或者没交测试文件）"
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
        self.scroll.setVisible(bool(res.cases))           # 一个用例都没有就不显示空列表，只看串口原文
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



def final_attempts(run_dir: Path | None) -> dict[str, Path]:
    """每个模块最后一次交上来的代码所在目录（新流程是 main/，旧流程是 src/）。"""
    out: dict[str, Path] = {}
    nodes = Path(run_dir) / "nodes" if run_dir else None
    if nodes is None or not nodes.is_dir():
        return out
    for mod in sorted(p for p in nodes.iterdir() if p.is_dir()):
        tries = sorted((p for p in mod.glob("attempt_*") if p.is_dir()), key=lambda p: int(p.name.split("_")[-1]))
        for a in reversed(tries):
            code = next((a / sub for sub in ("main", "src") if (a / sub).is_dir()), None)
            if code is not None and any(f.is_file() for f in code.rglob("*")):
                out[mod.name] = code
                break
    return out


def export_run(run_dir: Path, dest: Path) -> list[Path]:
    """把这次运行每个模块最后交的代码复制到 dest/<运行编号>/<模块>/，返回复制出来的文件。"""
    import shutil
    target = Path(dest) / Path(run_dir).name
    copied: list[Path] = []
    for mod, code in final_attempts(run_dir).items():
        for f in sorted(code.rglob("*")):
            if f.is_file():
                to = target / mod / f.relative_to(code)
                to.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, to)
                copied.append(to)
    return copied


# ---------------------------------------------------------------- 修正时间线（DECISIONS 第 105 条）
# 一个模块从第一次检查到结束的每一步排成一行：每次尝试的检查结果 → 诊断归因 → 交给谁改 → 下一次尝试。
# 参考 GitHub Actions 一次运行有多次尝试时可以在尝试之间切换、对比各次失败；点某一次尝试，下面各标签换成那一次的内容。

SCOPE_NAMES = {"compliance": "合规", "build": "编译", "board_test": "上板", "host_test": "电脑测试", "module": "检查",
               "all": "集成"}
BLAME_NAMES = {"code": "代码", "test": "测试", "rule": "规则", "hardware": "硬件或接线", "spec": "需求"}
FIXER_NAMES = {"coder": "编码 Agent", "tester": "测试 Agent", "reviewer": "审查 Agent", "researcher": "查资料 Agent"}


@dataclass
class TimelineStep:
    kind: str                        # attempt / diagnosis / fix / escalated / rollback / human / end
    attempt: int
    title: str
    lines: list[str] = field(default_factory=list)
    status: str = "info"             # pass / fail / skipped / running / warn / info
    layers: dict[str, str] = field(default_factory=dict)      # 尝试：层 → pass / fail / skipped


def _changed_lines(a: Path | None, b: Path | None) -> tuple[int, int]:
    """两次尝试之间改了多少行：（实现代码，测试代码）。"""
    import difflib

    def files(d: Path | None) -> dict[str, list[str]]:
        if d is None or not (d / "main").is_dir():
            return {}
        return {f.relative_to(d / "main").as_posix(): f.read_text(encoding="utf-8", errors="replace").splitlines()
                for f in (d / "main").rglob("*") if f.is_file() and f.suffix in (".c", ".h")}
    fa, fb = files(a), files(b)
    code = test = 0
    for name in set(fa) | set(fb):
        n = sum(1 for ln in difflib.unified_diff(fa.get(name, []), fb.get(name, []), lineterm="", n=0)
                if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
        if name.startswith("test"):
            test += n
        else:
            code += n
    return code, test


def repair_timeline(events: list[dict], module: str, run_dir: Path | None = None) -> list[TimelineStep]:
    """从事件流拼出一个模块的修正过程。run_dir 给了就补上每次上板通过几个用例、两次之间改了多少行。"""
    steps: list[TimelineStep] = []
    attempts: dict[int, TimelineStep] = {}
    for e in events:
        if e.get("node_id") != module:
            continue
        t, d = e.get("type"), e.get("data") or {}
        n = int(d.get("attempt") or 0)
        if t == "verify_started" and n:
            st = attempts.get(n)
            if st is None:
                st = attempts[n] = TimelineStep("attempt", n, f"第 {n} 次尝试", status="running")
                steps.append(st)
            st.layers.setdefault(d.get("scope", "module"), "running")
        elif t == "verify_finished" and n:
            st = attempts.get(n)
            if st is None:
                st = attempts[n] = TimelineStep("attempt", n, f"第 {n} 次尝试", status="running")
                steps.append(st)
            ok = d["ok"] if "ok" in d else d.get("verdict") == "pass"
            layer = d.get("failed_layer")
            res = "pass" if ok else ("skipped" if layer == "skipped" or d.get("verdict") == "skipped" else "fail")
            st.layers[d.get("scope", "module")] = res
            if res == "fail":
                st.status = "fail"
            elif res == "skipped" and st.status != "fail":
                st.status = "skipped"
            elif st.status == "running" and d.get("scope", "module") in ("board_test", "host_test", "module", "all"):
                st.status = "pass"
        elif t == "diagnosis":
            blame = BLAME_NAMES.get(d.get("blame"), d.get("blame") or "?")
            lines = [x for x in (d.get("instruction") or "", d.get("evidence") or "") if x][:1]
            if d.get("rule"):
                lines.append(f"涉及规则 {d['rule']}")
            steps.append(TimelineStep("diagnosis", n, f"诊断：错在{blame}",
                                      [ln if len(ln) <= 70 else ln[:68] + "…" for ln in lines],
                                      "warn" if d.get("blame") != "hardware" else "fail"))
        elif t == "agent_started" and n > 1 and str(d.get("purpose", "")).startswith("改"):
            who = FIXER_NAMES.get(d.get("role"), d.get("agent") or "Agent")
            steps.append(TimelineStep("fix", n, f"交给{who}", [f"{d.get('purpose')}（{d.get('agent', '')}）"]))
        elif t == "escalated":
            steps.append(TimelineStep("escalated", n, "升级处理", [str(d.get("note") or d.get("level") or "")[:90]], "warn"))
        elif t == "rollback":
            steps.append(TimelineStep("rollback", n, f"回到第 {d.get('to_attempt')} 次的代码",
                                      ["这次改得更差了"], "warn"))
        elif t == "human_wait":
            steps.append(TimelineStep("human", n, "停下等人", [str(d.get("reason") or "")[:90]], "fail"))
        elif t in ("node_done", "node_failed"):
            v = d.get("verdict") or ("pass" if t == "node_done" else "fail")
            names = {"pass": "通过", "fail": "没修好", "skipped": "跳过", "hardware": "等人检查硬件"}
            steps.append(TimelineStep("end", int(d.get("attempts") or 0), f"结果：{names.get(v, v)}",
                                      [f"共 {d['attempts']} 次尝试"] if d.get("attempts") else [],
                                      "pass" if v == "pass" else ("skipped" if v == "skipped" else "fail")))
    for n, st in attempts.items():
        st.lines = [" ".join(f"{SCOPE_NAMES.get(k, k)}{'✓' if v == 'pass' else '✗' if v == 'fail' else '…' if v == 'running' else '－'}"
                             for k, v in st.layers.items())]
        adir = attempt_dir(run_dir, module, n)
        if adir is not None and (adir / "serial.log").is_file():
            res = parse_board_output((adir / "serial.log").read_text(encoding="utf-8", errors="replace"))
            if res.cases:
                ok = sum(1 for c in res.cases if c.status == "pass")
                st.lines.append(f"上板用例 {ok} / {len(res.cases)}" + ("，芯片崩溃" if res.crash else ""))
                if res.failed:
                    st.lines.append("没过：" + res.failed[0].name[:40] + (f" 等 {len(res.failed)} 个" if len(res.failed) > 1 else ""))
        if n > 1 and run_dir is not None:
            code, test = _changed_lines(attempt_dir(run_dir, module, n - 1), adir)
            if code or test:
                st.lines.append(f"比上次改了代码 {code} 行、测试 {test} 行")
    return steps


def soft_wrap(text: str) -> str:
    """在下划线、斜杠、括号、逗号后面插零宽空格：长标识符和路径中间没有空格，QLabel 换不了行。"""
    out = []
    for ch in text:
        out.append(ch)
        if ch in "_/(),.=;":
            out.append("\u200b")
    return "".join(out)


CARD_W = 176


class RepairTimeline(QWidget):
    """横向排开的修正过程；点某一次尝试发出 attempt_clicked(n)。"""
    attempt_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(6)
        self.title = label("点一个模块看它的修正过程", "Muted", wrap=True)
        self.title.setMinimumWidth(10)
        lay.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.row = QWidget()
        self.row_lay = FlowLayout(self.row, spacing=6)      # 放不下就折行，窄面板上也能一眼看完
        self.scroll.setWidget(self.row)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        lay.addWidget(self.scroll, 1)
        self.hint = label("", "Faint", wrap=True)
        self.hint.setMinimumWidth(10)
        lay.addWidget(self.hint)
        self.steps: list[TimelineStep] = []
        self.selected = 0

    def show_steps(self, module: str, steps: list[TimelineStep], selected: int = 0) -> None:
        self.steps, self.selected = steps, selected
        while self.row_lay.count():
            w = self.row_lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        n_att = sum(1 for s in steps if s.kind == "attempt")
        self.title.setText(f"{module}：{n_att} 次尝试" if steps else f"{module}：还没有开始检查")
        self.hint.setText("点一次尝试，下面的检查、上板、代码标签换成那一次的内容" if n_att > 1 else "")
        c = T()
        tint = {"pass": ("pass_soft", "pass"), "fail": ("fail_soft", "fail"), "warn": ("run_soft", "run"),
                "running": ("run_soft", "run"), "skipped": ("bg", "ink2"), "info": ("surface", "ink")}
        for i, s in enumerate(steps):
            bg, fg = tint.get(s.status, ("surface", "ink"))
            card = QFrame()
            card.setObjectName("TimelineCard")
            sel = s.kind == "attempt" and s.attempt == selected
            border = f"2px solid {c['accent']}" if sel else f"1px solid {c['line']}"
            card.setStyleSheet(f"#TimelineCard {{ background: {c.c.get(bg, c['surface'])}; border: {border}; border-radius: 12px; }}"
                               f" QLabel {{ background: transparent; color: {c[fg] if fg != 'ink' else c['ink']}; }}")
            v = QVBoxLayout(card)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(2)
            head = QLabel(soft_wrap(s.title))
            head.setWordWrap(True)
            head.setStyleSheet("font-weight: 600; font-size: 13px;")
            v.addWidget(head)
            for ln in s.lines:
                q = QLabel(soft_wrap(ln))
                q.setWordWrap(True)
                q.setStyleSheet("font-size: 12px;")
                v.addWidget(q)
            for q in card.findChildren(QLabel):          # 折行布局按 sizeHint 排，先按固定宽度算好换行后的高度
                q.setMinimumHeight(q.heightForWidth(CARD_W - 22))
            card.setFixedWidth(CARD_W)        # 宽度固定，自动换行的文字才算得对高度
            card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)         # 按内容高度，不拉满面板
            if s.kind == "attempt":
                card.setCursor(Qt.PointingHandCursor)
                card.setToolTip(f"看第 {s.attempt} 次的检查报告、上板测试和代码")
                card.mousePressEvent = lambda ev, n=s.attempt: self.attempt_clicked.emit(n)
            if i:                                         # 箭头和后一步放在一起：折行时箭头在行首，表示接着上一行
                pair = QWidget()
                arrow = QLabel("→")
                arrow.setStyleSheet(f"color: {c['ink3']}; font-size: 16px; background: transparent;")
                pair.setLayout(hbox(arrow, card, spacing=4, margins=(0, 0, 0, 0)))
                pair.layout().setAlignment(arrow, Qt.AlignTop)
                arrow.setContentsMargins(0, 10, 0, 0)
                self.row_lay.addWidget(pair)
            else:
                self.row_lay.addWidget(card)

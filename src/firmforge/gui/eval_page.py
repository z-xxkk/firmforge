"""评测工作区（DECISIONS 第 100 条，界面草图 gui_sketch_board.md 第三节）：左设置、中结果、右运行记录。

结果直接读输出目录里的 runs.jsonl（和 `firmforge board-eval` 写的同一份），所以终端里跑的评测也能在这里看。
每次运行一个小方块：绿通过、红没通过、灰跳过、琥珀正在跑；点方块在对话页打开那次运行。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from PySide6.QtCore import QRectF, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView, QListWidget,
                               QProgressBar, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..embedded.board_eval import CONFIGS, load_rows, missing_scopes, plan_todo
from .runner import EvalBackend
from .widgets import GripSplitter, Panel, T, hbox, label

CONFIG_NOTES = {"single": "一个 Agent，只看编译错误", "none": "多 Agent，上板测试，不给规则",
                "unverified": "再加未核对的规则", "verified": "改用人工确认的规则库"}
LAYER_ZH = {"compliance": "合规", "build": "编译", "board_test": "上板测试", "no_code": "没交代码", "skipped": "跳过"}
PREVIOUS = Path("docs") / "eval_data" / "board_full_20261108" / "runs.jsonl"     # 估算花费用的上一轮数据


class Squares(QWidget):
    """一格里的几次运行：每次一个圆角小方块。"""
    clicked = Signal(str)

    def __init__(self, cells: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.cells = cells                       # [(状态, 运行目录)]
        self.setFixedHeight(22)
        self.setMinimumWidth(len(cells) * 18 + 8)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("点小方块在对话页打开那次运行")

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        c = T()
        for i, (st, _) in enumerate(self.cells):
            col = {"pass": c["accent"], "fail": c["fail"], "running": c["run"], "skipped": c["ink3"]}.get(st, c["line"])
            p.setBrush(QColor(col))
            p.drawRoundedRect(QRectF(4 + i * 18, 5, 13, 13), 4, 4)

    def mousePressEvent(self, ev):
        i = int((ev.position().x() - 4) // 18)
        if 0 <= i < len(self.cells) and self.cells[i][1]:
            self.clicked.emit(self.cells[i][1])


class EvalPage(QWidget):
    open_run = Signal(str)

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.hw = QSettings().value("embedded/hw", "hardware/esp32c3_supermini")
        self.backend: EvalBackend | None = None
        self.running: tuple[str, str, int] | None = None
        self.todo: list = []
        from ..embedded.board_task import discover_board_tasks
        root = project / "tasks_board"
        self.tasks = discover_board_tasks(root) if root.is_dir() else []
        self._build()
        self._load_dirs()
        self.restyle()

    # ------------------------------------------------------------ 布局

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.split = GripSplitter(Qt.Horizontal)
        root.addWidget(self.split)

        left = Panel(margins=(16, 16, 16, 16))
        scroll = QScrollArea()                           # 窗口矮时设置区可以滚动，不把预估挤没
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("ScrollInner")
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 8, 0)
        form.setSpacing(8)
        scroll.setWidget(inner)
        left.lay.addWidget(scroll, 1)
        outer, left.lay = left.lay, form
        left.lay.addWidget(label("上板评测", "H2"))
        left.lay.addWidget(label("题目", "Faint"))
        self.task_boxes: dict[str, QCheckBox] = {}
        default = {"rmt_pulse_meter", "ledc_pwm", "mpu6050_drv_idf", "mpu6050_drdy", "max30102_fifo", "max30102_temp"}
        for t in self.tasks:
            cb = QCheckBox(t.id)
            cb.setChecked(t.id in default)
            cb.setToolTip(t.title)
            cb.toggled.connect(self.update_plan)
            self.task_boxes[t.id] = cb
            left.lay.addWidget(cb)
        left.lay.addWidget(label("配置（每种只比上一种多一样东西）", "Faint"))
        self.cfg_boxes: dict[str, QCheckBox] = {}
        for c in CONFIGS:
            cb = QCheckBox(c)
            cb.setChecked(True)
            cb.setToolTip(CONFIG_NOTES[c])
            cb.toggled.connect(self.update_plan)
            self.cfg_boxes[c] = cb
            note = label(CONFIG_NOTES[c], "Faint", wrap=True)
            note.setMinimumWidth(20)                     # 说明换行，窄窗口下不被截断
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(note, 1)
            left.lay.addLayout(row)
        self.runs = QSpinBox()
        self.runs.setRange(1, 10)
        self.runs.setValue(2)
        self.runs.setSuffix(" 次")
        self.runs.valueChanged.connect(self.update_plan)
        self.max_cost = QDoubleSpinBox()
        self.max_cost.setRange(0, 500)
        self.max_cost.setValue(15)
        self.max_cost.setSuffix(" 元")
        left.lay.addLayout(hbox(label("每格跑"), self.runs, label("上限"), self.max_cost, "stretch"))
        left.lay.addWidget(label("结果放在", "Faint"))
        self.out_combo = QComboBox()
        self.out_combo.setEditable(True)
        self.out_combo.currentTextChanged.connect(lambda _: (self.refresh(), self.update_plan()))
        left.lay.addWidget(self.out_combo)
        self.plan = label("", "", wrap=True)
        left.lay.addWidget(self.plan)
        left.lay.addStretch(1)
        left.lay = outer
        self.start_btn = QPushButton("开始评测")
        self.start_btn.setObjectName("Primary")
        self.start_btn.clicked.connect(self.start)
        left.lay.addWidget(self.start_btn)
        left.setMinimumWidth(300)                         # 再窄"每格跑、上限"那一行就挤不下

        mid = Panel(margins=(18, 16, 18, 14))
        self.res_title = label("", "H2")
        self.res_sub = label("", "Faint")
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)

        self.spent = label("", "Mono")
        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setObjectName("Ghost")
        self.pause_btn.setToolTip("正在跑的一次跑完后停下；再点开始会接着跑")
        self.pause_btn.clicked.connect(self.pause)
        self.pause_btn.setVisible(False)
        top = hbox(self.res_title, self.res_sub, self.bar, self.spent, self.pause_btn)
        top.setStretch(2, 1)
        mid.lay.addLayout(top)
        self.table = QTableWidget(0, len(CONFIGS) + 1)
        self.table.setHorizontalHeaderLabels(["题目", *CONFIGS])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        mid.lay.addWidget(self.table, 3)
        mid.lay.addWidget(label("每个小方块是一次运行：绿色通过，红色没通过，灰色跳过，琥珀色正在跑。点方块在对话页打开那次运行。", "Faint", wrap=True))
        mid.lay.addWidget(label("没通过的卡在哪一层", "Faint"))
        self.layers = QTableWidget(0, 6)
        self.layers.setHorizontalHeaderLabels(["配置", "合规", "编译", "上板测试", "隐藏用例", "每次（元）"])
        self.layers.horizontalHeaderItem(4).setToolTip("隐藏用例通过率")
        self.layers.horizontalHeaderItem(5).setToolTip("平均每次运行的花费")
        self.layers.verticalHeader().setVisible(False)
        self.layers.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.layers.setShowGrid(False)
        self.layers.setEditTriggers(QTableWidget.NoEditTriggers)
        mid.lay.addWidget(self.layers, 2)

        right = Panel(margins=(16, 16, 16, 14))
        right.lay.addWidget(label("运行记录", "H3"))
        self.feed = QListWidget()
        self.feed.setWordWrap(True)
        right.lay.addWidget(self.feed, 1)
        right.lay.addWidget(label("来自实测记录的规则会在 summary.md 里单独列出（第 95 条）。隐藏测试只给评测判定用，任何 Agent 都看不到。",
                                  "Faint", wrap=True))
        right.setMinimumWidth(240)

        self.split.addWidget(left)
        self.split.addWidget(mid)
        self.split.addWidget(right)
        self.split.set_defaults([290, 760, 330])
        st = QSettings()
        if st.value("eval/split"):
            self.split.restoreState(st.value("eval/split"))

    def save_layout(self):
        QSettings().setValue("eval/split", self.split.saveState())

    def restyle(self):
        """跟着深浅色主题换颜色（启动时和切换主题时调用）。"""
        c = T()
        self.bar.setStyleSheet(f"QProgressBar {{ background: {c['line']}; border: none; border-radius: 3px; }}"
                               f"QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}")
        self.plan.setStyleSheet(f"background: {c['bg']}; border-radius: 12px; padding: 10px 12px;")
        self.refresh()

    # ------------------------------------------------------------ 数据

    def _load_dirs(self):
        """列出 results/ 下已有的评测目录；当前选的或自己填的目录保留，不会被换掉
        （换掉的话"再开始接着跑"会跑到别的目录去）。"""
        keep = self.out_combo.currentText().strip() if self.out_combo.count() else ""
        res = self.project / "results"
        dirs = sorted((p for p in res.iterdir() if (p / "runs.jsonl").is_file()), key=lambda p: p.stat().st_mtime,
                      reverse=True) if res.is_dir() else []
        new = f"results/board_{date.today():%Y%m%d}"
        items = [p.relative_to(self.project).as_posix() for p in dirs]
        for x in (new, keep):
            if x and x not in items:
                items.insert(0, x)
        self.out_combo.blockSignals(True)
        self.out_combo.clear()
        self.out_combo.addItems(items)
        self.out_combo.setCurrentText(keep or items[0])
        self.out_combo.blockSignals(False)

    @property
    def out_dir(self) -> Path:
        return self.project / self.out_combo.currentText().strip()

    def selected(self) -> tuple[list, list[str]]:
        tasks = [t for t in self.tasks if self.task_boxes[t.id].isChecked()]
        return tasks, [c for c in CONFIGS if self.cfg_boxes[c].isChecked()]

    def update_plan(self, *_):
        tasks, cfgs = self.selected()
        rows = load_rows(self.out_dir)
        todo = plan_todo(tasks, cfgs, self.runs.value(), rows)
        total = len(tasks) * len(cfgs) * self.runs.value()
        avg = self._avg_cost()
        est = sum(avg.get(c, 0.3) for _, c, _ in todo)
        lines = [f"{len(tasks)} 题 × {len(cfgs)} 种配置 × {self.runs.value()} 次 = <b>{total}</b> 次"]
        if len(todo) != total:
            lines.append(f"这个目录里已经跑完 {total - len(todo)} 次，还要跑 {len(todo)} 次")
        lines.append(f"按上一轮估计约 {est:.0f} 元，约 {len(todo) * 5 / 60:.1f} 小时")
        if "verified" in cfgs:
            try:
                from ..grounding.rules import RuleSet
                store = RuleSet.load(self.project / self.hw / "rules.json")
                miss = {t.id: missing_scopes(store, t.scopes) for t in tasks}
                miss = {k: v for k, v in miss.items() if v}
                lines.append("verified 规则库已覆盖全部题目" if not miss else
                             "verified 会跳过：" + "；".join(f"{k} 缺 {'、'.join(v)}" for k, v in miss.items()))
            except Exception as exc:                        # noqa: BLE001
                lines.append(f"读不到规则库：{exc}")
        self.plan.setText("<br>".join(lines))

    def _avg_cost(self) -> dict[str, float]:
        p = self.project / PREVIOUS
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.is_file() else []
        out = {}
        for c in CONFIGS:
            xs = [float(r["cost"]) for r in rows if r["config"] == c]
            if xs:
                out[c] = sum(xs) / len(xs)
        return out

    def refresh(self):
        rows = load_rows(self.out_dir)
        tasks, cfgs = self.selected()
        n_runs = max([self.runs.value()] + [int(r["run"]) for r in rows])
        shown = [t.id for t in tasks] or sorted({r["task"] for r in rows})
        for r in rows:
            if r["task"] not in shown:
                shown.append(r["task"])
        self.table.setRowCount(0)                        # 先清空，旧的小方块部件不会残留
        self.table.setRowCount(len(shown) + 1)
        by = {(r["task"], r["config"], int(r["run"])): r for r in rows}
        for i, tid in enumerate(shown):
            self.table.setItem(i, 0, QTableWidgetItem(tid))
            for j, c in enumerate(CONFIGS, 1):
                cells = []
                for n in range(1, n_runs + 1):
                    r = by.get((tid, c, n))
                    if self.running == (tid, c, n):
                        cells.append(("running", ""))
                    elif r is None:
                        cells.append(("", ""))
                    else:
                        cells.append((r["verdict"], r.get("run_dir", "")))
                sq = Squares(cells)
                sq.clicked.connect(lambda d: self.open_run.emit(str(self.project / d) if not Path(d).is_absolute() else d))
                self.table.setCellWidget(i, j, sq)
        last = len(shown)
        self.table.setItem(last, 0, QTableWidgetItem("通过"))
        for j, c in enumerate(CONFIGS, 1):
            rs = [r for r in rows if r["config"] == c and r["verdict"] != "skipped"]
            self.table.setItem(last, j, QTableWidgetItem(f"{sum(r['verdict'] == 'pass' for r in rs)} / {len(rs)}" if rs else ""))
        self.table.resizeRowsToContents()
        self.layers.setRowCount(len(CONFIGS))
        for i, c in enumerate(CONFIGS):
            rs = [r for r in rows if r["config"] == c and r["verdict"] != "skipped"]
            fails = [r for r in rs if r["verdict"] != "pass"]
            cnt = lambda k: str(sum(1 for r in fails if r["failed_layer"] == k))       # noqa: E731
            tot = sum(int(r.get("cases_total") or 0) for r in rs)
            rate = f"{100 * sum(int(r.get('cases_passed') or 0) for r in rs) / tot:.0f}%" if tot else ""
            avg = f"{sum(float(r['cost']) for r in rs) / len(rs):.3f}" if rs else ""
            for j, v in enumerate([c, cnt("compliance"), cnt("build"), cnt("board_test"), rate, avg]):
                self.layers.setItem(i, j, QTableWidgetItem(v if rs or j == 0 else ""))
        self.res_title.setText(self.out_combo.currentText().split("/")[-1])
        done = len([r for r in rows if r["verdict"] != "skipped"])
        total = len(shown) * len(cfgs) * self.runs.value()
        self.res_sub.setText(f"{done} / {total}")
        self.bar.setRange(0, max(total, 1))
        self.bar.setValue(min(done, total))
        self.spent.setText(f"{sum(float(r['cost']) for r in rows):.2f} 元")
        self.update_plan()

    # ------------------------------------------------------------ 运行

    def start(self):
        if self.backend is not None:
            return
        tasks, cfgs = self.selected()
        if not tasks or not cfgs:
            self.feed.addItem("先选至少一道题和一种配置。")
            return
        self.backend = EvalBackend(self)
        self.backend.progress.connect(self._on_progress)
        self.backend.finished.connect(self._done)
        out = self.out_dir
        try:
            self.backend.start(project=self.project, hw=self.hw, task_ids=[t.id for t in tasks], configs=cfgs,
                               runs=self.runs.value(), out_dir=out, max_cost=self.max_cost.value())
        except Exception as exc:
            self.feed.addItem(f"没能开始：{exc}")
            self.backend = None
            return
        self.todo = plan_todo(tasks, cfgs, self.runs.value(), load_rows(out))
        self.running = (self.todo[0][0].id, self.todo[0][1], self.todo[0][2]) if self.todo else None
        self.start_btn.setEnabled(False)
        self.pause_btn.setVisible(True)
        self.pause_btn.setEnabled(True)
        self.refresh()

    def pause(self):
        if self.backend is not None:
            self.backend.pause()
            self.pause_btn.setEnabled(False)
            self.feed.addItem("正在跑的一次跑完后停下。")

    def _on_progress(self, line: str):
        self.feed.addItem(line)
        self.feed.scrollToBottom()
        if line.startswith("[") and self.todo:
            self.todo.pop(0)
            self.running = (self.todo[0][0].id, self.todo[0][1], self.todo[0][2]) if self.todo else None
            self.refresh()

    def _done(self, info: dict):
        self.backend = None
        self.running = None
        self.start_btn.setEnabled(True)
        self.pause_btn.setVisible(False)
        if info.get("error"):
            self.feed.addItem(f"出错：{info['error']}")
        else:
            self.feed.addItem(f"结束。汇总：{Path(info['run_dir']) / 'summary.md'}")
        self._load_dirs()
        self.refresh()

    def showEvent(self, ev):
        super().showEvent(ev)
        if self.backend is None:
            self.refresh()                           # 终端里跑的评测也会写 runs.jsonl，回到本页时重新读

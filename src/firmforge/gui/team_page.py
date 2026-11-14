"""Agent 团队工作区（界面草图第五节，对应 DeepSeek Harness / Hermes 的"Agent 预设"）。

每个 Agent 一行：角色、版本、档位、思考强度、工具、记忆、历史表现。右边改设置，保存成新版本（旧版本存进 agents/history/）。
这里改的就是 agents/*.toml，DECISIONS 第 64 条的技能、工具集、记忆字段都在这里勾选。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QInputDialog, QMessageBox,
                               QPlainTextEdit, QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import config
from ..agents.registry import ROLES, AgentDef, Registry, save_agent
from .designer_page import bump
from .graph import ROLE_NAMES                          # 全部角色的中文名（原来这里少了设计分层、查资料、核对规则）
from .widgets import GripSplitter, Panel, Segmented, label

THINK = [("", "跟随档位"), ("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高")]
TOOLSET_NAMES = {"knowledge": "查知识库", "build": "试编译", "interface": "看接口", "sources": "查手册原文"}


def task_type_name(t: str) -> str:
    """调度器记成绩用的任务类型 → 中文。上板流程是 fw-<层>-<first|repair>（DECISIONS 第 110 条）。"""
    if t.startswith("fw-"):
        _, layer, when = (t.split("-") + ["", ""])[:3]
        return f"上板·{'硬件层' if layer == 'hardware' else '逻辑层'}·{'第一次写' if when == 'first' else '修改'}"
    return f"纯逻辑·{t}"


def _rate(k, n) -> str:
    return f"{k or 0}/{n}（{100 * (k or 0) // n}%）" if n else "—"


def perf_summary(rows: list[dict], current: str) -> str:
    """表格里那一格：当前版本的成绩；只有旧版本的成绩时写明是旧版本的。"""
    if not rows:
        return "还没有记录"
    cur = [r for r in rows if r["version"] == current]
    use = cur or rows
    n, ok = sum(r["attempts"] or 0 for r in use), sum(r["successes"] or 0 for r in use)
    where = "" if cur else f"（都是旧版本 {'、'.join(sorted({r['version'] for r in rows}))} 的）"
    return f"{n} 次，通过 {ok}，{len({r['task_type'] for r in use})} 类任务{where}"


class TeamPage(QWidget):
    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.split = GripSplitter(Qt.Horizontal)
        lay.addWidget(self.split)
        left = Panel(margins=(18, 14, 18, 14))
        head = QHBoxLayout()
        head.addWidget(label("Agent 团队", "H1"))
        head.addStretch(1)
        add = QPushButton("注册新 Agent")
        add.clicked.connect(self.add_agent)
        head.addWidget(add)
        left.lay.addLayout(head)
        left.lay.addWidget(label("改的就是 agents/*.toml。保存时版本号加一，旧版本留在 agents/history/。", "Muted"))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["名称", "角色", "版本", "档位", "思考", "工具", "历史表现"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selected)
        left.lay.addWidget(self.table, 1)
        right = Panel(margins=(18, 14, 18, 14))
        self.title = label("选一个 Agent", "H2")
        right.lay.addWidget(self.title)
        self.desc = label("", "Muted", wrap=True)
        right.lay.addWidget(self.desc)
        form_w = QWidget()
        self.form = QFormLayout(form_w)
        self.tier = QComboBox()
        self.think = Segmented(THINK)
        self.tools_box = QVBoxLayout()
        tools_w = QWidget()
        tools_w.setLayout(self.tools_box)
        self.mem_exp = QCheckBox("经验库")
        self.mem_kb = QCheckBox("知识库")
        mem = QHBoxLayout()
        mem.addWidget(self.mem_exp)
        mem.addWidget(self.mem_kb)
        mem.addStretch(1)
        mem_w = QWidget()
        mem_w.setLayout(mem)
        self.skills = QPlainTextEdit()
        self.skills.setObjectName("Field")
        self.skills.setPlaceholderText("一行一条，例如：按需求写嵌入式 C 模块")
        self.skills.setFixedHeight(90)
        self.prompt_btn = QPushButton("打开提示词文件")
        self.prompt_btn.clicked.connect(self._open_prompt)
        self.form.addRow("模型档位", self.tier)
        self.form.addRow("思考强度", self.think)
        self.form.addRow("能用的工具", tools_w)
        self.form.addRow("记忆", mem_w)
        self.form.addRow("技能", self.skills)
        self.prompt_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.form.addRow("提示词", self.prompt_btn)
        right.lay.addWidget(form_w)
        save = QPushButton("保存为新版本")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        right.lay.addWidget(save, 0, Qt.AlignLeft)
        right.lay.addSpacing(10)
        right.lay.addWidget(label("历史表现（调度器按任务类型分别打分）", "H3"))
        self.perf = QTableWidget(0, 6)
        self.perf.setHorizontalHeaderLabels(["任务类型", "版本", "次数", "一次通过", "最终通过", "平均花费"])
        self.perf.verticalHeader().setVisible(False)
        self.perf.setEditTriggers(QTableWidget.NoEditTriggers)
        self.perf.setSelectionMode(QTableWidget.NoSelection)
        self.perf.horizontalHeader().setStretchLastSection(True)
        right.lay.addWidget(self.perf, 1)
        self.perf_note = label("", "Faint", wrap=True)
        right.lay.addWidget(self.perf_note)
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.set_defaults([760, 460])
        self.tool_checks: dict[str, QCheckBox] = {}
        self.reload()

    def reload(self):
        self.reg = Registry().load_dir(config.agents_dir())
        settings = config.load_settings()
        self.tier.clear()
        for name, t in settings.models.items():
            self.tier.addItem(f"{name}（{t.name}）", name)
        self.stats_rows: dict[str, list[dict]] = {}       # Agent → 每个(版本, 任务类型)一行
        try:
            from ..store import Store
            db = self.project / ".ff_data" / "firmforge.db"
            if db.is_file():
                store = Store(str(db))
                for r in store.query("SELECT * FROM agent_stats ORDER BY agent, task_type, version"):
                    self.stats_rows.setdefault(r["agent"], []).append(r)
                store.close()
        except Exception:                                  # noqa: BLE001  成绩读不了不影响改设置
            pass
        names = self.reg.names()
        self.table.setRowCount(len(names))
        for r, n in enumerate(names):
            d = self.reg.get(n)
            perf = perf_summary(self.stats_rows.get(n, []), d.version)
            row = [n, ROLE_NAMES.get(d.role, d.role), "、".join(self.reg.versions(n)), d.model_tier,
                   dict(THINK).get(d.thinking or "", "跟随档位"), "、".join(d.toolsets) or "无", perf]
            for c, v in enumerate(row):
                self.table.setItem(r, c, QTableWidgetItem(v))
        self.table.resizeColumnsToContents()
        while self.tools_box.count():
            w = self.tools_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.tool_checks.clear()
        from ..tools import BUILTIN_TOOLSETS
        sets = [(t, f"{t}：{TOOLSET_NAMES.get(t, t)}") for t in BUILTIN_TOOLSETS]
        sets += [(f"mcp-{n}", f"mcp-{n}：外部 MCP 服务") for n in settings.mcp_servers]
        for key, text in sets:
            cb = QCheckBox(text)
            self.tools_box.addWidget(cb)
            self.tool_checks[key] = cb
        self._shown = None                                 # 重新读了配置：表单从头填，不算"没保存的改动"
        if names:
            self.table.selectRow(0)

    def current(self) -> AgentDef | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.reg.get(self.table.item(rows[0].row(), 0).text())

    def _form_def(self, d: AgentDef) -> AgentDef:
        """表单上现在的设置（版本号不变）。"""
        return dataclasses.replace(
            d, model_tier=self.tier.currentData(), thinking=self.think.value() or None,
            # 勾选框里没有的工具（例如手写进 toml 的单个工具名）原样保留，不能因为界面上没列出就丢掉
            toolsets=tuple(k for k, cb in self.tool_checks.items() if cb.isChecked())
            + tuple(t for t in d.toolsets if t not in self.tool_checks),
            memory_experience=self.mem_exp.isChecked(), memory_knowledge=self.mem_kb.isChecked(),
            skills=tuple(x.strip() for x in self.skills.toPlainText().splitlines() if x.strip()))

    def dirty(self) -> bool:
        d = getattr(self, "_shown", None)
        return d is not None and self._form_def(d) != d

    def _selected(self):
        d = self.current()
        if d is None:
            return
        prev = getattr(self, "_shown", None)
        if prev is not None and prev.name != d.name and self.dirty():
            ans = QMessageBox.question(self, "还没保存", f"{prev.name} 的设置改了还没保存。保存成新版本吗？",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if ans == QMessageBox.Cancel:                  # 回到原来那一行，改动留着
                self._select_name(prev.name)
                return
            if ans == QMessageBox.Save:
                self._save_def(prev)
                self._select_name(d.name)
                return
        self._shown = d
        self.title.setText(f"{d.name}（{ROLE_NAMES.get(d.role, d.role)}）　版本 {d.version}")
        self.desc.setText(d.description)
        self.tier.setCurrentIndex(max(0, self.tier.findData(d.model_tier)))
        self.think.set_value(d.thinking or "")
        for key, cb in self.tool_checks.items():
            cb.setChecked(key in d.toolsets)
        self.mem_exp.setChecked(d.memory_experience)
        self.mem_kb.setChecked(d.memory_knowledge)
        self.skills.setPlainText("\n".join(d.skills))
        self._fill_perf(d)

    def _select_name(self, name: str) -> None:
        for r in range(self.table.rowCount()):
            if self.table.item(r, 0).text() == name:
                self.table.blockSignals(True)
                self.table.selectRow(r)
                self.table.blockSignals(False)
                return

    def _fill_perf(self, d: AgentDef) -> None:
        rows = self.stats_rows.get(d.name, [])
        self.perf.setRowCount(len(rows))
        for i, r in enumerate(rows):
            n = r["attempts"] or 0
            cells = [task_type_name(r["task_type"]), r["version"] + ("（当前）" if r["version"] == d.version else ""),
                     str(n), _rate(r["first_pass"], n), _rate(r["successes"], n),
                     f"{(r['total_cost'] or 0) / n:.3f} 元" if n else "—"]
            for c, v in enumerate(cells):
                self.perf.setItem(i, c, QTableWidgetItem(v))
        self.perf.resizeColumnsToContents()
        self.perf.setVisible(bool(rows))
        self.perf_note.setText("还没有记录：这个 Agent 被调度器选中、跑完检查以后才有。" if not rows else
                               "调度器挑 Agent 时看的就是这些：同一类任务里成功率高、花费低的优先，另外留一点机会给记录少的。")

    def save(self):
        d = self.current()
        if d is None:
            return
        if not self._save_def(d):
            QMessageBox.information(self, "没有改动", "设置和当前版本一样。")

    def _save_def(self, d: AgentDef) -> bool:
        """表单上的设置存成 d 的新版本；没改动返回 False。"""
        new = self._form_def(d)
        if new == d:
            return False
        save_agent(dataclasses.replace(new, version=bump(d.version)), config.agents_dir())
        self._shown = None
        keep = d.name
        self.reload()
        self._select_name(keep)
        self._selected()
        return True

    def add_agent(self):
        name, ok = QInputDialog.getText(self, "注册新 Agent", "名字（字母、数字、横线）：")
        if not ok or not name.strip():
            return
        role, ok = QInputDialog.getItem(self, "注册新 Agent", "角色：", [f"{r} {ROLE_NAMES[r]}" for r in ROLES], 1, False)
        if not ok:
            return
        role = role.split()[0]
        base = self.reg.find(role)
        if not base:
            QMessageBox.warning(self, "没有模板", f"还没有角色为 {role} 的 Agent 可以照抄")
            return
        new = dataclasses.replace(base[0], name=name.strip(), version="1.0.0", description=f"照 {base[0].name} 新建")
        if (config.agents_dir() / f"{new.name}.toml").exists():
            QMessageBox.warning(self, "重名", f"已经有 {new.name}")
            return
        save_agent(new, config.agents_dir())
        self.reload()

    def _open_prompt(self):
        d = self.current()
        if d is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.project_root() / d.prompt)))

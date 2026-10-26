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
from .widgets import GripSplitter, Panel, Segmented, label

THINK = [("", "跟随档位"), ("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高")]
ROLE_NAMES = {"planner": "规划", "coder": "编码", "tester": "测试", "reviewer": "审查", "diagnoser": "诊断",
              "analyst": "需求分析"}


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
        right.lay.addStretch(1)
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
        stats = {}
        try:
            from ..store import Store
            db = self.project / ".ff_data" / "firmforge.db"
            if db.is_file():
                store = Store(str(db))
                for r in store.query("SELECT agent, SUM(attempts) a, SUM(successes) s, SUM(total_cost) c "
                                     "FROM agent_stats GROUP BY agent"):
                    stats[r["agent"]] = r
                store.close()
        except Exception:
            pass
        names = self.reg.names()
        self.table.setRowCount(len(names))
        for r, n in enumerate(names):
            d = self.reg.get(n)
            st = stats.get(n)
            perf = f"{st['a']} 次，通过 {st['s']}，{(st['c'] or 0):.2f} 元" if st and st["a"] else "还没有记录"
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
        sets = [("knowledge", "knowledge：查知识库"), ("build", "build：试编译"), ("interface", "interface：看接口")]
        sets += [(f"mcp-{n}", f"mcp-{n}：外部 MCP 服务") for n in settings.mcp_servers]
        for key, text in sets:
            cb = QCheckBox(text)
            self.tools_box.addWidget(cb)
            self.tool_checks[key] = cb
        if names:
            self.table.selectRow(0)

    def current(self) -> AgentDef | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.reg.get(self.table.item(rows[0].row(), 0).text())

    def _selected(self):
        d = self.current()
        if d is None:
            return
        self.title.setText(f"{d.name}（{ROLE_NAMES.get(d.role, d.role)}）　版本 {d.version}")
        self.desc.setText(d.description)
        self.tier.setCurrentIndex(max(0, self.tier.findData(d.model_tier)))
        self.think.set_value(d.thinking or "")
        for key, cb in self.tool_checks.items():
            cb.setChecked(key in d.toolsets)
        self.mem_exp.setChecked(d.memory_experience)
        self.mem_kb.setChecked(d.memory_knowledge)
        self.skills.setPlainText("\n".join(d.skills))

    def save(self):
        d = self.current()
        if d is None:
            return
        new = dataclasses.replace(
            d, version=bump(d.version), model_tier=self.tier.currentData(), thinking=self.think.value() or None,
            toolsets=tuple(k for k, cb in self.tool_checks.items() if cb.isChecked()),
            memory_experience=self.mem_exp.isChecked(), memory_knowledge=self.mem_kb.isChecked(),
            skills=tuple(x.strip() for x in self.skills.toPlainText().splitlines() if x.strip()))
        if new == dataclasses.replace(d, version=new.version):
            QMessageBox.information(self, "没有改动", "设置和当前版本一样。")
            return
        save_agent(new, config.agents_dir())
        self.reload()

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

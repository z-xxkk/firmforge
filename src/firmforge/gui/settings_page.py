"""设置（界面草图第五节）：通用、模型、工具与 MCP、记忆与知识库、人工与权限、预算、高级。

借鉴 DeepSeek Harness：这里改的就是 firmforge.toml 和 .env（右上角"打开 firmforge.toml"）；密钥只写不读，保存后只显示"已保存"。
新的外部 MCP 服务第一次启用要确认（借鉴 Claude Code）。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import config
from ..config_edit import has_secret, remove_table, set_secret, update_file
from .runner import DockerBackend, _Worker
from .panes import PaneSplitter, PaneToggle
from .widgets import Panel, Segmented, Toggle, hbox, label

NAV = [("通用", "语言、主题、默认后端"), ("模型", "密钥、型号、思考强度、价格"), ("工具与 MCP", "Agent 能用的工具、外部服务"),
       ("记忆与知识库", "经验库、知识库文档"), ("人工与权限", "什么时候问你"), ("预算", "token 和费用上限"),
       ("高级", "并发、超时、重试")]
THINK = [("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高")]


def section(title: str, lead: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 14, 0, 14)
    lay.addWidget(label(title, "H3"))
    if lead:
        lay.addWidget(label(lead, "Muted", wrap=True))
    return w, lay


class SettingsPage(QWidget):
    theme_changed = Signal()

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.toml = config.settings_path()
        self.env = project / ".env"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # 左边的分类是侧栏，右边是设置内容（DECISIONS 第 133 条）
        split = self.split = PaneSplitter("settings")
        lay.addWidget(split)
        nav = Panel(margins=(12, 18, 12, 12))
        nav.lay.addWidget(label("设置", "H1"))
        self.nav = QListWidget()
        for name, desc in NAV:
            self.nav.addItem(f"{name}\n{desc}")
        self.nav.currentRowChanged.connect(lambda i: self.stack.setCurrentIndex(i))
        nav.lay.addWidget(self.nav, 1)
        main = Panel(margins=(32, 22, 32, 16))
        self.stack = QStackedWidget()
        for build in (self._general, self._models, self._tools, self._memory, self._human, self._budget, self._advanced):
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 8, 0)
            build(pl)
            pl.addStretch(1)
            page.setObjectName("ScrollInner")
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        main.lay.addWidget(self.stack)
        split.add_pane(nav, "sidebar", minimum=180, default=240)
        split.add_pane(main, "main", minimum=480, default=1000)
        split.finish()
        self.nav.setCurrentRow(0)

    def toggle_pane(self, role: str) -> None:
        self.split.toggle(role)

    def _head(self, pl, title: str, lead: str):
        top = QHBoxLayout()
        top.addWidget(PaneToggle(self.split, "sidebar", "设置分类", "Ctrl+B"))     # 每个分类的标题左边都有（第 133 条）
        top.addWidget(label(title, "H1"))
        top.addStretch(1)
        b = QPushButton("打开 firmforge.toml")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.toml))))
        top.addWidget(b)
        pl.addLayout(top)
        pl.addWidget(label(lead, "Muted", wrap=True))

    def _save(self, changes):
        try:
            update_file(self.toml, changes)
        except Exception as exc:
            QMessageBox.warning(self, "没有保存", f"写 firmforge.toml 时出错：{exc}")
            return False
        return True

    # ------------------------------------------------------------ 通用

    def _general(self, pl):
        self._head(pl, "通用", "这一页的设置保存在本机（不影响命令行）。")
        st = QSettings()
        form = QFormLayout()
        self.backend = Segmented([("docker", "docker"), ("local", "本机")], st.value("general/backend", "docker"))
        self.backend.changed.connect(lambda v: QSettings().setValue("general/backend", v))
        note = "已检测到 Docker" if DockerBackend.available() else "没检测到 Docker：请先安装并启动 Docker Desktop，或者用\"本机\"（需要本机装好编译工具链）"
        self.theme = Segmented([("system", "跟随系统"), ("light", "浅色"), ("dark", "深色")], st.value("general/theme", "system"))
        self.theme.changed.connect(self._theme)
        form.addRow("运行后端", self.backend)
        form.addRow("", label(note, "Faint", wrap=True))
        form.addRow("主题", self.theme)
        form.addRow("运行目录", label(str(self.project / ".ff_runs"), "Mono"))
        form.addRow("对话记录", label(str(self.project / ".ff_chats"), "Mono"))
        pl.addLayout(form)

    def _theme(self, v):
        QSettings().setValue("general/theme", v)
        self.theme_changed.emit()

    # ------------------------------------------------------------ 模型

    def _models(self, pl):
        self._head(pl, "模型", "这里改的就是 firmforge.toml 和 .env，手改文件这里也会跟着变。密钥只写不读，保存后只显示\"已保存\"。")
        s = config.load_settings()
        w, lay = section("DeepSeek")
        form = QFormLayout()
        self.key = QLineEdit()
        self.key.setMinimumWidth(360)
        self.key.setEchoMode(QLineEdit.Password)
        saved = has_secret(self.env, s.llm.api_key_env)
        self.key.setPlaceholderText("已保存，留空则不修改" if saved else "还没有填写")
        self.key_state = label("已保存在 .env" if saved else "还没有密钥", "Faint")
        import os
        self.base = QLineEdit(os.environ.get(s.llm.base_url_env, "") or config.DEFAULT_BASE_URL)
        self.base.setMinimumWidth(360)
        test = QPushButton("测试连接并列出型号")
        test.clicked.connect(self._test_models)
        form.addRow("API 密钥", self._row(self.key, self.key_state))
        form.addRow("接口地址", self._row(self.base, test))
        lay.addLayout(form)
        pl.addWidget(w)
        w, lay = section("档位", "思考强度对应 DeepSeek 接口的 thinking 和 reasoning_effort（low、high、max），默认\"高\"。"
                         "每个 Agent 用哪一档在\"团队\"里设；对话框下方的选择只影响那一次运行。")
        self.tiers = {}
        tbl = QTableWidget(len(s.models), 7)
        tbl.setHorizontalHeaderLabels(["档位", "型号", "思考强度", "单次输出上限", "高峰入/出（元/百万 token）", "空闲入/出", ""])
        tbl.verticalHeader().setVisible(False)
        for r, (name, t) in enumerate(s.models.items()):
            tbl.setItem(r, 0, QTableWidgetItem(name))
            model = QLineEdit(t.name)
            think = Segmented(THINK, "off" if t.thinking is False else (t.reasoning_effort or "high"))
            mx = QSpinBox()
            mx.setRange(1024, 400000)
            mx.setValue(t.max_tokens or 8192)
            prices = [QDoubleSpinBox() for _ in range(4)]
            vals = [t.price_in_per_mtok, t.price_out_per_mtok, t.price_in_offpeak_per_mtok or 0, t.price_out_offpeak_per_mtok or 0]
            for sp, v in zip(prices, vals):
                sp.setRange(0, 1000)
                sp.setDecimals(2)
                sp.setValue(v)
            tbl.setCellWidget(r, 1, model)
            tbl.setCellWidget(r, 2, think)
            tbl.setCellWidget(r, 3, mx)
            tbl.setCellWidget(r, 4, self._row(prices[0], prices[1]))
            tbl.setCellWidget(r, 5, self._row(prices[2], prices[3]))
            self.tiers[name] = (model, think, mx, prices)
        tbl.resizeColumnsToContents()
        tbl.setMinimumHeight(60 + 44 * len(s.models))
        lay.addWidget(tbl)
        pl.addWidget(w)
        w, lay = section("计价时段")
        self.peak = QLineEdit(", ".join(f"{a // 60:02d}:{a % 60:02d}-{b // 60:02d}:{b % 60:02d}" for a, b in s.pricing.windows)
                              if s.pricing else "")
        self.peak.setMinimumWidth(320)
        lay.addWidget(self._row(label("工作日高峰时段（北京时间）"), self.peak))
        pl.addWidget(w)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_models)
        pl.addWidget(save, 0, Qt.AlignLeft)

    def _row(self, *ws):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for x in ws:
            lay.addWidget(x)
        lay.addStretch(1)
        return w

    def _save_models(self):
        s = config.load_settings()
        if self.key.text().strip():
            set_secret(self.env, s.llm.api_key_env, self.key.text().strip())
            self.key.clear()
            self.key.setPlaceholderText("已保存，留空则不修改")
            self.key_state.setText("已保存在 .env")
        if self.base.text().strip() and self.base.text().strip() != config.DEFAULT_BASE_URL:
            set_secret(self.env, s.llm.base_url_env, self.base.text().strip())
        changes = []
        for name, (model, think, mx, prices) in self.tiers.items():
            v = think.value()
            t = f"models.{name}"
            changes += [(t, "name", model.text().strip()), (t, "thinking", v != "off"), (t, "max_tokens", mx.value()),
                        (t, "price_in_per_mtok", prices[0].value()), (t, "price_out_per_mtok", prices[1].value()),
                        (t, "price_in_offpeak_per_mtok", prices[2].value()), (t, "price_out_offpeak_per_mtok", prices[3].value())]
            if v != "off":
                changes.append((t, "reasoning_effort", v))
        peaks = [x.strip() for x in self.peak.text().replace("，", ",").split(",") if x.strip()]
        if peaks:
            changes.append(("pricing", "peak_hours", peaks))
        if self._save(changes):
            QMessageBox.information(self, "已保存", "已写入 firmforge.toml（密钥写入 .env）。")

    def _test_models(self):
        from ..llm import OpenAICompatClient
        try:
            client = OpenAICompatClient.from_settings(config.load_settings())
        except Exception as exc:
            QMessageBox.warning(self, "连不上", str(exc))
            return
        w = self._mw = _Worker(client.list_models)
        # 用结束的那一个 worker（连点两次时 self._mw 已经换成新的）；被中途停掉时没有结果
        w.finished.connect(lambda: QMessageBox.information(
            self, "测试连接", w.error or ("可用型号：\n" + "\n".join(w.result) if w.result else "没有拿到结果（被中途停掉了）")))
        w.start()

    # ------------------------------------------------------------ 工具与 MCP

    def _tools(self, pl):
        self._head(pl, "工具与 MCP", "工具按工具集分组。哪个 Agent 能用哪些工具集，在\"团队\"里勾选。")
        s = config.load_settings()
        from ..tools.builtin import BUILTIN_TOOLS
        w, lay = section("内置工具")
        disabled = set(s.tools.get("disabled", []))
        tbl = QTableWidget(len(BUILTIN_TOOLS), 4)
        tbl.setHorizontalHeaderLabels(["工具集", "工具", "做什么", "启用"])
        tbl.verticalHeader().setVisible(False)
        self.tool_toggles = {}
        for r, t in enumerate(BUILTIN_TOOLS):
            for c, v in enumerate([t.toolset, t.name, t.description[:60]]):
                tbl.setItem(r, c, QTableWidgetItem(v))
            tg = Toggle(t.name not in disabled)
            tg.toggled.connect(self._save_tool_toggles)
            tbl.setCellWidget(r, 3, tg)
            self.tool_toggles[t.name] = tg
        tbl.resizeColumnsToContents()
        tbl.setMinimumHeight(170)
        lay.addWidget(tbl)
        pl.addWidget(w)
        w, lay = section("外部 MCP 服务", "让 Agent 用别人的工具。新服务第一次启用要你确认；评测时外部工具一律关闭。")
        self.mcp_table = QTableWidget(0, 6)
        self.mcp_table.setHorizontalHeaderLabels(["名称", "启动方式", "状态", "工具", "调用前问我", "启用"])
        self.mcp_table.verticalHeader().setVisible(False)
        self.mcp_table.setMinimumHeight(140)
        lay.addWidget(self.mcp_table)
        add, rm, test = QPushButton("添加 MCP 服务"), QPushButton("删除选中的"), QPushButton("测试选中的")
        add.clicked.connect(self._add_mcp)
        rm.clicked.connect(self._remove_mcp)
        test.clicked.connect(self._test_mcp)
        lay.addLayout(hbox(add, rm, test, "stretch"))
        pl.addWidget(w)
        self._fill_mcp()
        w, lay = section("让别的 AI 软件调用 FirmForge", "把下面这段加进 Claude Code、Cursor 等软件的 MCP 配置，"
                         "它们就能用 FirmForge 的判定器检查 C 代码（不会跑隐藏测试）。")
        snippet = json.dumps({"mcpServers": {"firmforge": {"command": "firmforge", "args": ["mcp"]}}}, indent=2)
        box = QPlainTextEdit(snippet)
        box.setObjectName("Code")
        box.setReadOnly(True)
        box.setFixedHeight(110)
        lay.addWidget(box)
        copy, check = QPushButton("复制"), QPushButton("自检")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(snippet))
        check.clicked.connect(self._selftest)
        lay.addLayout(hbox(copy, check, "stretch"))
        pl.addWidget(w)

    def _save_tool_toggles(self):
        self._save([("tools", "disabled", [n for n, t in self.tool_toggles.items() if not t.isChecked()])])

    def _fill_mcp(self):
        s = config.load_settings()
        self.mcp_table.setRowCount(len(s.mcp_servers))
        for r, (name, d) in enumerate(s.mcp_servers.items()):
            for c, v in enumerate([name, " ".join([d.get("command", ""), *d.get("args", [])]),
                                   "未启用" if not d.get("enabled") else "已启用", ""]):
                self.mcp_table.setItem(r, c, QTableWidgetItem(v))
            ask = Toggle(bool(d.get("ask", True)))
            ask.toggled.connect(lambda on, n=name: self._save([(f"mcp.servers.{n}", "ask", on)]))
            en = Toggle(bool(d.get("enabled", False)))
            en.toggled.connect(lambda on, n=name, t=en: self._enable_mcp(n, on, t))
            self.mcp_table.setCellWidget(r, 4, ask)
            self.mcp_table.setCellWidget(r, 5, en)
        self.mcp_table.resizeColumnsToContents()

    def _enable_mcp(self, name, on, toggle):
        if on:
            approved = set(QSettings().value("mcp/approved", [], list) or [])
            if name not in approved:
                d = config.load_settings().mcp_servers.get(name, {})
                cmd = " ".join([d.get("command", ""), *d.get("args", [])])
                ok = QMessageBox.question(self, "第一次启用外部服务",
                                          f"启用后，运行时会在本机执行：\n\n{cmd}\n\n它提供的工具只有在\"团队\"里勾选后 Agent 才能用，"
                                          "并且默认每次调用前都会问你。确定启用吗？")
                if ok != QMessageBox.Yes:
                    toggle.blockSignals(True)
                    toggle.setChecked(False)
                    toggle.blockSignals(False)
                    return
                QSettings().setValue("mcp/approved", sorted(approved | {name}))
        self._save([(f"mcp.servers.{name}", "enabled", on)])
        self._fill_mcp()

    def _add_mcp(self):
        name, ok = QInputDialog.getText(self, "添加 MCP 服务", "名称（字母、数字、下划线、横线）：")
        if not ok or not name.strip():
            return
        cmd, ok = QInputDialog.getText(self, "添加 MCP 服务", "启动命令和参数，例如 uvx mcp-server-fetch：")
        if not ok or not cmd.strip():
            return
        parts = cmd.split()
        t = f"mcp.servers.{name.strip()}"
        if self._save([(t, "command", parts[0]), (t, "args", parts[1:]), (t, "enabled", False), (t, "ask", True),
                       (t, "timeout_s", 60)]):
            self._fill_mcp()

    def _selected_mcp(self) -> str | None:
        rows = self.mcp_table.selectionModel().selectedRows() if self.mcp_table.selectionModel() else []
        r = rows[0].row() if rows else self.mcp_table.currentRow()
        return self.mcp_table.item(r, 0).text() if r >= 0 and self.mcp_table.item(r, 0) else None

    def _remove_mcp(self):
        name = self._selected_mcp()
        if not name:
            return
        text = self.toml.read_text(encoding="utf-8")
        self.toml.write_text(remove_table(text, f"mcp.servers.{name}"), encoding="utf-8")
        self._fill_mcp()

    def _test_mcp(self):
        name = self._selected_mcp()
        if not name:
            return
        from ..mcp.client import McpClient, ServerConfig
        cfg = ServerConfig.from_dict(name, config.load_settings().mcp_servers[name])

        def job():
            c = McpClient(cfg)
            try:
                c.connect()
                return c.version, [t["name"] for t in c.list_tools()]
            finally:
                c.close()
        w = self._tw = _Worker(job)
        w.finished.connect(lambda: self._mcp_tested(name, w))
        w.start()

    def _mcp_tested(self, name, w=None):
        w = w or self._tw
        for r in range(self.mcp_table.rowCount()):
            if self.mcp_table.item(r, 0).text() == name:
                if w.error or w.result is None:
                    self.mcp_table.setItem(r, 2, QTableWidgetItem("连不上"))
                    self.mcp_table.setItem(r, 3, QTableWidgetItem((w.error or "没有拿到结果（被中途停掉了）")[:60]))
                else:
                    version, tools = w.result
                    self.mcp_table.setItem(r, 2, QTableWidgetItem(f"已连接（{version}）"))
                    self.mcp_table.setItem(r, 3, QTableWidgetItem("、".join(f"mcp__{name}__{t}" for t in tools)))

    def _selftest(self):
        import subprocess
        import sys
        w = self._sw = _Worker(lambda: subprocess.run([sys.executable, "-m", "firmforge", "mcp", "--selftest"],
                                                      capture_output=True, text=True, encoding="utf-8", timeout=120))
        w.finished.connect(lambda: QMessageBox.information(
            self, "MCP 自检", w.error or ((w.result.stdout + w.result.stderr) if w.result is not None
                                          else "没有拿到结果（被中途停掉了）")))
        w.start()

    # ------------------------------------------------------------ 记忆与知识库

    def _memory(self, pl):
        self._head(pl, "记忆与知识库", "经验库记录\"之前同类问题是怎么修好的\"；知识库是 kb/ 下的嵌入式资料。")
        w, lay = section("经验库")
        self.exp = QTableWidget(0, 6)
        self.exp.setHorizontalHeaderLabels(["编号", "类别", "层", "失败的 key", "用过", "帮上忙"])
        self.exp.verticalHeader().setVisible(False)
        self.exp.setMinimumHeight(200)
        lay.addWidget(self.exp)
        rm, clear = QPushButton("删除选中的"), QPushButton("清空")
        rm.clicked.connect(lambda: self._exp_delete(False))
        clear.clicked.connect(lambda: self._exp_delete(True))
        lay.addLayout(hbox(rm, clear, "stretch"))
        pl.addWidget(w)
        self._fill_exp()
        w, lay = section("知识库文档")
        self.kb = QListWidget()
        self.kb.setMinimumHeight(160)
        lay.addWidget(self.kb)
        add, opendir = QPushButton("添加文档"), QPushButton("打开文件夹")
        add.clicked.connect(self._kb_add)
        opendir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.kb_dir()))))
        lay.addLayout(hbox(add, opendir, "stretch"))
        pl.addWidget(w)
        self._fill_kb()

    def _store(self):
        from ..store import Store
        return Store(str(self.project / ".ff_data" / "firmforge.db"))

    def _fill_exp(self):
        try:
            store = self._store()
            rows = store.query("SELECT id, category, layer, pattern, uses, helped FROM experiences ORDER BY id DESC")
            store.close()
        except Exception:
            rows = []
        self.exp.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, k in enumerate(["id", "category", "layer", "pattern", "uses", "helped"]):
                self.exp.setItem(r, c, QTableWidgetItem(str(row[k])))
        self.exp.resizeColumnsToContents()

    def _exp_delete(self, everything: bool):
        if everything and QMessageBox.question(self, "清空经验库", "确定删除全部经验吗？") != QMessageBox.Yes:
            return
        store = self._store()
        try:
            if everything:
                store.execute("DELETE FROM experiences")
            else:
                for idx in self.exp.selectionModel().selectedRows():
                    store.execute("DELETE FROM experiences WHERE id = ?", (int(self.exp.item(idx.row(), 0).text()),))
        finally:
            store.close()
        self._fill_exp()

    def _fill_kb(self):
        self.kb.clear()
        for f in sorted(config.kb_dir().glob("*.md")):
            first = next((ln[2:] for ln in f.read_text(encoding="utf-8").splitlines() if ln.startswith("# ")), f.stem)
            self.kb.addItem(f"{f.name}　{first}")

    def _kb_add(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Markdown 文档", "", "Markdown (*.md)")
        if path:
            shutil.copy(path, config.kb_dir() / Path(path).name)
            self._fill_kb()

    # ------------------------------------------------------------ 人工与权限、预算、高级

    def _human(self, pl):
        self._head(pl, "人工与权限", "这些是对话页的默认值；评测（命令行 eval）从来不问人。")
        st = QSettings()
        form = QFormLayout()
        review = Toggle(st.value("chat/human_review", True, bool))
        review.toggled.connect(lambda on: QSettings().setValue("chat/human_review", on))
        form.addRow("规划完成后等我确认", review)
        form.addRow("", label("工作流里的\"等人确认\"步骤和升级第 4 级（转人工）也只在打开这一项时才会问你。", "Faint", wrap=True))
        form.addRow("外部工具调用前问我", label("在\"工具与 MCP\"里按服务设置", "Faint"))
        pl.addLayout(form)

    def _budget(self, pl):
        self._head(pl, "预算", "每次运行的上限，超过就停，结论记为\"不完整\"。0 表示不限。")
        s = config.load_settings()
        form = QFormLayout()
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(0, 100_000_000)
        self.max_tokens.setSingleStep(100_000)
        self.max_tokens.setValue(int(s.budget.get("max_tokens", 0) or 0))
        self.max_cost = QDoubleSpinBox()
        self.max_cost.setRange(0, 10000)
        self.max_cost.setValue(float(s.budget.get("max_cost", 0) or 0))
        form.addRow("token 上限", self.max_tokens)
        form.addRow("费用上限（元）", self.max_cost)
        pl.addLayout(form)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(lambda: self._save([("budget", "max_tokens", self.max_tokens.value()),
                                                 ("budget", "max_cost", self.max_cost.value())]))
        pl.addWidget(save, 0, Qt.AlignLeft)

    def _advanced(self, pl):
        self._head(pl, "高级", "并发、超时、重试和工具调用的限制。")
        s = config.load_settings()
        form = QFormLayout()
        spins = {}
        for key, table, name, lo, hi, val in (
                ("llm", "concurrency", "同时进行的模型调用数", 1, 32, s.concurrency.get("llm", 4)),
                ("verify", "concurrency", "同时进行的判定数", 1, 32, s.concurrency.get("verify", 4)),
                ("timeout_s", "llm", "模型调用超时（秒）", 10, 3600, int(s.llm.timeout_s)),
                ("max_retries", "llm", "模型调用重试次数", 0, 10, s.llm.max_retries),
                ("max_rounds", "tools", "一次生成最多几轮工具往返", 0, 20, s.tools.get("max_rounds", 5)),
                ("max_chars", "tools", "每次工具结果最多几个字符", 500, 100000, s.tools.get("max_chars", 8000)),
                ("max_attempts_per_module", "loop", "每个模块最多尝试几次", 1, 20, s.loop.get("max_attempts_per_module", 6))):
            sp = QSpinBox()
            sp.setRange(lo, hi)
            sp.setValue(int(val))
            form.addRow(name, sp)
            spins[(table, key)] = sp
        pl.addLayout(form)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.clicked.connect(lambda: self._save([(t, k, sp.value()) for (t, k), sp in spins.items()]))
        pl.addWidget(save, 0, Qt.AlignLeft)

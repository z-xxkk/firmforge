"""对话工作区（界面草图第 4 版第三节）：左会话列表、中对话、右上模块图 / 流程图、右下详情。

对话和模块图是同一份事件的两种画法：对话按时间讲经过，模块图看全局状态。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .. import config
from ..agents.brief import parse_sections
from .graph import FlowCanvas, ModuleGraph
from .layout import LAYER_NAMES, LAYERS, RunView
from .runner import DockerBackend, LocalBackend, ReplayPlayer, _Worker
from .widgets import Avatar, Dot, GripSplitter, Panel, Segmented, T, Toggle, hbox, label

ROLE_NAMES = {"analyst": "需求分析", "planner": "规划", "coder": "编码", "tester": "测试", "diagnoser": "诊断",
              "reviewer": "审查"}
VERDICT_NAMES = {"pass": "通过", "fail": "没通过", "error": "出错", "incomplete": "不完整", "interrupted": "被中断",
                 "rejected": "没有确认"}
THINK = [("", "按 Agent"), ("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高")]
TIERS = [("", "按 Agent"), ("fast", "快档"), ("strong", "强档")]
CONFIGS = ["full", "b2", "b3", "b4", "full-no-tools", "full-no-review", "full-no-esc", "full-no-exp", "b1"]


def pill_combo(items: list[tuple[str, str]], prefix: str) -> QComboBox:
    cb = QComboBox()
    cb.setObjectName("Pill")
    for val, text in items:
        cb.addItem(f"{prefix} {text}", val)
    fit_combo(cb)
    return cb


def fit_combo(cb: QComboBox) -> None:
    """胶囊下拉框按最长的选项定宽，文字不会被截断。"""
    fm = cb.fontMetrics()
    cb.setMinimumWidth(max(fm.horizontalAdvance(cb.itemText(i)) for i in range(cb.count())) + 34)


# ---------------------------------------------------------------- 消息部件

class Bubble(QFrame):
    """你的消息：浅绿底气泡，靠右。"""

    def __init__(self, text: str):
        super().__init__()
        self.setStyleSheet(f"background: {T()['accent_soft']}; border-radius: 16px; border-bottom-right-radius: 6px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 9, 14, 9)
        lab = label(text, wrap=True)
        lab.setStyleSheet("background: transparent;")
        lay.addWidget(lab)


class AgentMessage(QWidget):
    """Agent 的消息：头像、名字、角色、型号，正文，可选按钮；右上"发给 AI 的内容"。"""
    show_brief = Signal(object)

    def __init__(self, name: str, role: str, text: str, meta: str = "", brief=None):
        super().__init__()
        self.brief = brief
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        av = Avatar(name, role)
        outer.addWidget(av, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        n = QLabel(f"<b style='font-family:monospace'>{name}</b>")
        head.addWidget(n)
        head.addWidget(label(ROLE_NAMES.get(role, role), "Faint"))
        if meta:
            head.addWidget(label(meta, "Faint"))
        head.addStretch(1)
        if brief is not None:
            link = QPushButton("发给 AI 的内容")
            link.setFlat(True)
            link.setCursor(Qt.PointingHandCursor)
            link.setStyleSheet(f"color: {T()['accent']}; border: none; font-size: 11px; padding: 0;")
            link.clicked.connect(lambda: self.show_brief.emit(self.brief))
            head.addWidget(link)
        col.addLayout(head)
        self.body = label(text, wrap=True)
        self.body.setTextFormat(Qt.MarkdownText)
        col.addWidget(self.body)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.actions.addStretch(1)
        col.addLayout(self.actions)
        outer.addLayout(col, 1)

    def add_action(self, text: str, fn, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        if primary:
            b.setObjectName("Primary")
        b.clicked.connect(fn)
        self.actions.insertWidget(self.actions.count() - 1, b)
        return b

    def settle(self, text: str) -> None:
        """问题回答以后：按钮换成一个说明标签。"""
        while self.actions.count() > 1:
            w = self.actions.takeAt(0).widget()
            if w:
                w.deleteLater()
        lab = QLabel(text)
        lab.setStyleSheet(f"border: 1px solid {T()['line']}; border-radius: 11px; padding: 1px 10px; color: {T()['ink2']};"
                          " font-size: 12px;")
        self.actions.insertWidget(0, lab)


class SegBar(QWidget):
    """5 段检查小条。"""

    def __init__(self, failed: str | None, passed_all: bool):
        super().__init__()
        self.setFixedSize(5 * 17, 8)
        self.failed, self.passed_all = failed, passed_all

    def paintEvent(self, ev):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        state = "pass"
        for i, layer in enumerate(LAYERS):
            if self.failed == layer:
                state = "fail"
            col = T()["accent"] if state == "pass" else (T()["fail"] if state == "fail" else T()["line"])
            p.setBrush(QColor(col))
            p.drawRoundedRect(i * 17, 1, 14, 6, 3, 3)
            if state == "fail":
                state = "none"


def line_widget(*parts, indent: int = 36, bg: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(indent, 2 if not bg else 5, 6, 2 if not bg else 5, )
    lay.setSpacing(8)
    for p in parts:
        lay.addWidget(p if isinstance(p, QWidget) else label(str(p), "Muted"))
    lay.addStretch(1)
    if bg:
        w.setStyleSheet(f"background: {T()['bg']}; border-radius: 10px;")
    return w


# ---------------------------------------------------------------- 主页面

class ChatPage(QWidget):
    open_designer = Signal(str)          # /workflow 指令转给设计器

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.runs_dir = project / ".ff_runs"
        self.chats_dir = project / ".ff_chats"
        self.view = RunView()
        self.backend = None
        self.replay: ReplayPlayer | None = None
        self.conv = None
        self.task = None                  # 选中的评测题或从需求生成的任务
        self.run_dir: Path | None = None
        self.pending: tuple[str, dict, AgentMessage | None] | None = None   # 等待人回答的问题
        self.messages_by_module: dict[str, list] = {}
        self.workflow = None
        self._worker = None
        self._plan_modules: list[dict] = []
        self._build()
        self.refresh_sessions()

    # ------------------------------------------------------------ 布局

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.split = GripSplitter(Qt.Horizontal)
        root.addWidget(self.split)

        # 左：会话列表
        left = Panel(margins=(10, 12, 10, 12))
        new = QPushButton("新对话")
        new.setObjectName("Primary")
        new.clicked.connect(self.new_chat)
        left.lay.addWidget(new)
        self.sessions = QListWidget()
        self.sessions.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sessions.setTextElideMode(Qt.ElideRight)
        self.sessions.itemClicked.connect(self._open_session)
        left.lay.addWidget(self.sessions, 1)
        left.setMinimumWidth(150)

        # 中：对话
        mid = Panel(margins=(18, 14, 18, 14))
        head = QHBoxLayout()
        self.title = label("新对话", "H2")
        self.head_pills = label("", "Faint")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Ghost")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setVisible(False)
        head.addWidget(self.title)
        head.addWidget(self.head_pills)
        head.addStretch(1)
        self.review_box = Toggle(QSettings().value("chat/human_review", True, bool))
        self.review_box.setToolTip("开启后：规划完成、工作流里的等人确认步骤、升级到转人工时，都会在对话里问你")
        head.addWidget(label("规划后等我确认", "Faint"))
        head.addWidget(self.review_box)
        head.addSpacing(8)
        head.addWidget(self.stop_btn)
        mid.lay.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("ScrollInner")
        self.msgs = QVBoxLayout(inner)
        self.msgs.setContentsMargins(0, 6, 6, 6)
        self.msgs.setSpacing(14)
        self.msgs.addStretch(1)
        self.scroll.setWidget(inner)
        mid.lay.addWidget(self.scroll, 1)
        # 回放控制条
        self.replay_bar = QWidget()
        rb = QHBoxLayout(self.replay_bar)
        rb.setContentsMargins(0, 0, 0, 0)
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._toggle_play)
        self.speed = QComboBox()
        self.speed.setObjectName("Pill")
        for s in (1, 2, 4, 8, 16):
            self.speed.addItem(f"{s} 倍速", s)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(lambda: self.replay and setattr(self.replay, "speed", self.speed.currentData()))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.sliderReleased.connect(lambda: self.replay and self.replay.seek(self.slider.value()))
        self.pos_label = label("", "Faint")
        for w in (label("回放", "H3"), self.play_btn, self.speed, self.slider, self.pos_label):
            rb.addWidget(w, 1 if w is self.slider else 0)
        self.replay_bar.setVisible(False)
        mid.lay.addWidget(self.replay_bar)
        # 输入框
        comp = QFrame()
        comp.setObjectName("Composer")
        cl = QVBoxLayout(comp)
        cl.setContentsMargins(12, 10, 12, 8)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("用一句话说要写什么模块，例如\"STM32F103 上的 Modbus RTU 从机，支持读保持寄存器\"；"
                                      "也可以 /task flash_kv 直接选一道评测题")
        self.input.setFixedHeight(64)
        cl.addWidget(self.input)
        self.task_combo = QComboBox()
        self.task_combo.setObjectName("Pill")
        self.task_combo.addItem("选评测题", "")
        from ..tasks import discover_tasks
        for t in discover_tasks(self.project / "tasks") if (self.project / "tasks").is_dir() else []:
            self.task_combo.addItem(t.id, t.id)
        self.task_combo.setToolTip("选一道评测题后直接点发送，就跳过需求对话、用题目的需求文档开始运行")
        self.task_combo.setMinimumWidth(self.task_combo.fontMetrics().horizontalAdvance("mavlink_parser") + 34)
        self.tier_combo = pill_combo(TIERS, "模型")
        self.think_combo = pill_combo(THINK, "思考")
        self.cfg_combo = pill_combo([(c, c) for c in CONFIGS], "配置")
        send = QPushButton("↑")
        send.setObjectName("Send")
        send.setToolTip("发送（Ctrl+Enter）")
        send.clicked.connect(self.send)
        cl.addLayout(hbox(self.task_combo, self.tier_combo, self.think_combo, self.cfg_combo, "stretch", send, spacing=6))
        mid.lay.addWidget(comp)
        mid.setMinimumWidth(380)

        # 右：模块图 + 详情
        right = GripSplitter(Qt.Vertical)
        gpanel = Panel(margins=(0, 0, 0, 0))
        gtop = QHBoxLayout()
        gtop.setContentsMargins(16, 12, 14, 0)
        self.graph_title = label("模块", "H3")
        self.graph_sub = label("", "Faint")
        self.view_seg = Segmented([("modules", "模块图"), ("flow", "流程图")])
        self.view_seg.changed.connect(self._switch_view)
        gtop.addWidget(self.graph_title)
        gtop.addWidget(self.graph_sub)
        gtop.addStretch(1)
        gtop.addWidget(self.view_seg)
        gpanel.lay.addLayout(gtop)
        self.graph_stack = QStackedWidget()
        self.graph = ModuleGraph()
        self.graph.module_clicked.connect(self.select_module)
        self.flow = FlowCanvas(editable=False)
        self.graph_stack.addWidget(self.graph)
        self.graph_stack.addWidget(self.flow)
        gpanel.lay.addWidget(self.graph_stack, 1)
        ipanel = Panel(margins=(16, 8, 16, 12))
        self.tabs = QTabWidget()
        self.layer_box = QWidget()
        lb = QVBoxLayout(self.layer_box)
        lb.setContentsMargins(0, 8, 0, 0)
        self.layer_title = label("点一个模块看它的检查结果", "Muted")
        self.layer_pills = QHBoxLayout()
        self.layer_detail = QTextEdit()
        self.layer_detail.setObjectName("Code")
        self.layer_detail.setReadOnly(True)
        lb.addWidget(self.layer_title)
        lb.addLayout(self.layer_pills)
        lb.addWidget(self.layer_detail, 1)
        self.code_view = QTextEdit()
        self.code_view.setObjectName("Code")
        self.code_view.setReadOnly(True)
        self.why = QTableWidget(0, 6)
        self.why.setHorizontalHeaderLabels(["候选", "成功率", "成本", "负载", "探索", "得分"])
        self.why.verticalHeader().setVisible(False)
        self.why_reason = label("", "Faint", wrap=True)
        why_box = QWidget()
        wl = QVBoxLayout(why_box)
        wl.setContentsMargins(0, 8, 0, 0)
        wl.addWidget(self.why_reason)
        wl.addWidget(self.why, 1)
        self.brief_view = QTextEdit()
        self.brief_view.setObjectName("Code")
        self.brief_view.setReadOnly(True)
        self.tabs.addTab(self.layer_box, "检查结果")
        self.tabs.addTab(self.code_view, "代码")
        self.tabs.addTab(why_box, "为什么交给它")
        self.tabs.addTab(self.brief_view, "发给 AI 的内容")
        ipanel.lay.addWidget(self.tabs)
        right.addWidget(gpanel)
        right.addWidget(ipanel)
        right.set_defaults([560, 280])
        self.right_split = right

        self.split.addWidget(left)
        self.split.addWidget(mid)
        self.split.addWidget(right)
        self.split.set_defaults([220, 540, 640])
        st = QSettings()
        for sp, key in ((self.split, "chat/split"), (self.right_split, "chat/right")):
            if st.value(key):
                sp.restoreState(st.value(key))

    def save_layout(self):
        st = QSettings()
        st.setValue("chat/split", self.split.saveState())
        st.setValue("chat/right", self.right_split.saveState())
        st.setValue("chat/human_review", self.review_box.isChecked())

    # ------------------------------------------------------------ 会话列表

    def refresh_sessions(self):
        from ..chat import list_conversations
        self.sessions.clear()
        today = datetime.now().date()

        def group(ts):
            d = datetime.fromtimestamp(ts).date()
            return "今天" if d == today else ("昨天" if (today - d).days == 1 else "更早")
        last = None
        rows = [("chat", c["title"], c["path"], c["mtime"], c["runs"]) for c in list_conversations(self.chats_dir)]
        linked = {Path(r).name for c in rows for r in c[4]}
        if self.runs_dir.is_dir():
            for rd in self.runs_dir.iterdir():
                if (rd / "events.jsonl").is_file() and rd.name not in linked:
                    rows.append(("run", rd.name, rd, (rd / "events.jsonl").stat().st_mtime, [str(rd)]))
        rows.sort(key=lambda r: r[3], reverse=True)
        for kind, title, path, mtime, runs in rows[:200]:
            g = group(mtime)
            if g != last:
                hdr = QListWidgetItem(g)
                hdr.setFlags(Qt.NoItemFlags)
                hdr.setForeground(Qt.gray)
                self.sessions.addItem(hdr)
                last = g
            verdict = ""
            if runs:
                info = Path(runs[-1]) / "run.json"
                if info.is_file():
                    try:
                        verdict = json.loads(info.read_text(encoding="utf-8")).get("verdict", "")
                    except (OSError, json.JSONDecodeError):
                        pass
            mark = {"pass": "●", "fail": "●", "error": "●"}.get(verdict, "○")
            it = QListWidgetItem(f"{title}" if kind == "chat" else f"运行 {title}")
            it.setData(Qt.UserRole, (kind, str(path)))
            it.setToolTip(f"结论：{VERDICT_NAMES.get(verdict, '还没有运行')}")
            it.setData(Qt.DecorationRole, None)
            if verdict:
                it.setText(f"{it.text()}  {mark}")
            self.sessions.addItem(it)

    def _open_session(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return
        kind, path = data
        if kind == "run":
            self.start_replay(Path(path))
            return
        from ..chat import Conversation
        self.clear()
        try:
            llm = self._llm()
        except Exception:
            llm = None
        self.conv = Conversation.load(Path(path), llm, config.load_settings())
        self.title.setText(self.conv.title)
        for t in self.conv.turns:
            if t.role == "user":
                self.add_user(t.text)
            else:
                self.add_agent("analyst", "analyst", t.text)
        if self.conv.runs:
            self.run_dir = Path(self.conv.runs[-1])
            self.start_replay(self.run_dir, append=True)

    # ------------------------------------------------------------ 消息

    def _add(self, w: QWidget, align=None):
        if align is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(w)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(60, 0, 0, 0)
            w = holder
        self.msgs.insertWidget(self.msgs.count() - 1, w)
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return w

    def add_user(self, text: str):
        self._add(Bubble(text), align="right")

    def add_agent(self, name, role, text, meta="", brief=None) -> AgentMessage:
        m = AgentMessage(name, role, text, meta, brief)
        m.show_brief.connect(self.show_brief)
        self._add(m)
        return m

    def add_line(self, *parts, bg=False):
        return self._add(line_widget(*parts, bg=bg))

    def clear(self):
        while self.msgs.count() > 1:
            w = self.msgs.takeAt(0).widget()
            if w:
                w.hide()                 # 先藏起来：deleteLater 要等事件循环，期间还会被画出来
                w.setParent(None)
                w.deleteLater()
        self.view = RunView()
        self.graph.refresh(self.view)
        self.pending = None
        self.messages_by_module.clear()

    def new_chat(self):
        if self.backend is not None:
            return
        self.clear()
        self.conv, self.task, self.run_dir = None, None, None
        self.title.setText("新对话")
        self.head_pills.setText("")
        self.replay_bar.setVisible(False)
        self.input.setFocus()

    # ------------------------------------------------------------ 发送

    def _llm(self):
        from ..llm import OpenAICompatClient
        return OpenAICompatClient.from_settings(config.load_settings())

    def send(self):
        text = self.input.toPlainText().strip()
        if self.pending is not None:
            self._answer_pending(text)
            return
        if not text and self.task_combo.currentData():
            text = f"/task {self.task_combo.currentData()}"
        if not text:
            return
        self.input.clear()
        if text.startswith("/"):
            self._slash(text)
            return
        self.add_user(text)
        try:
            if self.conv is None:
                from ..chat import Conversation
                self.conv = Conversation(self._llm(), config.load_settings(), chats_dir=self.chats_dir,
                                         thinking=self.think_combo.currentData() or None)
        except Exception as exc:
            self.add_line(f"没法联系模型：{exc}。请到 设置 › 模型 填写 API 密钥。", bg=True)
            return
        wf = self.workflow
        run_dir = self.run_dir if (self.run_dir and self.backend is None) else None
        typing = self.add_line(Dot(T()["run"]), "analyst 正在想")
        self._worker = _Worker(lambda: self.conv.send(text, run_dir=run_dir, workflow=wf))
        self._worker.finished.connect(lambda: self._analyst_done(typing))
        self._worker.start()

    def _analyst_done(self, typing):
        typing.deleteLater()
        w = self._worker
        if w.error:
            self.add_line(f"analyst 没有回复：{w.error}", bg=True)
            return
        self.title.setText(self.conv.title)
        brief = self.conv.turns[-2].sent if len(self.conv.turns) >= 2 else None
        text = w.result.text
        shown = text.split("<<<FILE")[0].strip() or "需求整理好了。"
        m = self.add_agent("analyst", "analyst", shown, brief=brief)
        d = self.conv.draft
        if d is not None and "<<<FILE" in text:
            files = ["spec.md", *d.headers]
            m.body.setText(shown + "\n\n文件：" + "、".join(f"`{f}`" for f in files))
            if d.ready:
                m.add_action("开始运行", lambda: self._start_from_draft(m), primary=True)
            else:
                m.add_action("还不能运行：" + "；".join(d.problems)[:80], lambda: None)
        self.refresh_sessions()

    def _start_from_draft(self, msg):
        try:
            task = self.conv.start_task(self.chats_dir / "drafts")
        except Exception as exc:
            self.add_line(str(exc), bg=True)
            return
        msg.settle("已开始运行")
        self.add_line("这是自己写的需求，没有隐藏测试：结论只代表通过了 Agent 自己写的测试。", bg=True)
        self.start_run(task)

    def _slash(self, text: str):
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd == "/task":
            from ..tasks import load_task
            try:
                task = load_task(self.project / "tasks" / arg)
            except Exception as exc:
                self.add_line(f"没有评测题 {arg}：{exc}", bg=True)
                return
            self.add_user(f"评测题 {task.id}：{task.title}")
            self.title.setText(task.title)
            self.start_run(task)
        elif cmd in ("/model", "/think", "/config"):
            combo = {"/model": self.tier_combo, "/think": self.think_combo, "/config": self.cfg_combo}[cmd]
            alias = {"agent": "", "按agent": "", "fast": "fast", "strong": "strong"}
            idx = combo.findData(alias.get(arg, arg))
            if idx < 0:
                self.add_line(f"{cmd} 不认识 {arg}", bg=True)
            else:
                combo.setCurrentIndex(idx)
                self.add_line(f"已切换：{combo.currentText()}")
        elif cmd == "/workflow":
            self.open_designer.emit(arg)
        else:
            self.add_line("可用的命令：/task 题目、/model fast|strong|agent、/think off|low|high|max、/config 配置、/workflow 修改要求",
                          bg=True)

    # ------------------------------------------------------------ 运行

    def start_run(self, task):
        if self.backend is not None:
            self.add_line("上一次运行还没结束。", bg=True)
            return
        self.clear_graph()
        self.task = task
        st = QSettings()
        use_docker = st.value("general/backend", "docker") == "docker" and DockerBackend.available()
        self.backend = DockerBackend(self) if use_docker else LocalBackend(self)
        self.backend.event.connect(self.on_event)
        self.backend.ask.connect(self.on_ask)
        self.backend.finished.connect(self.on_finished)
        self.backend.log.connect(lambda s: None)
        cfg = self.cfg_combo.currentData()
        self.head_pills.setText(f"{cfg}　{'docker' if use_docker else '本机'}")
        self.stop_btn.setVisible(True)
        from ..orchestrator.workflow import load_workflow
        self.workflow = self.workflow or load_workflow()
        self.flow.show_workflow(self.workflow)
        kw = dict(runs_dir=self.runs_dir, human_review=self.review_box.isChecked(),
                  tier=self.tier_combo.currentData() or None, thinking=self.think_combo.currentData() or None,
                  workflow=self.workflow)
        try:
            if use_docker:
                self.backend.start(task, cfg, project=self.project, **kw)
            else:
                self.backend.start(task, cfg, **kw)
        except Exception as exc:
            self.add_line(f"没能开始运行：{exc}", bg=True)
            self.backend = None
            self.stop_btn.setVisible(False)

    def clear_graph(self):
        self.view = RunView()
        self.graph.refresh(self.view)

    def stop(self):
        if self.backend is not None:
            self.backend.stop()
            self.add_line("已请求停止。", bg=True)

    def on_finished(self, info: dict):
        self.stop_btn.setVisible(False)
        self.backend = None
        if info.get("run_dir"):
            self.run_dir = Path(info["run_dir"])
            if self.conv is not None and str(self.run_dir) not in self.conv.runs:
                self.conv.runs.append(str(self.run_dir))
                self.conv.save()
        if info.get("error"):
            self.add_line(f"运行出错：{info['error']}", bg=True)
        self.input.setPlaceholderText("可以追问结果，例如\"为什么 pdu 第一次没过\"；也可以提新的修改要求")
        self.refresh_sessions()

    # ------------------------------------------------------------ 事件 → 对话和模块图

    def on_event(self, e: dict):
        self.view.apply(e)
        if self.view.run_id and self.run_dir is None and self.replay is None:
            cand = self.runs_dir / self.view.run_id
            if cand.is_dir():
                self.run_dir = cand
        self.graph.refresh(self.view)
        done = sum(1 for m in self.view.modules.values() if m.status == "done")
        self.graph_sub.setText(f"{len(self.view.modules)} 个，{done} 个通过" if self.view.modules else "")
        self._update_flow_states(e)
        self._narrate(e)
        if self.graph.selected and e.get("node_id") == self.graph.selected:
            self.select_module(self.graph.selected)
        win = self.window()
        if hasattr(win, "set_status"):
            v = self.view
            elapsed = (v.last_ts - v.started) if v.started else 0
            win.set_status(f"运行 {v.run_id}　用时 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}　token {v.tokens:,}"
                           f"　费用 {v.cost:.2f} 元" + (f"　结论 {VERDICT_NAMES.get(v.verdict, v.verdict)}" if v.verdict else ""))

    def _update_flow_states(self, e):
        t, d = e.get("type"), e.get("data", {})
        step = {"agent_started": {"planner": "plan", "coder": "code", "tester": "test", "reviewer": "review",
                                  "diagnoser": "repair"}.get(d.get("role")),
                "verify_started": "integrate" if d.get("scope") == "all" else "check",
                "human_wait": d.get("step") or "confirm_plan"}.get(t)
        if step and self.flow.items_by_id:
            states = {sid: ("done" if it.state in ("running", "done") else "") for sid, it in self.flow.items_by_id.items()}
            for sid in states:
                if states[sid] == "done" and sid == step:
                    states[sid] = ""
            states[step] = "running"
            self.flow.set_states(states)

    def _narrate(self, e: dict):
        t, d, node = e.get("type"), e.get("data", {}), e.get("node_id")
        if t == "run_started":
            self.add_line(f"开始运行：{d.get('task')}，配置 {d.get('config')}")
        elif t == "plan_ready":
            self._plan_modules = d.get("modules", [])
            rows = "\n".join(f"- `{m['id']}`　{m.get('title', '')}" + (f"，依赖 {'、'.join(m['depends_on'])}" if m.get("depends_on") else "")
                             for m in self._plan_modules)
            what = "重新规划" if d.get("replan") else "拆成"
            self.add_agent("planner", "planner", f"{what} {len(self._plan_modules)} 个模块：\n\n{rows}",
                           brief=("call", "planner", node))
        elif t == "verify_finished" and d.get("scope") == "module":
            failed = d.get("failed_layer")
            text = f"第 {d.get('attempt')} 次" + ("通过全部 5 层" if not failed else f"：{LAYER_NAMES.get(failed, failed)}没通过")
            self.add_line(SegBar(failed, not failed), label(node or "", "Mono"), text)
        elif t == "verify_finished" and d.get("scope") == "all":
            self.add_line(f"集成检查：{VERDICT_NAMES.get(d.get('verdict'), d.get('verdict'))}")
        elif t == "diagnosis":
            self.add_agent("diagnoser", "diagnoser", f"{d.get('summary', '')}\n\n交给{ROLE_NAMES.get(d.get('route'), d.get('route'))} Agent 改。",
                           meta=f"把握 {d.get('confidence')}", brief=("call", "diagnoser", node))
        elif t == "tool_result":
            col = T()["pass"] if d.get("ok") else T()["fail"]
            self.add_line(Dot(col), f"{d.get('agent')} 调用工具", label(d.get("tool", ""), "Mono"),
                          label(d.get("summary") or ("完成" if d.get("ok") else "失败"), "Faint"), bg=True)
        elif t == "escalated":
            self.add_line(f"{node} 升到第 {d.get('level')} 级（{d.get('name')}）：{d.get('reason')}")
        elif t == "rollback":
            self.add_line(f"{node} 回滚到第 {d.get('to_attempt')} 次（{d.get('reason')}）")
        elif t == "experience_used":
            self.add_line(f"{node} 参考了 {len(d.get('ids', []))} 条过往经验")
        elif t == "node_done":
            self.add_line(Dot(T()["pass"]), f"模块 {node} 完成")
        elif t == "node_failed" and d.get("verdict") != "replanned":
            self.add_line(Dot(T()["fail"]), f"模块 {node} 没有完成" + (f"：{d.get('reason')}" if d.get("reason") else ""))
        elif t == "agent_finished" and d.get("ok") is False:
            self.add_line(f"{d.get('agent')} 调用失败：{d.get('error')}", bg=True)
        elif t == "run_finished":
            v = d.get("verdict")
            self.add_agent("FirmForge", "reviewer", f"运行结束：**{VERDICT_NAMES.get(v, v)}**。共 {d.get('attempts')} 次尝试，"
                           f"token {d.get('tokens'):,}，费用 {d.get('cost', 0):.2f} 元，用时 {d.get('wall_s')} 秒。")

    # ------------------------------------------------------------ 需要人回答

    def on_ask(self, kind: str, payload: dict):
        if kind == "plan":
            m = self.add_agent("planner", "planner", "规划好了，请确认。确认后开始编写；有意见就写在下面输入框里发送，让规划 Agent 重做。")
            m.add_action("提修改意见", lambda: self._focus_input("写下对规划的修改意见，发送后让规划 Agent 重做"))
            m.add_action("确认，开始写", lambda: self._resolve("", m, "你已确认"), primary=True)
        elif kind == "checkpoint":
            where = f"模块 {payload.get('node')} 的" if payload.get("node") and payload.get("node") != payload.get("step") else ""
            m = self.add_agent("FirmForge", "reviewer", f"{where}步骤 **{payload.get('step')}** 等你确认：{payload.get('note') or ''}"
                               + (f"\n\n当前交付：{'、'.join(payload.get('files', []))}" if payload.get("files") else ""))
            m.add_action("写意见", lambda: self._focus_input("写下意见，发送后交给编码 Agent 修改"))
            m.add_action("通过", lambda: self._resolve("", m, "你已通过"), primary=True)
        elif kind == "hint":
            pts = "\n".join(f"- {p}" for p in payload.get("points", []))
            m = self.add_agent("FirmForge", "reviewer", f"模块 **{payload.get('module')}** 升级到转人工，失败要点：\n\n{pts}\n\n"
                               "在下面写一句给编码 Agent 的提示后发送；放弃就点按钮。")
            m.add_action("放弃这个模块", lambda: self._resolve("", m, "你放弃了这个模块"))
        elif kind == "approve":
            m = self.add_agent("FirmForge", "reviewer", f"{payload.get('agent')} 想调用外部工具 `{payload.get('tool')}`，参数：\n\n"
                               f"```\n{json.dumps(payload.get('args'), ensure_ascii=False)[:400]}\n```")
            m.add_action("拒绝", lambda: self._resolve(False, m, "你拒绝了"))
            m.add_action("允许", lambda: self._resolve(True, m, "你允许了"), primary=True)
        else:
            return
        self.pending = (kind, payload, m)

    def _focus_input(self, placeholder: str):
        self.input.setPlaceholderText(placeholder)
        self.input.setFocus()

    def _resolve(self, value, msg, text):
        if self.backend is not None:
            self.backend.answer(value)
        msg.settle(text)
        self.pending = None

    def _answer_pending(self, text: str):
        kind, payload, msg = self.pending
        if not text:
            return
        self.input.clear()
        self.add_user(text)
        self._resolve(text, msg, "已发送你的意见" if kind != "hint" else "已发送你的提示")

    # ------------------------------------------------------------ 右下详情

    def select_module(self, mid: str):
        self.graph.selected = mid
        mv = self.view.modules.get(mid)
        if mv is None:
            return
        while self.layer_pills.count():
            w = self.layer_pills.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.layer_title.setText(f"{mid} 第 {mv.attempt} 次" if mv.attempt else f"{mid} 还没有检查")
        for layer in LAYERS:
            st = mv.layers.get(layer, "")
            col = {"pass": T()["pass"], "fail": T()["fail"], "running": T()["run"]}.get(st, T()["line"])
            b = QLabel(f"{LAYER_NAMES[layer]}\n{ {'pass': '通过', 'fail': '没通过', 'running': '检查中'}.get(st, '未检查') }")
            b.setAlignment(Qt.AlignCenter)
            b.setStyleSheet(f"background: {col}; color: white; border-radius: 10px; padding: 5px 4px; font-size: 12px;")
            self.layer_pills.addWidget(b)
        detail, code = "", ""
        if self.run_dir and mv.attempt:
            adir = self.run_dir / "nodes" / mid / f"attempt_{mv.attempt}"
            rep = adir / "report.json"
            if rep.is_file():
                try:
                    from ..agents.context import report_details
                    from ..verifier.report import Report
                    detail = report_details(Report.from_dict(json.loads(rep.read_text(encoding="utf-8"))), adir)
                except Exception:
                    detail = rep.read_text(encoding="utf-8")[:6000]
            for f in sorted((adir / "src").glob("*")) if (adir / "src").is_dir() else []:
                code += f"// ===== src/{f.name} =====\n{f.read_text(encoding='utf-8', errors='replace')}\n"
        self.layer_detail.setPlainText(detail or "（还没有报告）")
        self.code_view.setPlainText(code or "（还没有代码）")
        a = self.view.assignments.get(mid, {})
        scores = a.get("scores", {})
        self.why_reason.setText(scores.get("reason", "") + (f"\n成本依据：{scores['cost_basis']}" if scores.get("cost_basis") else ""))
        cands = scores.get("candidates", {})
        self.why.setRowCount(len(cands))
        for r, (name, s) in enumerate(cands.items()):
            for col, val in enumerate([name, s.get("p_hat"), s.get("cost_norm"), s.get("load"), s.get("explore"), s.get("score")]):
                self.why.setItem(r, col, QTableWidgetItem(str(val if not isinstance(val, float) else round(val, 2))))
        self.show_brief(("call", None, mid), switch=False)

    def show_brief(self, brief, switch: bool = True):
        """brief 是任务单原文，或 ("call", Agent, 模块)：从运行目录的调用记录里找最近一次。"""
        text = ""
        if isinstance(brief, str):
            text = brief
        elif isinstance(brief, tuple) and self.run_dir and (self.run_dir / "llm_calls.jsonl").is_file():
            _, agent_role, node = brief
            best = None
            for line in (self.run_dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines():
                c = json.loads(line)
                full = self.run_dir / "llm_calls" / f"{c['n']:04d}.json"
                if not full.is_file():
                    continue
                if agent_role and not c.get("agent", "").startswith(agent_role[:4]):
                    continue
                best = full
            if best is not None:
                data = json.loads(best.read_text(encoding="utf-8"))
                users = [m for m in data.get("messages", []) if m.get("role") == "user"]
                for m in users:
                    secs = parse_sections(m.get("content") or "")
                    if node and node in secs.get("当前位置", "") + secs.get("意图", ""):
                        text = m["content"]
                text = text or (users[-1]["content"] if users else "")
        self.brief_view.setPlainText(text or "（没有找到这次调用的记录）")
        if switch:
            self.tabs.setCurrentWidget(self.brief_view)

    def _switch_view(self, v: str):
        self.graph_stack.setCurrentIndex(0 if v == "modules" else 1)
        if v == "flow" and not self.flow.items_by_id:
            from ..orchestrator.workflow import load_workflow
            self.flow.show_workflow(self.workflow or load_workflow())

    # ------------------------------------------------------------ 回放

    def start_replay(self, run_dir: Path, append: bool = False):
        if self.backend is not None:
            return
        if not append:
            self.clear()
            self.title.setText(f"回放 {run_dir.name}")
        self.clear_graph()
        self.run_dir = run_dir
        self.replay = ReplayPlayer(run_dir, speed=self.speed.currentData())
        self.replay.event.connect(self.on_event)
        self.replay.reset.connect(self._replay_reset)
        self.replay.position.connect(self._replay_pos)
        self.slider.setRange(0, len(self.replay.events))
        self.replay_bar.setVisible(True)
        self.play_btn.setText("暂停")
        self.replay.play()

    def _replay_reset(self):
        """拖进度条时从头重放：清掉对话里的运行消息，保留需求对话。"""
        keep_replay = self.replay
        self.clear()
        self.replay = keep_replay
        if self.conv is not None:
            for t in self.conv.turns:
                if t.role == "user":
                    self.add_user(t.text)
                else:
                    self.add_agent("analyst", "analyst", t.text.split("<<<FILE")[0].strip(), brief=None)

    def _replay_pos(self, i, n):
        self.slider.setValue(i)
        self.pos_label.setText(f"{i} / {n}")
        if i >= n:
            self.play_btn.setText("从头播放")

    def _toggle_play(self):
        if self.replay is None:
            return
        if self.replay.playing:
            self.replay.pause()
            self.play_btn.setText("播放")
        else:
            self.replay.play()
            self.play_btn.setText("暂停")

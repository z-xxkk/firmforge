"""对话工作区（界面草图第 4 版第三节）：左会话列表、中对话、右上模块图 / 流程图、右下详情。

对话和模块图是同一份事件的两种画法：对话按时间讲经过，模块图看全局状态。
两种流程（DECISIONS 第 100 条）：
- 上板：嵌入式新流程，本机运行；可以写一句需求，也可以选一道上板评测题按某种配置跑一次（最后用隐藏测试判定）；
- 纯逻辑：旧流程，docker 或本机。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QSizePolicy,
                               QPlainTextEdit, QPushButton, QScrollArea, QSlider, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from .. import config
from ..agents.brief import parse_sections
from .graph import FlowCanvas, ModuleGraph
from . import board_view as bv
from .layout import EMB_LAYER_NAMES, LAYER_NAMES, LAYERS, UNIT_NAMES, RunView, layer_names
from .runner import DockerBackend, EmbeddedBackend, LocalBackend, ReplayPlayer, _Worker
from .widgets import Avatar, Dot, FlowLayout, GripSplitter, Panel, Segmented, T, Toggle, hbox, label

ROLE_NAMES = {"analyst": "需求分析", "planner": "规划", "coder": "编码", "tester": "测试", "diagnoser": "诊断",
              "reviewer": "审查", "rules": "规则", "designer": "设计分层"}
VERDICT_NAMES = {"pass": "通过", "fail": "没通过", "error": "出错", "incomplete": "不完整", "interrupted": "被中断",
                 "rejected": "没有确认"}
THINK = [("", "按 Agent"), ("off", "关"), ("low", "低"), ("high", "高"), ("max", "最高")]
TIERS = [("", "按 Agent"), ("fast", "快档"), ("strong", "强档")]
CONFIGS = ["full", "b2", "b3", "b4", "full-no-tools", "full-no-review", "full-no-esc", "full-no-exp", "b1"]
FLOWS = [("embedded", "上板"), ("classic", "纯逻辑")]
RULE_MODES = [("verified", "规则库"), ("unverified", "未核对规则"), ("none", "不给规则"), ("single", "单 Agent")]
DEFAULT_HW = "hardware/esp32c3_supermini"
BLAME = {"code": "代码", "test": "测试", "rule": "规则", "hardware": "硬件或接线"}


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
    """检查小条：旧流程 5 段；新流程 3 段（合规、编译、测试），给出每段的状态。"""

    def __init__(self, failed: str | None, passed_all: bool, states: list[str] | None = None):
        super().__init__()
        self.states = states
        n = len(states) if states is not None else 5
        self.setFixedSize(n * 21 if states is not None else 5 * 17, 8)
        self.failed, self.passed_all = failed, passed_all

    def paintEvent(self, ev):
        from PySide6.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if self.states is not None:
            for i, st in enumerate(self.states):
                col = {"pass": T()["accent"], "fail": T()["fail"], "running": T()["run"]}.get(st, T()["line"])
                p.setBrush(QColor(col))
                p.drawRoundedRect(i * 21, 1, 18, 6, 3, 3)
            return
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
        if isinstance(p, QWidget):
            lay.addWidget(p)
        else:                                    # 长句换行，不把对话区撑出横向滚动条
            lab = label(str(p), "Muted", wrap=True)
            lab.setMinimumWidth(10)
            lay.addWidget(lab, 1)
    lay.addStretch(0)
    if bg:
        w.setStyleSheet(f"background: {T()['bg']}; border-radius: 10px;")
    return w


# ---------------------------------------------------------------- 主页面

class ChatPage(QWidget):
    open_designer = Signal(str)          # /workflow 指令转给设计器
    review_rules = Signal(dict, object)  # 规则等人确认：交给硬件页，确认完调用回调（DECISIONS 第 100 条）
    run_done = Signal()                  # 一次运行结束（规则可能入库了，硬件页要重新读）

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
        self.hw = QSettings().value("embedded/hw", DEFAULT_HW)
        self.judge: dict | None = None    # 评测题跑完后隐藏测试的判定
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
        self.review_label = label("规划后等我确认", "Faint")
        head.addWidget(self.review_label)
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
        self.input.setFixedHeight(64)
        cl.addWidget(self.input)
        self.flow_combo = pill_combo(FLOWS, "流程")
        self.flow_combo.setToolTip("上板：嵌入式新流程，在本机跑，硬件层烧到板子上测试；纯逻辑：旧流程，只在电脑上测")
        self.task_combo = QComboBox()
        self.task_combo.setObjectName("Pill")
        self.task_combo.setToolTip("选一道评测题后直接点发送，就跳过需求对话、用题目的需求文档开始运行")
        self.task_combo.setMinimumWidth(self.task_combo.fontMetrics().horizontalAdvance("mpu6050_drv_idf") + 34)
        self.tier_combo = pill_combo(TIERS, "模型")
        self.think_combo = pill_combo(THINK, "思考")
        self.cfg_combo = pill_combo([(c, c) for c in CONFIGS], "配置")
        self.rule_combo = pill_combo(RULE_MODES, "规则")
        self.rule_combo.setToolTip("规则库：用人工确认过的规则，缺了会查资料、等你确认；未核对规则：查资料 Agent 提的规则直接用；"
                                   "不给规则：相当于通用编程 Agent；单 Agent：只限评测题，一个 Agent 只看编译错误")
        send = QPushButton("↑")
        send.setObjectName("Send")
        send.setToolTip("发送（Ctrl+Enter）")
        send.clicked.connect(self.send)
        opts = QWidget()                                 # 选项放不下就换行，不撑大面板的最小宽度
        flow = FlowLayout(opts, spacing=6)
        for w in (self.flow_combo, self.task_combo, self.rule_combo, self.cfg_combo, self.tier_combo, self.think_combo):
            flow.addWidget(w)
        opts.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        cl.addLayout(hbox(opts, send, spacing=6))
        flow_pref = QSettings().value("chat/flow", "embedded")
        self.flow_combo.setCurrentIndex(max(0, self.flow_combo.findData(flow_pref)))
        self.flow_combo.currentIndexChanged.connect(self._flow_changed)
        mid.lay.addWidget(comp)
        # 固定最小宽度会盖过布局算出的最小值，窄了下拉框就压到发送按钮上；取两者较大的
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
        self.board_view = bv.BoardTestView()
        self.rules_view = QTextEdit()
        self.rules_view.setReadOnly(True)
        # 标签名尽量短；面板窄时标签缩短显示（省略号），不用左右箭头（箭头会盖住标签的字）
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setElideMode(Qt.ElideRight)
        for w, name, tip in ((self.layer_box, "检查", "检查结果"), (self.board_view, "上板", "上板测试的用例和串口输出"),
                             (self.code_view, "代码", "这一次交上来的代码"), (self.rules_view, "规则", "这次用到的规则"),
                             (why_box, "调度", "为什么交给这个 Agent"), (self.brief_view, "发给 AI", "发给 AI 的内容")):
            self.tabs.setTabToolTip(self.tabs.addTab(w, name), tip)
        self.why_box = why_box
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
        self._flow_changed()

    @property
    def embedded(self) -> bool:
        return self.flow_combo.currentData() == "embedded"

    def _flow_changed(self, *_):
        """切换流程：评测题列表、规则或配置选择、提示文字、详情区的标签跟着换。"""
        emb = self.embedded
        self.task_combo.clear()
        self.task_combo.addItem("选评测题", "")
        if emb:
            from ..embedded.board_task import discover_board_tasks
            root = self.project / "tasks_board"
            for t in discover_board_tasks(root) if root.is_dir() else []:
                self.task_combo.addItem(t.id, t.id)
            self.input.setPlaceholderText("用一句话说要在板子上写什么，例如\"MAX30102 片内温度读取，调用方传入 I2C 总线句柄，返回摄氏度\"；"
                                          "也可以选一道上板评测题直接发送")
        else:
            from ..tasks import discover_tasks
            for t in discover_tasks(self.project / "tasks") if (self.project / "tasks").is_dir() else []:
                self.task_combo.addItem(t.id, t.id)
            self.input.setPlaceholderText("用一句话说要写什么模块，例如\"STM32F103 上的 Modbus RTU 从机，支持读保持寄存器\"；"
                                          "也可以 /task flash_kv 直接选一道评测题")
        self.rule_combo.setVisible(emb)
        self.cfg_combo.setVisible(not emb)
        self.review_box.setVisible(not emb)
        self.review_label.setVisible(not emb)
        for w in (self.board_view, self.rules_view):
            self.tabs.setTabVisible(self.tabs.indexOf(w), emb)
        self.tabs.setTabVisible(self.tabs.indexOf(self.why_box), not emb)
        QSettings().setValue("chat/flow", self.flow_combo.currentData())
        self.refresh_status()

    def refresh_status(self, run_text: str = ""):
        """底部状态栏：上板流程下先显示板子、ESP-IDF、规则库（DECISIONS 第 100 条），后面跟运行信息。"""
        win = self.window()
        if not hasattr(win, "set_status"):
            return
        head = bv.board_status_text(bv.board_summary(self.project, self.hw)) if self.embedded else ""
        win.set_status(bv.GAP.join(x for x in (head, run_text.replace("　", bv.GAP)) if x))

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
        self._flow_changed()                            # 恢复这个流程的提示文字
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
        if self.embedded:
            if self.backend is None and self.run_dir is None:
                self.add_user(text)
                self.title.setText(text[:24])
                self.start_embedded(spec=text)
            else:
                self.add_line("上板流程运行结束后还不能追问；要改需求就点\"新对话\"重新写。", bg=True)
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
        if cmd == "/task" and self.embedded:
            from ..embedded.board_task import load_board_task
            try:
                task = load_board_task(self.project / "tasks_board" / arg)
            except Exception as exc:
                self.add_line(f"没有上板评测题 {arg}：{exc}", bg=True)
                return
            self.new_chat()
            cfg = self.rule_combo.currentData()
            self.add_user(f"上板评测题 {task.id}：{task.title}，配置 {cfg}")
            self.title.setText(task.title)
            self.start_embedded(task_id=task.id, config_name=cfg)
        elif cmd == "/task":
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

    def start_embedded(self, *, spec: str = "", task_id: str = "", config_name: str = "verified"):
        """上板流程只在本机跑（DECISIONS 第 100 条：Docker Desktop 在 Windows 上不能把 USB 板子交给容器）。"""
        if self.backend is not None:
            self.add_line("上一次运行还没结束。", bg=True)
            return
        mode = self.rule_combo.currentData()
        if not task_id and mode == "single":
            self.add_line("单 Agent 只用于评测题对比。写需求时请选规则库、未核对规则或不给规则。", bg=True)
            return
        self.clear_graph()
        self.judge = None
        self.backend = EmbeddedBackend(self)
        self.backend.event.connect(self.on_event)
        self.backend.ask.connect(self.on_ask)
        self.backend.finished.connect(self.on_finished)
        self.backend.log.connect(lambda s: self.add_line(s, bg=True))
        names = dict(RULE_MODES)
        self.head_pills.setText(f"上板　{names.get(config_name if task_id else mode, mode)}　本机")
        self.stop_btn.setVisible(True)
        kw = dict(project=self.project, hw=self.hw, runs_dir=self.runs_dir, tier=self.tier_combo.currentData() or None,
                  thinking=self.think_combo.currentData() or None)
        try:
            if task_id:
                self.backend.start(task_id=task_id, config_name=config_name, **kw)
            else:
                self.backend.start(spec=spec, name="gui", rule_mode=mode, **kw)
        except Exception as exc:
            from .runner import BOARD_USERS
            BOARD_USERS.discard(self.backend)
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
        j = info.get("judge")
        if j:
            self.judge = j
            v = {"pass": "通过", "fail": "没通过", "skipped": "跳过"}.get(j.get("verdict"), j.get("verdict"))
            text = f"隐藏测试判定：**{v}**，隐藏用例 {j.get('cases_passed')}/{j.get('cases_total')}。"
            if j.get("failed_cases"):
                text += "\n\n没通过的用例：" + "；".join(j["failed_cases"])
            if j.get("note"):
                text += f"\n\n{j['note']}"
            text += "\n\n隐藏测试只给评测判定用，任何 Agent 都看不到。"
            self.add_agent("FirmForge", "reviewer", text)
        self.refresh_status(self._run_text())
        self.run_done.emit()
        self.input.setPlaceholderText("点模块看每一次的检查结果、上板测试和代码；要换需求就点\"新对话\"" if self.embedded else
                                      "可以追问结果，例如\"为什么 pdu 第一次没过\"；也可以提新的修改要求")
        self.refresh_sessions()

    # ------------------------------------------------------------ 事件 → 对话和模块图

    def on_event(self, e: dict):
        if e.get("type") == "run_finished" and not e.get("data", {}).get("cost") and self.run_dir is not None:
            info = self.run_dir / "run.json"           # 旧的运行记录 run_finished 里没有费用，从 run.json 补
            if info.is_file():
                try:
                    e = {**e, "data": {**e.get("data", {}), "cost": json.loads(info.read_text(encoding="utf-8")).get("cost", 0)}}
                except (OSError, ValueError):
                    pass
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
        self.refresh_status(self._run_text())

    def _run_text(self) -> str:
        v = self.view
        if not v.run_id:
            return ""
        elapsed = (v.last_ts - v.started) if v.started else 0
        return (f"运行 {v.run_id}　用时 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}　token {v.tokens:,}"
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
        if self.view.embedded and self._narrate_embedded(t, d, node):
            return
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

    def _narrate_embedded(self, t: str, d: dict, node: str | None) -> bool:
        """新流程的事件怎么讲（DECISIONS 第 100 条）。返回 True 表示已经处理，不再走旧流程的讲法。"""
        if t == "run_started":
            cfg = str(d.get("config", "")).split("/", 1)[-1]
            self.add_line(f"开始运行，规则：{dict(RULE_MODES).get(cfg, cfg)}，板子 {d.get('board', '')}")
        elif t == "rules_started":
            self.add_agent("rules", "rules", f"规则库还缺这些范围的规则：{'、'.join(d.get('todo', []) or d.get('scopes', []))}，"
                           "开始查资料提炼。")
        elif t == "rules_round_done":
            self.add_line(f"规则第 {d.get('round')} 轮：已核对 {d.get('checked')} 条，退回 {d.get('rejected')} 条"
                          + (f"；还缺 {'、'.join(d['missing'])}" if d.get("missing") else ""))
        elif t == "rules_finished":
            self.add_line(f"规则入库 {d.get('trusted')} 条，没表态 {d.get('pending')} 条，退回 {d.get('rejected')} 条")
        elif t == "plan_ready":
            mods = d.get("modules", [])
            rows = "\n".join(f"- `{m['id']}`　{UNIT_NAMES.get(m.get('layer'), m.get('layer', ''))}"
                             + (f"，依赖 {'、'.join(m['depends_on'])}" if m.get("depends_on") else "") for m in mods)
            what = "按题目的公共接口固定设计" if d.get("fixed") else f"拆成 {len(mods)} 个模块"
            self.add_agent("designer", "designer", f"{what}：\n\n{rows}", brief=None if d.get("fixed") else ("call", "designer", node))
        elif t == "verify_finished":
            scope = d.get("scope")
            if scope not in ("compliance", "build", "board_test", "host_test"):
                return True
            mv = self.view.modules.get(node or "")
            ok = d.get("ok")
            if ok and scope in ("compliance",) and (mv is None or mv.unit != "logic"):
                return True                          # 合规通过不单独讲，等编译、测试的结果一起看
            if ok and scope == "build" and mv is not None and mv.unit == "hardware" and self.view.config != "embedded/single":
                return True
            states = [mv.layers.get(k, "") for k in mv.layer_keys] if mv is not None else None
            name = {"compliance": "合规检查", "build": "编译", "board_test": "上板测试", "host_test": "电脑上的单元测试"}[scope]
            if d.get("failed_layer") == "skipped":
                text = f"第 {d.get('attempt')} 次：{name}跳过（没有板子或工具）"
            elif ok:
                text = f"第 {d.get('attempt')} 次：{'编译、合规、上板测试都通过' if scope == 'board_test' else name + '通过'}"
            else:
                text = f"第 {d.get('attempt')} 次：{name}没通过"
            self.add_line(SegBar(None, bool(ok), states), label(node or "", "Mono"), text)
        elif t == "diagnosis":
            blame = BLAME.get(d.get("blame"), d.get("blame"))
            text = f"错在{blame}。{d.get('instruction') or d.get('summary') or ''}"
            if d.get("rule"):
                text += f"\n\n有争议的规则：`{d['rule']}`"
            self.add_agent("diagnoser", "diagnoser", text, brief=("call", "diagnoser", node))
        elif t == "design_rejected":
            self.add_line(f"设计第 {d.get('attempt')} 次没通过检查，带着问题重做：" + "；".join(d.get("problems", [])[:3]), bg=True)
        elif t == "format_retry":
            self.add_line(f"{d.get('agent')} 没按格式交文件（缺 {'、'.join(d.get('missing', []))}），再要一次", bg=True)
        elif t == "human_wait" and node != "rules":
            self.add_agent("FirmForge", "reviewer", f"模块 **{node}** 停下了：{d.get('reason', '')}\n\n{d.get('instruction', '')}")
        elif t == "escalated" and d.get("level") == "rule":
            self.add_line(f"{node} 对规则 {d.get('rule')} 有异议，交给规则判定器重新核对")
        elif t == "run_finished":
            v = d.get("verdict")
            self.add_agent("FirmForge", "reviewer", f"流程结束：**{VERDICT_NAMES.get(v, v)}**。token {int(d.get('tokens') or 0):,}，"
                           f"费用 {float(d.get('cost') or 0):.2f} 元。" + (f"\n\n{d['error']}" if d.get("error") else ""))
        else:
            return False
        return True

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
        elif kind == "rules":
            n, x = len(payload.get("pending", [])), len(payload.get("escalated", []))
            m = self.add_agent("FirmForge", "reviewer", f"规则判定器核对完了：**{n}** 条等你确认" + (f"，{x} 条多次被退回" if x else "")
                               + "。请到硬件页逐条通过或驳回；全部表态后流程才继续。")
            m.add_action("去确认", lambda: self.review_rules.emit(payload, lambda dec: self._rules_done(dec, m)), primary=True)
        elif kind == "approve":
            m = self.add_agent("FirmForge", "reviewer", f"{payload.get('agent')} 想调用外部工具 `{payload.get('tool')}`，参数：\n\n"
                               f"```\n{json.dumps(payload.get('args'), ensure_ascii=False)[:400]}\n```")
            m.add_action("拒绝", lambda: self._resolve(False, m, "你拒绝了"))
            m.add_action("允许", lambda: self._resolve(True, m, "你允许了"), primary=True)
        else:
            return
        self.pending = (kind, payload, m)

    def _rules_done(self, decisions: dict, msg):
        ok = sum(1 for v, _ in decisions.values() if v == "approve")
        no = sum(1 for v, _ in decisions.values() if v == "reject")
        self._resolve(decisions, msg, f"你通过了 {ok} 条，驳回 {no} 条")

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
        names = layer_names(mv.layer_keys)
        for layer in mv.layer_keys:
            st = mv.layers.get(layer, "")
            col = {"pass": T()["pass"], "fail": T()["fail"], "running": T()["run"]}.get(st, T()["line"])
            b = QLabel(f"{names[layer]}\n{ {'pass': '通过', 'fail': '没通过', 'running': '检查中', 'skipped': '跳过'}.get(st, '未检查') }")
            b.setAlignment(Qt.AlignCenter)
            b.setStyleSheet(f"background: {col}; color: white; border-radius: 10px; padding: 5px 4px; font-size: 12px;")
            self.layer_pills.addWidget(b)
        if self.view.embedded:
            self._select_embedded(mid, mv)
            return
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

    def _select_embedded(self, mid: str, mv):
        """新流程：检查报告、上板测试、代码、用到的规则都从运行目录里这一次尝试的文件读。"""
        adir = bv.attempt_dir(self.run_dir, mid, mv.attempt)
        rep = adir / "report.txt" if adir is not None else None
        self.layer_detail.setPlainText(rep.read_text(encoding="utf-8") if rep is not None and rep.is_file()
                                       else "（这一次还没有检查报告）")
        code_dir = bv.latest_attempt_with(self.run_dir, mid, "main", mv.attempt) if mv.attempt else None
        self.code_view.setPlainText(bv.attempt_code(code_dir) or "（还没有代码）")
        log_dir = bv.latest_attempt_with(self.run_dir, mid, "serial.log", mv.attempt) if mv.attempt else None
        self.board_view.show_log(log_dir / "serial.log" if log_dir is not None else None,
                                 f"{mid} 第 {log_dir.name.split('_')[-1]} 次" if log_dir is not None else mid)
        self.rules_view.setPlainText(bv.rules_used_text(self.run_dir, self.project / self.hw / "rules.json"))
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

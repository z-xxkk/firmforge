"""对话工作区（界面草图第 4 版第三节）：左会话列表、中对话、右上模块图 / 流程图、右下详情。

对话和模块图是同一份事件的两种画法：对话按时间讲经过，模块图看全局状态。
两种流程（DECISIONS 第 100 条）：
- 上板：嵌入式新流程，本机运行；可以写一句需求，也可以选一道上板评测题按某种配置跑一次（最后用隐藏测试判定）；
- 纯逻辑：旧流程，docker 或本机。
"""

from __future__ import annotations

import json
import threading
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


def _run_verdict(run_dir: Path) -> str:
    """运行目录里 run.json 记的流程结论；读不出来是空字符串。"""
    try:
        return json.loads((run_dir / "run.json").read_text(encoding="utf-8")).get("verdict", "")
    except (OSError, ValueError, AttributeError):
        return ""


def judge_summary(report: str, flow_verdict: str = "") -> str:
    """历史评测运行的隐藏测试判定写成一句话：取报告里"共 N 个用例，通过 X，失败 Y"那一行（不是最后一行，
    最后几行是调试信息）；流程自己的结论和隐藏测试不一样时说明以哪个为准。"""
    import re
    lines = report.strip().splitlines()
    head = next((x for x in lines if re.match(r"共 \d+ 个用例", x)), "")
    if head:
        m = re.match(r"共 (\d+) 个用例，通过 (\d+)", head)
        ok = bool(m) and m.group(1) == m.group(2) and m.group(1) != "0"
        text = f"隐藏测试判定：**{'通过' if ok else '没通过'}**（{head}）。"
    else:                                          # 跳过、没烧进去之类：报告第一行就是原因
        ok = False
        text = f"隐藏测试判定：{lines[0] if lines else '（没有内容）'}"
    if head and ok and flow_verdict and flow_verdict != "pass":
        text += ("\n\n流程自己的检查没通过（常见原因是 Agent 自己写的测试有错），但交付的代码过了隐藏测试；"
                 "评测以隐藏测试为准。")
    elif head and not ok and flow_verdict == "pass":
        text += "\n\n流程自己的检查通过了，但隐藏测试没过：Agent 自己的测试没覆盖到这些情况。"
    return text + "\n\n隐藏测试只给评测判定用，任何 Agent 都看不到。"


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


MAP_EVENTS = {"plan_ready", "wiring_ready", "wiring_checked", "verify_finished", "node_ready", "node_done",
              "node_failed", "agent_started", "run_finished"}


class RunBlock(QFrame):
    """一段运行过程折叠成一行（DECISIONS 第 107 条，照 Claude、ChatGPT 把思考和工具调用收起来的做法）：
    收起时只显示最新一步和步数，点一下展开看各个 Agent 的消息和工具调用。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RunBlock")
        self.started = time.time()
        self.steps = 0
        self.done = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QPushButton()
        self.head.setObjectName("RunBlockHead")
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.clicked.connect(self.toggle)
        self.head.setToolTip("展开或收起这段过程")
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 6, 0, 6)
        self.body_lay.setSpacing(10)
        self.body.setVisible(False)
        lay.addWidget(self.body)
        self.latest = "开始运行"
        self._paint()

    def add(self, w: QWidget, summary: str) -> None:
        self.body_lay.addWidget(w)
        self.steps += 1
        if summary:
            self.latest = " ".join(summary.split())[:60]
        self._paint()

    def finish(self) -> None:
        self.done = True
        self._paint()

    def toggle(self) -> None:
        self.body.setVisible(not self.body.isVisible())
        self._paint()

    def _paint(self) -> None:
        c = T()
        arrow = "▾" if self.body.isVisible() else "▸"
        if self.done:
            text = f"{arrow}  过程 · {self.steps} 步"
        else:
            text = f"{arrow}  正在：{self.latest}"
        self.head.setText(f"{text}")
        if c is not None:
            dot = c["ink3"] if self.done else c["run"]
            self.head.setStyleSheet(
                f"#RunBlockHead {{ text-align: left; padding: 7px 12px; border: 1px solid {c['line']}; border-radius: 10px;"
                f" background: {c['surface']}; color: {c['ink2'] if self.done else c['ink']}; border-left: 3px solid {dot}; }}")


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
        self.body.setMinimumWidth(10)                     # 诊断里的长代码片段不把对话区撑出横向滚动条
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
            lay.addWidget(p, 1 if p.property("stretch") else 0)     # 标了 stretch 的部件占剩下的宽度
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
    run_done = Signal()                  # 一次运行结束（规则可能入库了，知识库要重新读）
    source_added = Signal(object, str)   # 拖进来的资料登记完（后台线程发出，DECISIONS 第 114 条）

    def __init__(self, project: Path, parent=None):
        super().__init__(parent)
        self.project = project
        self.runs_dir = project / ".ff_runs"
        self.chats_dir = project / ".ff_chats"
        self.view = RunView()
        self.seen_events: list[dict] = []          # 这次运行到现在为止的事件（直播、回放都记），修正过程按它画
        self.run_block: RunBlock | None = None     # 正在往里放过程消息的折叠块
        self._in_process = False
        self.picked_attempt: int | None = None     # 在修正过程里点选的那一次尝试
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
        self.setAcceptDrops(True)                  # 手册拖进对话就登记（DECISIONS 第 114 条）
        self.input.setAcceptDrops(False)           # 输入框不吞掉拖进来的文件
        self.source_added.connect(self._on_source_added)
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
        # 评测、命令行跑出来的运行默认不列（一次评测就几十条，会把对话淹没）；要看再勾上
        self.show_cli_runs = QCheckBox("显示评测和命令行的运行")
        self.show_cli_runs.setObjectName("Faint")
        self.show_cli_runs.setChecked(QSettings().value("chat/show_cli_runs", False, type=bool))
        self.show_cli_runs.toggled.connect(lambda on: (QSettings().setValue("chat/show_cli_runs", on), self.refresh_sessions()))
        left.lay.addWidget(self.show_cli_runs)
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
        # 打开历史运行时直接显示完整记录（和常见的 Agent 软件一样）；想看过程动起来再点"从头回放"（录演示视频用）
        self.replay_btn = QPushButton("从头回放")
        self.replay_btn.setObjectName("Ghost")
        self.replay_btn.setToolTip("按原来的先后顺序重新播放这次运行，可以调速度、拖进度")
        self.replay_btn.clicked.connect(lambda: self.run_dir is not None and
                                        self.start_replay(self.run_dir, append=self.conv is not None))
        self.replay_btn.setVisible(False)
        head.addWidget(self.title)
        head.addWidget(self.head_pills)
        head.addStretch(1)
        head.addWidget(self.replay_btn)
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
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 对话只上下滚，和常见聊天软件一样
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
        # 上板流程：工程图、接线图、流程图共用这一块（DECISIONS 第 113 条）
        self.view_seg_emb = Segmented([("map", "工程图"), ("wiring", "接线图"), ("flow", "流程图")])
        self.view_seg_emb.changed.connect(self._switch_view)
        self.expand_btn = QPushButton("展开")
        self.expand_btn.setObjectName("Ghost")
        self.expand_btn.setToolTip("工程图铺满：会话列表收起、对话收成窄栏，积木里列出接口、连线上写出用到的函数")
        self.expand_btn.clicked.connect(self.toggle_expand)
        self.expanded = False
        gtop.addWidget(self.graph_title)
        gtop.addWidget(self.graph_sub)
        gtop.addStretch(1)
        gtop.addWidget(self.expand_btn)
        gtop.addWidget(self.view_seg)
        gtop.addWidget(self.view_seg_emb)
        gpanel.lay.addLayout(gtop)
        self.graph_stack = QStackedWidget()
        self.graph = ModuleGraph()
        self.graph.module_clicked.connect(self.select_module)
        self.flow = FlowCanvas(editable=False)
        self.graph_stack.addWidget(self.graph)
        self.graph_stack.addWidget(self.flow)
        from .project_map_view import ProjectMapView, WiringView
        self.pmap = ProjectMapView()
        self.pmap.module_clicked.connect(self.select_module)
        self.wiring_view = WiringView()
        self.graph_stack.addWidget(self.pmap)            # 2：工程图
        self.graph_stack.addWidget(self.wiring_view)     # 3：接线图
        self.project_map = None
        self._map_timer = QTimer(self)
        self._map_timer.setSingleShot(True)
        self._map_timer.setInterval(150)                 # 一串事件合并成一次重画
        self._map_timer.timeout.connect(self._refresh_map)
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
        # 拿到 Agent 写的代码：导出到你选的文件夹，或者打开运行目录里的那一份
        code_box = QWidget()
        cbl = QVBoxLayout(code_box)
        cbl.setContentsMargins(0, 8, 0, 0)
        self.export_btn = QPushButton("导出代码…")
        self.export_btn.setToolTip("把这次运行每个模块最后交的代码复制到你选的文件夹")
        self.export_btn.clicked.connect(self.export_code)
        self.open_btn = QPushButton("打开所在文件夹")
        self.open_btn.setObjectName("Ghost")
        self.open_btn.setToolTip("在文件管理器里打开这次运行的目录")
        self.open_btn.clicked.connect(self.open_run_folder)
        cbl.addLayout(hbox(self.export_btn, self.open_btn, "stretch"))
        cbl.addWidget(self.code_view, 1)
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
        self.timeline = bv.RepairTimeline()                 # 修正过程（DECISIONS 第 105 条）
        self.timeline.attempt_clicked.connect(lambda n: self.select_module(self.graph.selected, attempt=n))
        self.rules_view = QTextEdit()
        self.rules_view.setReadOnly(True)
        # 标签名尽量短；面板窄时标签缩短显示（省略号），不用左右箭头（箭头会盖住标签的字）
        self.tabs.tabBar().setUsesScrollButtons(False)
        self.tabs.tabBar().setElideMode(Qt.ElideRight)
        for w, name, tip in ((self.timeline, "过程", "每次尝试的检查结果、诊断和交给谁改"), (self.layer_box, "检查", "检查结果"), (self.board_view, "上板", "上板测试的用例和串口输出"),
                             (code_box, "代码", "这一次交上来的代码；可以导出"), (self.rules_view, "规则", "这次用到的规则"),
                             (why_box, "调度", "为什么交给这个 Agent"), (self.brief_view, "发给 AI", "发给 AI 的内容")):
            self.tabs.setTabToolTip(self.tabs.addTab(w, name), tip)
        self.why_box = why_box
        self.iface_label = label("", "Faint", wrap=True)   # 选中模块的接口概要（不另开"接口"标签，第 113 条）
        self.iface_label.setMinimumWidth(10)
        self.iface_label.setTextFormat(Qt.RichText)
        self.iface_label.setVisible(False)
        ipanel.lay.addWidget(self.iface_label)
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
        self.review_label.setText("设计后等我确认" if emb else "规划后等我确认")    # 两种流程都能停下等确认
        self.view_seg.setVisible(not emb)
        self.view_seg_emb.setVisible(emb)
        self.expand_btn.setVisible(emb)
        self.graph_title.setText("工程" if emb else "模块")
        if emb:
            self.view_seg_emb.set_value("map")
            self._switch_view("map")
        else:
            self.view_seg.set_value("modules")
            self._switch_view("modules")
            if self.expanded:
                self.toggle_expand()
        self.tabs.setTabVisible(self.tabs.indexOf(self.why_box), True)          # 上板流程也经过调度器（第 110 条）
        for w in (self.board_view, self.rules_view):
            self.tabs.setTabVisible(self.tabs.indexOf(w), emb)
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
        if self.runs_dir.is_dir() and self.show_cli_runs.isChecked():
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
            self.open_run(Path(path))
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
            self.open_run(Path(self.conv.runs[-1]), append=True)

    # ------------------------------------------------------------ 消息

    def _add(self, w: QWidget, align=None, summary: str = ""):
        if self._in_process and align is None:
            if self.run_block is None or self.run_block.done:
                self.run_block = RunBlock()
                self.msgs.insertWidget(self.msgs.count() - 1, self.run_block)
            self.run_block.add(w, summary)
            return w
        if self.run_block is not None and not self.run_block.done:
            self.run_block.finish()                      # 主线插进一条（要你决定的事、总结）：前面这段过程收尾，后面另起一段
        if align is not None:
            row = QHBoxLayout()
            row.addStretch(1)
            row.addWidget(w)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(60, 0, 0, 0)
            w = holder
        self.msgs.insertWidget(self.msgs.count() - 1, w)
        QTimer.singleShot(30, self.scroll, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))  # 带接收对象：页面先被销毁时定时器自动取消
        return w

    # ------------------------------------------------------------ 拖进来的资料（DECISIONS 第 114 条）

    @staticmethod
    def _drop_files(md) -> list[Path]:
        from ..grounding.sources import SUFFIX_KIND
        if not md.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in md.urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in SUFFIX_KIND]

    def dragEnterEvent(self, ev):
        if self._drop_files(ev.mimeData()):
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        files = self._drop_files(ev.mimeData())
        if files:
            ev.acceptProposedAction()
            self.register_files(files)

    def register_files(self, files: list[Path]):
        """登记要算指纹、提取文字（上千页的手册要几十秒），放后台线程做，做完由 source_added 回到界面。"""
        for f in files:
            self.add_user(f"（拖入资料）{f.name}")
            self.add_line(f"正在登记 {f.name}：算校验值、提取文字……", bg=True)
            threading.Thread(target=self._register_one, args=(Path(f),), daemon=True).start()

    def _register_one(self, f: Path):
        from ..grounding.sources import register_source
        try:
            # 两份同时拖进来、或者运行正在取资料文字：核心库里有锁，一份一份地写
            doc, added = register_source(self.project / self.hw / "sources.toml", self.project / ".ff_sources" / "raw",
                                         self.project / ".ff_sources" / "library", f)
            self.source_added.emit((f, doc, added), "")
        except Exception as exc:                           # noqa: BLE001  登记失败要告诉用户，不能当成功
            self.source_added.emit((f, None, False), str(exc))

    def _on_source_added(self, res, err: str):
        f, doc, added = res
        if doc is None:
            self.add_agent("FirmForge", "researcher", f"**{f.name}** 没登记上：{err}")
            return
        pages = f"{len(doc.pages)} 页，" if doc.kind == "pdf" else ""
        if not added:
            self.add_agent("FirmForge", "researcher", f"**{doc.title}** 已经登记过了（编号 {doc.id}，校验值相同），不重复登记。")
            return
        self.add_agent("FirmForge", "researcher",
                       f"登记好了：**{doc.title}**（编号 {doc.id}，{pages}校验值 {doc.sha256[:12]}…）。\n\n"
                       f"记在 {self.hw}/sources.toml，等级是\"用户提供\"（不算官方资料）。查资料 Agent 提炼规则时能检索它，"
                       f"从它提炼的规则照样要核对原文、等你确认。")
        self.run_done.emit()                               # 知识库重新读资料

    def add_user(self, text: str):
        self._add(Bubble(text), align="right")

    def add_agent(self, name, role, text, meta="", brief=None) -> AgentMessage:
        m = AgentMessage(name, role, text, meta, brief)
        m.show_brief.connect(self.show_brief)
        self._add(m, summary=f"{ROLE_NAMES.get(role, name)}：{text.splitlines()[0] if text else ''}".replace("*", ""))
        return m

    def add_line(self, *parts, bg=False):
        words = [p if isinstance(p, str) else p.text() for p in parts if isinstance(p, (str, QLabel))]
        return self._add(line_widget(*parts, bg=bg), summary=" ".join(w for w in words if w))

    def clear(self):
        while self.msgs.count() > 1:
            w = self.msgs.takeAt(0).widget()
            if w:
                w.hide()                 # 先藏起来：deleteLater 要等事件循环，期间还会被画出来
                w.setParent(None)
                w.deleteLater()
        self.view = RunView()
        self.seen_events = []
        self.run_block = None
        self.replay_btn.setVisible(False)
        self.graph.refresh(self.view)
        self.pending = None
        self.messages_by_module.clear()

    def new_chat(self):
        if self.backend is not None:
            return
        self.clear()
        self.conv, self.task, self.run_dir = None, None, None
        self.workflow = None                             # 不沿用上一次试运行的工作流草稿
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
        if self.workflow is None or self.workflow.engine != "module":    # 上板流程的工作流纯逻辑流程跑不了
            self.workflow = load_workflow()
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
                wf = self.workflow if (self.workflow is not None and self.workflow.engine == "firmware") else None
                self.backend.start(spec=spec, name="gui", rule_mode=mode, workflow=wf,
                                   human_review=self.review_box.isChecked(), prior_wiring=self._last_wiring(), **kw)
        except Exception as exc:
            from .runner import BOARD_USERS
            BOARD_USERS.discard(self.backend)
            self.add_line(f"没能开始运行：{exc}", bg=True)
            self.backend = None
            self.stop_btn.setVisible(False)

    def _last_wiring(self) -> dict | None:
        """同一个对话（项目）上一次运行定下的接线，交给设计 Agent 沿用（DECISIONS 第 108 条）。"""
        for r in reversed(self.conv.runs if self.conv is not None else []):
            p = Path(r) / "wiring.json"
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if info.get("source") == "design" and info.get("pins"):
                return dict(info["pins"])
        return None

    # ------------------------------------------------------------ 工程图、接线图（DECISIONS 第 109、113 条）

    def _hw_profile(self):
        from ..grounding.profile import HardwareProfile
        key = self.project / self.hw / "profile.json"
        if getattr(self, "_prof_key", None) != key:
            try:
                self._prof = HardwareProfile.load(key)
            except Exception:                              # noqa: BLE001  档案读不了就不画芯片和保留引脚
                self._prof = None
            self._prof_key = key
        return self._prof

    def _refresh_map(self):
        from ..embedded.project_map import build_project_map
        pm = build_project_map(self.run_dir, self._hw_profile()) if (self.embedded and self.run_dir) else None
        self.project_map = pm
        live = {mid: mv.status for mid, mv in self.view.modules.items()}
        self.pmap.set_map(pm, live, self.graph.selected, self.expanded)
        self.wiring_view.set_map(pm)
        if pm is not None:
            s = pm.summary()
            self.graph_sub.setText(f"{s['modules']} 个模块 · 测试过 {s['passed']}/{s['tested']} · "
                                   f"{s['pins']} 个引脚 · {s['devices']} 个器件")
        self._iface_summary(self.graph.selected)

    def _iface_summary(self, mid: str) -> None:
        import html as _h
        m = self.project_map.by_id.get(mid) if (self.project_map is not None and mid) else None
        self.iface_label.setVisible(m is not None)
        if m is None:
            return
        c = T()
        parts = [f"<b>{_h.escape(m.id)}</b>　接口 " + ("、".join(_h.escape(n) for n in m.names) or "（设计里没有）")]
        if m.used_by:
            parts.append("被 " + "、".join(_h.escape(x) for x in m.used_by) + " 用到")
        if m.failing_uses:
            parts.append(f"<span style='color:{c['fail']}'>没过的用例用到 " + "、".join(m.failing_uses) + "</span>")
        self.iface_label.setText("　·　".join(parts))

    def toggle_expand(self):
        """展开：会话列表收起、对话收成窄栏，工程图占满右侧上方；再点恢复（DECISIONS 第 113 条）。"""
        if not self.expanded:
            self._sizes = (self.split.sizes(), self.right_split.sizes())
            total = sum(self._sizes[0])
            self.split.setSizes([0, 260, max(400, total - 260)])
            rt = sum(self._sizes[1])
            self.right_split.setSizes([int(rt * 0.72), rt - int(rt * 0.72)])
            self.expanded = True
        else:
            sizes = getattr(self, "_sizes", None)
            if sizes:
                self.split.setSizes(sizes[0])
                self.right_split.setSizes(sizes[1])
            self.expanded = False
        self.expand_btn.setText("收起" if self.expanded else "展开")
        self._refresh_map()

    def clear_graph(self):
        self.view = RunView()
        self.seen_events = []
        self.picked_attempt = None
        self.project_map = None
        if hasattr(self, "pmap"):
            self.pmap.set_map(None)
            self.wiring_view.set_map(None)
            self.iface_label.setVisible(False)
        self.graph.refresh(self.view)

    def stop(self):
        if self.backend is not None:
            self.backend.stop()
            self.add_line("已请求停止：正在等你确认的地方会马上停；正在调用模型或烧录时，做完这一步就停。", bg=True)

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
        self.seen_events.append(e)
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
        if self.embedded and e.get("type") in MAP_EVENTS:
            self._map_timer.start()
        self.refresh_status(self._run_text())

    def _run_text(self) -> str:
        v = self.view
        if not v.run_id:
            return ""
        elapsed = (v.last_ts - v.started) if v.started else 0
        return (f"运行 {v.run_id}　用时 {int(elapsed // 60):02d}:{int(elapsed % 60):02d}"
                + (f"　结论 {VERDICT_NAMES.get(v.verdict, v.verdict)}" if v.verdict else ""))

    def _update_flow_states(self, e):
        t, d = e.get("type"), e.get("data", {})
        step = {"agent_started": {"planner": "plan", "coder": "code", "tester": "test", "reviewer": "review",
                                  "diagnoser": "repair", "designer": "design", "researcher": "rules"}.get(d.get("role")),
                "rules_started": "rules",
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
        """过程消息放进折叠块；运行结束的总结和"停下等人"放主线（DECISIONS 第 107 条）。"""
        t = e.get("type")
        bad_wiring = (t == "wiring_checked" and any(not x.get("ok") for x in e.get("data", {}).get("devices", []))) \
            or (t == "chip_detected" and bool(e.get("data", {}).get("mismatch")))          # 芯片对不上也要你处理
        self._in_process = t not in ("run_finished", "human_wait") and not bad_wiring     # 接线没应答要你去查板子：放主线
        try:
            self._narrate_one(e)
        finally:
            self._in_process = False
        if t == "run_finished" and self.run_block is not None:
            self.run_block.finish()

    def _narrate_one(self, e: dict):
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
            summary = label(d.get("summary") or ("完成" if d.get("ok") else "失败"), "Faint", wrap=True)
            summary.setMinimumWidth(10)                   # 查询内容很长时换行，不把对话区撑宽
            summary.setProperty("stretch", True)
            who = label(f"{d.get('agent')} 调用工具", "Muted")
            self.add_line(Dot(col), who, label(d.get("tool", ""), "Mono"), summary, bg=True)
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
            self._summary(d)

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
        elif t == "human_wait" and node == "design":
            pass                                         # 对话里会出现"设计好了，请确认"
        elif t == "wiring_ready":
            pins = d.get("pins") or {}
            src = "设计 Agent 按需求定的" if d.get("source") == "design" else "沿用硬件档案的默认接线"
            self.add_line(f"接线（{src}）：" + "，".join(f"{s}=GPIO{g}" for s, g in sorted(pins.items())))
        elif t == "chip_detected":
            if d.get("mismatch"):
                self.add_line(Dot(T()["fail"]), d["mismatch"])
            elif d.get("target"):
                flash = f"，Flash {d['flash_mb']} MB" if d.get("flash_mb") else ""
                self.add_line(Dot(T()["pass"]), f"识别到板子（{d.get('port')}）：{d.get('description')}{flash}，和硬件档案一致")
            else:
                self.add_line(f"没识别出板子上的芯片，照常烧录：{(d.get('error') or '')[-120:]}")
        elif t == "wiring_checked":
            for x in d.get("devices", []):
                mark = "已实测确认" if x.get("ok") else "没应答，请检查接线"
                self.add_line(Dot(T()["pass"] if x.get("ok") else T()["fail"]),
                              f"{x.get('part')}（{x.get('pins')}）：{mark}，{x.get('why')}")
        elif t == "node_assigned" and d.get("role") == "coder":
            sc = d.get("scores") or {}
            self.add_line(f"{node} 第 {d.get('attempt')} 次编码交给 {d.get('agent')}：{sc.get('reason', '')}")
        elif t == "experience_used":
            self.add_line(f"{node} 编码前参考了 {len(d.get('ids', []))} 条过往经验")
        elif t == "review":                              # 审查 Agent（DECISIONS 第 117 条）
            high = [i for i in d.get("issues", []) if i.get("severity") == "high"]
            if d.get("verdict") == "needs_fix" and high:
                self.add_line(f"{node} 审查：{len(high)} 个必须改的问题，交给编码 Agent 改一次："
                              + "；".join(f"{i.get('rule', '')} {i.get('message', '')}".strip() for i in high[:2]))
            else:
                self.add_line(f"{node} 审查：没发现必须改的问题" + (f"（另有 {len(d.get('issues', []))} 条建议）" if d.get("issues") else ""))
        elif t == "review_reverted":
            self.add_line(Dot(T()["run"]), f"{node} 按审查意见改了以后没通过检查，退回改之前通过的版本")
        elif t == "human_reverted":
            self.add_line(Dot(T()["run"]), f"{node} 按你的意见改了以后没通过检查，退回改之前通过的版本；可以换个说法再提")
        elif t == "mutation_test":                             # 变异体补测试（第 125 条）
            if d.get("total"):
                self.add_line(Dot(T()["pass"] if not d.get("survived") else T()["run"]),
                              f"{node} 平台把代码故意改坏 {d['total']} 处，测试发现了 {d.get('killed', 0)} 处"
                              + (f"；没发现的 {d['survived']} 处交给测试 Agent 补测试" if d.get("survived") else ""))
        elif t == "hardened_reverted":
            if d.get("kept") == "pass":                         # 补的测试本身有错，退回（第 131 条）
                self.add_line(Dot(T()["pass"]), f"{node} {d.get('note', '补强测试失败，退回补强前的测试和代码')}")
            else:
                self.add_line(Dot(T()["run"]), f"{node} 补强后的测试没通过，也没修好：交付补强前通过的那一版代码")
        elif t == "app_checked":                               # 主程序上板跑两轮的核对结果（第 131 条）
            if d.get("ok"):
                self.add_line(Dot(T()["pass"]), f"主程序第 {d.get('attempt')} 次：上板跑了两轮（每轮 {d.get('seconds', 0):.0f} 秒），"
                              f"期望的 {len(d.get('matched') or [])} 项输出都看到了，没有崩溃或重启")
            else:
                self.add_line(Dot(T()["fail"]), f"主程序第 {d.get('attempt')} 次上板没通过："
                              + "；".join((d.get("problems") or [])[:3]))
        elif t == "firmware_ready":
            self.add_line(Dot(T()["pass"] if d.get("verdict") == "pass" else T()["run"]),
                          f"完整工程已交付：{d.get('path')}（用 idf.py build flash 就能烧录）")
        elif t == "hardware_check":                            # 诊断说硬件问题时平台的接线自检（第 124 条）
            items = d.get("items") or []
            if d.get("skipped"):
                self.add_line(Dot(T()["run"]), f"{node} 诊断说是硬件问题；接线自检没能运行（{d['skipped'][:80]}），停下等你查")
            elif d.get("ok"):
                self.add_line(Dot(T()["pass"]), f"{node} 诊断说是硬件问题，平台实测接线 {len(items)} 项都正常，改回从代码里找原因")
            else:
                bad = "、".join(i["name"].removeprefix("接线自检：") for i in items if i.get("ok") is False)
                self.add_line(Dot(T()["fail"]), f"{node} 接线自检发现不通：{bad}，停下等你查线")
        elif t == "human_wait" and node != "rules":
            self.add_agent("FirmForge", "reviewer", f"模块 **{node}** 停下了：{d.get('reason', '')}\n\n{d.get('instruction', '')}")
        elif t == "escalated" and d.get("level") == "rule":
            self.add_line(f"{node} 对规则 {d.get('rule')} 有异议，交给规则判定器重新核对")
        elif t == "run_finished":
            self._summary(d)
        else:
            return False
        return True

    def _summary(self, d: dict) -> None:
        """运行结束的总结：结论、每个模块过没过；点按钮看模块的修正过程或导出代码。不讲 token 和费用。"""
        v = d.get("verdict")
        mods = list(self.view.modules.values())
        ok = [m.id for m in mods if m.status == "done"]
        bad = [m.id for m in mods if m.status != "done"]
        lines = [f"**{VERDICT_NAMES.get(v, v)}**" + (f"：{len(ok)} 个模块通过" if mods else "")
                 + (f"，{len(bad)} 个没完成（{'、'.join(bad)}）" if bad else "") + "。"]
        if d.get("error"):
            lines.append(str(d["error"]))
        m = self.add_agent("FirmForge", "reviewer", "\n\n".join(lines))
        if mods:
            first = bad[0] if bad else mods[0].id
            m.add_action("看修正过程", lambda: (self.select_module(first), self.tabs.setCurrentWidget(self.timeline)))
        if self.run_dir is not None:
            m.add_action("导出代码", self.export_code)

    # ------------------------------------------------------------ 需要人回答

    def on_ask(self, kind: str, payload: dict):
        if kind == "plan" and payload.get("embedded"):
            rows = "\n".join(f"- `{x['id']}`　{UNIT_NAMES.get(x.get('layer'), x.get('layer', ''))}"
                             + (f"，依赖 {'、'.join(x['depends_on'])}" if x.get("depends_on") else "")
                             for x in payload.get("modules", []))
            wiring = payload.get("wiring") or {}
            wires = ("\n\n接线：" + "，".join(f"`{s}` → GPIO{g}" for s, g in sorted(wiring.items()))) if wiring else \
                "\n\n接线：沿用硬件档案的默认接线"
            m = self.add_agent("designer", "designer", f"设计好了，请确认：\n\n{rows}{wires}\n\n确认后开始编写；"
                               "有意见（包括改接线）就写在下面输入框里发送，让设计 Agent 重做。")
            m.add_action("提修改意见", lambda: self._focus_input("写下对设计的修改意见，发送后让设计 Agent 重做"))
            m.add_action("确认，开始写", lambda: self._resolve("", m, "你已确认"), primary=True)
        elif kind == "plan":
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
                               + "。就在下面逐条看：规则、引用原文、手册原页并排；全部表态后提交，流程才继续。")
            self._rule_review(payload, m)
        elif kind == "approve":
            m = self.add_agent("FirmForge", "reviewer", f"{payload.get('agent')} 想调用外部工具 `{payload.get('tool')}`，参数：\n\n"
                               f"```\n{json.dumps(payload.get('args'), ensure_ascii=False)[:400]}\n```")
            m.add_action("拒绝", lambda: self._resolve(False, m, "你拒绝了"))
            m.add_action("允许", lambda: self._resolve(True, m, "你允许了"), primary=True)
        else:
            return
        self.pending = (kind, payload, m)

    def _rule_review(self, payload: dict, msg):
        """规则在对话里就地确认（DECISIONS 第 114 条）。"""
        from .rule_review import RuleReview
        try:
            from ..grounding.sources import SourceLibrary
            lib = SourceLibrary.load(self.project / ".ff_sources" / "library")
        except Exception:                                  # noqa: BLE001  资料缓存读不了就不显示原页文字
            lib = None
        self.rule_review = RuleReview(payload, self.project, self.hw, lib)
        self.rule_review.submitted.connect(lambda dec: self._rules_done(dec, msg))
        self._add(self.rule_review)

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
        if kind in ("rules", "approve"):                   # 这两种只能点按钮：打的字不能当成审核结果或"允许"
            where = "上面的规则卡片里逐条点通过或驳回" if kind == "rules" else "上面点「允许」或「拒绝」"
            self.add_line(f"这句话没有发出去：现在要先在{where}。", bg=True)
            return
        self.input.clear()
        self.add_user(text)
        self._resolve(text, msg, "已发送你的意见" if kind != "hint" else "已发送你的提示")

    # ------------------------------------------------------------ 右下详情

    def select_module(self, mid: str, attempt: int | None = None):
        """attempt：在修正过程里点了某一次尝试，下面各标签换成那一次的内容；不给就是最新一次。"""
        if self.graph.selected != mid:
            self.picked_attempt = None
        if attempt is not None:
            self.picked_attempt = attempt
        self.graph.selected = mid
        mv = self.view.modules.get(mid)
        if mv is None:
            return
        steps = bv.repair_timeline(self.seen_events, mid, self.run_dir)
        if self.embedded and self.pmap.selected != mid:
            self.pmap.set_map(self.project_map, {k: v.status for k, v in self.view.modules.items()}, mid, self.expanded)
        self._iface_summary(mid)
        att = self.picked_attempt or mv.attempt
        self.timeline.show_steps(mid, steps, att)
        if att and att != mv.attempt:                    # 看的是以前的某一次：检查结果按那一次的事件显示
            from copy import copy
            st = next((s for s in steps if s.kind == "attempt" and s.attempt == att), None)
            mv = copy(mv)
            mv.attempt = att
            if st is not None and self.view.embedded:
                got = {("test" if k in ("board_test", "host_test") else k): v for k, v in st.layers.items()}
                mv.layers = {k: got.get(k, "") for k in mv.layer_keys}
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

    def export_code(self):
        """导出这次运行的代码（每个模块最后一次交的那份）到你选的文件夹。"""
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        if self.run_dir is None or not bv.final_attempts(self.run_dir):
            QMessageBox.information(self, "还没有代码", "这次运行还没有交出代码。运行结束后，或者在会话列表里点开一次运行，再导出。")
            return
        dest = QFileDialog.getExistingDirectory(self, "把代码导出到哪个文件夹", str(Path.home()))
        if not dest:
            return
        try:
            files = bv.export_run(self.run_dir, Path(dest))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"没能导出到 {dest}：\n{exc}\n\n换一个有写入权限、空间够的文件夹再试。")
            return
        target = Path(dest) / self.run_dir.name
        QMessageBox.information(self, "已导出", f"导出了 {len(files)} 个文件到：\n{target}\n\n每个模块一个子文件夹；"
                                "test/ 或 test_host/ 里是 Agent 自己写的测试。")
        self._open_folder(target)

    def open_run_folder(self):
        if self.run_dir is not None and self.run_dir.is_dir():
            self._open_folder(self.run_dir)

    def _open_folder(self, path: Path):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def show_brief(self, brief, switch: bool = True):
        """brief 是任务单原文，或 ("call", Agent, 模块)：从运行目录的调用记录里找最近一次。"""
        text = ""
        if isinstance(brief, str):
            text = brief
        elif isinstance(brief, tuple) and self.run_dir and (self.run_dir / "llm_calls.jsonl").is_file():
            _, agent_role, node = brief
            best = None
            for line in (self.run_dir / "llm_calls.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    c = json.loads(line)
                    full = self.run_dir / "llm_calls" / f"{c['n']:04d}.json"
                except (ValueError, KeyError, TypeError):
                    continue                      # 运行被强制结束时最后一行可能只写了一半
                if not full.is_file():
                    continue
                if agent_role and not c.get("agent", "").startswith(agent_role[:4]):
                    continue
                best = full
            try:
                data = json.loads(best.read_text(encoding="utf-8")) if best is not None else None
            except ValueError:
                data = None
            if data is not None:
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
        self.graph_stack.setCurrentIndex({"modules": 0, "flow": 1, "map": 2, "wiring": 3}.get(v, 0))
        if v in ("map", "wiring"):
            self._refresh_map()
        if v == "flow" and not self.flow.items_by_id:
            from ..orchestrator.workflow import load_workflow
            want = "firmware" if self.embedded else "module"           # 上板流程看 firmware_dev（第 110 条）
            wf = self.workflow if (self.workflow is not None and self.workflow.engine == want) else None
            self.flow.show_workflow(wf or load_workflow("firmware_dev" if self.embedded else "module_dev"))

    # ------------------------------------------------------------ 回放

    def open_run(self, run_dir: Path, append: bool = False):
        """打开一次历史运行：一下子显示完整的对话记录和最终状态，不做动画（DECISIONS 第 106 条）。"""
        from ..runs import read_events
        if self.backend is not None:
            return
        if self.replay is not None:
            self.replay.pause()
            self.replay = None
        if not append:
            self.clear()
            self.title.setText(f"运行 {run_dir.name}")
        self.clear_graph()
        self.replay_bar.setVisible(False)
        self.run_dir = run_dir
        try:
            events = read_events(run_dir)
        except (OSError, ValueError) as exc:
            self.add_line(f"读不了这次运行的记录：{exc}", bg=True)
            return
        self.setUpdatesEnabled(False)                     # 一次性放进去，最后再画，打开大的运行也不卡
        try:
            for e in events:
                self.on_event(e)
        finally:
            self.setUpdatesEnabled(True)
        if self.run_block is not None:                    # 历史记录不会还在跑（即使当初被强制结束、没有结束事件）
            self.run_block.finish()
        self._map_timer.stop()
        self._refresh_map()
        judge = run_dir / "judge_report.txt"              # 评测题：流程自己的结论之外，还有隐藏测试的判定
        if judge.is_file():
            from PySide6.QtWidgets import QMessageBox
            text = judge.read_text(encoding="utf-8", errors="replace").strip()
            m = self.add_agent("FirmForge", "reviewer", judge_summary(text, _run_verdict(run_dir)))
            m.add_action("看判定报告", lambda: QMessageBox.information(self, "隐藏测试判定", text[:4000]))
        mods = list(self.view.modules.values())
        pick = next((m for m in mods if m.status == "failed"), mods[0] if mods else None)
        if pick is not None:                              # 默认选中出问题的模块（没有就第一个），直接看到修正过程
            self.select_module(pick.id)
            self.tabs.setCurrentWidget(self.timeline)
        self.replay_btn.setVisible(True)
        QTimer.singleShot(30, self.scroll, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))  # 带接收对象：页面先被销毁时定时器自动取消

    def start_replay(self, run_dir: Path, append: bool = False):
        if self.backend is not None:
            return
        self.replay_btn.setVisible(False)
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

"""界面用的纯计算（不依赖 Qt，可以单独测试）：模块图分层排布、流程图排布、事件折算成模块状态。"""

from __future__ import annotations

from dataclasses import dataclass, field

LAYERS = ("host_compile", "cross_compile", "resources", "static_check", "unit_tests")
LAYER_NAMES = {"host_compile": "主机编译", "cross_compile": "交叉编译", "resources": "资源",
               "static_check": "静态检查", "unit_tests": "单元测试", "hidden_tests": "隐藏测试"}
# 嵌入式新流程每次尝试的三段检查（DECISIONS 第 100 条）：合规 → 编译 → 测试（硬件层上板，逻辑层在电脑上）
EMB_LAYERS = ("compliance", "build", "test")
EMB_LAYER_NAMES = {"compliance": "合规", "build": "编译", "test": "测试"}
_EMB_SCOPE = {"compliance": "compliance", "build": "build", "board_test": "test", "host_test": "test"}
UNIT_NAMES = {"hardware": "硬件层，上板", "logic": "逻辑层，电脑上测"}
# 这些事件的 node_id 是流程里的步骤，不是模块，不画进模块图
NOT_MODULES = ("plan", "confirm_plan", "integrate", "design", "rules", "facts")


def layer_names(keys: tuple[str, ...]) -> dict[str, str]:
    return EMB_LAYER_NAMES if keys == EMB_LAYERS else LAYER_NAMES


def layer_depths(nodes: list[str], deps: dict[str, list[str]]) -> dict[str, int]:
    """按依赖深度分层：没有依赖的在第 0 层；有环时环上的节点放在能放的最深一层（不会死循环）。"""
    depth: dict[str, int] = {}
    for _ in range(len(nodes) + 1):
        changed = False
        for n in nodes:
            d = max((depth.get(p, 0) + 1 for p in deps.get(n, []) if p in nodes), default=0)
            d = min(d, len(nodes))
            if depth.get(n) != d:
                depth[n], changed = d, True
        if not changed:
            break
    return depth


def grid_positions(nodes: list[str], deps: dict[str, list[str]], *, w: float = 180, h: float = 96,
                   gap_x: float = 48, gap_y: float = 70) -> dict[str, tuple[float, float]]:
    """模块图：每层一行，同层按出现顺序从左到右，整体水平居中（以 x=0 为中线）。"""
    depth = layer_depths(nodes, deps)
    rows: dict[int, list[str]] = {}
    for n in nodes:
        rows.setdefault(depth[n], []).append(n)
    pos = {}
    for d, row in rows.items():
        total = len(row) * w + (len(row) - 1) * gap_x
        for i, n in enumerate(row):
            pos[n] = (-total / 2 + i * (w + gap_x), d * (h + gap_y))
    return pos


@dataclass
class ModuleView:
    """一个模块在界面上的状态，由事件折算出来。"""
    id: str
    title: str = ""
    deps: list[str] = field(default_factory=list)
    status: str = "pending"          # pending | ready | running | done | failed | skipped
    agent: str = ""
    attempt: int = 0
    level: int = 0                   # 升级级别
    layers: dict[str, str] = field(default_factory=dict)   # 层 -> pass | fail | skipped | running
    note: str = ""
    layer_keys: tuple[str, ...] = LAYERS                   # 这个模块要画的检查段（旧流程 5 层，新流程 3 段）
    unit: str = ""                                         # 新流程：hardware（上板）或 logic（电脑上测）


@dataclass
class RunView:
    """整次运行在界面上的状态。apply(event) 按事件更新；实时和回放共用。"""
    run_id: str = ""
    task: str = ""
    config: str = ""
    modules: dict[str, ModuleView] = field(default_factory=dict)
    verdict: str = ""
    tokens: int = 0
    cost: float = 0.0
    started: float = 0.0
    last_ts: float = 0.0
    agents_busy: dict[str, str] = field(default_factory=dict)    # Agent -> 正在做的模块
    agent_calls: dict[str, int] = field(default_factory=dict)
    assignments: dict[str, dict] = field(default_factory=dict)   # 模块 -> 最近一次 node_assigned 的数据
    embedded: bool = False           # 嵌入式新流程（run_started 的配置以 embedded/ 开头）

    @property
    def layer_keys(self) -> tuple[str, ...]:
        return EMB_LAYERS if self.embedded else LAYERS

    def module(self, mid: str) -> ModuleView:
        if mid not in self.modules:
            self.modules[mid] = ModuleView(mid, layer_keys=self.layer_keys)
        return self.modules[mid]

    def apply(self, e: dict) -> None:
        t, d, node = e.get("type"), e.get("data", {}), e.get("node_id")
        self.last_ts = e.get("ts", self.last_ts)
        if t == "run_started":
            self.run_id, self.task, self.config = e.get("run_id", ""), d.get("task", ""), d.get("config", "")
            self.started = e.get("ts", 0.0)
            self.embedded = str(self.config).startswith("embedded/")
        elif t == "plan_ready":
            keep = {m["id"] for m in d.get("modules", [])}
            for m in d.get("modules", []):
                mv = self.module(m["id"])
                mv.title, mv.deps = m.get("title", ""), list(m.get("depends_on", []))
            if d.get("replan"):
                for mid in list(self.modules):
                    if mid not in keep and self.modules[mid].status not in ("done",):
                        self.modules[mid].status = "skipped"
        elif node and node not in NOT_MODULES and t in (
                "node_ready", "node_assigned", "agent_started", "verify_started", "verify_finished",
                "node_done", "node_failed", "escalated", "rollback"):
            mv = self.module(node)
            if self.embedded and t in ("verify_started", "verify_finished"):
                self._apply_embedded_check(mv, t, d)
            elif t == "node_ready":
                mv.status = "running"
                mv.title = d.get("title", mv.title)
                mv.deps = list(d.get("depends_on", mv.deps))
                mv.unit = d.get("layer", mv.unit)
            elif t == "node_assigned" and d.get("role") in ("coder", None):
                mv.agent = d.get("agent", mv.agent)
                self.assignments[node] = d
            elif t == "agent_started":
                mv.status = "running"
            elif t == "verify_started" and d.get("scope", "module") == "module":
                mv.attempt = int(d.get("attempt") or mv.attempt)
                mv.layers = {LAYERS[0]: "running"}
            elif t == "verify_finished" and d.get("scope", "module") == "module":
                failed = d.get("failed_layer")
                mv.layers = {}
                for layer in LAYERS:
                    if failed is None:
                        mv.layers[layer] = "pass"
                    elif layer == failed:
                        mv.layers[layer] = "fail"
                        break
                    else:
                        mv.layers[layer] = "pass"
            elif t == "node_done":
                mv.status = "done"
            elif t == "node_failed":
                mv.status = "skipped" if d.get("verdict") in ("skipped", "replanned") else "failed"
                mv.note = d.get("reason", "")
            elif t == "escalated":
                mv.level = int(d.get("level") or 0)
        if self.embedded and t == "plan_ready":
            for m in d.get("modules", []):
                self.module(m["id"]).unit = m.get("layer", "")
        if t == "agent_started":
            self.agents_busy[d.get("agent", "")] = node or ""
            self.agent_calls[d.get("agent", "")] = self.agent_calls.get(d.get("agent", ""), 0) + 1
        elif t == "agent_finished":
            self.agents_busy.pop(d.get("agent", ""), None)
            self.tokens += int(d.get("tokens") or 0)
        elif t == "run_finished":
            self.verdict = d.get("verdict", "")
            self.tokens = int(d.get("tokens") or self.tokens)
            self.cost = float(d.get("cost") or self.cost)
            self.agents_busy.clear()
            if self.embedded:                    # 还在"进行中"的模块按整次结论收尾（单 Agent 基线没有 node_done）
                for mv in self.modules.values():
                    if mv.status == "running":
                        mv.status = "done" if self.verdict == "pass" else "failed"
                    mv.layers = {k: v for k, v in mv.layers.items() if v != "running"}


    def _apply_embedded_check(self, mv: ModuleView, t: str, d: dict) -> None:
        """新流程每次尝试：合规 → 编译 → 测试。合规是每次尝试的第一步，看到它就换成新的一次。"""
        key = _EMB_SCOPE.get(d.get("scope", ""))
        if key is None:
            return
        attempt = int(d.get("attempt") or mv.attempt or 1)
        if mv.status == "pending":
            mv.status = "running"                # 单 Agent 基线不发 node_ready，看到检查就算开始了
        if key == "compliance" or attempt != mv.attempt:
            mv.layers = {}
            mv.attempt = attempt
        if t == "verify_started":
            mv.layers[key] = "running"
            return
        failed = d.get("failed_layer")
        mv.layers[key] = "skipped" if failed == "skipped" else ("pass" if d.get("ok") else "fail")
        if key == "test" or failed or self.config == "embedded/single":
            return                               # 单 Agent 基线只编译，没有测试这一步
        nxt = EMB_LAYERS[EMB_LAYERS.index(key) + 1]
        if mv.unit == "logic" and key == "compliance":
            nxt = "test"                         # 逻辑层不编译固件，合规之后直接在电脑上测
            mv.layers["build"] = "skipped"
        mv.layers.setdefault(nxt, "running")


# ---------------------------------------------------------------- 流程图排布

def flow_positions(steps: list, *, x0: float = 0, y0: float = 0, w: float = 150, h: float = 40,
                   gap: float = 26) -> dict[str, tuple[float, float]]:
    """流程图：顶层步骤从上到下排一列；并行分支、条件的"是 / 否"分支排在右侧同一行。用户拖过的位置（pos）优先。"""
    pos: dict[str, tuple[float, float]] = {}
    referenced = {r for s in steps for r in (*s.branches, *s.then, *s.otherwise)}
    by_id = {s.id: s for s in steps}
    y = y0
    for s in steps:
        if s.id in referenced:
            continue
        pos[s.id] = s.pos or (x0, y)
        y = pos[s.id][1] + h + gap
        kids = list(s.branches) or [*s.then, *s.otherwise]
        if kids:
            for i, k in enumerate(kids):
                ks = by_id.get(k)
                if ks is not None:
                    pos[k] = ks.pos or (x0 + (i + 1) * (w + 30), pos[s.id][1])
    for s in steps:                               # 引用了不存在的步骤等情况：兜底放在最下面
        if s.id not in pos:
            pos[s.id] = s.pos or (x0, y)
            y += h + gap
    return pos

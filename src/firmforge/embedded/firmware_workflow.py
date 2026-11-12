"""上板流程的工作流（DECISIONS 第 110 条，轻量接入）：workflows/firmware_dev.toml 决定上板流程走哪些步骤。

执行仍由 embedded/flow.py 负责；这里把工作流翻译成执行参数（FirmwarePlan），并检查设计器画出来的东西
执行代码能不能照做（firmware_problems）。只开放执行代码真正支持的改动，其余在保存前就拦下，
不会出现"图上改了、实际没变"：

主流程（按顺序）：
- 查资料提炼规则（agent，role = researcher）：可删；删了就只用知识库里已有的规则
- 确认规则（human，紧跟在查资料后面）：可删；删了新规则不经人确认就不给编码 Agent
- 设计分层（agent，role = designer）：必须有
- 确认设计（human，在设计之后）：可选；enabled_by = human_review 时只在开了"设计后等我确认"时停下
- 每个模块各跑（foreach，subflow 指向模块流程）：必须有，放在最后

模块流程（按顺序）：
- 编码（agent，role = coder）、写测试（agent，role = tester）：都要有；可以放进同一个并行步骤同时写，
  也可以先编码后写测试；agent 字段指定某个 Agent，不填由调度器按得分挑
- 检查（verify，scope = module）：必须有，在编码和写测试之后——合规 → 编译 → 测试（硬件层上板）
- 修正循环（loop，body = repair_round）：可删；max 是最多尝试几次（含第一次），删了就只试一次
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.workflow import Step, Workflow, WorkflowError

FIRMWARE_ENGINE = "firmware"
DEFAULT_WORKFLOW = "firmware_dev"


@dataclass(frozen=True)
class FirmwarePlan:
    build_rules: bool = True                 # 查资料提炼规则
    confirm_rules: bool = True               # 新规则等人确认
    confirm_design: str = ""                 # ""：不停；"always"：总是停；"human_review"：开了才停
    parallel_write: bool = False             # 编码和写测试同时进行
    coder: str | None = None                 # 指定的编码 Agent；None 由调度器挑
    tester: str | None = None
    max_attempts: int = 6
    source: str = ""                         # 工作流名@版本（写进运行记录）

    def stop_after_design(self, human_review: bool) -> bool:
        return self.confirm_design == "always" or (self.confirm_design == "human_review" and human_review)


def _order(steps) -> list[Step]:
    return Workflow.top_level(tuple(steps))


def firmware_problems(wf: Workflow, registry=None) -> list[str]:
    """设计器画的上板流程，执行代码能不能照做。返回问题列表（空表示没问题）。"""
    problems: list[str] = []
    for s in (*wf.main, *[x for ss in wf.subflows.values() for x in ss]):
        if s.detached:
            problems.append(f"步骤 {s.id} 还没连上：把它拖到连线上或某个步骤的正下方，或者删掉它")
    main = _order(wf.main)
    kinds = []
    for s in main:
        if s.kind == "agent" and s.role == "researcher":
            kinds.append("rules")
        elif s.kind == "agent" and s.role == "designer":
            kinds.append("design")
        elif s.kind == "human":
            kinds.append("human")
        elif s.kind == "foreach":
            kinds.append("foreach")
        else:
            kinds.append("?")
            problems.append(f"主流程的步骤 {s.id}：上板流程的主流程只能放查资料提炼规则、等人确认、设计分层、每个模块各跑")
    if kinds.count("design") != 1:
        problems.append("主流程要有且只有一个设计分层步骤（Agent：designer）")
    if kinds.count("foreach") != 1:
        problems.append("主流程要有且只有一个\"每个模块各跑\"步骤")
    elif kinds[-1] != "foreach":
        problems.append("\"每个模块各跑\"要放在主流程最后")
    if kinds.count("rules") > 1:
        problems.append("主流程最多一个查资料提炼规则步骤")
    if "design" in kinds:
        d = kinds.index("design")
        if "rules" in kinds and kinds.index("rules") > d:
            problems.append("查资料提炼规则要放在设计分层之前（设计要用到规则）")
        for i, k in enumerate(kinds):
            if k != "human":
                continue
            before = kinds[i - 1] if i else ""
            if before not in ("rules", "design"):
                problems.append(f"主流程的步骤 {main[i].id}：等人确认只能紧跟在查资料提炼规则或设计分层后面")
            if main[i].enabled_by not in (None, "human_review"):
                problems.append(f"主流程的步骤 {main[i].id}：等人确认的开关只能是 human_review")
    fe = next((s for s in main if s.kind == "foreach"), None)
    if fe is not None:
        steps = wf.subflows.get(fe.subflow or "", ())
        if not steps:
            problems.append(f"\"每个模块各跑\"指向的模块流程 {fe.subflow} 是空的")
        else:
            problems += _module_problems(fe.subflow, steps, registry)
    return problems


def _module_problems(name: str, steps: tuple[Step, ...], registry) -> list[str]:
    problems: list[str] = []
    by = Workflow.by_id(steps)
    order = _order(steps)
    seen: list[str] = []                       # 按执行顺序：write / check / repair
    roles_seen: list[str] = []
    for s in order:
        if s.kind == "parallel":
            kids = [by[b] for b in s.branches if b in by]
            if not kids:
                problems.append(f"模块流程 {name} 的步骤 {s.id}：并行还没有分支")
            if {k.role for k in kids if k.kind == "agent"} != {"coder", "tester"} or len(kids) != 2:
                problems.append(f"模块流程 {name} 的步骤 {s.id}：上板流程的并行只支持\"编码 + 写测试\"两个分支")
            roles_seen += [k.role for k in kids if k.kind == "agent"]
            seen.append("write")
        elif s.kind == "agent" and s.role in ("coder", "tester"):
            roles_seen.append(s.role)
            seen.append("write")
        elif s.kind == "verify":
            if s.scope != "module":
                problems.append(f"模块流程 {name} 的步骤 {s.id}：检查只能检查本模块（scope = module）")
            seen.append("check")
        elif s.kind == "loop":
            if s.body != "repair_round":
                problems.append(f"模块流程 {name} 的步骤 {s.id}：循环只支持修正循环（body = repair_round）")
            if s.max in (None, ""):
                problems.append(f"模块流程 {name} 的步骤 {s.id}：循环必须有次数上限")
            elif isinstance(s.max, int) and not 1 <= s.max <= 10:
                problems.append(f"模块流程 {name} 的步骤 {s.id}：尝试次数要在 1 到 10 之间")
            seen.append("repair")
        else:
            problems.append(f"模块流程 {name} 的步骤 {s.id}：上板流程的模块流程只能放编码、写测试、并行（编码 + 写测试）、检查、修正循环")
            seen.append("?")
    if sorted(roles_seen) != ["coder", "tester"]:
        problems.append(f"模块流程 {name}：要有且只有一个编码步骤和一个写测试步骤")
    if roles_seen and roles_seen[0] == "tester" and "parallel" not in [s.kind for s in order]:
        problems.append(f"模块流程 {name}：写测试要在编码之后（或者和编码放进同一个并行步骤）")
    if seen.count("check") != 1:
        problems.append(f"模块流程 {name}：要有且只有一个检查步骤")
    elif "write" in seen and seen.index("check") < max(i for i, k in enumerate(seen) if k == "write"):
        problems.append(f"模块流程 {name}：检查要放在编码和写测试之后")
    if seen.count("repair") > 1:
        problems.append(f"模块流程 {name}：最多一个修正循环")
    elif "repair" in seen and "check" in seen and seen.index("repair") < seen.index("check"):
        problems.append(f"模块流程 {name}：修正循环要放在检查之后")
    for s in steps:
        if s.kind == "agent" and s.agent and registry is not None:
            try:
                d = registry.get(s.agent)
            except Exception:                              # noqa: BLE001
                problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 没有注册")
            else:
                if d.role != s.role:
                    problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 的角色是 {d.role}，不是 {s.role}")
    return problems


def firmware_plan(wf: Workflow, settings_loop: dict | None = None, registry=None) -> FirmwarePlan:
    """把（检查过的）上板工作流翻译成执行参数。registry 不给就用默认注册表核对指定的 Agent。"""
    if registry is None:
        from ..agents.registry import default_registry
        registry = default_registry()
    probs = firmware_problems(wf, registry)
    if probs:
        raise WorkflowError(f"上板工作流 {wf.name} {wf.version} 执行不了：" + "；".join(probs))
    main = _order(wf.main)
    kinds = ["rules" if s.kind == "agent" and s.role == "researcher" else
             "design" if s.kind == "agent" and s.role == "designer" else s.kind for s in main]
    confirm_rules = any(k == "human" and i and kinds[i - 1] == "rules" for i, k in enumerate(kinds))
    hd = next((main[i] for i, k in enumerate(kinds) if k == "human" and i and kinds[i - 1] == "design"), None)
    fe = next(s for s in main if s.kind == "foreach")
    steps = wf.subflows[fe.subflow]
    by = Workflow.by_id(steps)
    coder = next(s for s in steps if s.kind == "agent" and s.role == "coder")
    tester = next(s for s in steps if s.kind == "agent" and s.role == "tester")
    parallel = any(s.kind == "parallel" and set(s.branches) >= {coder.id, tester.id} for s in _order(steps))
    loop = next((s for s in _order(steps) if s.kind == "loop"), None)
    attempts = wf.resolve_max(loop, settings_loop or {}) if loop is not None else 1
    del by
    return FirmwarePlan(build_rules="rules" in kinds, confirm_rules=confirm_rules,
                        confirm_design="" if hd is None else ("human_review" if hd.enabled_by == "human_review" else "always"),
                        parallel_write=parallel, coder=coder.agent, tester=tester.agent,
                        max_attempts=max(1, min(10, attempts)), source=f"{wf.name}@{wf.version}")

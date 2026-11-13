"""上板流程的工作流（DECISIONS 第 110、117 条）：workflows/firmware_dev.toml 由解释器（embedded/fw_interpreter.py）逐步执行。

这里检查设计器画出来的东西解释器能不能照做（firmware_problems），不能做的保存前就拦下，不会"图上改了、实际没变"。

主流程（按顺序执行）：
- 查资料提炼规则（agent，role = researcher）：可选，最多一个，要在设计之前；没有就只用知识库里已有的规则
- 等人确认（human）：只能紧跟在查资料后（确认新规则）或设计后（确认设计）；enabled_by = human_review 表示开了开关才停
- 设计分层（agent，role = designer）：必须有且只有一个
- 条件（condition，if = human_review）：开关打开时才走 then 里的步骤
- 每个模块各跑（foreach，subflow 指向模块流程）：必须有且只有一个，放在最后

模块流程（每个模块按顺序执行一遍）：
- 编码（coder）、写测试（tester）：各至少一个；写测试可以排在编码前面（先写测试，只看设计给出的接口）
- 并行（parallel）：只支持"编码 + 写测试"两个分支
- 检查（verify，scope = module）：至少一个；第一个检查之前，编码和写测试都要已经做过
- 修正循环（loop，body = repair_round）：要在某个检查之后；max 是本模块最多检查几次（含第一次），1–10
- 审查（reviewer）：要在某个检查之后；检查通过才审，needs_fix 时最多让编码 Agent 改 1 次，改坏了退回通过的版本
- 等人确认（human）：要在某个检查之后；写意见就交给编码 Agent 改，再走后面的步骤
- 条件（condition）：if 用 verify_passed、hardware_layer、logic_layer、attempts_exhausted、review_needs_fix、human_review
任何步骤都可以带 when（条件不成立就跳过）和 enabled_by = human_review。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..orchestrator.workflow import Step, Workflow, WorkflowError

FIRMWARE_ENGINE = "firmware"
DEFAULT_WORKFLOW = "firmware_dev"
MAIN_CONDITIONS = ("human_review",)
MODULE_CONDITIONS = ("verify_passed", "hardware_layer", "logic_layer", "attempts_exhausted", "review_needs_fix",
                     "human_review")
MODULE_ROLES = ("coder", "tester", "reviewer")


@dataclass(frozen=True)
class FirmwarePlan:
    """评测（不给工作流）用的固定做法；给了工作流时只用来记来源和显示摘要。"""
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


def _main_kind(s: Step) -> str:
    if s.kind == "agent" and s.role == "researcher":
        return "rules"
    if s.kind == "agent" and s.role == "designer":
        return "design"
    return s.kind if s.kind in ("human", "foreach", "condition") else "?"


def firmware_problems(wf: Workflow, registry=None) -> list[str]:
    """设计器画的上板流程，解释器能不能照做。返回问题列表（空表示没问题）。"""
    problems: list[str] = []
    every = (*wf.main, *[x for ss in wf.subflows.values() for x in ss])
    for s in every:
        if s.detached:
            problems.append(f"步骤 {s.id} 还没连上：把它拖到连线上或某个步骤的正下方，或者删掉它")
    by = Workflow.by_id(wf.main)
    main = _order(wf.main)
    kinds = [_main_kind(s) for s in main]
    for s, k in zip(main, kinds):
        if k == "?":
            problems.append(f"主流程的步骤 {s.id}：主流程只能放查资料提炼规则、等人确认、设计分层、条件、每个模块各跑")
        _cond_problems(s, MAIN_CONDITIONS, "主流程", problems)
    for s in main:
        if s.kind == "condition":
            for r in (*s.then, *s.otherwise):
                x = by.get(r)
                if x is not None and _main_kind(x) not in ("human",):
                    problems.append(f"主流程的条件 {s.id}：分支里只能放等人确认")
    if kinds.count("design") != 1:
        problems.append("主流程要有且只有一个设计分层步骤（Agent：designer）")
    if kinds.count("foreach") != 1:
        problems.append("主流程要有且只有一个\"每个模块各跑\"步骤")
    elif kinds[-1] != "foreach":
        problems.append("\"每个模块各跑\"要放在主流程最后")
    if kinds.count("rules") > 1:
        problems.append("主流程最多一个查资料提炼规则步骤")
    if "design" in kinds and "rules" in kinds and kinds.index("rules") > kinds.index("design"):
        problems.append("查资料提炼规则要放在设计分层之前（设计要用到规则）")
    for i, k in enumerate(kinds):
        if k == "human" and (kinds[i - 1] if i else "") not in ("rules", "design"):
            problems.append(f"主流程的步骤 {main[i].id}：等人确认只能紧跟在查资料提炼规则或设计分层后面")
    fe = next((s for s in main if s.kind == "foreach"), None)
    if fe is not None:
        steps = wf.subflows.get(fe.subflow or "", ())
        if not steps:
            problems.append(f"\"每个模块各跑\"指向的模块流程 {fe.subflow} 是空的")
        else:
            problems += module_problems(fe.subflow, steps, registry)
    return problems


def _cond_problems(s: Step, allowed: tuple[str, ...], where: str, problems: list[str]) -> None:
    for key, c in (("if", s.cond), ("when", s.when), ("until", s.until), ("enabled_by", s.enabled_by)):
        if c is None:
            continue
        if key == "enabled_by" and c != "human_review":
            problems.append(f"{where}的步骤 {s.id}：enabled_by 只能是 human_review")
        elif key == "until" and c != "verify_passed":
            problems.append(f"{where}的步骤 {s.id}：循环的 until 只能是 verify_passed")
        elif c not in allowed:
            problems.append(f"{where}的步骤 {s.id}：这里的条件只能用 {'、'.join(allowed)}")


def module_problems(name: str, steps: tuple[Step, ...], registry) -> list[str]:
    problems: list[str] = []
    where = f"模块流程 {name} "
    by = Workflow.by_id(steps)
    refs = [r for s in steps for r in (*s.branches, *s.then, *s.otherwise)]
    for r in sorted({r for r in refs if refs.count(r) > 1}):
        problems.append(f"{where}的步骤 {r} 被引用了不止一次")
    roles = [s.role for s in steps if s.kind == "agent"]
    if "coder" not in roles:
        problems.append(f"{where}要有编码步骤")
    if "tester" not in roles:
        problems.append(f"{where}要有写测试步骤")
    if not any(s.kind == "verify" for s in steps):
        problems.append(f"{where}要有检查步骤")

    # 按执行顺序走到这一步时，一定已经做过的事：coder / tester / verify。在条件分支里、带 when 或 enabled_by 的
    # 步骤不一定执行，不算数——否则检查器放行了，跑起来编码步骤被跳过，修正循环就没有代码可改
    done: set[str] = set()

    def sure(x: Step, nested: bool) -> bool:
        return not nested and not x.when and not x.enabled_by

    def visit(s: Step, nested: bool) -> None:
        _cond_problems(s, MODULE_CONDITIONS, where, problems)
        if s.kind == "agent":
            if s.role not in MODULE_ROLES:
                problems.append(f"{where}的步骤 {s.id}：模块流程里的 Agent 只能是编码、写测试、审查")
                return
            if s.role == "reviewer" and "verify" not in done:
                problems.append(f"{where}的步骤 {s.id}：审查要放在检查之后（检查通过才审）")
            _agent_problems(s, registry, problems)
            if sure(s, nested):
                done.add(s.role)
        elif s.kind == "parallel":
            kids = [by[b] for b in s.branches if b in by]
            if sorted(k.role for k in kids if k.kind == "agent") != ["coder", "tester"] or len(kids) != 2:
                problems.append(f"{where}的步骤 {s.id}：上板流程的并行只支持\"编码 + 写测试\"两个分支")
            for k in kids:
                _cond_problems(k, MODULE_CONDITIONS, where, problems)
                if k.kind == "agent":
                    _agent_problems(k, registry, problems)
                    if sure(s, nested) and sure(k, False):
                        done.add(k.role or "")
        elif s.kind == "verify":
            if s.scope != "module":
                problems.append(f"{where}的步骤 {s.id}：检查只能检查本模块（scope = module）")
            if not {"coder", "tester"} <= done:
                problems.append(f"{where}的步骤 {s.id}：第一次检查之前，编码和写测试都要做过"
                                f"（不能只放在条件分支里或带执行条件）")
            if sure(s, nested):
                done.add("verify")
        elif s.kind == "loop":
            if s.body != "repair_round":
                problems.append(f"{where}的步骤 {s.id}：循环只支持修正循环（body = repair_round）")
            if s.max in (None, ""):
                problems.append(f"{where}的步骤 {s.id}：循环必须有次数上限")
            elif isinstance(s.max, int) and not 1 <= s.max <= 10:
                problems.append(f"{where}的步骤 {s.id}：尝试次数要在 1 到 10 之间")
            if "verify" not in done:
                problems.append(f"{where}的步骤 {s.id}：修正循环要放在检查之后")
        elif s.kind == "human":
            if "verify" not in done:
                problems.append(f"{where}的步骤 {s.id}：等人确认要放在检查之后（要有结果给你看）")
        elif s.kind == "condition":
            if not s.cond:
                problems.append(f"{where}的步骤 {s.id}：条件要有 if")
            for r in (*s.then, *s.otherwise):
                if r in by:
                    if by[r].kind in ("condition", "parallel"):
                        problems.append(f"{where}的条件 {s.id}：分支里不能再套条件或并行")
                    else:
                        visit(by[r], True)
        else:
            problems.append(f"{where}的步骤 {s.id}：上板流程的模块流程只能放编码、写测试、并行、检查、修正循环、审查、"
                            f"等人确认、条件")

    for s in _order(steps):
        visit(s, False)
    return problems


def _agent_problems(s: Step, registry, problems: list[str]) -> None:
    if not s.agent or registry is None:
        return
    try:
        d = registry.get(s.agent)
    except Exception:                              # noqa: BLE001
        problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 没有注册")
    else:
        if d.role != s.role:
            problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 的角色是 {d.role}，不是 {s.role}")


def module_steps(wf: Workflow) -> tuple[Step, ...]:
    fe = next(s for s in _order(wf.main) if s.kind == "foreach")
    return wf.subflows[fe.subflow]


def firmware_plan(wf: Workflow, settings_loop: dict | None = None, registry=None) -> FirmwarePlan:
    """检查工作流并给出摘要（显示用、记来源）。检查不过抛 WorkflowError。"""
    if registry is None:
        from ..agents.registry import default_registry
        registry = default_registry()
    probs = firmware_problems(wf, registry)
    if probs:
        raise WorkflowError(f"上板工作流 {wf.name} {wf.version} 执行不了：" + "；".join(probs))
    main = _order(wf.main)
    kinds = [_main_kind(s) for s in main]
    confirm_rules = any(k == "human" and i and kinds[i - 1] == "rules" for i, k in enumerate(kinds))
    hd = next((main[i] for i, k in enumerate(kinds) if k == "human" and i and kinds[i - 1] == "design"), None)
    steps = module_steps(wf)
    coder = next(s for s in steps if s.kind == "agent" and s.role == "coder")
    tester = next(s for s in steps if s.kind == "agent" and s.role == "tester")
    parallel = any(s.kind == "parallel" and set(s.branches) >= {coder.id, tester.id} for s in _order(steps))
    loop = next((s for s in _order(steps) if s.kind == "loop"), None)
    attempts = wf.resolve_max(loop, settings_loop or {}) if loop is not None else 1
    return FirmwarePlan(build_rules="rules" in kinds, confirm_rules=confirm_rules,
                        confirm_design="" if hd is None else ("human_review" if hd.enabled_by == "human_review" else "always"),
                        parallel_write=parallel, coder=coder.agent, tester=tester.agent,
                        max_attempts=max(1, min(10, attempts)), source=f"{wf.name}@{wf.version}")

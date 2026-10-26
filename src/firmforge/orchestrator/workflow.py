"""工作流模板 workflows/*.toml 的读取、校验、保存与版本（ARCHITECTURE §2.7，DECISIONS 第 62 条）。

节点类型七种：agent、verify、parallel、condition、loop、subflow/foreach、human。
条件只能用预定义名称，不能用 eval。被 parallel 或 condition 引用的步骤只在引用处执行。

桌面软件的设计器和引擎读写的是同一份 TOML（像 Qt Designer 的 .ui 文件）：
- pos、note 只给设计器用（画布位置、说明），引擎忽略；
- 保存新版本时，旧版本另存到 workflows/history/<名字>-<版本>.toml；
- engine_problems() 检查"设计器画出来的东西引擎能不能执行"，设计器保存前和引擎启动时都用它。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import config
from ..agents.registry import ROLES

KINDS = ("agent", "verify", "parallel", "condition", "loop", "subflow", "foreach", "human")
CONDITIONS = ("verify_passed", "signature_repeated", "attempts_exhausted", "review_needs_fix", "human_review")
LOOP_BODIES = ("repair_round",)
SCOPES = ("module", "all")


class WorkflowError(ValueError):
    pass


@dataclass(frozen=True)
class Step:
    id: str
    kind: str
    role: str | None = None
    scope: str | None = None
    branches: tuple[str, ...] = ()
    then: tuple[str, ...] = ()
    otherwise: tuple[str, ...] = ()
    cond: str | None = None          # condition 的 if
    when: str | None = None          # 任何步骤都可以带的执行条件
    until: str | None = None
    body: str | None = None
    max: str | int | None = None
    enabled_by: str | None = None
    over: str | None = None
    subflow: str | None = None
    agent: str | None = None          # agent 步骤指定某个 Agent（不填就由调度器按角色挑）
    note: str | None = None           # 说明（设计器显示）
    pos: tuple[float, float] | None = None   # 画布位置（只给设计器用）


@dataclass(frozen=True)
class Workflow:
    name: str
    version: str
    main: tuple[Step, ...]
    subflows: dict[str, tuple[Step, ...]]
    description: str = ""
    based_on: str = ""                # 改自哪个版本

    @staticmethod
    def top_level(steps: tuple[Step, ...]) -> list[Step]:
        """按顺序执行的步骤：去掉被 parallel / condition 引用的。"""
        referenced = {r for s in steps for r in (*s.branches, *s.then, *s.otherwise)}
        return [s for s in steps if s.id not in referenced]

    @staticmethod
    def by_id(steps: tuple[Step, ...]) -> dict[str, Step]:
        return {s.id: s for s in steps}

    def resolve_max(self, step: Step, settings_loop: dict) -> int:
        if isinstance(step.max, int):
            return step.max
        if isinstance(step.max, str) and step.max.startswith("loop."):
            return int(settings_loop.get(step.max[len("loop."):], 6))
        return 6


def _step(d: dict, where: str) -> Step:
    if not isinstance(d, dict) or not isinstance(d.get("id"), str):
        raise WorkflowError(f"{where}：每个步骤都要有字符串 id")
    sid, kind = d["id"], d.get("kind")
    if kind not in KINDS:
        raise WorkflowError(f"{where} 步骤 {sid}：未知类型 {kind!r}，可选：{', '.join(KINDS)}")
    for key in ("when", "if", "until", "enabled_by"):
        if d.get(key) is not None and d[key] not in CONDITIONS:
            raise WorkflowError(f"{where} 步骤 {sid}：{key} 只能用预定义条件 {', '.join(CONDITIONS)}")
    if kind == "agent" and d.get("role") not in ROLES:
        raise WorkflowError(f"{where} 步骤 {sid}：agent 步骤的 role 必须是 {', '.join(ROLES)} 之一")
    if kind == "verify" and d.get("scope") not in SCOPES:
        raise WorkflowError(f"{where} 步骤 {sid}：verify 的 scope 必须是 module 或 all")
    if kind == "loop" and d.get("body") not in LOOP_BODIES:
        raise WorkflowError(f"{where} 步骤 {sid}：loop 的 body 目前只支持 {', '.join(LOOP_BODIES)}")
    if kind == "condition" and not d.get("if"):
        raise WorkflowError(f"{where} 步骤 {sid}：condition 要有 if")
    if kind in ("foreach", "subflow") and not d.get("subflow"):
        raise WorkflowError(f"{where} 步骤 {sid}：{kind} 要指明 subflow")
    if kind == "parallel" and not d.get("branches"):
        raise WorkflowError(f"{where} 步骤 {sid}：parallel 要有 branches")
    pos = d.get("pos")
    if pos is not None and (not isinstance(pos, list) or len(pos) != 2):
        raise WorkflowError(f"{where} 步骤 {sid}：pos 要写成 [x, y]")
    return Step(id=sid, kind=kind, role=d.get("role"), scope=d.get("scope"),
                branches=tuple(d.get("branches", ())), then=tuple(d.get("then", ())),
                otherwise=tuple(d.get("else", ())), cond=d.get("if"), when=d.get("when"),
                until=d.get("until"), body=d.get("body"), max=d.get("max"),
                enabled_by=d.get("enabled_by"), over=d.get("over"), subflow=d.get("subflow"),
                agent=d.get("agent"), note=d.get("note"), pos=tuple(float(x) for x in pos) if pos else None)


def _check_refs(steps: tuple[Step, ...], where: str, subflows: dict) -> None:
    ids = [s.id for s in steps]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        raise WorkflowError(f"{where}：步骤 id 重复：{', '.join(sorted(dup))}")
    for s in steps:
        for r in (*s.branches, *s.then, *s.otherwise):
            if r not in ids:
                raise WorkflowError(f"{where} 步骤 {s.id} 引用了不存在的步骤 {r}")
        if s.subflow and s.subflow not in subflows:
            raise WorkflowError(f"{where} 步骤 {s.id} 引用了不存在的子流程 {s.subflow}")


def parse_workflow(data: dict, source: str = "") -> Workflow:
    if not data.get("main"):
        raise WorkflowError(f"{source}：缺少 [[main]] 步骤")
    subflows = {name: tuple(_step(x, f"{source} 子流程 {name}") for x in sf.get("steps", []))
                for name, sf in data.get("subflow", {}).items()}
    main = tuple(_step(x, f"{source} main") for x in data["main"])
    _check_refs(main, f"{source} main", subflows)
    for name, steps in subflows.items():
        _check_refs(steps, f"{source} 子流程 {name}", subflows)
    return Workflow(str(data.get("name", "")), str(data.get("version", "")), main, subflows,
                    str(data.get("description", "")), str(data.get("based_on", "")))


def load_workflow(name_or_path: str | Path = "module_dev", version: str | None = None) -> Workflow:
    p = Path(name_or_path)
    if p.suffix != ".toml":
        p = config.workflows_dir() / f"{name_or_path}.toml"
        if version is not None:
            cur = load_workflow(p)
            if cur.version != version:
                p = config.workflows_dir() / "history" / f"{name_or_path}-{version}.toml"
    if not p.is_file():
        raise WorkflowError(f"找不到工作流模板 {p}")
    with p.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise WorkflowError(f"{p} 解析失败：{exc}") from exc
    return parse_workflow(data, p.name)


# ---------------------------------------------------------------- 写回 TOML（设计器保存用）

_STEP_KEYS = (("id", "id"), ("kind", "kind"), ("role", "role"), ("agent", "agent"), ("scope", "scope"),
              ("branches", "branches"), ("cond", "if"), ("then", "then"), ("otherwise", "else"), ("when", "when"),
              ("until", "until"), ("body", "body"), ("max", "max"), ("enabled_by", "enabled_by"), ("over", "over"),
              ("subflow", "subflow"), ("note", "note"), ("pos", "pos"))


def _toml_value(v) -> str:
    import json
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(v, 1) if isinstance(v, float) else v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v), ensure_ascii=False)          # TOML 基本字符串和 JSON 字符串转义规则兼容


def step_toml(step: Step, table: str = "main") -> str:
    lines = [f"[[{table}]]"]
    for attr, key in _STEP_KEYS:
        v = getattr(step, attr)
        if v in (None, (), ""):
            continue
        lines.append(f"{key} = {_toml_value(v)}")
    return "\n".join(lines)


def dump_workflow(wf: Workflow) -> str:
    head = [f"# 工作流 {wf.name} {wf.version}。由 FirmForge 设计器保存；也可以手改，设计器打开时会照着画。",
            f"name = {_toml_value(wf.name)}", f"version = {_toml_value(wf.version)}"]
    if wf.description:
        head.append(f"description = {_toml_value(wf.description)}")
    if wf.based_on:
        head.append(f"based_on = {_toml_value(wf.based_on)}")
    parts = ["\n".join(head)]
    parts += [step_toml(s, "main") for s in wf.main]
    for name, steps in wf.subflows.items():
        parts += [step_toml(s, f"subflow.{name}.steps") for s in steps]
    return "\n\n".join(parts) + "\n"


def save_workflow(wf: Workflow, directory: Path | None = None) -> Path:
    """保存为当前版本；目录里原来的版本（版本号不同）先存进 history/。"""
    directory = Path(directory) if directory else config.workflows_dir()
    path = directory / f"{wf.name}.toml"
    if path.is_file():
        old = load_workflow(path)
        if old.version != wf.version:
            hist = directory / "history"
            hist.mkdir(parents=True, exist_ok=True)
            (hist / f"{old.name}-{old.version}.toml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    text = dump_workflow(wf)
    parse_workflow(tomllib.loads(text), path.name)        # 写之前确认能读回
    path.write_text(text, encoding="utf-8")
    return path


def list_versions(name: str, directory: Path | None = None) -> list[str]:
    directory = Path(directory) if directory else config.workflows_dir()
    out = []
    cur = directory / f"{name}.toml"
    if cur.is_file():
        out.append(load_workflow(cur).version)
    for f in sorted((directory / "history").glob(f"{name}-*.toml")) if (directory / "history").is_dir() else []:
        out.append(f.stem[len(name) + 1:])
    return out


# ---------------------------------------------------------------- 引擎能不能执行

MAIN_KINDS = {"agent": ("planner",), "human": None, "foreach": None, "verify": ("all",)}
SUB_ROLES = ("coder", "tester", "reviewer")


def engine_problems(wf: Workflow, registry=None) -> list[str]:
    """设计器画的流程，引擎能不能执行。返回问题列表（空表示没问题）。"""
    problems: list[str] = []
    if not any(s.kind == "agent" and s.role == "planner" for s in wf.main):
        problems.append("主流程要有一个规划步骤（Agent：planner）")
    if not any(s.kind == "foreach" for s in wf.main):
        problems.append("主流程要有一个\"每个模块各跑\"步骤")
    for s in wf.main:
        if s.kind not in MAIN_KINDS:
            problems.append(f"主流程的步骤 {s.id}：主流程只能放规划、等人确认、每个模块各跑、集成检查")
        elif s.kind == "agent" and s.role != "planner":
            problems.append(f"主流程的步骤 {s.id}：主流程里的 Agent 步骤只能是规划")
        elif s.kind == "verify" and s.scope != "all":
            problems.append(f"主流程的步骤 {s.id}：主流程里的检查只能检查全部模块（scope = all）")
    kinds = [s.kind for s in wf.main]
    if "foreach" in kinds and "agent" in kinds and kinds.index("foreach") < kinds.index("agent"):
        problems.append("\"每个模块各跑\"必须放在规划之后")
    seen_stack: list[str] = []

    def walk(name: str) -> None:
        if name in seen_stack:
            problems.append(f"子流程 {name} 直接或间接地嵌套了自己")
            return
        seen_stack.append(name)
        steps = wf.subflows.get(name, ())
        refs = [r for s in steps for r in (*s.branches, *s.then, *s.otherwise)]
        for r in {r for r in refs if refs.count(r) > 1}:
            problems.append(f"子流程 {name} 的步骤 {r} 被引用了不止一次")
        for s in steps:
            if s.kind == "agent" and s.role not in SUB_ROLES:
                problems.append(f"子流程 {name} 的步骤 {s.id}：子流程里的 Agent 步骤只能是编码、测试、审查")
            elif s.kind == "verify" and s.scope != "module":
                problems.append(f"子流程 {name} 的步骤 {s.id}：子流程里的检查只能检查本模块（scope = module）")
            elif s.kind == "foreach":
                problems.append(f"子流程 {name} 的步骤 {s.id}：\"每个模块各跑\"只能放在主流程")
            elif s.kind == "loop" and s.max in (None, ""):
                problems.append(f"子流程 {name} 的步骤 {s.id}：循环必须有次数上限")
            elif s.kind == "subflow":
                walk(s.subflow)
            if s.kind == "agent" and s.agent and registry is not None:
                try:
                    d = registry.get(s.agent)
                except Exception:
                    problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 没有注册")
                else:
                    if d.role != s.role:
                        problems.append(f"步骤 {s.id} 指定的 Agent {s.agent} 的角色是 {d.role}，不是 {s.role}")
            elif s.kind == "agent" and registry is not None and not registry.find(s.role):
                problems.append(f"步骤 {s.id}：没有注册角色为 {s.role} 的 Agent")
        seen_stack.pop()

    for s in wf.main:
        if s.kind == "foreach":
            walk(s.subflow)
    return problems

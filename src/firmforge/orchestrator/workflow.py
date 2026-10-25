"""工作流模板 workflows/*.toml 的读取与校验（ARCHITECTURE §2.7）。

节点类型七种：agent、verify、parallel、condition、loop、subflow/foreach、human。
条件只能用预定义名称，不能用 eval。被 parallel 或 condition 引用的步骤只在引用处执行。
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


@dataclass(frozen=True)
class Workflow:
    name: str
    version: str
    main: tuple[Step, ...]
    subflows: dict[str, tuple[Step, ...]]

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
    return Step(id=sid, kind=kind, role=d.get("role"), scope=d.get("scope"),
                branches=tuple(d.get("branches", ())), then=tuple(d.get("then", ())),
                otherwise=tuple(d.get("else", ())), cond=d.get("if"), when=d.get("when"),
                until=d.get("until"), body=d.get("body"), max=d.get("max"),
                enabled_by=d.get("enabled_by"), over=d.get("over"), subflow=d.get("subflow"))


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
    return Workflow(str(data.get("name", "")), str(data.get("version", "")), main, subflows)


def load_workflow(name_or_path: str | Path = "module_dev") -> Workflow:
    p = Path(name_or_path)
    if p.suffix != ".toml":
        p = config.workflows_dir() / f"{name_or_path}.toml"
    if not p.is_file():
        raise WorkflowError(f"找不到工作流模板 {p}")
    with p.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise WorkflowError(f"{p} 解析失败：{exc}") from exc
    return parse_workflow(data, p.name)

"""规划结果 dag.json 的解析与校验（ARCHITECTURE §2.7）。

引擎校验：无环、id 唯一、文件不重叠、路径都在 src/ 下、公共函数由且只由一个模块负责、
interface 的头文件属于本模块。不通过时把问题列表交回规划 Agent 重做。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..agents.protocol import check_path
from ..tasks import CATEGORIES, Task

MAX_MODULES = 5
_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_JSON_BLOCK = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.S)


class PlanError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("；".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    task_type: str
    files: tuple[str, ...]
    provides: tuple[str, ...] = ()
    interface: dict[str, tuple[str, ...]] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()

    @property
    def test_file(self) -> str:
        return f"test_{self.id}.c"

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "task_type": self.task_type, "files": list(self.files),
                "provides": list(self.provides), "interface": {k: list(v) for k, v in self.interface.items()},
                "depends_on": list(self.depends_on), "acceptance": list(self.acceptance)}


@dataclass
class Plan:
    modules: list[Module]
    fallback: bool = False           # 规划 Agent 多次不合格，退化为单模块

    def __post_init__(self):
        self.by_id = {m.id: m for m in self.modules}

    def to_dict(self) -> dict:
        d = {"modules": [m.to_dict() for m in self.modules]}
        if self.fallback:
            d["fallback"] = True
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def dependents(self, mid: str) -> list[str]:
        return [m.id for m in self.modules if mid in m.depends_on]

    def transitive_deps(self, mid: str) -> list[str]:
        """mid 依赖的全部模块（按拓扑序）。"""
        seen: set[str] = set()

        def walk(x: str) -> None:
            for d in self.by_id[x].depends_on:
                if d not in seen:
                    seen.add(d)
                    walk(d)
        walk(mid)
        return [m for m in self.topo_order() if m in seen]

    def topo_order(self) -> list[str]:
        order: list[str] = []
        done: set[str] = set()

        def visit(x: str) -> None:
            if x in done:
                return
            for d in self.by_id[x].depends_on:
                visit(d)
            done.add(x)
            order.append(x)
        for m in self.modules:
            visit(m.id)
        return order

    def critical_path(self) -> dict[str, int]:
        """每个模块到终点的最长路径长度（含自己），用于关键路径优先。"""
        memo: dict[str, int] = {}

        def length(x: str) -> int:
            if x not in memo:
                memo[x] = 1 + max((length(d) for d in self.dependents(x)), default=0)
            return memo[x]
        return {m.id: length(m.id) for m in self.modules}


# ---------------------------------------------------------------- 公共函数清单

def _strip_c(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    prev = None
    while prev != text:                         # 去掉结构体、枚举等花括号块
        prev, text = text, re.sub(r"\{[^{}]*\}", " ", text)
    return text


def declared_functions(header_text: str) -> list[str]:
    names = []
    for stmt in _strip_c(header_text).split(";"):
        s = " ".join(stmt.split())
        if "(" not in s or s.startswith("typedef") or "(*" in s.replace(" ", ""):
            continue
        m = re.search(r"([A-Za-z_]\w*)\s*\($", s[: s.index("(") + 1])
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def is_hal_header(name: str) -> bool:
    """驱动类任务的 HAL 接口头文件：函数由外部（测试替身）提供，不需要模块实现。"""
    return name.startswith("hal_")


def public_functions(task: Task) -> list[str]:
    names: list[str] = []
    if not task.include_dir.is_dir():
        return names
    for h in sorted(task.include_dir.glob("*.h")):
        if is_hal_header(h.name):
            continue
        for n in declared_functions(h.read_text(encoding="utf-8")):
            if n not in names:
                names.append(n)
    return names


# ---------------------------------------------------------------- 解析与校验

def extract_json(text: str) -> dict:
    """从回复中取出 JSON 对象：优先 ```json 代码块，否则取第一个 { 到最后一个 }。"""
    candidates = [m.group(1) for m in _JSON_BLOCK.finditer(text)]
    if not candidates and "{" in text:
        candidates = [text[text.index("{"): text.rindex("}") + 1]]
    errors = []
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError as exc:
            errors.append(str(exc))
            continue
        if isinstance(data, dict):
            return data
        errors.append("内容不是 JSON 对象")
    raise PlanError([f"没有找到可解析的 JSON 对象（{errors[0]}）" if errors else "回复里没有 JSON 代码块"])


def _str_list(v, what: str, problems: list[str], *, allow_empty: bool = True) -> tuple[str, ...]:
    if v is None:
        v = []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        problems.append(f"{what} 必须是字符串列表")
        return ()
    if not allow_empty and not v:
        problems.append(f"{what} 不能为空")
    return tuple(x.strip() for x in v)


def parse_plan(data: dict, task: Task, max_modules: int = MAX_MODULES) -> Plan:
    problems: list[str] = []
    raw = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        raise PlanError(["顶层必须是 {\"modules\": [...]}，且至少有 1 个模块"])
    if len(raw) > max_modules:
        problems.append(f"模块数 {len(raw)} 超过上限 {max_modules}")

    modules: list[Module] = []
    for i, m in enumerate(raw, 1):
        if not isinstance(m, dict):
            problems.append(f"第 {i} 个模块不是 JSON 对象")
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not _ID.fullmatch(mid):
            problems.append(f"第 {i} 个模块的 id {mid!r} 不合法（只用小写字母、数字、下划线，字母开头）")
            mid = f"#{i}"
        where = f"模块 {mid}"
        title = m.get("title") if isinstance(m.get("title"), str) else ""
        task_type = m.get("task_type")
        if task_type not in CATEGORIES:
            problems.append(f"{where} 的 task_type {task_type!r} 不在 {', '.join(CATEGORIES)} 之中")
        files = []
        for f in _str_list(m.get("files"), f"{where} 的 files", problems, allow_empty=False):
            norm, why = check_path(f, ("src",))
            if norm is None:
                problems.append(f"{where} 的文件 {f}：{why}")
            elif norm not in files:
                files.append(norm)
        iface: dict[str, tuple[str, ...]] = {}
        raw_iface = m.get("interface") or {}
        if not isinstance(raw_iface, dict):
            problems.append(f"{where} 的 interface 必须是 {{头文件路径: [函数原型, ...]}}")
            raw_iface = {}
        for path, protos in raw_iface.items():
            norm, _ = check_path(path, ("src",))
            if norm is None or PurePosixPath(norm).suffix != ".h":
                problems.append(f"{where} 的 interface 键 {path} 必须是 src/ 下的 .h 文件")
                continue
            if norm not in files:
                problems.append(f"{where} 的 interface 头文件 {norm} 必须列在它自己的 files 里")
            iface[norm] = _str_list(protos, f"{where} 的 interface[{path}]", problems)
        modules.append(Module(
            id=mid, title=title, task_type=task_type if task_type in CATEGORIES else task.category,
            files=tuple(files), provides=_str_list(m.get("provides"), f"{where} 的 provides", problems),
            interface=iface, depends_on=_str_list(m.get("depends_on"), f"{where} 的 depends_on", problems),
            acceptance=_str_list(m.get("acceptance"), f"{where} 的 acceptance", problems, allow_empty=False)))

    ids = [m.id for m in modules]
    for dup in sorted({x for x in ids if ids.count(x) > 1}):
        problems.append(f"模块 id {dup} 重复")
    owner: dict[str, str] = {}
    for m in modules:
        for f in m.files:
            if f in owner and owner[f] != m.id:
                problems.append(f"文件 {f} 同时属于模块 {owner[f]} 和 {m.id}")
            owner.setdefault(f, m.id)
    public = public_functions(task)
    provided: dict[str, list[str]] = {}
    for m in modules:
        for fn in m.provides:
            provided.setdefault(fn, []).append(m.id)
            if fn not in public:
                problems.append(f"模块 {m.id} 的 provides 里的 {fn} 不是公共头文件里的函数")
    for fn in public:
        who = provided.get(fn, [])
        if not who:
            problems.append(f"公共函数 {fn} 没有模块负责（写进某个模块的 provides）")
        elif len(who) > 1:
            problems.append(f"公共函数 {fn} 被多个模块负责：{', '.join(who)}")
    known = set(ids)
    for m in modules:
        for d in m.depends_on:
            if d == m.id:
                problems.append(f"模块 {m.id} 依赖了自己")
            elif d not in known:
                problems.append(f"模块 {m.id} 依赖的 {d} 不存在")
    if not problems:
        cycle = _find_cycle(modules)
        if cycle:
            problems.append("存在循环依赖：" + " → ".join(cycle))
    if problems:
        raise PlanError(problems)
    return Plan(modules)


def _find_cycle(modules: list[Module]) -> list[str] | None:
    deps = {m.id: m.depends_on for m in modules}
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(x: str) -> list[str] | None:
        state[x] = 1
        stack.append(x)
        for d in deps[x]:
            if state.get(d) == 1:
                return stack[stack.index(d):] + [d]
            if state.get(d) is None:
                found = visit(d)
                if found:
                    return found
        stack.pop()
        state[x] = 2
        return None
    for m in modules:
        if state.get(m.id) is None:
            found = visit(m.id)
            if found:
                return found
    return None


def plan_from_text(text: str, task: Task, max_modules: int = MAX_MODULES) -> Plan:
    return parse_plan(extract_json(text), task, max_modules)


def single_module_plan(task: Task) -> Plan:
    """规划多次不合格时的退路：整个任务作为一个模块。"""
    mid = re.sub(r"[^a-z0-9_]", "_", task.id.lower()).strip("_") or "module"
    if not mid[0].isalpha():
        mid = "m_" + mid
    return Plan([Module(id=mid, title=task.title, task_type=task.category if task.category in CATEGORIES
                        else "data_structure", files=(f"src/{mid}.c",), provides=tuple(public_functions(task)),
                        acceptance=("满足需求文档中的全部要求",))], fallback=True)

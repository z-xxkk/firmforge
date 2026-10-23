"""评测任务加载（tasks/<id>/task.toml）。"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FORBIDDEN_SYMBOLS = (
    "malloc", "calloc", "realloc", "free", "aligned_alloc",
    "printf", "sprintf", "fprintf", "puts", "putchar",
)

CATEGORIES = ("data_structure", "protocol", "state_machine", "driver")
MUTANT_LAYERS = ("host_compile", "cross_compile", "resources", "static_check", "hidden_tests")


class TaskError(ValueError):
    """task.toml 内容不合法。"""


@dataclass(frozen=True)
class Budget:
    flash_bytes: int | None = None
    ram_bytes: int | None = None
    stack_bytes: int | None = None


@dataclass(frozen=True)
class Mutant:
    file: str
    replaces: str
    expect_fail: str
    why: str = ""


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    category: str
    difficulty: int
    source: str
    root: Path
    budget: Budget = field(default_factory=Budget)
    extra_forbidden_symbols: tuple[str, ...] = ()
    allowed_symbols: tuple[str, ...] = ()
    mutants: tuple[Mutant, ...] = ()
    spec_file: Path | None = None      # 临时任务（run --spec）用，覆盖 root/spec.md
    include_path: Path | None = None   # 临时任务（run --include）用，覆盖 root/include

    @property
    def spec_path(self) -> Path:
        return self.spec_file or self.root / "spec.md"

    @property
    def include_dir(self) -> Path:
        return self.include_path or self.root / "include"

    @property
    def hidden_tests_dir(self) -> Path:
        # 只有评测流程（eval、check-task）可以读这个目录
        return self.root / "hidden_tests"

    @property
    def reference_dir(self) -> Path:
        return self.root / "reference"

    @property
    def forbidden_symbols(self) -> tuple[str, ...]:
        names = [s for s in DEFAULT_FORBIDDEN_SYMBOLS if s not in self.allowed_symbols]
        names += [s for s in self.extra_forbidden_symbols if s not in names]
        return tuple(names)


def _opt_int(table: dict, key: str) -> int | None:
    value = table.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise TaskError(f"budget.{key} 必须是非负整数")
    return value


def load_task(path: Path | str) -> Task:
    """从任务目录（或其中的 task.toml）加载任务。"""
    path = Path(path)
    root = path.parent if path.name == "task.toml" else path
    toml_path = root / "task.toml"
    if not toml_path.is_file():
        raise TaskError(f"找不到 {toml_path}")
    with toml_path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise TaskError(f"{toml_path} 解析失败：{exc}") from exc

    for key in ("id", "title", "category", "difficulty"):
        if key not in data:
            raise TaskError(f"{toml_path} 缺少字段 {key}")
    if data["category"] not in CATEGORIES:
        raise TaskError(f"未知类别 {data['category']}，可选：{', '.join(CATEGORIES)}")

    b = data.get("budget", {})
    budget = Budget(_opt_int(b, "flash_bytes"), _opt_int(b, "ram_bytes"), _opt_int(b, "stack_bytes"))
    rules = data.get("rules", {})

    mutants = []
    for m in data.get("mutants", []):
        if m.get("expect_fail") not in MUTANT_LAYERS:
            raise TaskError(f"变异体 {m.get('file')} 的 expect_fail 必须是 {', '.join(MUTANT_LAYERS)} 之一")
        mutants.append(Mutant(m["file"], m["replaces"], m["expect_fail"], m.get("why", "")))

    return Task(
        id=data["id"], title=data["title"], category=data["category"],
        difficulty=int(data["difficulty"]), source=data.get("source", ""),
        root=root.resolve(), budget=budget,
        extra_forbidden_symbols=tuple(rules.get("extra_forbidden_symbols", [])),
        allowed_symbols=tuple(rules.get("allowed_symbols", [])),
        mutants=tuple(mutants),
    )


def adhoc_task(spec: Path | str, include: Path | str) -> Task:
    """由一份需求文档和一个头文件目录构成的临时任务：没有隐藏测试、参考实现和预算。"""
    spec, include = Path(spec).resolve(), Path(include).resolve()
    if not spec.is_file():
        raise TaskError(f"找不到需求文档 {spec}")
    if not include.is_dir() or not list(include.glob("*.h")):
        raise TaskError(f"头文件目录 {include} 不存在或里面没有 .h 文件")
    tid = re.sub(r"[^A-Za-z0-9_]+", "_", spec.stem).strip("_") or "module"
    title = tid
    for line in spec.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    return Task(id=tid, title=title, category="adhoc", difficulty=0, source="命令行临时任务",
                root=spec.parent, spec_file=spec, include_path=include)


def discover_tasks(tasks_dir: Path | str) -> list[Task]:
    """加载目录下所有含 task.toml 的任务，按 id 排序。"""
    tasks_dir = Path(tasks_dir)
    found = [load_task(p.parent) for p in sorted(tasks_dir.glob("*/task.toml"))]
    return sorted(found, key=lambda t: t.id)

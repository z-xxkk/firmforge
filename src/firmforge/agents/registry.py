"""Agent 注册表（ARCHITECTURE §2.5、§5.3）。

    registry.load_dir("agents/")
    registry.register(agent_def)
    registry.unregister(name)
    registry.find(role, task_type) -> list[AgentDef]
    registry.get(name, version=None)

同名的不同版本可以共存，默认取最新版本。能力画像来自 SQLite 的 agent_stats 表。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import config

ROLES = ("planner", "coder", "tester", "reviewer", "diagnoser")


class RegistryError(ValueError):
    """Agent 定义不合法，或找不到要用的 Agent。"""


def _version_key(v: str) -> tuple:
    parts = []
    for x in v.split("."):
        parts.append((0, int(x), "") if x.isdigit() else (1, 0, x))
    return tuple(parts)


@dataclass(frozen=True)
class AgentDef:
    name: str
    role: str
    version: str
    model_tier: str
    prompt: str                      # 相对项目根目录的路径，如 prompts/coder_module.md
    temperature: float = 0.2
    max_tokens: int | None = None    # 不填时用模型档位的 max_tokens
    task_types: tuple[str, ...] = ("*",)
    description: str = ""

    def handles(self, task_type: str) -> bool:
        return "*" in self.task_types or task_type in self.task_types

    @property
    def prompt_name(self) -> str:
        return Path(self.prompt).name


def parse_agent(data: dict, source: str = "") -> AgentDef:
    where = f"（{source}）" if source else ""
    for key in ("name", "role", "version", "model_tier", "prompt"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise RegistryError(f"Agent 定义缺少字段 {key}{where}")
    if data["role"] not in ROLES:
        raise RegistryError(f"未知角色 {data['role']}{where}，可选：{', '.join(ROLES)}")
    types = data.get("task_types", ["*"])
    if not isinstance(types, list) or not types:
        raise RegistryError(f"task_types 必须是非空列表{where}")
    mt = data.get("max_tokens")
    return AgentDef(name=data["name"], role=data["role"], version=data["version"],
                    model_tier=data["model_tier"], prompt=data["prompt"],
                    temperature=float(data.get("temperature", 0.2)),
                    max_tokens=int(mt) if mt else None, task_types=tuple(types),
                    description=data.get("description", ""))


class Registry:
    def __init__(self):
        self._defs: dict[str, dict[str, AgentDef]] = {}     # name -> version -> def
        self._order: list[str] = []                         # 注册顺序

    def register(self, d: AgentDef) -> None:
        if d.name not in self._defs:
            self._defs[d.name] = {}
            self._order.append(d.name)
        self._defs[d.name][d.version] = d

    def unregister(self, name: str) -> None:
        self._defs.pop(name, None)
        if name in self._order:
            self._order.remove(name)

    def load_dir(self, path: Path | str | None = None) -> "Registry":
        path = Path(path) if path else config.agents_dir()
        files = sorted(Path(path).glob("*.toml"))
        if not files:
            raise RegistryError(f"{path} 下没有 Agent 定义（*.toml）")
        for f in files:
            with f.open("rb") as fh:
                try:
                    data = tomllib.load(fh)
                except tomllib.TOMLDecodeError as exc:
                    raise RegistryError(f"{f} 解析失败：{exc}") from exc
            self.register(parse_agent(data, f.name))
        return self

    def get(self, name: str, version: str | None = None) -> AgentDef:
        versions = self._defs.get(name)
        if not versions:
            raise RegistryError(f"没有注册 Agent {name}")
        if version is None:
            return versions[max(versions, key=_version_key)]
        if version not in versions:
            raise RegistryError(f"Agent {name} 没有版本 {version}，已有：{', '.join(sorted(versions))}")
        return versions[version]

    def find(self, role: str, task_type: str = "*") -> list[AgentDef]:
        """按注册顺序返回某角色、适用某类任务的 Agent（每个名字取最新版本）。"""
        found = []
        for name in self._order:
            d = self.get(name)
            if d.role == role and (task_type == "*" or d.handles(task_type)):
                found.append(d)
        return found

    def names(self) -> list[str]:
        return list(self._order)


def default_registry() -> Registry:
    return Registry().load_dir()

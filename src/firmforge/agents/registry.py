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

ROLES = ("planner", "coder", "tester", "reviewer", "diagnoser", "analyst", "researcher", "rule_checker")


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
    skills: tuple[str, ...] = ()               # 擅长什么（给人看，也给规划和调度参考）
    toolsets: tuple[str, ...] = ()             # 能用的工具集或单个工具（DECISIONS 第 63、64 条）
    memory_experience: bool = True             # 上下文里放不放经验库条目
    memory_knowledge: bool = True              # 上下文里放不放知识库条目
    thinking: str | None = None                # 思考强度覆盖：off | low | high | max；None 跟随档位

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
    for key in ("skills", "toolsets"):
        v = data.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise RegistryError(f"{key} 必须是字符串列表{where}")
    memory = data.get("memory", {})
    if not isinstance(memory, dict) or any(k not in ("experience", "knowledge") for k in memory):
        raise RegistryError(f"memory 只能写 experience、knowledge 两项{where}")
    thinking = data.get("thinking")
    if thinking is not None and thinking not in ("off", "low", "high", "max"):
        raise RegistryError(f"thinking 只能是 off、low、high、max{where}")
    return AgentDef(name=data["name"], role=data["role"], version=data["version"],
                    model_tier=data["model_tier"], prompt=data["prompt"],
                    temperature=float(data.get("temperature", 0.2)),
                    max_tokens=int(mt) if mt else None, task_types=tuple(types),
                    description=data.get("description", ""), skills=tuple(data.get("skills", [])),
                    toolsets=tuple(data.get("toolsets", [])),
                    memory_experience=bool(memory.get("experience", True)),
                    memory_knowledge=bool(memory.get("knowledge", True)), thinking=thinking)


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

    def versions(self, name: str) -> list[str]:
        return sorted(self._defs.get(name, {}), key=_version_key)

    def check_toolsets(self, known: set[str]) -> None:
        """Agent 写了不存在的工具集或工具名时报错，指出是哪个 Agent。"""
        for name in self._order:
            for d in self._defs[name].values():
                bad = [t for t in d.toolsets if t not in known and not t.startswith("mcp-")]
                if bad:
                    raise RegistryError(f"Agent {d.name} {d.version} 的 toolsets 里有不存在的工具：{', '.join(bad)}")


def default_registry() -> Registry:
    return Registry().load_dir()


# ---------------------------------------------------------------- 写回（桌面软件"Agent 团队"页用）

def dump_agent(d: AgentDef) -> str:
    from ..config_edit import toml_value
    lines = [f"# Agent {d.name} {d.version}。由 FirmForge 桌面软件保存，也可以手改。",
             f"name = {toml_value(d.name)}", f"role = {toml_value(d.role)}", f"version = {toml_value(d.version)}",
             f"model_tier = {toml_value(d.model_tier)}", f"temperature = {toml_value(d.temperature)}",
             f"prompt = {toml_value(d.prompt)}", f"task_types = {toml_value(list(d.task_types))}",
             f"description = {toml_value(d.description)}", f"skills = {toml_value(list(d.skills))}",
             f"toolsets = {toml_value(list(d.toolsets))}",
             f"memory = {toml_value({'experience': d.memory_experience, 'knowledge': d.memory_knowledge})}"]
    if d.max_tokens:
        lines.append(f"max_tokens = {d.max_tokens}")
    if d.thinking:
        lines.append(f"thinking = {toml_value(d.thinking)}")
    return "\n".join(lines) + "\n"


def save_agent(d: AgentDef, directory: Path | None = None) -> Path:
    """保存为当前版本；目录里原来的版本（版本号不同）先存进 history/。"""
    directory = Path(directory) if directory else config.agents_dir()
    path = directory / f"{d.name}.toml"
    text = dump_agent(d)
    parse_agent(tomllib.loads(text), path.name)          # 写之前确认能读回
    if path.is_file():
        with path.open("rb") as fh:
            old = parse_agent(tomllib.load(fh), path.name)
        if old.version != d.version:
            hist = directory / "history"
            hist.mkdir(parents=True, exist_ok=True)
            (hist / f"{old.name}-{old.version}.toml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    return path

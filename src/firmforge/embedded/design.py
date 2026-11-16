"""设计分层 Agent（DECISIONS 第 66 条第 ④ 步）：把需求拆成模块，并分出硬件层与逻辑层。

- 硬件层：直接操作外设和外接器件，受硬件规则约束，要上板测试；scopes 写明涉及哪些外设和器件，
  编码时只把这些范围的规则放进 system prompt（第 67 条）。
- 逻辑层：协议解析、状态机、算法等和硬件无关的部分，可以在电脑上做单元测试；不涉及任何外设。

程序检查（不合格就带着问题退回，最多重做 2 次，沿用规划 Agent 的做法）：
1. 模块编号唯一、只用小写字母数字下划线；层只能是 hardware / logic；
2. 文件都在 main/ 下（写相对路径），只能是 .c / .h，不同模块不能写同一个文件；每个模块有 include/<编号>.h；
3. 硬件层至少有一个 scope，而且必须是硬件档案里用到的外设或器件；逻辑层不能有 scope；
4. ★ 逻辑层不能依赖硬件层（否则就没法在电脑上测）；依赖必须存在、不能成环；
5. provides 里的函数原型能被解析出函数名。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..agents.brief import Brief
from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet
from ..orchestrator.dag import declared_functions, extract_json
from .prompting import system_prompt

LAYERS = ("hardware", "logic")
MAX_MODULES = 8
_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


@dataclass(frozen=True)
class DesignModule:
    id: str
    title: str
    layer: str
    files: tuple[str, ...]
    provides: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()

    @property
    def header(self) -> str:
        return f"include/{self.id}.h"

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "layer": self.layer, "files": list(self.files),
                "provides": list(self.provides), "scopes": list(self.scopes), "depends_on": list(self.depends_on),
                "acceptance": list(self.acceptance)}


@dataclass
class Design:
    modules: list[DesignModule]
    app: str = ""                       # app_main 里做什么（集成说明）
    by_id: dict[str, DesignModule] = field(default_factory=dict)
    wiring: dict[str, int] = field(default_factory=dict)   # 这个项目的接线（DECISIONS 第 108 条）；空表示沿用硬件档案的默认接线
    connections: list = field(default_factory=list)       # 完整连线（第 134 条）：每根线另一端、为什么、自检应测到什么

    def __post_init__(self):
        self.by_id = {m.id: m for m in self.modules}

    def order(self) -> list[DesignModule]:
        """按依赖排好的顺序（被依赖的在前）。"""
        done, out = set(), []
        while len(out) < len(self.modules):
            for m in self.modules:
                if m.id not in done and all(d in done for d in m.depends_on):
                    done.add(m.id)
                    out.append(m)
        return out

    def to_dict(self) -> dict:
        out = {"modules": [m.to_dict() for m in self.modules], "app": self.app}
        if self.connections:
            out["wiring"] = [c.to_dict() for c in self.connections]
        elif self.wiring:
            out["wiring"] = dict(self.wiring)
        return out

    def devices_used(self, profile: HardwareProfile) -> set[str]:
        """硬件层模块的 scopes 里写到的外接器件（按器件型号对上，例如 mpu6050 → imu）。"""
        scopes = {s for m in self.modules if m.layer == "hardware" for s in m.scopes}
        return {d.name for d in profile.devices if d.part.lower() in scopes or d.name in scopes}


def _strs(v, what: str, problems: list[str]) -> tuple[str, ...]:
    if v is None:
        return ()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        problems.append(f"{what} 必须是字符串数组")
        return ()
    return tuple(x.strip() for x in v if x.strip())


def parse_design(data: dict, profile: HardwareProfile, max_modules: int = MAX_MODULES) -> tuple[Design | None, list[str]]:
    problems: list[str] = []
    raw = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return None, ["缺少 modules 数组"]
    if len(raw) > max_modules:
        problems.append(f"模块太多（{len(raw)} 个），最多 {max_modules} 个")
    known_scopes = profile.design_scopes()
    mods, owner = [], {}
    for i, m in enumerate(raw, 1):
        if not isinstance(m, dict):
            problems.append(f"第 {i} 个模块不是对象")
            continue
        mid = str(m.get("id", "")).strip()
        tag = mid or f"第 {i} 个模块"
        if not _ID.match(mid):
            problems.append(f"{tag}：编号只能用小写字母、数字、下划线，字母开头")
        layer = m.get("layer")
        if layer not in LAYERS:
            problems.append(f"{tag}：layer 只能是 hardware 或 logic")
        files = _strs(m.get("files"), f"{tag}.files", problems)
        for f in files:
            if f.startswith(("/", "..", "main/")) or "/../" in f or not f.endswith((".c", ".h")):
                problems.append(f"{tag}：文件 {f} 不合格（写 main/ 下的相对路径，只能是 .c/.h，不要带 main/ 前缀）")
            if f == "include/hw_config.h":
                problems.append(f"{tag}：include/hw_config.h 由程序按硬件档案生成，不能写")
            if f in owner and owner[f] != mid:
                problems.append(f"文件 {f} 同时属于 {owner[f]} 和 {mid}")
            owner.setdefault(f, mid)
        if mid and f"include/{mid}.h" not in files:
            problems.append(f"{tag}：必须有公共头文件 include/{mid}.h")
        scopes = tuple(s.lower() for s in _strs(m.get("scopes"), f"{tag}.scopes", problems))
        if layer == "hardware":
            if not scopes:
                problems.append(f"{tag}：硬件层必须写 scopes（涉及哪些外设和器件）")
            bad = [s for s in scopes if s not in known_scopes]
            if bad:
                problems.append(f"{tag}：scopes {', '.join(bad)} 不在硬件档案里（可用：{', '.join(sorted(known_scopes))}）")
        elif layer == "logic" and scopes:
            problems.append(f"{tag}：逻辑层不能涉及外设，scopes 必须为空")
        provides = _strs(m.get("provides"), f"{tag}.provides", problems)
        for p in provides:
            if not declared_functions(p if p.rstrip().endswith(";") else p + ";"):
                problems.append(f"{tag}：provides 里的 {p!r} 不是合法的函数原型")
        mods.append(DesignModule(mid, str(m.get("title", "")).strip() or mid, str(layer), files, provides, scopes,
                                 _strs(m.get("depends_on"), f"{tag}.depends_on", problems),
                                 _strs(m.get("acceptance"), f"{tag}.acceptance", problems)))
    ids = [m.id for m in mods]
    for d in sorted({x for x in ids if ids.count(x) > 1}):
        problems.append(f"模块编号重复：{d}")
    by = {m.id: m for m in mods}
    for m in mods:
        for d in m.depends_on:
            if d not in by:
                problems.append(f"{m.id} 依赖的模块 {d} 不存在")
            elif m.layer == "logic" and by[d].layer == "hardware":
                problems.append(f"逻辑层 {m.id} 不能依赖硬件层 {d}（逻辑层要能在电脑上测试）")
    if not problems and _has_cycle(mods):
        problems.append("模块依赖成环")
    raw = data.get("wiring") if isinstance(data, dict) else None
    conns = []
    if raw is not None and not problems:                    # 完整连线（第 134 条）；不写就沿用板上现有的接线
        from .wiring import parse_connections
        conns, more = parse_connections(raw, profile, Design(mods).devices_used(profile))
        problems += more
    if problems:
        return None, problems
    return Design(mods, str(data.get("app", "")).strip(), wiring={c.signal: c.gpio for c in conns},
                  connections=conns), []


def _has_cycle(mods: list[DesignModule]) -> bool:
    deps = {m.id: set(m.depends_on) for m in mods}
    done: set[str] = set()
    while deps:
        ready = [k for k, v in deps.items() if v <= done]
        if not ready:
            return True
        for k in ready:
            done.add(k)
            deps.pop(k)
    return False


def design_from_text(text: str, profile: HardwareProfile) -> tuple[Design | None, list[str]]:
    try:
        data = extract_json(text)
    except ValueError as exc:
        return None, [str(exc)]
    return parse_design(data, profile)


def designer_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None,
                      where: str = "", prior_wiring=None) -> list[dict]:
    system = system_prompt("designer.md", profile, rules, None, max_modules=str(MAX_MODULES))
    b = Brief(intent="把需求拆成模块，分出硬件层和逻辑层", location=where or "设计 › 设计分层")
    b.constraints += [f"最多 {MAX_MODULES} 个模块", "逻辑层不能依赖硬件层",
                      f"开发框架：{profile.framework.value} {profile.framework_version.value}",
                      "引脚和器件地址一律用 hw_config.h 里的宏（程序按硬件档案生成）",
                      "硬件层的 scopes 只能从这些里选，按模块实际用到的外设写（片内温度传感器写 temp_sensor，"
                      "不要写成 gpio）：" + ", ".join(sorted(profile.design_scopes()))]
    b.attach("需求文档", requirement)
    from .wiring import board_wiring_lines
    lines = board_wiring_lines(profile)
    if lines:                                          # 板上现在已经接着的线（第 134 条：以前设计看不到跳线，选了被占用的脚）
        b.attach("板上现在已经接着的线（不要把这些 GPIO 挪去接别的东西；沿用同一个信号可以）", "\n".join(f"- {x}" for x in lines))
    if prior_wiring:                                   # 同一个项目上一次的接线：需求没要求改就沿用（第 108 条）
        b.attach("这个项目上一次的接线（需求没要求改就照抄进 wiring，缺的 to、why、expect 补上）",
                 json.dumps(prior_wiring, ensure_ascii=False))
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def designer_retry_message(problems: list[str]) -> str:
    return ("你的设计没有通过检查，请修正后重新输出完整的 JSON：\n" + "\n".join(f"- {p}" for p in problems))


def design_json(design: Design) -> str:
    return json.dumps(design.to_dict(), ensure_ascii=False, indent=2)

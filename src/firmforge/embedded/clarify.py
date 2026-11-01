"""需求澄清：产出需求文档和硬件档案（DECISIONS 第 66 条第 ① 步）。

需求澄清 Agent 只负责弄清楚三件事，写成固定格式：
- 用哪块板子（必须是 hardware/ 下已有的板子）；
- 接了哪些外接器件（型号、总线、地址）；
- 需要哪些信号（如 i2c0.sda、uart1.tx、imu.int）。
不清楚的关键信息（芯片、器件型号、接法）要先问用户，回复里带 questions 就不出档案。

★ 引脚不让模型分配：程序从板子引出的空闲引脚里按顺序分配，避开保留引脚和已用引脚；
板子模板里已经分配过的信号沿用原来的引脚（接线不用改）。分配结果记为"默认值"来源，由用户确认。
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..agents.brief import Brief
from ..agents.context import load_prompt
from ..grounding.profile import Device, Fact, HardwareProfile, ProfileError
from ..orchestrator.dag import extract_json

_SIGNAL = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$|^[a-z][a-z0-9_]*$")
_SPEC = re.compile(r"^<<<FILE[ \t]+spec\.md[ \t]*\n(.*?)^>>>FILE[ \t]*$", re.M | re.S)


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    exposed: tuple[int, ...]
    prefer: tuple[int, ...]
    template: HardwareProfile

    @classmethod
    def load(cls, hwdir: Path) -> "Board":
        hwdir = Path(hwdir)
        meta = tomllib.loads((hwdir / "board.toml").read_text(encoding="utf-8"))
        return cls(hwdir.name, meta.get("name", hwdir.name), tuple(meta["exposed"]),
                   tuple(meta.get("prefer", meta["exposed"])), HardwareProfile.load(hwdir / "profile.json"))


def list_boards(root: Path) -> dict[str, Board]:
    return {p.name: Board.load(p) for p in sorted(Path(root).iterdir())
            if (p / "board.toml").is_file() and (p / "profile.json").is_file()}


@dataclass
class Clarified:
    spec: str = ""
    board: str = ""
    devices: list[Device] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)


def parse_clarified(text: str) -> tuple[Clarified | None, list[str]]:
    out, problems = Clarified(), []
    m = _SPEC.search(text)
    out.spec = m.group(1).strip() if m else ""
    try:
        data = extract_json(text)
    except ValueError as exc:
        return None, [str(exc)]
    out.questions = [str(q).strip() for q in data.get("questions", []) if str(q).strip()]
    if out.questions:
        return out, []
    if not out.spec:
        problems.append("缺少需求文档（<<<FILE spec.md … >>>FILE）")
    out.board = str(data.get("board", "")).strip()
    if not out.board:
        problems.append("缺少 board")
    for i, d in enumerate(data.get("devices", []), 1):
        try:
            addr = d.get("address")
            out.devices.append(Device(str(d["name"]).strip(), str(d["part"]).strip(), str(d["bus"]).strip(),
                                      int(addr, 0) if isinstance(addr, str) else addr))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            problems.append(f"第 {i} 个器件不合格：{exc}")
    for s in data.get("signals", []):
        s = str(s).strip().lower()
        if not _SIGNAL.match(s):
            problems.append(f"信号名 {s!r} 不合格（写成 外设.功能 或 器件.功能，如 i2c0.sda、imu.int）")
        elif s not in out.signals:
            out.signals.append(s)
    return (out, []) if not problems else (None, problems)


def allocate(board: Board, clarified: Clarified) -> tuple[HardwareProfile, list[str]]:
    """按板子模板生成硬件档案：沿用模板里已分配的信号，新信号从空闲引脚里按顺序分配。"""
    t = board.template
    problems = []
    pins: dict[str, Fact] = {}
    used = set()
    for s in clarified.signals:
        if s in t.pins:
            pins[s] = t.pins[s]
            used.add(t.pins[s].value)
    free = [g for g in board.prefer if g in board.exposed and g not in t.reserved_pins and g not in used]
    for s in clarified.signals:
        if s in pins:
            continue
        if not free:
            problems.append(f"没有空闲引脚可以分给 {s}")
            continue
        g = free.pop(0)
        pins[s] = Fact(g, "default", "程序按空闲引脚自动分配，请确认接线")
    prof = replace(t, pins=pins, devices=tuple(clarified.devices))
    try:
        problems += prof.problems()
    except ProfileError as exc:
        problems.append(str(exc))
    return prof, problems


def clarify_messages(conversation: list[dict], boards: dict[str, Board]) -> list[dict]:
    """conversation：用户与需求澄清 Agent 之前的对话（user / assistant 交替）。"""
    listing = "\n".join(f"- {b.id}：{b.name}（芯片 {b.template.chip.value}，{b.template.framework.value} "
                        f"{b.template.framework_version.value}）" for b in boards.values()) or "（没有可用的板子）"
    system = load_prompt("analyst_idf.md").safe_substitute(boards=listing)
    return [{"role": "system", "content": system}, *conversation]


def wiring_table(profile: HardwareProfile) -> str:
    """给用户看的接线表。"""
    rows = [f"- {sig}：GPIO{f.value}" + ("（自动分配）" if f.origin == "default" else "")
            for sig, f in sorted(profile.pins.items())]
    return "\n".join(rows)


def confirm_brief(profile: HardwareProfile, spec: str) -> Brief:
    b = Brief(intent="请确认需求文档和硬件档案", location="需求澄清 › 确认")
    b.attach("需求文档", spec)
    b.attach("硬件档案", "\n".join(profile.summary_lines()))
    b.attach("接线表", wiring_table(profile))
    return b

"""项目（DECISIONS 第 134 条第 2 阶段，ARCHITECTURE §15.1）：对话页里每个上板对话是一个项目，有自己的目录，
彼此不相通——就像各自独立的进程。

    .ff_projects/<项目>/project.json
        title     对话标题（需求开头）
        specs     发过的需求，按先后
        runs      这个项目跑过的运行目录
        wiring    这个项目的完整连线（embedded/wiring.py 的 Connection.to_dict()）
        status    每根线的状态：{信号: {"state": 计划|已接|实测|不符, "detail": 实测值或说明, "at": 时间}}

板子只有一块，记录不能代替实物：状态只是上次的结论，每次开工都要重新自检（第 3 阶段）。
状态只能由平台改（自检、负责人在界面上确认），Agent 只能通过设计提出新的接线，提出时状态回到"计划"。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROJECTS_DIR = ".ff_projects"
STATES = ("计划", "已接", "实测", "不符")


@dataclass
class Project:
    path: Path
    id: str
    title: str = ""
    specs: list[str] = field(default_factory=list)
    runs: list[str] = field(default_factory=list)
    wiring: list[dict] = field(default_factory=list)
    status: dict[str, dict] = field(default_factory=dict)
    created: float = 0.0

    # ------------------------------------------------------------ 读写

    @classmethod
    def create(cls, root: Path | str, title: str = "") -> "Project":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        pid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        p = cls(root / pid, pid, title=title.strip()[:60], created=time.time())
        p.path.mkdir()
        p.save()
        return p

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        path = Path(path)
        d = json.loads((path / "project.json").read_text(encoding="utf-8"))
        return cls(path, d.get("id", path.name), d.get("title", ""), list(d.get("specs", [])), list(d.get("runs", [])),
                   list(d.get("wiring", [])), dict(d.get("status", {})), float(d.get("created", 0.0)))

    def save(self) -> None:
        data = {"id": self.id, "title": self.title, "created": self.created, "specs": self.specs, "runs": self.runs,
                "wiring": self.wiring, "status": self.status}
        tmp = self.path / "project.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path / "project.json")

    # ------------------------------------------------------------ 内容

    def add_spec(self, spec: str) -> None:
        self.specs.append(spec)
        if not self.title:
            self.title = " ".join(spec.split())[:60]
        self.save()

    def add_run(self, run_dir: Path | str) -> None:
        r = str(Path(run_dir).resolve())
        if r not in self.runs:
            self.runs.append(r)
            self.save()

    def set_wiring(self, connections: list[dict]) -> None:
        """设计定下（或改了）接线：改动过的线状态回到"计划"，没动的线保留上次的状态（开工时照样重新自检）。"""
        old = {c.get("signal"): c for c in self.wiring}
        status = {}
        for c in connections:
            sig = c.get("signal")
            same = old.get(sig) is not None and {k: old[sig].get(k) for k in ("gpio", "to", "expect")} == \
                {k: c.get(k) for k in ("gpio", "to", "expect")}
            status[sig] = self.status.get(sig) if same and sig in self.status else \
                {"state": "计划", "detail": "", "at": time.time()}
        self.wiring = [dict(c) for c in connections]
        self.status = status
        self.save()

    def set_state(self, signal: str, state: str, detail: str = "") -> None:
        if state not in STATES:
            raise ValueError(f"状态只能是 {STATES}")
        if signal not in {c.get("signal") for c in self.wiring}:
            raise KeyError(f"这个项目没有信号 {signal}")
        self.status[signal] = {"state": state, "detail": detail, "at": time.time()}
        self.save()

    def state(self, signal: str) -> str:
        return (self.status.get(signal) or {}).get("state", "计划")


def list_projects(root: Path | str = DEFAULT_PROJECTS_DIR) -> list[Project]:
    """最近动过的在前；读不了的目录跳过。"""
    out = []
    root = Path(root)
    if not root.is_dir():
        return out
    for d in root.iterdir():
        if (d / "project.json").is_file():
            try:
                out.append(Project.load(d))
            except (OSError, ValueError):
                continue
    out.sort(key=lambda p: (p.path / "project.json").stat().st_mtime, reverse=True)
    return out

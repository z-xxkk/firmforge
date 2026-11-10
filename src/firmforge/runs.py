"""运行目录 .ff_runs/<run_id>/（ARCHITECTURE §2.9）。

    run.json  events.jsonl  llm_calls.jsonl  llm_calls/<n>.json
    nodes/<module>/attempt_<n>/   src/ tests/ report.json   ← 快照
    final/   src/ tests/ report.json report.md
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

DEFAULT_RUNS_DIR = ".ff_runs"
SNAPSHOT_DIRS = ("src", "tests")


class RunDir:
    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def create(cls, root: Path | str, task_id: str, now: float | None = None) -> "RunDir":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
        base = f"{stamp}-{task_id}"
        path, i = root / base, 2
        while path.exists():
            path, i = root / f"{base}-{i}", i + 1
        path.mkdir()
        return cls(path)

    @property
    def run_id(self) -> str:
        return self.path.name

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def final_dir(self) -> Path:
        return self.path / "final"

    def attempt_dir(self, module: str, n: int) -> Path:
        return self.path / "nodes" / module / f"attempt_{n}"

    def write_json(self, name: str, data: dict) -> Path:
        p = self.path / name
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def read_json(self, name: str) -> dict:
        return json.loads((self.path / name).read_text(encoding="utf-8"))


def copy_snapshot(src: Path, dst: Path) -> None:
    """复制工作目录中的 src/ 与 tests/（不含 .ff_build 等构建产物）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for d in SNAPSHOT_DIRS:
        if (src / d).is_dir():
            shutil.copytree(src / d, dst / d, dirs_exist_ok=True)


def read_events(run_dir: Path | str) -> list[dict]:
    path = Path(run_dir) / "events.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"{run_dir} 里没有 events.jsonl，不是运行目录")
    events = []
    lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    for i, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except ValueError:
            if i == len(lines) - 1:          # 最后一行写到一半就被强制结束：丢掉这一行，前面的照常回放
                break
            raise
    return events

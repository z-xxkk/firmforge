"""运行与事件来源：本机后端（后台线程直接调核心库）、docker 后端（容器里跑命令行）、回放。

界面只读事件（ARCHITECTURE §2.8），三种来源发出同样的 event(dict) 信号，所以实时显示和回放走同一套代码。
需要人回答的地方（确认规划、等人确认步骤、转人工提示、外部工具调用前确认）统一发 ask(kind, payload)，
界面在对话里显示问题，回答后调用 answer(...)：
- 本机后端：后台线程在 threading.Event 上等这个回答；
- docker 后端：把回答当作"在终端里打字"写进容器的标准输入（DECISIONS 第 62 条，方案 A）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QThread, QTimer, Signal

from .. import config


class EventFollower(QObject):
    """每 0.2 秒读一次运行目录里新增的 events.jsonl 行。"""
    event = Signal(dict)

    def __init__(self, runs_parent: Path, since: float, parent=None):
        super().__init__(parent)
        self.parent_dir, self.since = Path(runs_parent), since
        self.run_dir: Path | None = None
        self._pos = 0
        self._buf = ""
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(200)

    def _find(self) -> Path | None:
        if not self.parent_dir.is_dir():
            return None
        cands = [p for p in self.parent_dir.iterdir() if (p / "events.jsonl").is_file()
                 and p.stat().st_mtime >= self.since - 1]
        return max(cands, key=lambda p: p.stat().st_mtime) if cands else None

    def poll(self) -> None:
        if self.run_dir is None:
            self.run_dir = self._find()
            if self.run_dir is None:
                return
        path = self.run_dir / "events.jsonl"
        try:
            with path.open("r", encoding="utf-8") as f:
                f.seek(self._pos)
                chunk = f.read()
                self._pos = f.tell()
        except OSError:
            return
        self._buf += chunk
        *lines, self._buf = self._buf.split("\n")
        for ln in lines:
            if ln.strip():
                try:
                    self.event.emit(json.loads(ln))
                except json.JSONDecodeError:
                    pass

    def stop(self) -> None:
        self.poll()
        self.timer.stop()


class _Worker(QThread):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.result = None
        self.error = ""

    def run(self):
        try:
            self.result = self.fn()
        except BaseException as exc:          # 包括中断
            self.error = f"{type(exc).__name__}: {exc}"


class Backend(QObject):
    event = Signal(dict)
    ask = Signal(str, dict)          # kind: plan | checkpoint | hint | approve
    finished = Signal(dict)          # {"verdict", "run_dir", "error"}
    log = Signal(str)

    def answer(self, value) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class LocalBackend(Backend):
    """在本机后台线程里直接调用核心库（需要本机装好工具链）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answer = None
        self._got = threading.Event()
        self.worker: _Worker | None = None
        self._manager = None

    def _wait(self, kind: str, payload: dict):
        self._got.clear()
        self.ask.emit(kind, payload)
        self._got.wait()
        return self._answer

    def answer(self, value) -> None:
        self._answer = value
        self._got.set()

    def start(self, task, cfg_name: str, *, runs_dir: Path, human_review: bool, tier: str | None,
              thinking: str | None, workflow=None, use_mcp: bool = True) -> None:
        from ..evaluation.configs import get_config
        from ..evaluation.runner import run_config
        from ..llm import OpenAICompatClient
        from ..store import Store
        settings = config.load_settings()
        cfg = get_config(cfg_name)
        llm = OpenAICompatClient.from_settings(settings)
        secrets = tuple(x for x in [os.environ.get(settings.llm.api_key_env, "")] if x)
        box = None
        if use_mcp and any(d.get("enabled") for d in settings.mcp_servers.values()):
            from ..mcp import McpManager
            from ..tools import default_registry
            box = default_registry()
            self._manager = McpManager(settings.mcp_servers, log=self.log.emit)
            self._manager.start(box)

        def plan_cb(plan):
            ans = self._wait("plan", {"modules": [m.to_dict() for m in plan.modules]})
            return (ans or "") == "", ans or ""

        def hint_cb(mid, points):
            ans = self._wait("hint", {"module": mid, "points": points})
            return ans or None

        def checkpoint_cb(info):
            ans = self._wait("checkpoint", info)
            return (ans or "") == "", ans or ""

        def approve_cb(agent, tool, args):
            return bool(self._wait("approve", {"agent": agent, "tool": tool, "args": args}))

        def job():
            store = Store()
            try:
                return run_config(task, cfg, llm=llm, settings=settings, runs_root=runs_dir, store=store,
                                  subscribers=(self.event.emit,), secrets=secrets, human_review=human_review,
                                  human=plan_cb if human_review else None, hint=hint_cb if human_review else None,
                                  checkpoint=checkpoint_cb if human_review else None, tools=box,
                                  approve=approve_cb, tier_override=tier, thinking=thinking, workflow=workflow)
            finally:
                store.close()

        self.worker = _Worker(job)
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _done(self):
        if self._manager is not None:
            self._manager.close()
        r = self.worker.result
        self.finished.emit({"verdict": getattr(r, "verdict", "error"), "run_dir": str(getattr(r, "run_dir", "")),
                            "error": self.worker.error or getattr(r, "error", "") or ""})

    def stop(self) -> None:
        # 后台线程里的 LLM 调用不能硬停：把人工等待放行，并标记中断，运行会在下一个检查点结束
        self.answer("")
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()


class DockerBackend(Backend):
    """用 docker run 在容器里执行 `firmforge run`，运行目录挂载到本机；界面跟随 events.jsonl。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.follower: EventFollower | None = None
        self.name = ""
        self.runs_parent: Path | None = None

    @staticmethod
    def available() -> bool:
        return shutil.which("docker") is not None

    def start(self, task, cfg_name: str, *, runs_dir: Path, human_review: bool, tier: str | None,
              thinking: str | None, workflow=None, use_mcp: bool = False, project: Path | None = None) -> None:
        project = Path(project or Path.cwd()).resolve()
        self.name = f"firmforge-gui-{int(time.time())}"
        self.runs_parent = Path(runs_dir).resolve()
        rel_runs = self.runs_parent.relative_to(project).as_posix()
        args = ["run", "--rm", "-i", "--name", self.name]
        if (project / ".env").is_file():
            args += ["--env-file", str(project / ".env")]
        if sys.platform != "win32" and hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        # PYTHONPATH 指向挂载进去的源码：容器里跑的是当前目录的代码，不用每次改完都重建镜像
        args += ["-v", f"{project}:/work", "-w", "/work", "-e", "PYTHONPATH=/work/src", "firmforge", "run",
                 "--config", cfg_name,
                 "--runs-dir", rel_runs, "--no-mcp"]
        if task.category == "adhoc":
            args += ["--spec", Path(task.spec_path).resolve().relative_to(project).as_posix(),
                     "--include", Path(task.include_dir).resolve().relative_to(project).as_posix()]
        else:
            args += ["--task", Path(task.root).resolve().relative_to(project).as_posix()]
        if human_review:
            args.append("--human-review")
        if tier:
            args += ["--tier", tier]
        if thinking:
            args += ["--think", thinking]
        if workflow is not None:
            args += ["--workflow", f"{workflow.name}@{workflow.version}"]
        self.follower = EventFollower(self.runs_parent, time.time())
        self.follower.event.connect(self._on_event)
        self.proc = QProcess(self)
        self.proc.setProgram("docker")
        self.proc.setArguments(args)
        self.proc.readyReadStandardError.connect(lambda: self.log.emit(bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")))
        self.proc.readyReadStandardOutput.connect(lambda: self.log.emit(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")))
        self.proc.finished.connect(self._done)
        self.proc.start()

    def _on_event(self, e: dict) -> None:
        self.event.emit(e)
        t, d = e.get("type"), e.get("data", {})
        if t == "human_wait":
            if e.get("node_id") == "confirm_plan":
                self.ask.emit("plan", {"modules": d.get("modules_full") or [{"id": m} for m in d.get("modules", [])]})
            elif d.get("reason"):
                self.ask.emit("hint", {"module": e.get("node_id"), "points": d.get("points", [])})
            else:
                self.ask.emit("checkpoint", {"node": e.get("node_id"), **d})

    def answer(self, value) -> None:
        if self.proc is None:
            return
        text = value if isinstance(value, str) else ("y" if value else "")
        self.proc.write((text.replace("\n", " ") + "\n").encode("utf-8"))

    def _done(self, code: int, _status=None) -> None:
        if self.follower:
            self.follower.stop()
        verdict, run_dir = "error", ""
        if self.follower and self.follower.run_dir:
            run_dir = str(self.follower.run_dir)
            info = Path(run_dir) / "run.json"
            if info.is_file():
                verdict = json.loads(info.read_text(encoding="utf-8")).get("verdict") or verdict
        self.finished.emit({"verdict": verdict, "run_dir": run_dir, "error": "" if code in (0, 1, 2) else f"退出码 {code}"})

    def stop(self) -> None:
        if self.name:
            QProcess.execute("docker", ["kill", self.name])


class ReplayPlayer(QObject):
    """回放：按事件的时间间隔重放，倍速可调；单个间隔最多等 3 秒，免得空等。"""
    event = Signal(dict)
    reset = Signal()
    position = Signal(int, int)
    MAX_GAP_S = 3.0

    def __init__(self, run_dir: Path, speed: float = 4.0, parent=None):
        super().__init__(parent)
        from ..runs import read_events
        self.events = read_events(Path(run_dir))
        self.speed = speed
        self.i = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._step)

    def play(self) -> None:
        if self.i >= len(self.events):
            self.seek(0)
        self._schedule()

    def pause(self) -> None:
        self.timer.stop()

    @property
    def playing(self) -> bool:
        return self.timer.isActive()

    def seek(self, i: int) -> None:
        was = self.playing
        self.timer.stop()
        self.reset.emit()
        self.i = 0
        for _ in range(max(0, min(i, len(self.events)))):
            self.event.emit(self.events[self.i])
            self.i += 1
        self.position.emit(self.i, len(self.events))
        if was:
            self._schedule()

    def _schedule(self) -> None:
        if self.i >= len(self.events):
            return
        gap = 0.0 if self.i == 0 else self.events[self.i]["ts"] - self.events[self.i - 1]["ts"]
        self.timer.start(int(min(max(gap, 0) / max(self.speed, 0.1), self.MAX_GAP_S) * 1000))

    def _step(self) -> None:
        self.event.emit(self.events[self.i])
        self.i += 1
        self.position.emit(self.i, len(self.events))
        self._schedule()

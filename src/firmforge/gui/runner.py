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


LIVE_WORKERS: set = set()           # 还在跑的后台线程；关窗口前要等它们结束（否则 Qt 直接中止程序）


def friendly_error(exc: BaseException) -> str:
    """后台运行出错时给人看的一句话：说清楚是哪一类问题、怎么办。"""
    from ..llm import LLMError
    if isinstance(exc, LLMError):
        return f"模型调用失败：{exc}。检查网络和 .env 里的 API key；已经完成的部分保留在运行记录里"
    if isinstance(exc, PermissionError):
        return f"没有写入权限：{exc}"
    if isinstance(exc, OSError):
        return f"读写文件或设备出错：{exc}"
    return f"{type(exc).__name__}: {exc}"


class _Worker(QThread):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.result = None
        self.error = ""
        self.finished.connect(lambda: LIVE_WORKERS.discard(self))

    def start(self, *args):
        LIVE_WORKERS.add(self)                   # 同时也保住引用：跑着的线程不会被当垃圾回收掉
        super().start(*args)

    def run(self):
        try:
            self.result = self.fn()
        except BaseException as exc:          # 包括中断
            self.error = friendly_error(exc)


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


def shutdown_workers(timeout_ms: int = 3000) -> None:
    """关窗口前：等后台线程结束，等不到就强行停掉（模型调用卡在网络上时）。"""
    for w in list(LIVE_WORKERS):
        if w.isRunning() and not w.wait(timeout_ms):
            w.terminate()
            w.wait(2000)
        LIVE_WORKERS.discard(w)


def rule_review_payload(pending, escalated, results: dict | None = None) -> dict:
    """人工确认规则时交给界面的内容：每条规则的原文、出处和核对每一步的结果（DECISIONS 第 100 条）。"""
    results = results or {}

    def one(rule, steps):
        return {**rule.to_dict(), "steps": [list(s) for s in steps]}
    return {"pending": [one(r, results[r.id].steps if r.id in results else []) for r in pending],
            "escalated": [{**one(x.rule, x.steps), "reasons": x.reasons()} for x in escalated]}


BOARD_USERS: set = set()           # 正在用板子的后台运行（同一时间只能有一个，否则两边同时烧录同一个串口）


def claim_board(owner, what: str) -> None:
    """占用板子；已经有别的运行在用就报错，说明是谁在用。"""
    from ..embedded.workspace import WorkspaceError
    others = [o for o in BOARD_USERS if o is not owner]
    if others:
        raise WorkspaceError(f"板子正在被{getattr(others[0], 'what', '另一次运行')}使用，等它结束（或停止它）再开始{what}")
    owner.what = what
    BOARD_USERS.add(owner)


class EmbeddedBackend(Backend):
    """嵌入式新流程（只用本机，DECISIONS 第 100 条：要上板的运行不进 docker）。

    两种跑法：
    - spec：按你写的需求跑完整流程（规则 → 设计 → 编码、测试 → 上板 → 诊断修正）；
    - task：拿一道上板评测题按评测的某种配置跑一次，最后用隐藏测试判定（和 board-eval 完全一样）。
    规则需要人确认时发 ask("rules", 规则和核对结果)，回答是 {规则编号: ("approve"|"reject", 意见)}。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answer = None
        self._got = threading.Event()
        self.worker: _Worker | None = None
        self.row = None

    def _wait(self, kind: str, payload: dict):
        self._got.clear()
        self.ask.emit(kind, payload)
        self._got.wait()
        return self._answer

    def answer(self, value) -> None:
        self._answer = value
        self._got.set()

    def start(self, *, project: Path, hw: str, runs_dir: Path, spec: str = "", name: str = "gui", rule_mode: str = "verified",
              task_id: str = "", config_name: str = "verified", tier: str | None = None, thinking: str | None = None,
              port: str | None = None, workflow=None, human_review: bool = False, prior_wiring=None) -> None:
        """workflow：写需求时用的上板工作流（名字、路径或 Workflow 对象，默认 firmware_dev，DECISIONS 第 110 条）；
        human_review：设计好后停下等确认（ask("plan")，回答空字符串表示确认，写了意见就让设计 Agent 重做）。"""
        from ..embedded.workspace import load_hw
        from ..llm import OpenAICompatClient
        from ..target.board import BoardRunner
        from ..target.esp_idf import IdfEnv, IdfUnavailable
        claim_board(self, "对话页的上板运行")
        try:
            settings = config.load_settings()
            llm = OpenAICompatClient.from_settings(settings)
            hwc = load_hw(project / hw, need_sources=(rule_mode != "none") or config_name == "unverified", root=project)
        except Exception:
            BOARD_USERS.discard(self)
            raise
        try:
            idf = IdfEnv.load(want_version=str(hwc.profile.framework_version.value))
        except IdfUnavailable as exc:
            self.log.emit(f"ESP-IDF 不可用：{exc}；硬件层只做合规检查")
            idf = None
        board = BoardRunner(idf, port) if idf is not None else None
        if board is not None and board.port is None:
            board = None
            self.log.emit("没找到板子：硬件层只做到编译和合规检查")
        sub = (self.event.emit,)

        if task_id:
            from ..embedded.board_eval import run_once
            from ..embedded.board_task import load_board_task
            from ..embedded.workspace import make_eval_context
            task = load_board_task(project / "tasks_board" / task_id)
            ctx = make_eval_context(hwc, project / "tasks_board", llm=llm, settings=settings, idf=idf, board=board,
                                    runs_root=runs_dir, build_root=project / ".ff_data" / "idf_build",
                                    tier=tier or "fast", think=thinking or "low", subscribers=sub)
            ctx.rules_cache = project / ".ff_data" / "gui_unverified_rules"

            def job():
                self.row = run_once(task, config_name, 1, ctx)
                return self.row
        else:
            from ..embedded.workspace import make_firmware_flow
            from ..store import Store
            holder = {}
            db = Store(project / ".ff_data" / "firmforge.db")       # 调度器的成绩、经验库

            def confirm_design(design):
                ans = self._wait("plan", {"modules": [m.to_dict() for m in design.modules], "embedded": True,
                                          "wiring": dict(design.wiring)})
                return ans if isinstance(ans, str) else ""

            def human(pending, escalated):
                b = holder.get("builder")
                results = getattr(b, "results", {}) if b is not None else {}
                ans = self._wait("rules", rule_review_payload(pending, escalated, results))
                return ans or {}
            if workflow is not None and not isinstance(workflow, (str, Path)):
                from ..orchestrator.workflow import dump_workflow
                tmp = runs_dir.parent / ".ff_data" / "gui_workflow.toml"     # 设计器里还没保存的版本，照它跑
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(dump_workflow(workflow), encoding="utf-8")
                workflow = tmp
            flow = make_firmware_flow(hwc, spec, llm=llm, settings=settings, rule_mode=rule_mode, idf=idf, board=board,
                                      runs_root=runs_dir, name=name, human=human, tier=tier, think=thinking,
                                      subscribers=sub, workflow=workflow or "firmware_dev", store=db,
                                      human_review=human_review, confirm_design=confirm_design,
                                      prior_wiring=prior_wiring)
            flow.on_builder = lambda b: holder.__setitem__("builder", b)

            def job():
                res = flow.run()
                if rule_mode == "verified":
                    hwc.store.save(hwc.rules_path)
                return res

        self.worker = _Worker(job)
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _done(self):
        BOARD_USERS.discard(self)
        r = self.worker.result
        info = {"verdict": getattr(r, "verdict", "error"), "run_dir": str(getattr(r, "run_dir", "") or ""),
                "error": self.worker.error or getattr(r, "error", "") or getattr(r, "note", "") or ""}
        if self.row is not None:                       # 评测题：带上隐藏测试的判定
            info.update(judge=self.row.to_dict())
            info["error"] = ""
        self.finished.emit(info)

    def stop(self) -> None:
        self.answer({})
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()


class RulesBackend(Backend):
    """在硬件页发起"查资料提炼规则"（DECISIONS 第 100 条）：本机后台线程跑规则流程，等人确认时发 ask("rules")。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answer = None
        self._got = threading.Event()
        self.worker: _Worker | None = None
        self.meter = None
        self.rd = None

    def answer(self, value) -> None:
        self._answer = value
        self._got.set()

    def start(self, *, project: Path, hw: str, scopes: list[str], runs_dir: Path, tier: str | None = "fast",
              thinking: str | None = "low") -> None:
        from ..embedded.workspace import load_hw, make_rule_builder
        from ..llm import OpenAICompatClient
        settings = config.load_settings()
        hwc = load_hw(project / hw, need_sources=True, root=project)
        holder = {}

        def human(pending, escalated):
            b = holder.get("b")
            self._got.clear()
            self.ask.emit("rules", rule_review_payload(pending, escalated, getattr(b, "results", {})))
            self._got.wait()
            return self._answer or {}
        builder, self.meter, self.rd = make_rule_builder(hwc, llm=OpenAICompatClient.from_settings(settings),
                                                         settings=settings, runs_root=runs_dir, human=human, tier=tier,
                                                         think=thinking, subscribers=(self.event.emit,))
        holder["b"] = builder

        def job():
            try:
                return builder.run(scopes)
            finally:                              # 中途出错或中断，已入库的规则也保存
                hwc.store.save(hwc.rules_path)

        self.worker = _Worker(job)
        self.worker.finished.connect(self._done)
        self.worker.start()

    def _done(self):
        out = self.worker.result
        self.finished.emit({"verdict": "pass" if out is not None and out.ok else "fail",
                            "run_dir": str(self.rd.path) if self.rd else "", "error": self.worker.error,
                            "cost": self.meter.cost if self.meter else 0.0,
                            "trusted": len(out.trusted) if out else 0, "pending": len(out.pending) if out else 0,
                            "missing": list(out.missing) if out else []})

    def stop(self) -> None:
        self.answer({})
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()


class EvalBackend(Backend):
    """评测页的上板评测（DECISIONS 第 92–94、100 条）：本机后台线程跑 board_eval.evaluate，每跑完一次发一行进度。"""
    progress = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: _Worker | None = None
        self._stop = False

    def start(self, *, project: Path, hw: str, task_ids: list[str], configs: list[str], runs: int, out_dir: Path,
              max_cost: float, tier: str = "fast", thinking: str = "low", port: str | None = None) -> None:
        from ..embedded.workspace import WorkspaceError
        if max_cost <= 0:
            raise WorkspaceError("要先填花费上限（元）")
        claim_board(self, "评测")
        try:
            self._prepare(project, hw, task_ids, configs, runs, out_dir, max_cost, tier, thinking, port)
        except Exception:
            BOARD_USERS.discard(self)
            raise

    def _prepare(self, project, hw, task_ids, configs, runs, out_dir, max_cost, tier, thinking, port) -> None:
        from ..embedded.board_eval import evaluate
        from ..embedded.board_task import load_board_task
        from ..embedded.workspace import WorkspaceError, load_hw, make_eval_context
        from ..llm import OpenAICompatClient
        from ..target.board import BoardRunner
        from ..target.esp_idf import IdfEnv, IdfUnavailable
        settings = config.load_settings()
        hwc = load_hw(project / hw, need_sources="unverified" in configs, root=project)
        try:
            idf = IdfEnv.load(want_version=str(hwc.profile.framework_version.value))
        except IdfUnavailable as exc:
            raise WorkspaceError(f"上板评测需要 ESP-IDF：{exc}") from exc
        board = BoardRunner(idf, port)
        if board.port is None:
            raise WorkspaceError("找不到板子的串口：请插上板子")
        tasks = [load_board_task(project / "tasks_board" / i) for i in task_ids]
        ctx = make_eval_context(hwc, project / "tasks_board", llm=OpenAICompatClient.from_settings(settings),
                                settings=settings, idf=idf, board=board, runs_root=project / ".ff_runs",
                                build_root=project / ".ff_data" / "idf_build", tier=tier, think=thinking,
                                subscribers=(self.event.emit,))
        self._stop = False

        def job():
            return evaluate(tasks, configs, runs, out_dir, ctx, progress=self.progress.emit, max_cost=max_cost,
                            stop=lambda: self._stop)

        self.worker = _Worker(job)
        self.worker.finished.connect(lambda: (BOARD_USERS.discard(self),
                                              self.finished.emit({"verdict": "done", "run_dir": str(out_dir),
                                                                  "error": self.worker.error})))
        self.worker.start()

    def pause(self) -> None:
        """正在跑的一次跑完后停下；同一个输出目录再开始会接着跑。"""
        self._stop = True

    def stop(self) -> None:
        """关窗口时用：不等正在跑的一次，直接停（已经写进 runs.jsonl 的结果保留，再开始会接着跑）。"""
        self.pause()
        if self.worker is not None and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(2000)
        BOARD_USERS.discard(self)

    def answer(self, value) -> None:
        pass


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

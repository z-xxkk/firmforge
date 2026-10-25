"""单 Agent 基线执行器（ARCHITECTURE §8 末尾、§10）。

B1：单 Agent（coder-strong）一次生成，不修正。
B2：单 Agent + 修正循环，判定报错直接回喂，最多 3 轮，没有诊断 Agent，也没有升级。
B5：同等花费对照（DECISIONS 第 30 条）：单 Agent 每次从头生成、不带报错，直到通过或用完 token 预算。
    只在已花的 token 还没达到预算时才开始新的一次。

同一个 Agent 同时写实现和单元测试（DECISIONS 第 21 条）。
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agents.context import FORMAT_REMINDER, coder_messages, repair_message
from ..agents.protocol import parse_files, write_files
from ..config import ModelTier, PeakSchedule, Toolchain
from ..llm import CallLogger, LLMClient, LLMError, LoggedLLM
from ..runs import RunDir, copy_snapshot
from ..store import Store
from ..tasks import Task
from ..verifier import verify
from ..verifier.report import Report, attempt_score
from .events import EventBus

CODER_DIRS = ("src", "tests")


@dataclass(frozen=True)
class BaselineConfig:
    name: str
    max_repairs: int
    tier: str = "strong"
    agent: str = "coder-strong"
    temperature: float = 0.2
    max_tokens: int = 8192          # 档位没配置 max_tokens 时使用
    fresh_restarts: bool = False    # True：每次从头生成，不带报错（b5）
    max_attempts: int = 30          # fresh_restarts 时的次数上限，防止预算填错时停不下来
    kind: str = "baseline"


B1 = BaselineConfig("b1", max_repairs=0)
B2 = BaselineConfig("b2", max_repairs=3)
B5 = BaselineConfig("b5", max_repairs=0, fresh_restarts=True)
BASELINES = {"b1": B1, "b2": B2, "b5": B5}


class RunInterrupted(KeyboardInterrupt):
    """运行被 Ctrl+C 中断；运行目录里的记录已经写完整。"""

    def __init__(self, result: "RunResult"):
        super().__init__(f"运行 {result.run_id} 被中断")
        self.result = result
        self.run_dir = result.run_dir


@dataclass
class AttemptRecord:
    n: int
    dir: Path
    report: Report
    score: float
    tokens: int
    latency_s: float


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    task_id: str
    config: str
    verdict: str                 # pass | fail | incomplete | error | interrupted
    attempts: list[AttemptRecord] = field(default_factory=list)
    best: int | None = None      # 交付的是第几次尝试
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0    # 已包含在 completion_tokens 里
    cost: float = 0.0
    wall_s: float = 0.0
    error: str | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def final_dir(self) -> Path:
        return self.run_dir / "final"

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def has_code(self) -> bool:
        return bool(self.attempts)

    @property
    def final_report(self) -> Report | None:
        best = next((a for a in self.attempts if a.n == self.best), None)
        return best.report if best else None

    @property
    def first_dir(self) -> Path | None:
        return self.attempts[0].dir if self.attempts else None


def run_baseline(task: Task, cfg: BaselineConfig, *, llm: LLMClient, model: ModelTier,
                 runs_root: Path | str, store: Store | None = None,
                 subscribers: tuple[Callable[[dict], None], ...] = (),
                 toolchain: Toolchain | None = None, secrets: tuple[str, ...] = (),
                 pricing: PeakSchedule | None = None, token_budget: int | None = None) -> RunResult:
    if cfg.fresh_restarts and token_budget is None:
        raise ValueError(f"配置 {cfg.name} 需要 token 预算（与 full 同等花费）")
    t0 = time.perf_counter()
    rd = RunDir.create(runs_root, task.id)
    bus = EventBus(rd.run_id, rd.events_path)
    for fn in subscribers:
        bus.subscribe(fn)
    agent_llm = LoggedLLM(llm, CallLogger(rd.path, list(secrets)), cfg.agent)
    result = RunResult(rd.run_id, rd.path, task.id, cfg.name, "fail")
    module = task.id
    run_info = {
        "run_id": rd.run_id, "task": task.id, "task_root": str(task.root), "spec": str(task.spec_path),
        "config": cfg.name, "agent": cfg.agent, "model_tier": cfg.tier, "model": model.name,
        "max_repairs": cfg.max_repairs, "temperature": cfg.temperature, "thinking": model.thinking,
        "max_tokens": model.max_tokens or cfg.max_tokens, "token_budget": token_budget,
        "fresh_restarts": cfg.fresh_restarts, "started_at": time.time(),
    }
    rd.write_json("run.json", run_info)
    if store:
        store.start_run(rd.run_id, task.id, cfg.name)

    bus.emit("run_started", task=task.id, config=cfg.name, model=model.name)
    bus.emit("node_ready", module, title=task.title)
    reason = ("单 Agent 同等花费对照，每次从头生成" if cfg.fresh_restarts else "单 Agent 基线，固定分配")
    bus.emit("node_assigned", module, agent=cfg.agent, scores={"reason": reason})

    messages = coder_messages(task)
    prev_dir: Path | None = None
    rejected: list[tuple[str, str]] = []

    def call(msgs: list[dict], attempt: int):
        bus.emit("agent_started", module, agent=cfg.agent, attempt=attempt)
        resp = agent_llm.complete(msgs, model=model.name, temperature=cfg.temperature,
                                  max_tokens=model.max_tokens or cfg.max_tokens, extra_body=model.extra_body())
        result.prompt_tokens += resp.prompt_tokens
        result.completion_tokens += resp.completion_tokens
        result.reasoning_tokens += resp.reasoning_tokens
        # 按调用结束的时刻判断高峰 / 空闲价
        result.cost += model.cost(resp.prompt_tokens, resp.completion_tokens, ts=time.time(), schedule=pricing)
        return resp

    interrupted = False
    try:
        n = 0
        while True:
            n += 1
            if cfg.fresh_restarts:
                if n > cfg.max_attempts or (n > 1 and result.tokens >= token_budget):
                    break
                messages, prev_dir = coder_messages(task), None      # 从头生成：不带上一次的代码和报错
            elif n > cfg.max_repairs + 1:
                break
            try:
                resp = call(messages, n)
                parsed = parse_files(resp.text, CODER_DIRS)
                tokens = resp.prompt_tokens + resp.completion_tokens
                latency = resp.latency_s
                if not parsed.files and prev_dir is None:
                    # 第一次就没有可用的文件块：带着格式提醒重试 1 次
                    messages = messages + [{"role": "assistant", "content": resp.text},
                                           {"role": "user", "content": FORMAT_REMINDER}]
                    resp = call(messages, n)
                    parsed = parse_files(resp.text, CODER_DIRS)
                    tokens += resp.prompt_tokens + resp.completion_tokens
                    latency += resp.latency_s
            except LLMError as exc:
                result.verdict, result.error = "error", str(exc)
                bus.emit("agent_finished", module, agent=cfg.agent, attempt=n, ok=False, error=str(exc))
                break
            rejected = parsed.rejected
            bus.emit("agent_finished", module, agent=cfg.agent, attempt=n, ok=True, tokens=tokens,
                     latency_s=round(latency, 3), files=sorted(parsed.files), rejected=[p for p, _ in rejected])

            adir = rd.attempt_dir(module, n)
            if prev_dir is not None:
                copy_snapshot(prev_dir, adir)
            adir.mkdir(parents=True, exist_ok=True)
            write_files(adir, parsed.files)

            bus.emit("verify_started", module, attempt=n)
            report = verify(adir, task, toolchain=toolchain)      # 不含隐藏测试
            shutil.rmtree(adir / ".ff_build", ignore_errors=True)
            (adir / "report.json").write_text(report.for_agent().to_json(), encoding="utf-8")
            bus.emit("verify_finished", module, attempt=n, verdict=report.verdict,
                     failed_layer=report.failed_layer, signature=report.signature)
            rec = AttemptRecord(n, adir, report, attempt_score(report), tokens, latency)
            result.attempts.append(rec)
            if store:
                store.add_attempt(rd.run_id, module, n, cfg.agent, report.verdict, report.failed_layer,
                                  report.signature, tokens, latency)
            if report.failed_layer is None:
                break
            if cfg.fresh_restarts:
                continue
            prev_dir = adir
            if n > cfg.max_repairs:
                break
            messages = messages + [{"role": "assistant", "content": resp.text},
                                   {"role": "user", "content": repair_message(report, n, cfg.max_repairs, rejected, adir)}]
    except KeyboardInterrupt:
        # 按 Ctrl+C：照常整理已有的尝试、写完记录，再把中断往外抛
        interrupted = True
        result.verdict = "interrupted"

    if result.attempts:
        best = max(result.attempts, key=lambda a: (a.score, a.n))
        result.best = best.n
        copy_snapshot(best.dir, rd.final_dir)
        agent_report = best.report.for_agent()
        (rd.final_dir / "report.json").write_text(agent_report.to_json(), encoding="utf-8")
        (rd.final_dir / "report.md").write_text(
            agent_report.to_markdown(f"{task.title}（{task.id}）验证报告：第 {best.n} 次尝试"), encoding="utf-8")
        if result.verdict not in ("error", "interrupted"):
            result.verdict = agent_report.verdict
    best_rec = next((a for a in result.attempts if a.n == result.best), None)
    if result.verdict not in ("error", "interrupted") and best_rec is not None and best_rec.report.failed_layer is None:
        bus.emit("node_done", module, attempt=result.best, verdict=result.verdict)
    else:
        bus.emit("node_failed", module, attempts=len(result.attempts), verdict=result.verdict)

    result.wall_s = time.perf_counter() - t0
    bus.emit("run_finished", verdict=result.verdict, attempts=len(result.attempts), best=result.best,
             tokens=result.tokens, cost=round(result.cost, 6), wall_s=round(result.wall_s, 2))
    run_info.update({"finished_at": time.time(), "verdict": result.verdict, "attempts": len(result.attempts),
                     "best_attempt": result.best, "prompt_tokens": result.prompt_tokens,
                     "completion_tokens": result.completion_tokens, "reasoning_tokens": result.reasoning_tokens,
                     "total_tokens": result.tokens,
                     "cost": result.cost, "wall_s": result.wall_s, "error": result.error})
    rd.write_json("run.json", run_info)
    if store:
        store.finish_run(rd.run_id, result.verdict, result.tokens, result.cost, result.wall_s)
    if interrupted:
        raise RunInterrupted(result)
    return result

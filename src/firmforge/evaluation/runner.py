"""批量评测：任务 × 配置 × 重复次数（ARCHITECTURE §10）。

最终是否通过 = 交付代码通过 L1–L4 和隐藏测试。隐藏测试只在这里（评测流程）运行，
并且在临时目录里判定，结果写进评测输出目录，不进入任何 Agent 的上下文。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..agents.registry import Registry
from ..config import Settings, Toolchain
from ..llm import LLMClient
from ..orchestrator.baseline import RunResult, run_baseline
from ..orchestrator.engine import MultiRunResult, run_multi
from ..store import Store
from ..tasks import Task
from ..verifier import verify
from ..verifier.report import PASS, Report
from .configs import RunConfig
from .metrics import read_csv, summarize, write_csv

JUDGE_LAYERS = ["host_compile", "cross_compile", "resources", "static_check"]


def judge(code_dir: Path, task: Task, toolchain: Toolchain | None = None) -> Report:
    """在临时目录里用 L1–L4 + 隐藏测试判定交付代码（不带 Agent 的测试）。"""
    with tempfile.TemporaryDirectory(prefix=f"ff_judge_{task.id}_") as tmp:
        work = Path(tmp)
        if (Path(code_dir) / "src").is_dir():
            shutil.copytree(Path(code_dir) / "src", work / "src")
        else:
            (work / "src").mkdir()
        return verify(work, task, layers=JUDGE_LAYERS, include_hidden=True, stop_on_fail=True,
                      toolchain=toolchain)


def _passed(report: Report | None) -> bool:
    return report is not None and report.verdict == PASS


def run_config(task: Task, cfg: RunConfig, *, llm: LLMClient, settings: Settings, runs_root: Path | str,
               store: Store | None = None, subscribers: tuple[Callable[[dict], None], ...] = (),
               toolchain: Toolchain | None = None, secrets: tuple[str, ...] = (), token_budget: int | None = None,
               registry: Registry | None = None, human_review: bool = False,
               human=None) -> RunResult | MultiRunResult:
    """按配置类型运行一次：单 Agent 基线或多 Agent 引擎。命令行和评测共用。"""
    if cfg.kind == "multi":
        return run_multi(task, cfg, llm=llm, settings=settings, runs_root=runs_root, registry=registry, store=store,
                         subscribers=subscribers, toolchain=toolchain, secrets=secrets,
                         human_review=human_review, human=human)
    return run_baseline(task, cfg, llm=llm, model=settings.tier(cfg.tier), runs_root=runs_root, store=store,
                        subscribers=subscribers, toolchain=toolchain, secrets=secrets, pricing=settings.pricing,
                        token_budget=token_budget)


def make_row(task: Task, cfg: RunConfig, run_no: int, res: RunResult | MultiRunResult,
             first: Report | None, final: Report | None) -> dict:
    res_layer = final.layer("resources") if final else None
    agent_report = res.final_report
    ut = agent_report.layer("unit_tests") if agent_report else None
    if res.verdict == "error":
        failed = "llm_error"
    elif final is None:
        failed = "no_code"
    elif final.verdict == PASS:
        failed = None
    else:
        failed = final.failed_layer or "incomplete"
    return {
        "task": task.id, "category": task.category, "difficulty": task.difficulty,
        "config": cfg.name, "run": run_no, "first_pass": _passed(first), "final_pass": _passed(final),
        "attempts": res.attempt_count, "tokens": res.tokens, "cost": round(res.cost, 6),
        "wall_s": round(res.wall_s, 2),
        "flash": res_layer.metrics.get("flash") if res_layer else None,
        "ram": res_layer.metrics.get("ram") if res_layer else None,
        "coverage_pct": ut.metrics.get("coverage_pct") if ut else None,
        "failed_layer": failed, "escalations": 0, "experiences_used": 0,
    }


@dataclass
class EvalOutput:
    rows: list[dict]
    csv_path: Path
    summary_path: Path


def full_budget(rows: list[dict], task_id: str) -> int:
    """b5 的 token 预算：同一实验里 full 在该任务上的平均 token。"""
    xs = [int(r["tokens"]) for r in rows if r["task"] == task_id and r["config"] == "full"]
    return round(sum(xs) / len(xs)) if xs else 0


def evaluate(tasks: list[Task], configs: list[RunConfig], runs: int, out_dir: Path | str, *,
             llm: LLMClient, settings: Settings, toolchain: Toolchain | None = None,
             subscribers: tuple[Callable[[dict], None], ...] = (), secrets: tuple[str, ...] = (),
             progress: Callable[[str], None] = lambda s: None, registry: Registry | None = None) -> EvalOutput:
    out = Path(out_dir)
    (out / "judges").mkdir(parents=True, exist_ok=True)
    store = Store(out / "firmforge.db")       # 每个实验用独立的数据库
    csv_path, summary_path = out / "results.csv", out / "summary.md"
    rows: list[dict] = []
    total = len(tasks) * len(configs) * runs
    i = 0
    try:
        for task in tasks:
            for cfg in configs:
                budget = full_budget(rows, task.id) if getattr(cfg, "fresh_restarts", False) else None
                for run_no in range(1, runs + 1):
                    i += 1
                    extra = f"，token 预算 {budget}（full 的平均）" if budget is not None else ""
                    progress(f"[{i}/{total}] 任务 {task.id}，配置 {cfg.name}，第 {run_no} 次{extra}")
                    res = run_config(task, cfg, llm=llm, settings=settings, runs_root=out / "runs", store=store,
                                     subscribers=subscribers, toolchain=toolchain, secrets=secrets,
                                     token_budget=budget, registry=registry)
                    final = judge(res.final_dir, task, toolchain) if res.has_code else None
                    first = None
                    if res.has_code:
                        same = getattr(res, "best", None) == 1
                        first = final if same else judge(res.first_dir, task, toolchain)
                    judge_info = {"run_id": res.run_id, "final": final.to_dict() if final else None,
                                  "first": first.to_dict() if first else None}
                    (out / "judges" / f"{res.run_id}.json").write_text(
                        json.dumps(judge_info, ensure_ascii=False, indent=2), encoding="utf-8")
                    row = make_row(task, cfg, run_no, res, first, final)
                    rows.append(row)
                    write_csv(rows, csv_path)           # 每跑完一次就落盘，中途中断也不丢
                    progress(f"      最终 {'通过' if row['final_pass'] else '未通过'}"
                             f"（{row['failed_layer'] or '全部通过'}），尝试 {row['attempts']} 次，"
                             f"token {row['tokens']}，成本 {row['cost']:.4f} 元")
    finally:
        store.close()
        # 中途被中断也要给已完成的部分出汇总
        if rows:
            title = "评测汇总" if len(rows) == total else f"评测汇总（中断，只完成 {len(rows)}/{total} 次）"
            summary_path.write_text(summarize(read_csv(csv_path), title), encoding="utf-8")
    return EvalOutput(rows, csv_path, summary_path)


def estimate(tasks: list[Task], configs: list[RunConfig], runs: int, out_dir: Path | str, *,
             llm: LLMClient, settings: Settings, toolchain: Toolchain | None = None,
             secrets: tuple[str, ...] = (), progress: Callable[[str], None] = lambda s: None,
             registry: Registry | None = None, subscribers: tuple[Callable[[dict], None], ...] = ()) -> dict:
    """只跑第 1 个任务、每个配置 1 次，按规模推算总 token 和成本。"""
    sample = evaluate(tasks[:1], configs, 1, Path(out_dir) / "estimate", llm=llm, settings=settings,
                      toolchain=toolchain, secrets=secrets, progress=progress, registry=registry,
                      subscribers=subscribers)
    scale = len(tasks) * runs
    per_config = {r["config"]: {"tokens": r["tokens"], "cost": r["cost"]} for r in sample.rows}
    return {
        "sample_task": tasks[0].id, "scale": scale, "per_config": per_config,
        "total_tokens": sum(v["tokens"] for v in per_config.values()) * scale,
        "total_cost": sum(v["cost"] for v in per_config.values()) * scale,
    }

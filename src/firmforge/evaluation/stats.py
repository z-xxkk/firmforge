"""`firmforge stats`（DECISIONS 第 65 条）：从真实运行记录统计响应延迟和 token。

输入可以是一个运行目录，也可以是包含很多运行目录的上级目录（如 .ff_runs、results/<实验>/runs）。
统计：每个 Agent、每个模型的调用延迟（中位数、90% 分位、最大）和平均 token；每个工具的调用次数、成功率、耗时；
每次判定的耗时。
"""

from __future__ import annotations

import json
from pathlib import Path


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def run_dirs(path: Path) -> list[Path]:
    path = Path(path)
    if (path / "events.jsonl").is_file():
        return [path]
    return sorted(p.parent for p in path.rglob("events.jsonl"))


def collect(path: Path) -> dict:
    by_agent: dict[str, list] = {}
    by_model: dict[str, list] = {}
    tools: dict[str, list] = {}
    verify: list[float] = []
    runs = run_dirs(path)
    for rd in runs:
        calls = rd / "llm_calls.jsonl"
        if calls.is_file():
            for line in calls.read_text(encoding="utf-8").splitlines():
                c = json.loads(line)
                if not c.get("ok") or c.get("latency_s") is None:
                    continue
                rec = (float(c["latency_s"]), int(c.get("prompt_tokens", 0)) + int(c.get("completion_tokens", 0)))
                by_agent.setdefault(c.get("agent", "?"), []).append(rec)
                by_model.setdefault(c.get("model", "?"), []).append(rec)
        started: dict = {}
        for line in (rd / "events.jsonl").read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            d = e.get("data", {})
            if e["type"] == "tool_result":
                tools.setdefault(d.get("tool", "?"), []).append((float(d.get("latency_s") or 0), bool(d.get("ok"))))
            elif e["type"] == "verify_started":
                started[(e.get("node_id"), d.get("attempt"), d.get("scope"))] = e["ts"]
            elif e["type"] == "verify_finished":
                t = started.pop((e.get("node_id"), d.get("attempt"), d.get("scope")), None)
                if t is not None:
                    verify.append(e["ts"] - t)
    return {"runs": len(runs), "agents": by_agent, "models": by_model, "tools": tools, "verify": verify}


def to_markdown(stats: dict) -> str:
    def row(name, recs):
        lat = [r[0] for r in recs]
        tok = [r[1] for r in recs]
        return (f"| {name} | {len(recs)} | {_quantile(lat, .5):.1f} | {_quantile(lat, .9):.1f} | {max(lat):.1f} | "
                f"{sum(tok) / len(tok):.0f} |")
    lines = ["# 响应延迟统计", "", f"运行目录 {stats['runs']} 个。", ""]
    for title, key in (("按 Agent", "agents"), ("按模型", "models")):
        lines += [f"## {title}", "", "| 名称 | 调用次数 | 延迟中位数 (s) | 90% 分位 (s) | 最大 (s) | 平均 token |",
                  "|---|---|---|---|---|---|"]
        lines += [row(n, r) for n, r in sorted(stats[key].items())] or ["| （没有记录） | | | | | |"]
        lines.append("")
    lines += ["## 工具", "", "| 工具 | 调用次数 | 成功率 | 平均耗时 (s) |", "|---|---|---|---|"]
    for n, recs in sorted(stats["tools"].items()):
        ok = sum(1 for r in recs if r[1]) / len(recs)
        lines.append(f"| {n} | {len(recs)} | {ok:.0%} | {sum(r[0] for r in recs) / len(recs):.2f} |")
    if not stats["tools"]:
        lines.append("| （没有记录） | | | |")
    v = stats["verify"]
    lines += ["", "## 判定", "",
              f"共 {len(v)} 次；中位数 {_quantile(v, .5):.1f} 秒，90% 分位 {_quantile(v, .9):.1f} 秒，最长 {max(v) if v else 0:.1f} 秒。"]
    return "\n".join(lines) + "\n"

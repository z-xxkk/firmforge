"""评测结果：results.csv 的列定义与 summary.md 汇总。"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path

ROW_FIELDS = ["task", "category", "difficulty", "config", "run", "first_pass", "final_pass", "attempts",
              "tokens", "cost", "wall_s", "flash", "ram", "coverage_pct", "failed_layer", "escalations",
              "experiences_used"]


def write_csv(rows: list[dict], path: Path) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROW_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in ROW_FIELDS})


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _num(v) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, str) and v in ("True", "False"):
        return 1.0 if v == "True" else 0.0
    return float(v)


def mean_std(values: list, digits: int = 1) -> str:
    xs = [x for x in (_num(v) for v in values) if x is not None]
    if not xs:
        return "-"
    m = statistics.fmean(xs)
    s = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return f"{m:.{digits}f} ± {s:.{digits}f}"


def summarize(rows: list[dict], title: str = "评测汇总") -> str:
    configs = list(dict.fromkeys(r["config"] for r in rows))
    lines = [f"# {title}", "",
             f"共 {len(rows)} 次运行：{len(set(r['task'] for r in rows))} 个任务 × {len(configs)} 个配置。"
             "最终是否通过 = 交付代码通过 L1–L4 和隐藏测试。数值为均值 ± 标准差。", "",
             "## 各配置总体", "",
             "| 配置 | 最终通过率 | 首次通过率 | 尝试次数 | token | 成本（元） | 用时（秒） |",
             "|---|---|---|---|---|---|---|"]
    for c in configs:
        rs = [r for r in rows if r["config"] == c]
        lines.append(f"| {c} | {mean_std([r['final_pass'] for r in rs], 2)} | {mean_std([r['first_pass'] for r in rs], 2)} "
                     f"| {mean_std([r['attempts'] for r in rs])} | {mean_std([r['tokens'] for r in rs], 0)} "
                     f"| {mean_std([r['cost'] for r in rs], 4)} | {mean_std([r['wall_s'] for r in rs])} |")
    cats = sorted(set(r["category"] for r in rows))
    lines += ["", "## 按任务类别的最终通过率", "", "| 类别 | " + " | ".join(configs) + " |",
              "|---|" + "---|" * len(configs)]
    for cat in cats:
        cells = [mean_std([r["final_pass"] for r in rows if r["category"] == cat and r["config"] == c], 2)
                 for c in configs]
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")
    lines += ["", "## 未通过时首个失败层（次数）", "", "| 配置 | 失败层分布 |", "|---|---|"]
    for c in configs:
        counts: dict[str, int] = {}
        for r in rows:
            if r["config"] == c and _num(r["final_pass"]) == 0.0:
                key = r.get("failed_layer") or "未知"
                counts[key] = counts.get(key, 0) + 1
        dist = "，".join(f"{k} × {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "无"
        lines.append(f"| {c} | {dist} |")
    lines += ["", "## 逐次结果", "", "| 任务 | 配置 | 第几次 | 首次通过 | 最终通过 | 尝试 | 失败层 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['task']} | {r['config']} | {r['run']} | {r['first_pass']} | {r['final_pass']} "
                     f"| {r['attempts']} | {r.get('failed_layer') or '-'} |")
    return "\n".join(lines) + "\n"

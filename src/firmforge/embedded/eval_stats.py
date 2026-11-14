"""评测结果的统计（DECISIONS 第 119 条，docs/TEST_PLAN.md 第 6 节）。只用标准库。

做法照 Miller《Adding Error Bars to Evals》（Anthropic，2024）：
- 同一道题的几次运行彼此相关，通过率的标准误按题聚类算（比把每次运行当独立样本算出来的大）；
- 前后两版比较时，在同一批题上逐题配对：先算每道题前后通过率的差，再对这些差求平均和标准误。
稳定性 pass^k 照 τ-bench（Yao 等，2024）：同一题 n 次里通过 c 次时，k 次都通过的无偏估计是 C(c,k)/C(n,k)。

输入是 runs.jsonl 的行（dict）；跳过（skipped）的运行一律不计。
"""

from __future__ import annotations

import math
from collections import defaultdict
from math import comb


def valid(rows: list[dict], config: str | None = None) -> list[dict]:
    return [r for r in rows if r.get("verdict") != "skipped" and (config is None or r.get("config") == config)]


def _by_task(rows: list[dict]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        out[r["task"]].append(1 if r.get("verdict") == "pass" else 0)
    return dict(out)


def pass_rate(rows: list[dict]) -> tuple[float, float, int]:
    """(通过率, 按题聚类的标准误, 有效次数)。没有有效运行时返回 (nan, nan, 0)。
    聚类标准误 = sqrt(Σ_题 (Σ_次 (x − p))²) / N；只有一道题时没法估，返回 nan。"""
    xs = _by_task(rows)
    n = sum(len(v) for v in xs.values())
    if n == 0:
        return math.nan, math.nan, 0
    p = sum(sum(v) for v in xs.values()) / n
    if len(xs) < 2:
        return p, math.nan, n
    g = len(xs)
    s = sum(sum(x - p for x in v) ** 2 for v in xs.values())
    se = math.sqrt(s * g / (g - 1)) / n            # 小样本修正 G/(G−1)
    return p, se, n


def pass_hat_k(rows: list[dict], k: int) -> tuple[float, int]:
    """τ-bench 的 pass^k：每道题 C(c,k)/C(n,k) 的平均（只算跑够 k 次的题）。返回 (值, 参与的题数)。"""
    vals = []
    for v in _by_task(rows).values():
        if len(v) >= k:
            vals.append(comb(sum(v), k) / comb(len(v), k))
    return (sum(vals) / len(vals), len(vals)) if vals else (math.nan, 0)


def self_check_agreement(rows: list[dict]) -> tuple[float, int, int, int]:
    """流程自己的结论和隐藏测试一致的比例（只算多 Agent 配置有自检的运行）。
    返回 (一致率, 有效次数, 流程判没过但隐藏测试过了的次数, 流程判过了但隐藏测试没过的次数)。"""
    n = agree = too_strict = too_loose = 0
    for r in rows:
        fv = str(r.get("flow_verdict") or "")
        if r.get("verdict") not in ("pass", "fail") or not fv:
            continue
        flow_ok = fv.startswith("pass")
        judge_ok = r["verdict"] == "pass"
        n += 1
        agree += flow_ok == judge_ok
        too_strict += (not flow_ok) and judge_ok
        too_loose += flow_ok and not judge_ok
    return (agree / n if n else math.nan), n, too_strict, too_loose


def cost_per_pass(rows: list[dict]) -> float:
    """所有有效运行的总花费 ÷ 通过次数（没有通过时为 inf）。"""
    passes = sum(1 for r in rows if r.get("verdict") == "pass")
    total = sum(float(r.get("cost") or 0) for r in rows)
    return total / passes if passes else math.inf


def mean_attempts_of_passes(rows: list[dict]) -> float:
    """收敛速度：通过的运行平均用了几次尝试。"""
    xs = [int(r.get("attempts") or 0) for r in rows if r.get("verdict") == "pass"]
    return sum(xs) / len(xs) if xs else math.nan


def paired_diff(before: list[dict], after: list[dict]) -> tuple[float, float, int, list[tuple[str, float, float]]]:
    """同一批题上逐题配对：每道题 after 通过率 − before 通过率，再对题求平均和标准误。
    只算前后都有有效运行的题。返回 (平均差, 标准误, 题数, [(题, 前, 后)])。"""
    b, a = _by_task(before), _by_task(after)
    common = sorted(set(b) & set(a))
    per = [(t, sum(b[t]) / len(b[t]), sum(a[t]) / len(a[t])) for t in common]
    if not per:
        return math.nan, math.nan, 0, []
    d = [y - x for _, x, y in per]
    m = sum(d) / len(d)
    se = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1) / len(d)) if len(d) > 1 else math.nan
    return m, se, len(d), per


def pct(x: float, se: float | None = None) -> str:
    if x != x:                                       # nan
        return "-"
    s = f"{x:.0%}"
    if se is not None and se == se:
        s += f" ± {se:.0%}"
    return s


def num(x: float, fmt: str = ".2f") -> str:
    if x != x:
        return "-"
    if x == math.inf:
        return "∞"
    return format(x, fmt)


# 前后对比时，改进后的配置和改进前的哪种配置配对（full 的前身是 verified，DECISIONS 第 119 条）
PAIRS = (("single", "single"), ("none", "none"), ("verified", "full"))


def compare_report(before: list[dict], after: list[dict], before_name: str = "改进前",
                   after_name: str = "改进后") -> str:
    """前后两次评测的对比（Markdown）。single 只在改进前跑时，前后共用同一份数据（这条路径不受改进影响）。"""
    lines = [f"# 前后对比：{before_name} → {after_name}", "",
             "通过率后面的 ± 是按题聚类的标准误；差值是逐题配对后的平均差 ± 标准误（Miller 2024）。"
             "差值的绝对值小于两倍标准误时，不能说有变化。", "",
             "| 配置（前 → 后） | 改进前通过率 | 改进后通过率 | 逐题配对差值 | 题数 | 自检准确率 前 → 后 | 每通过一次花费（元）前 → 后 |",
             "|---|---|---|---|---|---|---|"]
    detail = []
    for cb, ca in PAIRS:
        rb = valid(before, cb)
        ra = valid(after, ca) or (rb if ca == "single" else [])
        if not rb and not ra:
            continue
        pb, sb, _ = pass_rate(rb)
        pa, sa, _ = pass_rate(ra)
        d, sd, nt, per = paired_diff(rb, ra)
        ab, ae = ((math.nan, math.nan) if cb == "single" else      # 单 Agent 不写测试，没有自检
                  (self_check_agreement(rb)[0], self_check_agreement(ra)[0]))
        same = " （同一份数据）" if ca == "single" and not valid(after, ca) else ""
        lines.append(f"| {cb} → {ca}{same} | {pct(pb, sb)} | {pct(pa, sa)} | "
                     f"{('+' if d == d and d >= 0 else '')}{pct(d, sd)} | {nt} | {pct(ab)} → {pct(ae)} | "
                     f"{num(cost_per_pass(rb))} → {num(cost_per_pass(ra))} |")
        if per:
            detail += ["", f"### {cb} → {ca}：每道题的通过率", "", "| 题目 | 前 | 后 |", "|---|---|---|"]
            detail += [f"| {t} | {x:.0%} | {y:.0%} |" for t, x, y in per]
    return "\n".join(lines + detail) + "\n"

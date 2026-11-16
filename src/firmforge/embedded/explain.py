"""模块没通过时，讲清"从第一次失败到最后到底发生了什么"和"放行有什么风险"（DECISIONS 第 139 条）。

负责人的要求：人工放行的前提是用户知道为什么没通过、放行的风险有哪些。这里只按运行记录（事件、每次尝试的报告和
代码快照）程序生成，不经模型改写——讲解本身不能再出错。
"""

from __future__ import annotations

from pathlib import Path

from .roles import BLAME_NAMES

LAYER_NAMES = {"compliance": "合规检查", "build": "编译", "board_test": "上板测试", "host_test": "电脑上的测试"}


def _first_problem(report: str) -> str:
    for ln in report.splitlines():
        ln = ln.strip()
        if ln.endswith("：") and len(ln) < 12:                 # "编译失败："这种标题行：看下一行的具体报错
            continue
        if ln.startswith(("失败：", "编译失败", "main/")) or "error:" in ln or "违规" in ln:
            return ln[:160]
    return next((ln.strip()[:160] for ln in report.splitlines() if ln.strip()), "")


def _snapshot(run_dir: Path, module: str, n: int, files: tuple[str, ...]) -> dict[str, str]:
    d = Path(run_dir) / "nodes" / module / f"attempt_{n}" / "main"
    out = {}
    for f in files:
        p = d / f
        if p.is_file():
            out[f] = p.read_text(encoding="utf-8", errors="replace")
    return out


def explain_failure(events: list[dict], run_dir: Path, module) -> list[str]:
    """一行一件事，按先后：每次检查的结果和第一处问题、诊断判给谁、交给谁改、补测试、退回设计……最后一句总结。"""
    mid = module.id
    lines: list[str] = []
    attempts: dict[int, dict] = {}
    node_dir = Path(run_dir) / "nodes" / mid
    on_disk = sorted(int(p.name.split("_")[1]) for p in node_dir.glob("attempt_*") if p.name.split("_")[1].isdigit()) \
        if node_dir.is_dir() else []
    with_verify = {int((e.get("data") or {}).get("attempt") or 0) for e in events
                   if e.get("node_id") == mid and e.get("type") == "verify_finished"}

    def report(n: int) -> str:
        rep = node_dir / f"attempt_{n}" / "report.txt"
        return rep.read_text(encoding="utf-8", errors="replace") if rep.is_file() else ""

    def from_report(n: int) -> None:
        """这次检查没有 verify 事件（例如旧记录）：按这次的报告写一行。"""
        attempts.setdefault(n, {"failed": None})
        rep = report(n)
        why = _first_problem(rep)
        bad = any(k in rep for k in ("失败", "error:", "违规", "没通过"))
        lines.append(f"第 {n} 次：没通过——{why}" if bad else f"第 {n} 次：通过了当时的测试")

    def upto(n: int) -> None:
        for k in on_disk:
            if k <= n and k not in attempts and k not in with_verify:
                from_report(k)
    for e in events:
        if e.get("node_id") != mid:
            continue
        t, d = e.get("type"), e.get("data") or {}
        n = int(d.get("attempt") or 0)
        if n:
            upto(n)
        if t == "verify_finished" and n:
            a = attempts.setdefault(n, {"failed": None})
            if d.get("ok") is False and a["failed"] is None and d.get("failed_layer") not in (None, "skipped"):
                a["failed"] = d.get("failed_layer")
                why = _first_problem(report(n))
                lines.append(f"第 {n} 次：{LAYER_NAMES.get(a['failed'], a['failed'])}没通过" + (f"——{why}" if why else ""))
            elif d.get("scope") in ("board_test", "host_test") and d.get("ok"):
                lines.append(f"第 {n} 次：通过了当时的测试")
        elif t == "diagnosis":
            who = BLAME_NAMES.get(d.get("blame"), d.get("blame"))
            lines.append(f"　诊断：错在{who}。{(d.get('instruction') or '')[:160]}")
        elif t == "mutation_test" and d.get("survived"):
            lines.append(f"　平台把代码故意改坏 {d.get('total')} 处，测试没发现 {d.get('survived')} 处，让测试 Agent 补测试")
        elif t == "hardened_reverted":
            lines.append(f"　{d.get('note', '补强后的测试没通过')}")
        elif t == "handoff_checked" and not d.get("ok"):
            lines.append(f"　测试 Agent 没照诊断删掉用例：{'、'.join(d.get('left', []))}")
        elif t == "design_revised":
            lines.append(f"　设计改了验收条件：{'；'.join(d.get('new', []))[:160]}")
        elif t == "escalated" and d.get("level") == "rule":
            lines.append(f"　诊断认为规则 {d.get('rule')} 有问题，先退回规则核对")
    upto(max(on_disk) if on_disk else 0)
    # 代码到底改没改过：按每次尝试的快照比
    code_files = tuple(f for f in module.files if f.endswith((".c", ".h")))
    snaps = [_snapshot(run_dir, mid, n, code_files) for n in sorted(set(attempts) | set(on_disk))]
    snaps = [s for s in snaps if s]
    if len(snaps) >= 2:
        changed = sum(1 for a, b in zip(snaps, snaps[1:]) if a != b)
        lines.append(f"代码在 {len(snaps)} 次尝试里改过 {changed} 次" +
                     ("——代码一直没变，问题都出在测试或验收条件上" if changed == 0 else ""))
    blames = [ln for ln in lines if ln.startswith("　诊断：")]
    ever_passed = any("通过了当时的测试" in ln for ln in lines)
    if blames and all("错在测试" in b for b in blames):
        lines.append("每次诊断都判为测试的问题：代码本身可能是对的，"
                     + ("但通过的那一次用的是当时的测试，最终的测试没通过" if ever_passed else "但没有一次能证明它"))
    return lines


def release_risks(module, mr, design=None, passed_files: dict | None = None, last_report: str = "") -> list[str]:
    """放行的风险：哪些验收条件没被证明、交付的是哪一版代码、谁会在它的基础上继续、结论怎么记。"""
    out = []
    acc = list(getattr(module, "acceptance", ()) or ())
    if passed_files:
        out.append("交付的是曾经通过检查的那一版代码，但它通过的是当时的测试；之后更严的测试没通过，"
                   "下面这些验收条件没有被最终的测试证明：")
    else:
        out.append("这个模块一次都没通过检查，交付的是最后一版代码；下面这些验收条件都没有被证明：")
    out += [f"　- {a}" for a in acc] or ["　- （设计没有写验收条件）"]
    first = _first_problem(last_report)
    if first:
        out.append(f"最后一次没通过的地方：{first}")
    if design is not None:
        users = [m.id for m in design.modules if module.id in m.depends_on]
        if users:
            out.append(f"依赖它的模块 {'、'.join(users)} 会接着做，它们会把这个模块当作正常的；"
                       "如果这个模块真的有问题，问题会出现在它们身上")
        out.append("集成和主程序会照常上板运行，这个模块的问题可能让整个程序的结果不对")
    if getattr(module, "layer", "") == "hardware":
        out.append("这是硬件层模块，代码会烧到板子上直接操作外设")
    out.append("这次运行的结论会记成\"人工放行\"，不算通过；报告和交付的工程里都写明是你放行的、你写的理由")
    return out

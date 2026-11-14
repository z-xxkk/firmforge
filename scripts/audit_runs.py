"""运行记录体检（DECISIONS 第 123 条）：把真实运行的记录逐条过一遍，找"不该发生"的情况，按类计数。

只读平台自己的记录（events.jsonl、每次尝试的检查报告、run.json、评测的 runs.jsonl），不读隐藏测试内容，不调用模型。
分类参照 MAST（Cemri 等，NeurIPS 2025）的三大类：任务和角色定义、Agent 之间配合、验证和终止。

用法：python scripts/audit_runs.py [.ff_runs] [--out docs/AUDIT.md] [--since 20261108]
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_BUILD_ERR = re.compile(r"([\w./-]+\.[ch]):\d+(?::\d+)?:\s*(?:fatal )?error:\s*(.*)")
_TEST_FILE = re.compile(r"(^|/)(test_|st_)[\w-]*\.c$")


@dataclass
class Finding:
    code: str
    category: str               # 定义 / 配合 / 验证
    title: str
    hits: list[str] = field(default_factory=list)


CHECKS = {
    "C1": ("配合", "同一个编译错误原样出现在相邻两次尝试（反馈没起作用）"),
    "C2": ("配合", "编译报错只在测试文件里，下一轮却交给编码 Agent（它不能改测试）"),
    "C3": ("配合", "诊断说问题在测试，下一轮却是编码 Agent 在改（或反过来）"),
    "C4": ("验证", "诊断为硬件或接线问题，运行就此停下（评测里接线已上板验证过，多半是误诊）"),
    "C5": ("验证", "流程判没过，交付代码却过了隐藏测试（自检过严）"),
    "C6": ("验证", "流程判通过，交付代码没过隐藏测试（自检过松）"),
    "C7": ("定义", "Agent 调工具的参数不完整（例如 doc_search 没有 query）"),
    "C8": ("定义", "工具调用返回失败"),
    "C9": ("定义", "Agent 没按格式交文件，被要求补交"),
    "C10": ("验证", "诊断 Agent 的输出不合格，按代码问题兜底"),
    "C11": ("配合", "同一个上板失败用例在相邻两次尝试原样重复（改了也没变）"),
    "C12": ("终止", "运行开始了却没有结束事件（崩溃或被中断）"),
    "C13": ("定义", "单次 Agent 调用 token 异常多（超过同角色中位数 4 倍）"),
    "C14": ("配合", "编译报错在驱动代码里，但出错的符号是测试才会用的平台宏（Agent 职责混了）"),
}


def load_events(d: Path) -> list[dict]:
    out = []
    try:
        for line in (d / "events.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def attempt_reports(d: Path) -> dict[int, str]:
    out = {}
    for p in d.glob("nodes/*/attempt_*/report.txt"):
        try:
            n = int(p.parent.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        out[n] = out.get(n, "") + p.read_text(encoding="utf-8", errors="replace")
    return out


def build_errors(report: str) -> list[tuple[str, str]]:
    return [(Path(f).name if not _TEST_FILE.search(f) else f, msg.strip()) for f, msg in _BUILD_ERR.findall(report)]


def failed_cases(report: str) -> set[str]:
    return set(re.findall(r"失败：([^（\n]+)", report))


def judge_rows(root: Path) -> dict[str, dict]:
    """runs.jsonl 里有隐藏测试判定的运行：运行目录名 → 行。"""
    out = {}
    for f in (root / "results").glob("*/runs.jsonl"):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("run_dir"):
                out[Path(r["run_dir"]).name] = r
    return out


def audit(runs_dir: Path, since: str = "") -> tuple[dict[str, Finding], dict]:
    found = {k: Finding(k, c, t) for k, (c, t) in CHECKS.items()}
    judged = judge_rows(ROOT)
    tokens_by_role: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    stats = collections.Counter()
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        if since and d.name[:8] < since:
            continue
        ev = load_events(d)
        if not ev:
            continue
        stats["运行"] += 1
        name = d.name
        types = [e["type"] for e in ev]
        if "run_started" in types and "run_finished" not in types:
            found["C12"].hits.append(name)
        reports = attempt_reports(d)
        # 每次尝试第一个没过的层、之后谁被叫去改
        layer_of: dict[int, str] = {}
        next_role: dict[int, str] = {}           # 第 n 次没过以后，第 n+1 次是谁在干活
        diag_of: dict[int, str] = {}
        for e in ev:
            t, x = e["type"], e.get("data") or {}
            a = x.get("attempt")
            try:
                a = int(a)
            except (TypeError, ValueError):
                a = None
            if t == "verify_finished" and str(x.get("ok")) == "False" and a is not None:
                layer_of.setdefault(a, x.get("failed_layer") or "")
            elif t == "diagnosis" and a is not None:
                diag_of[a] = x.get("blame", "")
                if "诊断输出不合格" in str(x.get("evidence", "")):
                    found["C10"].hits.append(f"{name} 第 {a} 次")
            elif t == "agent_started" and a is not None and x.get("role") in ("coder", "tester"):
                if a >= 2 and x.get("purpose") in ("改代码", "改测试", "按审查意见改"):
                    next_role.setdefault(a - 1, x.get("role"))
            elif t == "human_wait" and "硬件" in str(x.get("reason", "")):
                found["C4"].hits.append(f"{name}：{str(x.get('evidence', ''))[:80]}")
            elif t == "tool_called":
                stats["工具调用"] += 1
                args = x.get("args")
                try:
                    args = json.loads(args) if isinstance(args, str) else (args or {})
                except ValueError:
                    args = {}
                if x.get("tool") == "doc_search" and not str(args.get("query", "")).strip():
                    found["C7"].hits.append(f"{name}：{x.get('agent')} {json.dumps(args, ensure_ascii=False)[:60]}")
            elif t == "tool_result" and x.get("ok") is False:
                found["C8"].hits.append(f"{name}：{x.get('tool')} {str(x.get('summary', ''))[:60]}")
            elif t == "format_retry":
                found["C9"].hits.append(f"{name}：{x.get('agent')} 第 {x.get('attempt')} 次缺 {x.get('missing')}")
            elif t == "agent_finished" and x.get("tokens"):
                tokens_by_role[x.get("role", "?")].append((int(x["tokens"]), f"{name} {x.get('role')} 第 {a} 次"))
        stats["失败的尝试"] += len(layer_of)
        for a, layer in sorted(layer_of.items()):
            rep = reports.get(a, "")
            errs = build_errors(rep) if layer == "build" else []
            files = {f for f, _ in errs}
            only_tests = bool(files) and all(_TEST_FILE.search(f) for f in files)
            if layer == "build":
                stats["编译没过的尝试"] += 1
                if only_tests:
                    stats["其中报错只在测试里"] += 1
                    if next_role.get(a) == "coder":
                        found["C2"].hits.append(f"{name} 第 {a} 次")
                for f, msg in errs:
                    if not _TEST_FILE.search(f) and re.search(r"'FF_(REG|TEST)_\w+'|ff_test\.h|ff_regs\.h", msg):
                        found["C14"].hits.append(f"{name} 第 {a} 次：{f} {msg[:60]}")
                        break
                nxt = reports.get(a + 1)
                if nxt is not None and errs and layer_of.get(a + 1) == "build":
                    if {m for _, m in errs} & {m for _, m in build_errors(nxt)}:
                        found["C1"].hits.append(f"{name} 第 {a}→{a + 1} 次：{errs[0][1][:60]}")
            if layer == "board_test":
                nxt = reports.get(a + 1)
                cases = failed_cases(rep)
                if nxt is not None and layer_of.get(a + 1) == "board_test" and cases and cases == failed_cases(nxt):
                    found["C11"].hits.append(f"{name} 第 {a}→{a + 1} 次：{len(cases)} 个用例原样没过")
            blame = diag_of.get(a)
            who = next_role.get(a)
            if blame in ("code", "test") and who and {"code": "coder", "test": "tester"}[blame] != who:
                found["C3"].hits.append(f"{name} 第 {a} 次：诊断 {blame}，接手的是 {who}")
        r = judged.get(name)
        if r and r.get("config") != "single" and r.get("verdict") in ("pass", "fail") and r.get("flow_verdict"):
            flow_ok = str(r["flow_verdict"]).startswith("pass")
            if not flow_ok and r["verdict"] == "pass":
                found["C5"].hits.append(f"{name}（{r['attempts']} 次，{r['cost']:.2f} 元）")
            if flow_ok and r["verdict"] == "fail":
                found["C6"].hits.append(name)
    for role, xs in tokens_by_role.items():
        if len(xs) < 5:
            continue
        med = sorted(t for t, _ in xs)[len(xs) // 2]
        for t, where in xs:
            if t > 4 * med and t > 20000:
                found["C13"].hits.append(f"{where}：{t // 1000}k token（中位数 {med // 1000}k）")
    return found, stats


def render(found: dict[str, Finding], stats: dict, since: str) -> str:
    lines = ["# 运行记录体检", "",
             f"范围：.ff_runs 里{'自 ' + since + ' 起' if since else '全部'}的 {stats.get('运行', 0)} 次运行；"
             f"失败的尝试 {stats.get('失败的尝试', 0)} 次，其中编译没过 {stats.get('编译没过的尝试', 0)} 次"
             f"（报错只在测试里 {stats.get('其中报错只在测试里', 0)} 次）；工具调用 {stats.get('工具调用', 0)} 次。",
             "只读平台自己的记录，不读隐藏测试内容。分类参照 MAST 的三大类（定义、配合、验证与终止）。", "",
             "| 编号 | 类别 | 检查 | 命中次数 |", "|---|---|---|---|"]
    for f in sorted(found.values(), key=lambda f: -len(f.hits)):
        lines.append(f"| {f.code} | {f.category} | {f.title} | {len(f.hits)} |")
    for f in sorted(found.values(), key=lambda f: -len(f.hits)):
        if f.hits:
            lines += ["", f"## {f.code} {f.title}（{len(f.hits)}）", ""]
            lines += [f"- {h}" for h in f.hits[:15]]
            if len(f.hits) > 15:
                lines.append(f"- ……另外 {len(f.hits) - 15} 条")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="?", default=str(ROOT / ".ff_runs"))
    ap.add_argument("--since", default="", help="只看这一天以后的运行，如 20261108")
    ap.add_argument("--out", help="报告写到这个文件（Markdown）")
    a = ap.parse_args(argv)
    found, stats = audit(Path(a.runs), a.since)
    text = render(found, stats, a.since)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

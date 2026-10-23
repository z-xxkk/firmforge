"""命令行入口。

退出码：0 = 通过，1 = 失败，2 = 不完整，3 = 用法或配置错误。
输出使用 [PASS] [FAIL] [SKIP] 这类 ASCII 标记，避免 Windows 控制台编码问题。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__, config
from .config import Toolchain
from .tasks import Task, TaskError, discover_tasks, load_task
from .verifier import OBJECTIVE_LAYERS, check_task, verify
from .verifier.report import FAIL, INCOMPLETE, PASS, SKIPPED, Report
from .verifier.selfcheck import MUTANT_OK, MUTANT_UNVERIFIABLE, SelfCheckResult

EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3
MARK = {PASS: "[PASS]", FAIL: "[FAIL]", SKIPPED: "[SKIP]", INCOMPLETE: "[INCO]"}
VERDICT_EXIT = {PASS: EXIT_PASS, FAIL: EXIT_FAIL, INCOMPLETE: EXIT_INCOMPLETE}
MAX_DIAGS = 8


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse 默认退出码是 2，这里改成 3
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: 用法错误：{message}\n")


def _tasks_dir() -> Path:
    cwd_tasks = Path.cwd() / "tasks"
    return cwd_tasks if cwd_tasks.is_dir() else config.project_root() / "tasks"


def _load(path: str) -> Task:
    try:
        return load_task(path)
    except TaskError as exc:
        raise UsageError(str(exc)) from exc


# ---------------------------------------------------------------- doctor

def cmd_doctor(args) -> int:
    tc = Toolchain.from_env()
    print(f"FirmForge {__version__}，Python {sys.version.split()[0]}")
    missing = 0
    for name, (var, _default, desc) in config.TOOL_SPECS.items():
        path = tc.resolve(name)
        if path:
            ver = config.tool_version(path)
            print(f"[ OK ] {getattr(tc, name):<20} {desc}  {ver}")
        else:
            missing += 1
            print(f"[MISS] {getattr(tc, name):<20} {desc}  未找到（可用 {var} 指定）")
    unity = config.unity_dir() / "unity.c"
    if unity.is_file():
        print(f"[ OK ] Unity                {unity.parent}")
    else:
        missing += 1
        print(f"[MISS] Unity                找不到 {unity}")
    key_set = bool(os.environ.get("DEEPSEEK_API_KEY"))
    print(f"[INFO] DEEPSEEK_API_KEY     {'已设置' if key_set else '未设置'}（阶段 2 才需要）")
    if missing:
        print(f"\n有 {missing} 项缺失：对应的判定层会标记为 skipped，结论最多是 incomplete。")
        return EXIT_INCOMPLETE
    print("\n所有工具可用。")
    return EXIT_PASS


# ---------------------------------------------------------------- tasks

def cmd_tasks(args) -> int:
    tasks = discover_tasks(Path(args.dir) if args.dir else _tasks_dir())
    if not tasks:
        print("没有找到任务。")
        return EXIT_FAIL
    print(f"{'id':<16}{'类别':<16}{'难度':<6}{'变异体':<8}标题")
    for t in tasks:
        print(f"{t.id:<16}{t.category:<16}{t.difficulty:<6}{len(t.mutants):<8}{t.title}")
    return EXIT_PASS


# ---------------------------------------------------------------- verify

def print_report(report: Report, max_diags: int = MAX_DIAGS) -> None:
    for lr in report.layers:
        mark = MARK.get(lr.status, "[????]")
        extra = "" if lr.complete or lr.status == SKIPPED else "（不完整）"
        metrics = ", ".join(f"{k}={v}" for k, v in lr.metrics.items() if not isinstance(v, dict))
        print(f"{mark} {lr.title} {lr.layer}{extra}  {lr.duration_s:.2f}s  {metrics}")
        shown = [d for d in lr.diagnostics if d.severity != "info"][:max_diags]
        for d in shown:
            loc = f"{d.file}:{d.line}: " if d.file and d.line else (f"{d.file}: " if d.file else "")
            print(f"       - {loc}{d.message}")
        rest = len([d for d in lr.diagnostics if d.severity != "info"]) - len(shown)
        if rest > 0:
            print(f"       … 另有 {rest} 条")
        for note in lr.notes:
            print(f"       * {note}")
    print(f"结论：{MARK[report.verdict]} {report.verdict}", end="")
    if report.failed_layer:
        print(f"，首个失败层 {report.failed_layer}，签名 {report.signature}", end="")
    print(f"，耗时 {report.duration_s:.2f}s")


def cmd_verify(args) -> int:
    workdir = Path(args.workdir)
    if not workdir.is_dir():
        raise UsageError(f"工作目录不存在：{workdir}")
    task = _load(args.task)
    layers = None
    if args.layers:
        layers = [x.strip() for x in args.layers.split(",") if x.strip()]
        bad = [x for x in layers if x not in OBJECTIVE_LAYERS]
        if bad:
            raise UsageError(f"未知的层：{', '.join(bad)}；可选 {', '.join(OBJECTIVE_LAYERS)}")
    report = verify(workdir, task, layers=layers, include_hidden=args.hidden,
                    stop_on_fail=not args.no_stop)
    print(f"判定 {workdir} （任务 {task.id}）")
    print_report(report)
    if args.json:
        Path(args.json).write_text(report.to_json(), encoding="utf-8")
        print(f"报告已写入 {args.json}")
    return VERDICT_EXIT[report.verdict]


# ---------------------------------------------------------------- check-task

def print_selfcheck(res: SelfCheckResult) -> None:
    t = res.task
    print(f"== 任务 {t.id}（{t.title}）")
    ref = res.reference
    print(f"  参考实现：{MARK[ref.verdict]} {ref.verdict}")
    for lr in ref.layers:
        if lr.status != PASS or not lr.complete:
            reason = "；".join(lr.notes) or "；".join(d.message for d in lr.diagnostics[:3])
            mark = MARK[INCOMPLETE] if lr.status == PASS else MARK.get(lr.status, "[????]")
            print(f"    {mark} {lr.layer}：{reason}")
            if lr.status == FAIL:
                for d in lr.diagnostics[:MAX_DIAGS]:
                    print(f"       - {d.message}")
    m = res.measured
    b = t.budget
    fmt = lambda v: "-" if v is None else str(v)
    print(f"  实测资源：flash={fmt(m['flash'])}（预算 {fmt(b.flash_bytes)}），"
          f"ram={fmt(m['ram'])}（预算 {fmt(b.ram_bytes)}），"
          f"最大栈帧={fmt(m['stack'])}（预算 {fmt(b.stack_bytes)}）")
    if m["flash"] is None:
        print("    * 资源层未运行（需要 arm-none-eabi 工具链），无法实测")
    width = max([len(mr.mutant.file) for mr in res.mutants] + [10]) + 2
    print(f"  {'变异体':<{width - 3}}{'期望失败层':<11}{'实际首个失败层':<11}结果")
    for mr in res.mutants:
        mark = {MUTANT_OK: "[PASS]", MUTANT_UNVERIFIABLE: "[SKIP] 预期层未运行"}.get(mr.status, "[FAIL]")
        print(f"  {mr.mutant.file:<{width}}{mr.mutant.expect_fail:<16}{str(mr.actual):<18}{mark}")
    print(f"  任务结论：{MARK[res.verdict]} {res.verdict}\n")


def cmd_check_task(args) -> int:
    if args.all:
        tasks = discover_tasks(_tasks_dir())
        if not tasks:
            raise UsageError(f"{_tasks_dir()} 下没有任务")
    elif args.dirs:
        tasks = [_load(d) for d in args.dirs]
    else:
        raise UsageError("请给出任务目录，或使用 --all")
    verdicts = []
    for t in tasks:
        res = check_task(t)
        print_selfcheck(res)
        verdicts.append(res.verdict)
    print("汇总：" + "，".join(f"{t.id}={v}" for t, v in zip(tasks, verdicts)))
    if FAIL in verdicts:
        return EXIT_FAIL
    if INCOMPLETE in verdicts:
        return EXIT_INCOMPLETE
    return EXIT_PASS


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="firmforge", description="面向嵌入式固件开发的多 Agent 编排与验证平台")
    p.add_argument("--version", action="version", version=f"firmforge {__version__}")
    sub = p.add_subparsers(dest="command", parser_class=_Parser)

    s = sub.add_parser("doctor", help="检查工具链是否可用")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("tasks", help="列出评测任务")
    s.add_argument("--dir", help="任务目录（默认 ./tasks）")
    s.set_defaults(func=cmd_tasks)

    s = sub.add_parser("check-task", help="任务自检：参考实现要通过，变异体要在预期层失败")
    s.add_argument("dirs", nargs="*", help="任务目录")
    s.add_argument("--all", action="store_true", help="自检 ./tasks 下全部任务")
    s.set_defaults(func=cmd_check_task)

    s = sub.add_parser("verify", help="对一个工作目录跑判定器")
    s.add_argument("workdir")
    s.add_argument("--task", required=True, help="任务目录")
    s.add_argument("--hidden", action="store_true", help="同时运行隐藏测试（仅供人工评测使用）")
    s.add_argument("--layers", help="只运行指定层，逗号分隔")
    s.add_argument("--no-stop", action="store_true", help="某层失败后继续运行后面的层")
    s.add_argument("--json", help="把报告写入 JSON 文件")
    s.set_defaults(func=cmd_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())

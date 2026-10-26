"""命令行入口。

退出码：0 = 通过，1 = 失败，2 = 不完整，3 = 用法或配置错误。
输出使用 [PASS] [FAIL] [SKIP] 这类 ASCII 标记，避免 Windows 控制台编码问题。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from . import __version__, config
from .config import Toolchain
from .evaluation.configs import ConfigError, get_config, parse_configs
from .agents.registry import RegistryError
from .evaluation.runner import estimate, evaluate, run_config
from .llm import LLMError, OpenAICompatClient
from .orchestrator.baseline import RunInterrupted
from .orchestrator.dag import Plan
from .orchestrator.workflow import WorkflowError
from .runs import DEFAULT_RUNS_DIR, read_events
from .store import Store
from .tasks import Task, TaskError, adhoc_task, discover_tasks, load_task
from .verifier import OBJECTIVE_LAYERS, check_task, verify
from .verifier.report import FAIL, INCOMPLETE, PASS, SKIPPED, Report
from .verifier.selfcheck import MUTANT_OK, MUTANT_UNVERIFIABLE, SelfCheckResult

EXIT_PASS, EXIT_FAIL, EXIT_INCOMPLETE, EXIT_USAGE = 0, 1, 2, 3
EXIT_INTERRUPTED = 130                 # 按 Ctrl+C 中断，沿用 shell 惯例
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
          f"调用链最坏栈={fmt(m['stack'])}（预算 {fmt(b.stack_bytes)}）")
    if m.get("stack_path"):
        print(f"    * 最坏调用链：{m['stack_path']}")
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


# ---------------------------------------------------------------- LLM 相关命令（阶段 2）

def make_llm(settings: config.Settings):
    """创建真实的 LLM 客户端；单元测试会替换这个函数。"""
    return OpenAICompatClient.from_settings(settings)


def _secrets(settings: config.Settings) -> tuple[str, ...]:
    key = os.environ.get(settings.llm.api_key_env, "")
    return (key,) if key else ()


ROLE_NAMES = {"planner": "规划", "coder": "编码", "tester": "测试", "reviewer": "审查", "diagnoser": "诊断"}


def format_event(e: dict, t0: float) -> str:
    """把一个事件格式化成一行中文，实时显示和回放共用。"""
    d = e.get("data", {})
    t = e.get("ts", t0) - t0
    node = e.get("node_id")
    typ = e.get("type")
    role = d.get("role")
    who = f"[{node}] " if role else ""          # 多 Agent 运行的 Agent 事件前面标出模块
    if typ == "run_started":
        msg = f"运行开始：任务 {d.get('task')}，配置 {d.get('config')}，模型 {d.get('model')}"
        if d.get("strategy"):
            msg += f"，调度 {d['strategy']}"
    elif typ == "plan_ready":
        mods = d.get("modules", [])
        parts = [m["id"] + (f"（依赖 {', '.join(m['depends_on'])}）" if m.get("depends_on") else "") for m in mods]
        what = f"重新规划完成（替换模块 {d.get('replaced')}）" if d.get("replan") else "规划完成"
        msg = f"{what}：{len(mods)} 个模块：{'、'.join(parts)}"
        if d.get("fallback"):
            msg += "（规划 Agent 多次不合格，退化为单模块）"
    elif typ == "human_wait" and d.get("reason"):
        msg = f"[{node}] 等待负责人给出提示（{d['reason']}）"
    elif typ == "human_wait":
        msg = f"等待负责人确认规划（第 {d.get('round')} 次）"
    elif typ == "human_done" and node != "confirm_plan":
        msg = f"[{node}] 负责人给出了提示：{d.get('comment')}" if d.get("approved") else f"[{node}] 负责人放弃了这个模块"
    elif typ == "human_done":
        msg = "负责人确认了规划" if d.get("approved") else f"负责人没有确认规划：{d.get('comment')}"
    elif typ == "escalated":
        msg = f"[{node}] 升级到第 {d.get('level')} 级（{d.get('name')}）：{d.get('reason')}"
        if d.get("knowledge"):
            msg += "；找到资料：" + "、".join(d["knowledge"])
    elif typ == "experience_used":
        msg = f"[{node}] {d.get('where')}时参考了 {len(d.get('ids', []))} 条过往经验"
    elif typ == "experience_saved":
        msg = f"[{node}] {'合并进已有经验' if d.get('merged') else '记下一条新经验'}（{d.get('layer')}：{d.get('pattern')}）"
    elif typ == "node_ready":
        msg = f"模块 {node} 就绪（{d.get('title', '')}）"
    elif typ == "node_assigned":
        reason = d.get("scores", {}).get("reason", "")
        if role == "planner":
            msg = f"规划分配给 {d.get('agent')}"
        else:
            what = f" 的{ROLE_NAMES[role]}" if role in ROLE_NAMES else " "
            msg = f"模块 {node}{what}分配给 {d.get('agent')}"
        msg += f"（{reason}）" if reason else ""
    elif typ == "agent_started":
        msg = f"{who}{d.get('agent')} 开始" + (d.get("purpose") or f"第 {d.get('attempt')} 次编写")
    elif typ == "agent_finished":
        if not d.get("ok"):
            msg = f"[FAIL] {who}{d.get('agent')} 调用失败：{d.get('error')}"
        else:
            cost = f"token {d.get('tokens')}，耗时 {d.get('latency_s')}s"
            if d.get("parse_error"):
                msg = f"{who}{d.get('agent')} 的输出无法使用（{d['parse_error']}）；{cost}"
            elif role == "planner":
                ok = d.get("valid")
                msg = (f"{who}{d.get('agent')} 交出规划：" + ("通过检查" if ok else "没通过检查："
                       + "；".join(d.get("problems", [])[:3])) + f"；{cost}")
            elif role == "diagnoser":
                msg = f"{who}{d.get('agent')} 诊断完成：责任 {d.get('blame')}；{cost}"
            elif role == "reviewer":
                msg = f"{who}{d.get('agent')} 审查完成：{d.get('verdict')}，{d.get('issues')} 条意见；{cost}"
            else:
                msg = f"{who}{d.get('agent')} 交卷：{', '.join(d.get('files', [])) or '没有文件'}；{cost}"
                if d.get("rejected"):
                    msg += f"；被拒绝的路径：{', '.join(d['rejected'])}"
    elif typ == "verify_started":
        pre = f"[{node}] " if d.get("scope") == "module" else ""
        msg = "集成判定开始" if d.get("scope") == "all" else f"{pre}第 {d.get('attempt')} 次判定开始"
    elif typ == "verify_finished":
        v = d.get("verdict")
        what = "集成判定" if d.get("scope") == "all" else (
            f"[{node}] " if d.get("scope") == "module" else "") + f"第 {d.get('attempt')} 次判定"
        msg = f"{MARK.get(v, '[????]')} {what}：{v}"
        if d.get("failed_layer"):
            msg += f"，首个失败层 {d['failed_layer']}，签名 {d.get('signature')}"
    elif typ == "diagnosis":
        msg = (f"[{node}] 诊断第 {d.get('attempt')} 次：责任 {d.get('blame')} → 交给{ROLE_NAMES.get(d.get('route'), d.get('route'))}"
               f" Agent（把握 {d.get('confidence')}）：{d.get('summary')}")
    elif typ == "rollback":
        msg = f"[{node}] 回滚：第 {d.get('from_attempt')} 次 → 第 {d.get('to_attempt')} 次（{d.get('reason')}）"
    elif typ == "tool_called":
        msg = f"{who}{d.get('agent')} 调用工具 {d.get('tool')}"
    elif typ == "tool_result":
        msg = (f"{who}{d.get('agent')} 的工具 {d.get('tool')}：" + ("完成" if d.get("ok") else "失败")
               + (f"，{d['summary']}" if d.get("summary") else "") + f"（{d.get('latency_s')}s）")
    elif typ == "node_done":
        msg = f"模块 {node} 完成，交付第 {d.get('attempt')} 次尝试（{d.get('verdict')}）"
    elif typ == "node_failed" and d.get("verdict") == "interrupted":
        msg = f"模块 {node} 被中断，已完成 {d.get('attempts')} 次尝试"
    elif typ == "node_failed" and d.get("verdict") == "replanned":
        msg = f"模块 {node} 已重新规划，由新的模块接替"
    elif typ == "node_failed" and d.get("verdict") == "skipped":
        msg = f"[SKIP] 模块 {node} 跳过：{d.get('reason')}"
    elif typ == "node_failed":
        msg = f"[FAIL] 模块 {node} 未通过，共尝试 {d.get('attempts')} 次"
    elif typ == "run_finished" and d.get("verdict") == "interrupted":
        msg = f"运行被中断：已完成 {d.get('attempts')} 次尝试，token {d.get('tokens')}，成本 {d.get('cost')} 元"
    elif typ == "run_finished":
        mods = f"{d['modules']} 个模块，" if d.get("modules") else ""
        msg = (f"运行结束：{MARK.get(d.get('verdict'), '[FAIL]')} {d.get('verdict')}，{mods}尝试 {d.get('attempts')} 次，"
               f"token {d.get('tokens')}，成本 {d.get('cost')} 元，用时 {d.get('wall_s')}s")
    else:
        msg = f"{typ} {node or ''} {json.dumps(d, ensure_ascii=False)}"
    return f"[{t:7.1f}s] {msg}"


class LivePrinter:
    """实时打印事件；等待模型回复时每隔 heartbeat_s 秒提示一次，避免看起来像卡住。"""

    def __init__(self, heartbeat_s: float = 30.0, out=None):
        self.heartbeat_s = heartbeat_s
        self.out = out
        self.t0: float | None = None
        self._waiting_since: float | None = None
        self._in_flight: set[tuple] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)
        self._thread.start()

    def _print(self, text: str) -> None:
        print(text, flush=True, file=self.out or sys.stdout)

    def __call__(self, e: dict) -> None:
        with self._lock:
            if self.t0 is None:
                self.t0 = e["ts"]
            # 按（模块, Agent）记录正在等回复的调用：并行时一个模块开始判定，不影响另一个模块还在等的 Agent；
            # 单 Agent 基线带格式提醒重试时会连发两次 agent_started，用集合也不会多算
            key = (e.get("node_id"), e.get("data", {}).get("agent"))
            if e["type"] == "agent_started":
                self._in_flight.add(key)
            elif e["type"] == "agent_finished":
                self._in_flight.discard(key)
            elif e["type"] == "run_finished":
                self._in_flight.clear()
            # 每来一个事件就重新计时；还有 Agent 在等回复才提示
            self._waiting_since = time.monotonic() if self._in_flight else None
            self._print(format_event(e, self.t0))

    def _beat(self) -> None:
        shown = 0
        while not self._stop.wait(min(1.0, self.heartbeat_s / 4)):
            with self._lock:
                if self._waiting_since is None or self.t0 is None:
                    shown = 0
                    continue
                waited = time.monotonic() - self._waiting_since
                if waited >= (shown + 1) * self.heartbeat_s:
                    shown = int(waited // self.heartbeat_s)
                    elapsed = time.time() - self.t0
                    self._print(f"[{elapsed:7.1f}s] …模型还在思考，已等待 {int(waited)} 秒（开启思考模式时通常要几分钟）")

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def cmd_models(args) -> int:
    settings = config.load_settings()
    client = make_llm(settings)
    try:
        names = client.list_models()
    except LLMError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    print(f"可用型号（{client.base_url}）：")
    for n in names:
        print(f"  {n}")
    tiers = "，".join(f"{k}={v.name or '未填写'}" for k, v in settings.models.items())
    print(f"\n当前 firmforge.toml：{tiers}")
    print("在 firmforge.toml 的 [models.fast] / [models.strong] 里填写 name 和价格（元 / 百万 token）。")
    return EXIT_PASS


def _run_task(args) -> Task:
    if args.task and (args.spec or args.include):
        raise UsageError("--task 与 --spec/--include 只能二选一")
    if args.task:
        return _load(args.task)
    if args.spec and args.include:
        try:
            return adhoc_task(args.spec, args.include)
        except TaskError as exc:
            raise UsageError(str(exc)) from exc
    raise UsageError("请给出 --task <任务目录>，或者同时给出 --spec 和 --include")


def terminal_confirm(plan: Plan) -> tuple[bool, str]:
    """人工节点的终端版：列出规划，回车确认，输入意见则让规划 Agent 重做。"""
    print("\n规划结果：")
    for m in plan.modules:
        deps = f"，依赖 {', '.join(m.depends_on)}" if m.depends_on else ""
        print(f"  - {m.id}：{m.title}（{', '.join(m.files)}{deps}）")
        for fn in m.provides:
            print(f"      负责公共函数 {fn}")
        for path, protos in m.interface.items():
            for proto in protos:
                print(f"      接口 {path}：{proto}")
    try:
        ans = input("确认这个规划吗？直接回车 = 确认；输入修改意见后回车 = 让规划 Agent 重做：").strip()
    except EOFError:
        ans = ""
    return ans == "", ans


def terminal_hint(module_id: str, points: list[str]) -> str | None:
    """升级阶梯第 4 级（转人工）的终端版：列出失败要点，输入提示让编码 Agent 再改一次；直接回车 = 放弃。"""
    print(f"\n模块 {module_id} 升级到转人工，失败要点：")
    for p in points:
        print(f"  - {p}")
    try:
        ans = input("输入给编码 Agent 的提示后回车；直接回车 = 放弃这个模块：").strip()
    except EOFError:
        ans = ""
    return ans or None


def terminal_approve(agent: str, tool: str, arguments: dict) -> bool:
    """外部工具调用前的终端确认（MCP 规范：宿主在调用工具前要取得用户同意）。"""
    shown = json.dumps(arguments, ensure_ascii=False)
    print(f"\n{agent} 想调用外部工具 {tool}，参数：{shown[:500]}")
    try:
        ans = input("允许吗？输入 y 后回车允许，直接回车拒绝：").strip().lower()
    except EOFError:
        ans = ""
    return ans in ("y", "yes", "是")


def _external_tools(settings, enabled: bool):
    """按配置启动外部 MCP 服务，返回 (工具箱, 管理器)。不启用时返回 (None, None)，用默认的内置工具。"""
    if not enabled or not any(d.get("enabled") for d in settings.mcp_servers.values()):
        return None, None
    from .mcp import McpManager
    from .tools import default_registry as default_tools
    box = default_tools()
    manager = McpManager(settings.mcp_servers)
    manager.start(box)
    for name, err in manager.errors.items():
        print(f"[SKIP] 外部 MCP 服务 {name} 没有连上：{err}", file=sys.stderr)
    return box, manager


def cmd_run(args) -> int:
    task = _run_task(args)
    cfg = get_config(args.config)
    settings = config.load_settings()
    if cfg.kind == "baseline":
        settings.tier(cfg.tier)
        if cfg.fresh_restarts and args.token_budget is None:
            raise UsageError(f"配置 {cfg.name} 需要 --token-budget（单独运行时手动给出；评测里自动取 full 的平均）")
    elif args.token_budget is not None:
        raise UsageError("--token-budget 只用于 b5")
    if args.human_review and cfg.kind != "multi":
        raise UsageError("--human-review 只用于多 Agent 配置（b3、b4、full）")
    llm = make_llm(settings)
    box, manager = _external_tools(settings, not args.no_mcp)
    store = Store(args.db) if args.db else Store()
    printer = LivePrinter()
    try:
        res = run_config(task, cfg, llm=llm, settings=settings, runs_root=args.runs_dir, store=store,
                         tools=box, approve=terminal_approve,
                         subscribers=(printer,), secrets=_secrets(settings), token_budget=args.token_budget,
                         human_review=args.human_review, human=terminal_confirm if args.human_review else None,
                         hint=terminal_hint if args.human_review else None,
                         tier_override=args.tier, thinking=args.think)
    finally:
        printer.close()
        store.close()
        if manager is not None:
            manager.close()
    print(f"\n运行目录：{res.run_dir}")
    if cfg.kind == "multi":
        print(f"规划：{res.run_dir / 'dag.json'}")
        print(f"交付物：{res.final_dir}" if res.has_code else "没有交付物")
    else:
        print(f"交付物：{res.final_dir}（第 {res.best} 次尝试）" if res.best else "没有交付物")
    if res.error:
        print(f"错误：{res.error}", file=sys.stderr)
        return EXIT_FAIL
    return VERDICT_EXIT.get(res.verdict, EXIT_FAIL)


def cmd_agents(args) -> int:
    """列出注册的 Agent（DECISIONS 第 64 条）：名称、角色、全部版本、档位、思考强度、技能、工具、记忆、历史表现。"""
    from .agents.registry import default_registry
    from .tools import default_registry as default_tools
    reg = default_registry()
    tools = default_tools()
    reg.check_toolsets(set(tools.toolsets()) | {t.name for t in tools.all()})
    settings = config.load_settings()
    stats: dict[str, dict] = {}
    db = Path(args.db) if args.db else None
    if db is None or db.is_file():
        store = Store(args.db) if args.db else Store()
        try:
            for r in store.query("SELECT agent, SUM(attempts) AS attempts, SUM(successes) AS successes, "
                                 "SUM(total_cost) AS cost FROM agent_stats GROUP BY agent"):
                stats[r["agent"]] = r
        finally:
            store.close()
    shown = 0
    for name in reg.names():
        d = reg.get(name)
        if args.role and d.role != args.role:
            continue
        shown += 1
        tier = settings.models.get(d.model_tier)
        names = {"off": "关", "low": "低", "high": "高", "max": "最高"}
        if d.thinking:
            think = names[d.thinking]
        elif tier is None or tier.thinking is None:
            think = "服务端默认"
        else:
            think = names[(tier.reasoning_effort or "high") if tier.thinking else "off"] + "（跟随档位）"
        memory = "、".join(x for x, on in (("经验库", d.memory_experience), ("知识库", d.memory_knowledge)) if on) or "不用"
        st = stats.get(name)
        record = (f"{st['attempts']} 次尝试，通过 {st['successes']} 次，累计 {st['cost'] or 0:.2f} 元"
                  if st and st["attempts"] else "还没有记录")
        print(f"{name}（{d.role}）版本 {'、'.join(reg.versions(name))}")
        print(f"  档位 {d.model_tier}（{tier.name if tier else '未配置'}），思考强度 {think}")
        print(f"  技能：{'；'.join(d.skills) or '（未写）'}")
        print(f"  工具：{'、'.join(d.toolsets) or '无'}；记忆：{memory}")
        print(f"  历史表现：{record}")
    if not shown:
        print("没有符合条件的 Agent。")
    return EXIT_PASS


def cmd_mcp(args) -> int:
    """以 stdio 方式提供 MCP 服务；--selftest 启动自己作为外部服务，按新旧两种协议各走一遍。"""
    from .mcp import McpServer
    if not args.selftest:
        McpServer().serve()
        return EXIT_PASS
    from .mcp.client import McpClient, ServerConfig
    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    cfg = ServerConfig("firmforge", sys.executable, ["-m", "firmforge", "mcp"], env=env, timeout_s=120)
    client = McpClient(cfg)
    try:
        client.connect()
        tools = [t["name"] for t in client.list_tools()]
        text, is_error = client.call_tool("list_tasks", {})
        print(f"[PASS] 连接成功：协议 {client.version}（{'新版' if client.era == 'modern' else '旧版'}）")
        print(f"[PASS] 工具：{', '.join(tools)}")
        print(f"[{'FAIL' if is_error else 'PASS'}] list_tasks 返回 {len(text.splitlines())} 个任务")
        return EXIT_FAIL if is_error else EXIT_PASS
    finally:
        client.close()


def _eval_tasks(spec: str) -> list[Task]:
    all_tasks = discover_tasks(_tasks_dir())
    if spec.strip().lower() == "all":
        if not all_tasks:
            raise UsageError(f"{_tasks_dir()} 下没有任务")
        return all_tasks
    by_id = {t.id: t for t in all_tasks}
    chosen = []
    for tid in (x.strip() for x in spec.split(",") if x.strip()):
        if tid not in by_id:
            raise UsageError(f"没有任务 {tid}；可选：{', '.join(by_id)}")
        chosen.append(by_id[tid])
    if not chosen:
        raise UsageError("--tasks 不能为空")
    return chosen


def _say(text: str) -> None:
    """进度行立即输出：docker run 不带 -t 时标准输出是块缓冲，不 flush 就要等很久才看到。"""
    print(text, flush=True)


def cmd_eval(args) -> int:
    configs = parse_configs(args.configs)
    tasks = _eval_tasks(args.tasks)
    if args.runs < 1:
        raise UsageError("--runs 至少为 1")
    settings = config.load_settings()
    for cfg in configs:
        if cfg.kind == "baseline":
            settings.tier(cfg.tier)   # 先检查型号都填了（多 Agent 的档位在引擎启动时检查）
    if any(c.kind == "multi" for c in configs):
        for tier in ("fast", "strong"):
            settings.tier(tier)
    llm = make_llm(settings)
    out = Path(args.out)
    if args.estimate:
        printer = LivePrinter()
        try:
            est = estimate(tasks, configs, args.runs, out, llm=llm, settings=settings, subscribers=(printer,),
                           secrets=_secrets(settings), progress=_say)
        finally:
            printer.close()
        print(f"\n估算（按任务 {est['sample_task']} 实测 × {est['scale']}）：")
        for name, v in est["per_config"].items():
            print(f"  {name}：单次 token {v['tokens']}，成本 {v['cost']:.4f} 元")
        print(f"全量预计：token 约 {est['total_tokens']}，成本约 {est['total_cost']:.2f} 元")
        return EXIT_PASS
    if (out / "results.csv").exists():
        raise UsageError(f"{out} 里已经有 results.csv，请换一个 --out 目录，避免覆盖之前的结果")
    print(f"评测：{len(tasks)} 个任务 × {len(configs)} 个配置 × {args.runs} 次 = "
          f"{len(tasks) * len(configs) * args.runs} 次运行")
    if args.memory and not Path(args.memory).is_file():
        raise UsageError(f"找不到经验库数据库 {args.memory}")
    memory = Store(args.memory) if args.memory else None
    printer = LivePrinter()
    try:
        res = evaluate(tasks, configs, args.runs, out, llm=llm, settings=settings, subscribers=(printer,),
                       secrets=_secrets(settings), progress=_say, memory=memory)
    finally:
        printer.close()
        if memory:
            memory.close()
    print(f"\n结果：{res.csv_path}\n汇总：{res.summary_path}")
    return EXIT_PASS


def cmd_replay(args) -> int:
    try:
        events = read_events(args.run_dir)
    except FileNotFoundError as exc:
        raise UsageError(str(exc)) from exc
    if not events:
        print("没有事件。")
        return EXIT_PASS
    t0 = events[0]["ts"]
    prev = t0
    for e in events:
        gap = e["ts"] - prev
        if args.speed > 0 and gap > 0:
            time.sleep(gap / args.speed)
        prev = e["ts"]
        print(format_event(e, t0), flush=True)
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

    s = sub.add_parser("models", help="列出 DeepSeek 可用型号")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("run", help="对一个任务运行 Agent（阶段 2：单 Agent 基线 b1 / b2）")
    s.add_argument("--task", help="任务目录")
    s.add_argument("--spec", help="需求文档（与 --include 一起使用）")
    s.add_argument("--include", help="公共头文件目录")
    s.add_argument("--config", default="b2", help="配置：b1、b2、b3、b4、full、b5 及消融配置（默认 b2）")
    s.add_argument("--human-review", action="store_true",
                   help="规划完成后停下来等你确认；升级到转人工时也在终端询问（多 Agent 配置）")
    s.add_argument("--token-budget", type=int, help="b5 的 token 预算（与 full 同等花费）")
    s.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="运行目录的上级目录（默认 .ff_runs）")
    s.add_argument("--db", help="SQLite 数据库路径（默认 .ff_data/firmforge.db）")
    s.add_argument("--tier", choices=["fast", "strong"], help="这次运行所有 Agent 都用这一档（默认按各 Agent 的设置）")
    s.add_argument("--think", choices=["off", "low", "high", "max"], help="这次运行的思考强度（默认按各 Agent 的设置）")
    s.add_argument("--no-mcp", action="store_true", help="不启动 firmforge.toml 里启用的外部 MCP 服务")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("agents", help="列出注册的 Agent：角色、版本、档位、技能、工具、记忆、历史表现")
    s.add_argument("--role", help="只看某个角色，如 coder")
    s.add_argument("--db", help="读历史表现的数据库（默认 .ff_data/firmforge.db）")
    s.set_defaults(func=cmd_agents)

    s = sub.add_parser("eval", help="批量评测：任务 × 配置 × 重复次数")
    s.add_argument("--configs", required=True, help="配置列表，逗号分隔，如 b1,b2,b3,b4,full,b5（b5 要排在 full 后面）")
    s.add_argument("--tasks", default="all", help="all 或任务 id 列表，逗号分隔")
    s.add_argument("--runs", type=int, default=1, help="每个组合重复几次（默认 1）")
    s.add_argument("--out", required=True, help="输出目录，如 results/first")
    s.add_argument("--estimate", action="store_true", help="只跑 1 个任务 1 次，推算全量 token 和成本")
    s.add_argument("--memory", help="沿用另一个数据库里的经验库（默认每个实验独立）")
    s.set_defaults(func=cmd_eval)

    s = sub.add_parser("mcp", help="以 stdio 方式启动 MCP 服务，让 Claude Code 等调用 FirmForge 的判定器")
    s.add_argument("--selftest", action="store_true", help="启动自己作为外部服务并调用一次，检查是否可用")
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("replay", help="在终端按时间顺序回放一次运行")
    s.add_argument("run_dir")
    s.add_argument("--speed", type=float, default=4.0, help="倍速（默认 4；0 表示不等待）")
    s.set_defaults(func=cmd_replay)
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
    config.load_default_dotenv()
    try:
        return args.func(args)
    except (UsageError, config.SettingsError, ConfigError, RegistryError, WorkflowError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_USAGE
    except LLMError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt as exc:
        where = f"，已有的记录保留在 {exc.run_dir}" if isinstance(exc, RunInterrupted) else ""
        print(f"\n已按 Ctrl+C 中断{where}。", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())

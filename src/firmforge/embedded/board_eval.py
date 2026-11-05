"""上板评测运行器（DECISIONS 第 69 条 B 方向）：同一批上板题，在几种配置下各跑几次，用同一把尺子打分。

配置（每一级只比上一级多一样东西，便于说明"多出来的效果来自哪里"）：
- single：单 Agent 基线。一个编码 Agent，不给规则、不写测试，只把编译错误反馈给它，最多 max_attempts 次；
- none：多 Agent 流程（编码、测试 Agent 上板测试、诊断修正），不给规则；
- unverified：再加上查资料 Agent 提炼、没有核对的规则（每道题生成一次，各次运行复用，DECISIONS 第 94 条；
  生成的费用单独记在 unverified_rules/<题目>.json，汇总里单列）；
- verified：改用经过规则判定器核对、人工确认过的规则库（事先用 `firmforge rules build` 建好）。

判定（所有配置相同，Agent 看不到）：固定判定规则 tasks_board/judge_rules.json 的合规检查 → 编译 →
上板跑隐藏测试（board_task.judge_on_board）。公共头文件一律用题目自带的。

记录：每次运行一行写进 <out>/runs.jsonl（中断后重跑会跳过已有的组合），汇总写 summary.md 和 runs.csv。
verified 模式要求规则库覆盖题目的每个适用范围，否则这次记为 skipped 并写明原因，不会临时去查资料或等人确认。
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .. import config
from ..agents.registry import Registry, default_registry
from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet
from ..grounding.sources import SourceLibrary
from ..llm import CallLogger
from ..orchestrator.dag import declared_functions
from ..orchestrator.events import EventBus
from ..runs import RunDir
from ..target.esp_idf import DEFAULT_REQUIRES, IdfEnv, IdfProject
from ..target.harness import TEST_REQUIRES, harness_files
from .board_task import BoardTask, JudgeResult, hidden_case_count, judge_on_board
from .design import Design, DesignModule
from .flow import MAX_FORMAT_RETRIES, EmbeddedFlow, FlowResult, _Agents
from ..grounding.research import parse_rules, research_messages
from ..grounding.rules import Rule
from .roles import coder_messages, coder_repair_message, format_reminder, parse_output

CONFIGS = ("single", "none", "unverified", "verified")
CSV_FIELDS = ("task", "config", "run", "verdict", "failed_layer", "cases_passed", "cases_total", "failed_cases",
              "flow_verdict", "attempts", "tokens", "cost", "seconds", "rules_used", "run_dir", "note")


def task_design(task: BoardTask) -> Design:
    """评测题的固定设计：一个硬件层模块，编号取公共头文件名，文件就是标准答案的那几个 .c 文件。"""
    headers = task.public_files()
    if len(headers) != 1:
        raise ValueError(f"{task.id}：评测题只能有一个公共头文件，现在有 {len(headers)} 个")
    (hpath, htext), = headers.items()
    mid = Path(hpath).stem
    files = tuple(sorted(p for p in task.reference_files() if p.endswith(".c")))
    return Design([DesignModule(mid, task.title, "hardware", files, tuple(declared_functions(htext)),
                                task.scopes or ("all",))])


def parse_configs(text: str) -> list[str]:
    out = [c.strip() for c in text.split(",") if c.strip()]
    bad = [c for c in out if c not in CONFIGS]
    if bad:
        raise ValueError(f"未知的配置 {', '.join(bad)}，只能是 {', '.join(CONFIGS)}")
    return out


def missing_scopes(rules: RuleSet, scopes: tuple[str, ...]) -> list[str]:
    """规则库里没有已确认规则的适用范围（verified 模式的前提）。"""
    return [s for s in scopes if s != "all" and not [r for r in rules.select({s}, "approved") if s in r.scope]]


@dataclass
class EvalContext:
    llm: object
    settings: config.Settings
    profile: HardwareProfile
    judge: RuleSet
    rules: RuleSet                       # verified 模式用的规则库（每次运行拷贝一份，互不影响）
    library: SourceLibrary | None
    idf: IdfEnv | None
    board: object | None                 # target.board.BoardRunner
    runs_root: Path
    build_root: Path
    max_attempts: int = 4
    registry: Registry | None = None     # 评测时用 Registry.with_overrides 统一档位和思考强度
    rules_cache: Path | None = None      # 每题一份未核对规则；None 时用 <输出目录>/unverified_rules
    secrets: tuple[str, ...] = ()
    subscribers: tuple[Callable[[dict], None], ...] = ()
    judge_fn: Callable[..., JudgeResult] = judge_on_board      # 单元测试里换成假的判定


@dataclass
class EvalRow:
    task: str
    config: str
    run: int
    verdict: str = "fail"                # pass / fail / skipped（评测判定）
    failed_layer: str = ""
    cases_passed: int = 0
    cases_total: int = 0
    failed_cases: list[str] = field(default_factory=list)
    flow_verdict: str = ""               # 流程自己的结论（它自己的测试是否通过），和评测判定对照
    attempts: int = 0
    tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    rules_used: int = 0
    run_dir: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in CSV_FIELDS}


# ---------------------------------------------------------------- 单 Agent 基线

def run_single(task: BoardTask, ctx: EvalContext, name: str) -> tuple[FlowResult, dict[str, str], int]:
    """一个编码 Agent，只看编译错误。返回 (结果, 最后的代码, 尝试次数)。"""
    design = task_design(task)
    m = design.modules[0]
    rd = RunDir.create(ctx.runs_root, name)
    bus = EventBus(rd.run_id, rd.events_path)
    for s in ctx.subscribers:
        bus.subscribe(s)
    res = FlowResult(rd.path)
    agents = _Agents(ctx.llm, ctx.settings, ctx.registry or default_registry(), bus,
                     CallLogger(rd.path, list(ctx.secrets)), res, None)
    bus.emit("run_started", None, task=task.id, config="embedded/single", board=ctx.profile.board.value)
    header = task.public_files()[m.header]
    msgs = coder_messages(task.spec_path.read_text(encoding="utf-8"), ctx.profile, None, design, m, {},
                          locked_header=header)
    files: dict[str, str] = {}
    proj = (IdfProject(ctx.build_root / f"{rd.run_id}-single", ctx.idf, target=str(ctx.profile.chip.value),
                       requires=DEFAULT_REQUIRES + TEST_REQUIRES + task.requires) if ctx.idf is not None else None)
    attempt = 0
    for attempt in range(1, ctx.max_attempts + 1):
        need = sorted(f for f in m.files if f.endswith(".c") and f not in files) if attempt == 1 else []
        for k in range(MAX_FORMAT_RETRIES + 1):                  # 没按格式交文件：当场补要（和多 Agent 流程一样）
            resp = agents.call("coder-strong", msgs, m.id, attempt=attempt, purpose="编码" if attempt == 1 else "改代码")
            out = parse_output(resp.text, set(m.files))
            files.update(out.files)
            msgs = msgs + [{"role": "assistant", "content": resp.text}]
            missing = [f for f in need if f not in files] or ([] if out.files else sorted(f for f in m.files if f.endswith(".c")))
            if not missing or k == MAX_FORMAT_RETRIES:
                break
            bus.emit("format_retry", m.id, agent="coder-strong", attempt=attempt, missing=missing, round=k + 1)
            msgs = msgs + [{"role": "user", "content": format_reminder(missing, out.rejected)}]
        if proj is None:
            break
        # 带上平台的测试主程序（没有用例），否则缺 app_main 链接不过，Agent 会收到和它的代码无关的报错
        proj.write(ctx.profile, {**task.public_files(), **files, **harness_files(None, None)})
        b = proj.build()
        bus.emit("verify_finished", m.id, attempt=attempt, scope="build", ok=b.ok, failed_layer=None if b.ok else "build")
        if b.ok:
            res.verdict = "pass"
            break
        errs = "\n".join(d.render() for d in b.errors) or b.log_tail[-1500:]
        if attempt < ctx.max_attempts:
            msgs = msgs + [{"role": "user", "content": coder_repair_message(attempt, ctx.max_attempts, "编译失败：\n" + errs)}]
    rd.write_json("run.json", {"verdict": res.verdict, "rule_mode": "single", "tokens": res.prompt_tokens +
                               res.completion_tokens, "cost": round(res.cost, 6), "attempts": attempt})
    bus.emit("run_finished", None, verdict=res.verdict, tokens=res.prompt_tokens + res.completion_tokens, error="")
    return res, files, attempt


# ---------------------------------------------------------------- 未核对规则（每题一次）

def unverified_rules(task: BoardTask, ctx: EvalContext) -> RuleSet:
    """查资料 Agent 为这道题的适用范围提炼规则，不核对、不经人工，直接当作已确认使用；结果缓存，同一题各次运行复用。"""
    cache = Path(ctx.rules_cache) / f"{task.id}.json"
    if cache.is_file():
        data = json.loads(cache.read_text(encoding="utf-8"))
        return RuleSet([Rule.from_dict(d) for d in data["rules"]])
    rd = RunDir.create(ctx.runs_root, f"eval-rules-{task.id}")
    bus = EventBus(rd.run_id, rd.events_path)
    res = FlowResult(rd.path)
    agents = _Agents(ctx.llm, ctx.settings, ctx.registry or default_registry(), bus,
                     CallLogger(rd.path, list(ctx.secrets)), res, ctx.library)
    scopes = sorted(set(task.scopes) - {"all"})
    resp = agents.call("researcher", research_messages(ctx.profile, scopes, ctx.library), "rules",
                       purpose="提炼规则（不核对）", tools=ctx.library is not None)
    rules, errors = parse_rules(resp.text)
    rules = [replace(r, status="approved", notes=r.notes + ("规则模式 unverified：未核对",)) for r in rules]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"task": task.id, "scopes": scopes, "rules": [r.to_dict() for r in rules],
                                 "parse_errors": errors[:20], "tokens": res.prompt_tokens + res.completion_tokens,
                                 "cost": round(res.cost, 6), "run_dir": str(rd.path)}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    return RuleSet(rules)


# ---------------------------------------------------------------- 一次运行

def run_once(task: BoardTask, cfg: str, run_no: int, ctx: EvalContext) -> EvalRow:
    row = EvalRow(task.id, cfg, run_no, cases_total=hidden_case_count(task))
    t0 = time.time()
    name = f"eval-{task.id}-{cfg}-{run_no}"
    if cfg == "verified":
        miss = missing_scopes(ctx.rules, task.scopes)
        if miss:
            row.verdict, row.failed_layer = "skipped", "skipped"
            row.note = f"规则库里没有这些范围的已确认规则：{'、'.join(miss)}（先用 firmforge rules build 建好并人工确认）"
            return row
    if cfg == "single":
        res, files, attempts = run_single(task, ctx, name)
    else:
        design = task_design(task)
        rules = (RuleSet(list(ctx.rules)) if cfg == "verified" else
                 unverified_rules(task, ctx) if cfg == "unverified" else RuleSet())
        flow = EmbeddedFlow(llm=ctx.llm, settings=ctx.settings, profile=ctx.profile,
                            spec=task.spec_path.read_text(encoding="utf-8"), rules=rules,
                            library=ctx.library if cfg != "none" else None, verifier=None, runs_root=ctx.runs_root,
                            name=name, rule_mode=cfg, idf=ctx.idf, board=ctx.board, registry=ctx.registry,
                            max_attempts=ctx.max_attempts, subscribers=ctx.subscribers, secrets=ctx.secrets,
                            design=design, locked=task.public_files(), extra_requires=task.requires,
                            build_rules=False)
        res = flow.run()
        mr = res.modules.get(design.modules[0].id)
        files = dict(mr.files) if mr else {}
        attempts = mr.attempts if mr else 0
        row.rules_used = len(res.rules_used)
    row.flow_verdict = res.verdict + (f"（{res.error}）" if res.error else "")
    row.attempts, row.tokens, row.cost = attempts, res.prompt_tokens + res.completion_tokens, round(res.cost, 6)
    row.run_dir = str(res.run_dir)
    if not files:
        row.verdict, row.failed_layer, row.note = "fail", "no_code", "Agent 没有交出代码"
    else:
        j: JudgeResult = ctx.judge_fn(task, ctx.profile, ctx.judge, ctx.idf, ctx.board, files,
                                        ctx.build_root / f"{name}-judge", Path(res.run_dir) / "judge_serial.log")
        row.verdict, row.failed_layer = j.verdict, j.failed_layer
        row.cases_passed, row.cases_total, row.failed_cases = j.cases_passed, j.cases_total, j.failed_cases
        (Path(res.run_dir) / "judge_report.txt").write_text(j.report, encoding="utf-8")
        if j.verdict == "skipped":
            row.note = j.report[:200]
    row.seconds = round(time.time() - t0, 1)
    return row


# ---------------------------------------------------------------- 批量与汇总

def load_rows(out_dir: Path) -> list[dict]:
    p = Path(out_dir) / "runs.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(tasks: list[BoardTask], configs: list[str], runs: int, out_dir: Path, ctx: EvalContext,
             progress: Callable[[str], None] = print, max_cost: float = 0.0) -> list[dict]:
    """按 题目 × 配置 × 次数 跑；已经在 runs.jsonl 里的组合跳过（skipped 的会重跑）。
    max_cost > 0：这次调用累计花费（元）达到就不再开始新的运行（正在跑的一次会跑完）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if ctx.rules_cache is None:
        ctx.rules_cache = out_dir / "unverified_rules"
    rows = load_rows(out_dir)
    done = {(r["task"], r["config"], r["run"]) for r in rows if r["verdict"] != "skipped"}
    rows = [r for r in rows if r["verdict"] != "skipped"]
    (out_dir / "runs.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                        encoding="utf-8")         # 过期的 skipped 行去掉（会重跑）
    todo = [(t, c, n) for n in range(1, runs + 1) for t in tasks for c in configs if (t.id, c, n) not in done]
    progress(f"共 {len(todo)} 次运行要跑（已完成 {len(done)} 次）")
    spent = 0.0
    cache = Path(ctx.rules_cache)
    known = {f.name for f in cache.glob("*.json")}
    for k, (t, c, n) in enumerate(todo, 1):
        if max_cost > 0 and spent >= max_cost:
            progress(f"已花 {spent:.2f} 元，达到上限 {max_cost:.2f} 元，停止；还剩 {len(todo) - k + 1} 次没跑，"
                     f"用同一个输出目录重跑会接着跑")
            break
        row = run_once(t, c, n, ctx).to_dict()
        spent += float(row["cost"])
        for f in cache.glob("*.json"):                     # 这次新生成的未核对规则也算进花费
            if f.name not in known:
                known.add(f.name)
                spent += float(json.loads(f.read_text(encoding="utf-8")).get("cost", 0))
        rows.append(row)
        with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        cases = f"隐藏用例 {row['cases_passed']}/{row['cases_total']}"
        progress(f"[{k}/{len(todo)}] {t.id} · {c} · 第 {n} 次：{row['verdict']}"
                 + (f"（{row['failed_layer']}）" if row["failed_layer"] else "")
                 + f"，{cases}，{row['tokens']} token，{row['cost']:.4f} 元，{row['seconds']:.0f} 秒"
                 + (f"；{row['note']}" if row["note"] else ""))
    write_outputs(rows, out_dir, configs, ctx.rules if "verified" in configs else None)
    return rows


MEASURED_DOCS = ("board-measured",)        # 上板实测记录的资料编号（DECISIONS 第 95 条）


def measured_rules(rules: RuleSet | None) -> list[Rule]:
    """引用上板实测记录的已确认规则：人按实测补充或纠正的知识，汇总里必须披露（DECISIONS 第 95 条）。
    按规则引用的资料判断（规则自己写的 tier 可能不准）。"""
    return [r for r in (rules.select(None, "approved") if rules is not None else [])
            if r.cite.doc in MEASURED_DOCS or r.tier == "user"]


def write_outputs(rows: list[dict], out_dir: Path, configs: list[str], verified_rules: RuleSet | None = None) -> None:
    with (Path(out_dir) / "runs.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({**r, "failed_cases": "；".join(r["failed_cases"])})
    text = summarize(rows, configs)
    gen = sorted((Path(out_dir) / "unverified_rules").glob("*.json"))
    if gen:
        lines = ["", "## 未核对规则（每题生成一次，各次运行复用；费用不含在上表里）", "",
                 "| 题目 | 规则数 | token | 成本（元） |", "|---|---|---|---|"]
        total = 0.0
        for f in gen:
            d = json.loads(f.read_text(encoding="utf-8"))
            total += float(d.get("cost", 0))
            lines.append(f"| {d['task']} | {len(d['rules'])} | {d.get('tokens', 0)} | {float(d.get('cost', 0)):.4f} |")
        lines.append(f"| 合计 | | | {total:.4f} |")
        text += "\n".join(lines) + "\n"
    shown = [("verified（规则库，人工确认过）", r) for r in measured_rules(verified_rules)]
    for f in gen:
        d = json.loads(f.read_text(encoding="utf-8"))
        shown += [(f"unverified（{d['task']}，未核对）", r)
                  for r in measured_rules(RuleSet([Rule.from_dict(x) for x in d["rules"]]))]
    if shown:
        lines = ["", "## 披露：来自上板实测记录的规则（DECISIONS 第 95 条）", "",
                 "这些规则的依据是在本板上实测的结果，而不是芯片或器件资料。", "",
                 "| 配置 | 规则 | 内容 | 引用 |", "|---|---|---|---|"]
        for where, r in shown:
            lines.append(f"| {where} | {r.id} | {r.text} | {r.cite.doc}：{r.cite.quote[:60]} |")
        text += "\n".join(lines) + "\n"
    (Path(out_dir) / "summary.md").write_text(text, encoding="utf-8")


def summarize(rows: list[dict], configs: list[str] | None = None) -> str:
    configs = configs or sorted({r["config"] for r in rows}, key=lambda c: CONFIGS.index(c) if c in CONFIGS else 99)
    lines = ["# 上板评测汇总", "",
             "判定：固定判定规则的合规检查 → 编译 → 上板隐藏测试（所有配置相同）。skipped 不计入通过率。", "",
             "| 配置 | 通过 / 有效次数 | 通过率 | 隐藏用例通过率 | 平均尝试次数 | 平均 token | 平均成本（元） | 平均用时（秒） | 跳过 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for c in configs:
        rs = [r for r in rows if r["config"] == c]
        valid = [r for r in rs if r["verdict"] != "skipped"]
        skipped = len(rs) - len(valid)
        if not valid:
            lines.append(f"| {c} | 0 / 0 | - | - | - | - | - | - | {skipped} |")
            continue
        passes = sum(1 for r in valid if r["verdict"] == "pass")
        cp, ct = sum(r["cases_passed"] for r in valid), sum(r["cases_total"] for r in valid)
        avg = lambda k: sum(float(r[k]) for r in valid) / len(valid)       # noqa: E731
        lines.append(f"| {c} | {passes} / {len(valid)} | {passes / len(valid):.0%} | "
                     f"{(cp / ct if ct else 0):.0%} | {avg('attempts'):.1f} | {avg('tokens'):.0f} | "
                     f"{avg('cost'):.4f} | {avg('seconds'):.0f} | {skipped} |")
    tasks = sorted({r["task"] for r in rows})
    lines += ["", "## 每道题（通过次数 / 有效次数）", "", "| 题目 | " + " | ".join(configs) + " |",
              "|---|" + "---|" * len(configs)]
    for t in tasks:
        cells = []
        for c in configs:
            rs = [r for r in rows if r["task"] == t and r["config"] == c and r["verdict"] != "skipped"]
            cells.append(f"{sum(1 for r in rs if r['verdict'] == 'pass')} / {len(rs)}" if rs else "-")
        lines.append(f"| {t} | " + " | ".join(cells) + " |")
    layers: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["verdict"] == "fail":
            layers[(r["config"], r["failed_layer"] or "?")] = layers.get((r["config"], r["failed_layer"] or "?"), 0) + 1
    if layers:
        lines += ["", "## 没通过的卡在哪一层", "", "| 配置 | 层 | 次数 |", "|---|---|---|"]
        for (c, layer), n in sorted(layers.items(), key=lambda x: (configs.index(x[0][0]) if x[0][0] in configs else 99, x[0][1])):
            lines.append(f"| {c} | {layer} | {n} |")
    return "\n".join(lines) + "\n"

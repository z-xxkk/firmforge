"""嵌入式新流程的执行器（DECISIONS 第 66 条全流程）。

    ② ③ 规则：查资料 → 规则判定器 → 退回修改 → 人工确认 → 入库（grounding.pipeline）
    ④   设计分层（不合格带着问题重做，最多 3 次）
    ⑤   每个模块按依赖顺序：编码、写测试
    ⑥   编译 → 代码合规检查 → 测试（硬件层上板、逻辑层在电脑上）
    ⑦   没通过：诊断 Agent 判断错在代码 / 测试 / 规则 / 硬件，分别退回；最多 max_attempts 次
    ⑧   人工确认过的规则已在第 ③ 步入库；编码 Agent 新列出的事实经判定器核对后记为"已核对"，等人确认

和旧引擎（orchestrator/engine.py，旧题目格式）分开，复用事件总线、调用日志和工具调用循环。
测试 Agent 看不到实现代码；隐藏测试不进任何 Agent 的上下文（CLAUDE.md 硬性规则 5）。

规则模式（量化评测 B 方向用，DECISIONS 第 69 条）：
- verified：完整流程（默认）
- unverified：查资料 Agent 提出的规则不经核对、不经人工，直接当作已确认使用
- none：不查资料、不给规则（相当于通用编程 Agent）

评测（embedded/board_eval.py）另外传入：固定的设计（按题目公共接口生成，不经设计 Agent）、锁定的公共头文件
（编码 Agent 只读）、事先建好的规则库（verified 模式不再查资料和等人确认，build_rules=False）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .. import config
from ..agents.registry import AgentDef, Registry, default_registry
from ..grounding.compliance import check_code
from ..grounding.pipeline import Human, RuleBuilder
from ..grounding.profile import HardwareProfile
from ..grounding.research import SOURCE_TOOLS, parse_rules, research_messages
from ..grounding.rules import RuleSet
from ..grounding.sources import SourceLibrary
from ..grounding.verifier import RuleVerifier
from ..llm import CallLogger, LLMResponse, LoggedLLM
from ..orchestrator.dag import extract_json
from ..orchestrator.events import EventBus
from ..runs import RunDir
from ..target.board import BoardRunner
from ..target.esp_idf import DEFAULT_REQUIRES, IdfEnv, IdfProject, IdfUnavailable
from ..target.harness import TEST_REQUIRES, harness_files
from ..target.host_test import run_host_tests
from ..tools import ToolContext, ToolPipeline, default_registry as default_tools
from ..tools.loop import call_with_tools
from .design import Design, DesignModule, design_from_text, designer_messages, designer_retry_message
from .roles import (coder_messages, coder_repair_message, diagnoser_messages, format_reminder, parse_diagnosis,
                    parse_output, test_file, tester_messages, tester_repair_message)

RULE_MODES = ("verified", "unverified", "none")
MAX_FORMAT_RETRIES = 2              # 没按格式交文件时，当场补要几次
MAX_DESIGN_TRIES = 3
DEFAULT_MAX_TOKENS = 8192


@dataclass
class ModuleResult:
    id: str
    layer: str
    verdict: str = "pending"            # pass / fail / skipped（没有板子等）/ hardware（诊断为硬件问题，等人处理）
    attempts: int = 0
    files: dict[str, str] = field(default_factory=dict)
    tests: dict[str, str] = field(default_factory=dict)
    last_report: str = ""


@dataclass
class FlowResult:
    run_dir: Path
    verdict: str = "fail"
    design: Design | None = None
    modules: dict[str, ModuleResult] = field(default_factory=dict)
    rules_used: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0                   # 元，按配置里的模型价格估算
    error: str = ""


class _Agents:
    """按 Agent 定义调用模型：选档位和思考强度、记调用日志、发事件、统计 token。"""

    def __init__(self, llm, settings: config.Settings, registry: Registry, bus: EventBus, logger: CallLogger,
                 result: FlowResult, library: SourceLibrary | None):
        self.llm, self.settings, self.registry, self.bus, self.logger = llm, settings, registry, bus, logger
        self.result, self.library = result, library

    def call(self, name: str, messages: list[dict], node: str, *, attempt: int = 1, purpose: str = "",
             tools: bool = False) -> LLMResponse:
        agent: AgentDef = self.registry.get(name)
        tier = self.settings.tier(agent.model_tier)
        client = LoggedLLM(self.llm, self.logger, agent.name)
        params = dict(model=tier.name, temperature=agent.temperature,
                      max_tokens=agent.max_tokens or tier.max_tokens or DEFAULT_MAX_TOKENS,
                      extra_body=tier.extra_body(agent.thinking) or None)
        self.bus.emit("agent_started", node, agent=agent.name, role=agent.role, attempt=attempt, purpose=purpose)
        if tools and self.library is not None:
            reg = default_tools()
            allowed = [t for t in reg.all() if t.name in {x.name for x in SOURCE_TOOLS}]
            pipe = ToolPipeline(reg, allowed=allowed, ctx=ToolContext(agent=agent.name, library=self.library),
                                emit=self.bus.emit, node_id=node)
            resp = call_with_tools(lambda m, tools: client.complete(m, tools=tools, **params), messages, allowed, pipe,
                                   max_rounds=int(self.settings.tools.get("max_rounds", 5)))
        else:
            resp = client.complete(messages, **params)
        self.result.prompt_tokens += resp.prompt_tokens
        self.result.completion_tokens += resp.completion_tokens
        self.result.cost += tier.cost(resp.prompt_tokens, resp.completion_tokens, ts=time.time(),
                                      schedule=self.settings.pricing)
        self.bus.emit("agent_finished", node, agent=agent.name, role=agent.role, attempt=attempt,
                      tokens=resp.prompt_tokens + resp.completion_tokens, latency_s=round(resp.latency_s, 3))
        return resp


class EmbeddedFlow:
    def __init__(self, *, llm, settings: config.Settings, profile: HardwareProfile, spec: str, rules: RuleSet,
                 library: SourceLibrary | None, verifier: RuleVerifier | None, runs_root: Path, name: str = "embedded",
                 rule_mode: str = "verified", human: Human | None = None, idf: IdfEnv | None = None,
                 board: BoardRunner | None = None, registry: Registry | None = None, max_attempts: int = 6,
                 subscribers: tuple[Callable[[dict], None], ...] = (), secrets: tuple[str, ...] = (),
                 coder: str = "coder-strong", design: Design | None = None, locked: dict[str, str] | None = None,
                 extra_requires: tuple[str, ...] = (), build_rules: bool = True):
        if rule_mode not in RULE_MODES:
            raise ValueError(f"规则模式只能是 {', '.join(RULE_MODES)}")
        self.profile, self.spec, self.rules, self.library, self.verifier = profile, spec, rules, library, verifier
        self.rule_mode, self.human, self.idf, self.board, self.max_attempts = rule_mode, human, idf, board, max_attempts
        self.coder, self.fixed_design, self.locked = coder, design, dict(locked or {})
        self.extra_requires, self.build_rules = tuple(extra_requires), build_rules
        self.on_builder: Callable | None = None
        self.rd = RunDir.create(runs_root, name)
        self.bus = EventBus(self.rd.run_id, self.rd.events_path)
        for s in subscribers:
            self.bus.subscribe(s)
        self.result = FlowResult(self.rd.path)
        self.agents = _Agents(llm, settings, registry or default_registry(), self.bus,
                              CallLogger(self.rd.path, list(secrets)), self.result, library)

    # ---------------------------------------------------------------- 规则
    def _rules_for_flow(self) -> RuleSet:
        if self.rule_mode == "none":
            return RuleSet()
        if not self.build_rules:                       # 评测：规则事先准备好（verified 用规则库，unverified 用每题生成一次的）
            return self.rules
        if self.fixed_design is not None:              # 评测：只查本题涉及的外设和器件
            scopes = sorted({x for m in self.fixed_design.modules for x in m.scopes} - {"all"})
        else:
            scopes = sorted(self.profile.scopes())
        if self.rule_mode == "unverified":
            resp = self.agents.call("researcher", research_messages(self.profile, scopes, self.library),
                                    "rules", purpose="提炼规则（不核对）", tools=True)
            rules, _ = parse_rules(resp.text)
            return RuleSet([replace(r, status="approved", notes=r.notes + ("规则模式 unverified：未核对",))
                            for r in rules])
        builder = RuleBuilder(self._research_with_tools, self.profile, self.library, self.verifier, self.rules,
                              emit=self.bus.emit, human=self.human)
        if self.on_builder is not None:          # 界面要拿到核对结果，确认规则时显示每一步
            self.on_builder(builder)
        builder.run(scopes)
        return self.rules

    def _research_with_tools(self, messages, tools):
        # RuleBuilder 自己跑工具循环，这里只负责一次模型调用（带工具清单）
        agent = self.agents.registry.get("researcher")
        tier = self.agents.settings.tier(agent.model_tier)
        client = LoggedLLM(self.agents.llm, self.agents.logger, agent.name)
        resp = client.complete(messages, model=tier.name, tools=tools, temperature=agent.temperature,
                               max_tokens=agent.max_tokens or tier.max_tokens or DEFAULT_MAX_TOKENS,
                               extra_body=tier.extra_body(agent.thinking) or None)
        self.result.prompt_tokens += resp.prompt_tokens
        self.result.completion_tokens += resp.completion_tokens
        self.result.cost += tier.cost(resp.prompt_tokens, resp.completion_tokens, ts=time.time(),
                                      schedule=self.agents.settings.pricing)
        return resp

    # ---------------------------------------------------------------- 设计
    def _design(self, rules: RuleSet) -> Design | None:
        if self.fixed_design is not None:
            self.rd.write_json("design.json", self.fixed_design.to_dict())
            self.bus.emit("plan_ready", "design", modules=[m.to_dict() for m in self.fixed_design.modules],
                          app=self.fixed_design.app, fixed=True)
            return self.fixed_design
        msgs = designer_messages(self.spec, self.profile, rules)
        for k in range(1, MAX_DESIGN_TRIES + 1):
            resp = self.agents.call("designer", msgs, "design", attempt=k, purpose="设计分层")
            design, problems = design_from_text(resp.text, self.profile)
            if design is not None:
                self.rd.write_json("design.json", design.to_dict())
                self.bus.emit("plan_ready", "design", modules=[m.to_dict() for m in design.modules], app=design.app)
                return design
            self.bus.emit("design_rejected", "design", attempt=k, problems=problems[:10])
            msgs = msgs + [{"role": "assistant", "content": resp.text},
                           {"role": "user", "content": designer_retry_message(problems)}]
        return None

    # ---------------------------------------------------------------- 模块
    def _dep_headers(self, design: Design, m: DesignModule) -> dict[str, str]:
        out = {p: c for p, c in self.locked.items() if p != m.header}
        for d in m.depends_on:
            dm = self.result.modules.get(d)
            if dm and design.by_id[d].header in dm.files:
                out[design.by_id[d].header] = dm.files[design.by_id[d].header]
        return out

    def _dep_files(self, design: Design, m: DesignModule) -> dict[str, str]:
        out = {}
        for d in m.depends_on:
            out.update(self._dep_files(design, design.by_id[d]))
            out.update(self.result.modules[d].files)
        return out

    def _check(self, design: Design, m: DesignModule, mr: ModuleResult, rules: RuleSet, attempt: int) -> tuple[str, str, str]:
        """编译 → 合规 → 测试。返回 (结论, 出问题的层, 报告)。结论：pass / fail / skipped。"""
        scopes = set(m.scopes)
        comp = check_code(mr.files, rules.select(scopes, "approved") if rules is not None else [])
        self.bus.emit("verify_finished", m.id, attempt=attempt, scope="compliance", ok=comp.ok,
                      failed_layer=None if comp.ok else "compliance")
        if not comp.ok:
            return "fail", "compliance", comp.summary()
        deps = self._dep_files(design, m)
        if m.layer == "logic":
            r = run_host_tests({**self.locked, **deps, **mr.files, **mr.tests}, test_file(m), self.rd.path / "host" / m.id)
            self.bus.emit("verify_finished", m.id, attempt=attempt, scope="host_test", ok=r.ok,
                          failed_layer=None if r.ok else ("skipped" if r.skipped else "host_test"))
            return r.verdict(), "host_test", r.report()
        if self.idf is None:
            return "skipped", "build", "ESP-IDF 不可用，硬件层无法编译和上板测试"
        proj = IdfProject(Path(config.project_root()) / ".ff_data" / "idf_build" / f"{self.rd.run_id}-{m.id}", self.idf,
                          target=str(self.profile.chip.value),
                          requires=DEFAULT_REQUIRES + TEST_REQUIRES + self.extra_requires, whole_archive=True)
        svd = getattr(self.verifier, "svd", None)
        proj.write(self.profile, {**self.locked, **deps, **mr.files, **mr.tests, **harness_files(rules, svd, scopes, profile=self.profile)})
        self.bus.emit("verify_started", m.id, attempt=attempt, scope="build")
        b = proj.build()
        self.bus.emit("verify_finished", m.id, attempt=attempt, scope="build", ok=b.ok,
                      failed_layer=None if b.ok else "build")
        if not b.ok:
            errs = "\n".join(d.render() for d in b.errors) or b.log_tail[-1500:]
            return "fail", "build", "编译失败：\n" + errs
        if self.board is None:
            return "skipped", "board_test", "没有配置板子，硬件层只做到编译和合规检查"
        r = self.board.run(proj, self.rd.attempt_dir(m.id, attempt) / "serial.log")
        self.bus.emit("verify_finished", m.id, attempt=attempt, scope="board_test", ok=r.ok,
                      failed_layer=None if r.ok else ("skipped" if r.skipped else "board_test"))
        return r.verdict(), "board_test", r.report()

    def _write_snapshot(self, m: DesignModule, mr: ModuleResult, attempt: int, report: str) -> None:
        d = self.rd.attempt_dir(m.id, attempt)
        d.mkdir(parents=True, exist_ok=True)              # 一个文件都没交时也要能写报告
        for rel, c in {**mr.files, **mr.tests}.items():
            p = d / "main" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(c, encoding="utf-8")
        (d / "report.txt").write_text(report, encoding="utf-8")

    def _module(self, design: Design, m: DesignModule, rules: RuleSet) -> ModuleResult:
        mr = ModuleResult(m.id, m.layer)
        self.result.modules[m.id] = mr
        self.bus.emit("node_ready", m.id, title=m.title, layer=m.layer)
        deps_h = self._dep_headers(design, m)
        c_msgs = coder_messages(self.spec, self.profile, rules, design, m, deps_h,
                                locked_header=self.locked.get(m.header))
        writable = set(m.files)
        need_code = {f for f in writable if f.endswith(".c")} or writable     # 第一次要交齐全部 .c 文件
        c_conv, out = self._ask_files(self.coder, c_msgs, m.id, writable, need_code, attempt=1, purpose="编码",
                                      tools=self.rule_mode != "none")
        mr.files.update(out.files)
        self._new_facts(out.facts)
        header = self.locked.get(m.header) or mr.files.get(m.header, "")
        t_msgs = tester_messages(self.spec, self.profile, rules, m, header, deps_h)
        t_conv, tout = self._ask_files("tester", t_msgs, m.id, {test_file(m)}, {test_file(m)}, attempt=1,
                                       purpose="写测试")
        mr.tests.update(tout.files)
        for attempt in range(1, self.max_attempts + 1):
            mr.attempts = attempt
            verdict, layer, report = self._check(design, m, mr, rules, attempt)
            mr.last_report = report
            self._write_snapshot(m, mr, attempt, report)
            if verdict in ("pass", "skipped"):
                mr.verdict = verdict
                self.bus.emit("node_done" if verdict == "pass" else "node_failed", m.id, verdict=verdict,
                              attempts=attempt, note=report[-300:])
                return mr
            if attempt == self.max_attempts:
                break
            diag = self._diagnose(m, mr, rules, layer, report, attempt)
            if diag["blame"] == "hardware":
                mr.verdict = "hardware"
                self.bus.emit("human_wait", m.id, reason="诊断为硬件或接线问题，请检查板子和接线",
                              evidence=diag["evidence"], instruction=diag["instruction"])
                return mr
            if diag["blame"] == "rule":
                self.bus.emit("escalated", m.id, level="rule", rule=diag["rule"], note=diag["instruction"])
                rules = self._dispute_rule(rules, diag)
            if diag["blame"] == "test":
                msg = tester_repair_message(attempt, self.max_attempts, report, mr.files, diag)
                t_conv, tout = self._ask_files("tester", t_conv + [{"role": "user", "content": msg}], m.id,
                                               {test_file(m)}, {test_file(m)}, attempt=attempt + 1, purpose="改测试")
                mr.tests.update(tout.files)
            else:
                msg = coder_repair_message(attempt, self.max_attempts, report, diag)
                # 修改轮可以只交改动的文件，但至少要交一个
                c_conv, o = self._ask_files(self.coder, c_conv + [{"role": "user", "content": msg}], m.id, writable,
                                            None, attempt=attempt + 1, purpose="改代码", tools=self.rule_mode != "none")
                mr.files.update(o.files)
                self._new_facts(o.facts)
        mr.verdict = "fail"
        self.bus.emit("node_failed", m.id, verdict="fail", attempts=mr.attempts, note=mr.last_report[-300:])
        return mr

    def _ask_files(self, agent: str, conv: list[dict], node: str, allowed: set[str], need: set[str] | None, *,
                   attempt: int, purpose: str, tools: bool = False):
        """调用 Agent 并取文件块；need 里的文件没交齐（need 为 None 时：一个文件都没交）就带着格式提醒当场再要，
        最多 MAX_FORMAT_RETRIES 次。返回 (加上本次回复的对话, 解析结果)。"""
        for k in range(MAX_FORMAT_RETRIES + 1):
            resp = self.agents.call(agent, conv, node, attempt=attempt, purpose=purpose if k == 0 else purpose + "（补交文件）",
                                    tools=tools)
            out = parse_output(resp.text, allowed)
            conv = conv + [{"role": "assistant", "content": resp.text}]
            if need is None:
                missing = [] if out.files else (sorted(f for f in allowed if f.endswith(".c")) or sorted(allowed))
            else:
                missing = sorted(need - set(out.files))
            if not missing:
                return conv, out
            self.bus.emit("format_retry", node, agent=agent, attempt=attempt, missing=missing, round=k + 1)
            if k < MAX_FORMAT_RETRIES:
                conv = conv + [{"role": "user", "content": format_reminder(missing, out.rejected)}]
        return conv, out

    def _diagnose(self, m: DesignModule, mr: ModuleResult, rules: RuleSet, layer: str, report: str,
                  attempt: int) -> dict:
        if layer in ("compliance", "build"):                 # 编译错误、合规违规几乎总是代码的错，不花钱诊断
            return {"blame": "code", "instruction": "", "evidence": layer, "rule": ""}
        msgs = diagnoser_messages(self.profile, rules, m, mr.files, mr.tests, report)
        resp = self.agents.call("diagnoser", msgs, m.id, attempt=attempt, purpose="诊断")
        try:
            diag = parse_diagnosis(extract_json(resp.text), rules)
        except ValueError as exc:
            diag = {"blame": "code", "instruction": "", "evidence": f"诊断输出不合格（{exc}），按代码问题处理", "rule": ""}
        self.bus.emit("diagnosis", m.id, attempt=attempt, **diag)
        return diag

    def _dispute_rule(self, rules: RuleSet, diag: dict) -> RuleSet:
        """诊断认为某条规则有问题：先把它退回"待核对"，不再给编码 Agent，等查资料 Agent 或人复核。"""
        r = rules.rules.get(diag["rule"])
        if r is not None:
            rules.put(r.reject(f"诊断认为规则有问题：{diag['instruction']}"))
        return rules

    def _new_facts(self, facts) -> None:
        """编码 Agent 从手册新用到的事实（DECISIONS 第 80 条）：交给判定器，通过的记为已核对，等人确认。"""
        if not facts or self.verifier is None or self.rule_mode != "verified":
            return
        rep = self.verifier.run(RuleSet(facts), scopes=set())
        for r in rep.checked_rules():
            self.rules.put(r)
        for res in rep.rejected:
            self.bus.emit("rule_rejected", "facts", rule=res.rule.id, times=1, reasons=res.reasons())

    # ---------------------------------------------------------------- 总流程
    def run(self) -> FlowResult:
        res = self.result
        self.bus.emit("run_started", None, task=self.rd.run_id, config=f"embedded/{self.rule_mode}",
                      board=self.profile.board.value)
        t0 = time.time()
        try:
            rules = self._rules_for_flow()
            res.rules_used = [r.id for r in rules.select(None, "approved")]
            design = self._design(rules)
            res.design = design
            if design is None:
                res.error = f"设计 {MAX_DESIGN_TRIES} 次都没有通过检查"
            else:
                for m in design.order():
                    if any(res.modules.get(d) is None or res.modules[d].verdict not in ("pass", "skipped")
                           for d in m.depends_on):
                        res.modules[m.id] = ModuleResult(m.id, m.layer, verdict="skipped",
                                                         last_report="依赖的模块没有完成")
                        self.bus.emit("node_failed", m.id, verdict="skipped", note="依赖的模块没有完成")
                        continue
                    self._module(design, m, rules)
                verdicts = {mr.verdict for mr in res.modules.values()}
                res.verdict = ("pass" if verdicts == {"pass"} else
                               "incomplete" if verdicts <= {"pass", "skipped"} else "fail")
        except IdfUnavailable as exc:
            res.error = str(exc)
        except Exception as exc:                         # 模型调用失败等：先把记录写完整（运行目录能正常回放），再往上抛
            res.verdict, res.error = "error", f"{type(exc).__name__}: {exc}"
            self._finish(res, t0)
            raise
        self._finish(res, t0)
        return res

    def _finish(self, res: FlowResult, t0: float) -> None:
        self.rd.write_json("run.json", {"verdict": res.verdict, "error": res.error, "rule_mode": self.rule_mode,
                                        "rules_used": res.rules_used,
                                        "modules": {k: v.verdict for k, v in res.modules.items()},
                                        "tokens": res.prompt_tokens + res.completion_tokens,
                                        "cost": round(res.cost, 6),
                                        "seconds": round(time.time() - t0, 1)})
        self.bus.emit("run_finished", None, verdict=res.verdict, tokens=res.prompt_tokens + res.completion_tokens,
                      cost=round(res.cost, 6), error=res.error)

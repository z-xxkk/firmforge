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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from .firmware_workflow import FirmwarePlan, firmware_plan
from .roles import (coder_messages, coder_repair_message, diagnoser_messages, format_reminder, parse_diagnosis,
                    parse_output, test_file, tester_messages, tester_repair_message)

RULE_MODES = ("verified", "unverified", "none")
MAX_FORMAT_RETRIES = 2              # 没按格式交文件时，当场补要几次
MAX_DESIGN_TRIES = 3
DEFAULT_MAX_TOKENS = 8192
_PLAN = object()                    # _pick_coder 没传 pinned：按 self.plan


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
        self._lock = threading.Lock()                # 编码和写测试并行时，两边同时累加 token 和费用

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
        with self._lock:
            self.result.prompt_tokens += resp.prompt_tokens
            self.result.completion_tokens += resp.completion_tokens
            self.result.cost += tier.cost(resp.prompt_tokens, resp.completion_tokens, ts=time.time(),
                                          schedule=self.settings.pricing)
        self.bus.emit("agent_finished", node, agent=agent.name, role=agent.role, attempt=attempt,
                      tokens=resp.prompt_tokens + resp.completion_tokens, latency_s=round(resp.latency_s, 3))
        return resp


_BUILD_ERR = re.compile(r"([\w./-]+\.[ch]):\d+(?::\d+)?:\s*(?:fatal )?error:")


def build_error_files(report: str) -> set[str]:
    """编译报告里出错的源文件名（只取文件名，不带目录）。"""
    return {Path(f).name for f in _BUILD_ERR.findall(report or "")}


class EmbeddedFlow:
    def __init__(self, *, llm, settings: config.Settings, profile: HardwareProfile, spec: str, rules: RuleSet,
                 library: SourceLibrary | None, verifier: RuleVerifier | None, runs_root: Path, name: str = "embedded",
                 rule_mode: str = "verified", human: Human | None = None, idf: IdfEnv | None = None,
                 board: BoardRunner | None = None, registry: Registry | None = None, max_attempts: int = 6,
                 subscribers: tuple[Callable[[dict], None], ...] = (), secrets: tuple[str, ...] = (),
                 coder: str | None = "coder-strong", design: Design | None = None, locked: dict[str, str] | None = None,
                 extra_requires: tuple[str, ...] = (), build_rules: bool = True, workflow=None, store=None,
                 human_review: bool = False, confirm_design: Callable[[Design], str | None] | None = None,
                 prior_wiring: dict[str, int] | None = None,
                 confirm_module: Callable[[DesignModule, "ModuleResult", str], str | None] | None = None):
        """workflow：上板工作流（firmware_dev.toml，DECISIONS 第 110 条）决定走哪些步骤、在哪停下、最多试几次、
        编码交给谁（不指定就由调度器挑）。不给（评测）就照原来的固定做法：编码固定用 coder（coder=None 时由调度器挑）；
        store 给了才有调度成绩和经验库（评测的 full 配置，DECISIONS 第 119 条）。
        给了工作流时由解释器逐步执行（fw_interpreter.py，DECISIONS 第 117 条）；confirm_module 是模块流程里
        "等人确认"步骤的回调，返回意见（空表示没意见）。"""
        if rule_mode not in RULE_MODES:
            raise ValueError(f"规则模式只能是 {', '.join(RULE_MODES)}")
        self.profile, self.spec, self.rules, self.library, self.verifier = profile, spec, rules, library, verifier
        self.plan = (firmware_plan(workflow, settings.loop, registry or default_registry()) if workflow is not None else
                     FirmwarePlan(build_rules=build_rules, confirm_rules=True, coder=coder, tester="tester",
                                  max_attempts=max_attempts))
        self.rule_mode, self.idf, self.board = rule_mode, idf, board
        self.workflow = workflow
        self._human_in = human                           # 解释器按"查资料后面有没有等人确认"决定用不用
        self.human = human if self.plan.confirm_rules else None
        self.confirm_module = confirm_module
        self.max_attempts = self.plan.max_attempts
        self.coder, self.fixed_design, self.locked = coder, design, dict(locked or {})
        self.extra_requires, self.build_rules = tuple(extra_requires), self.plan.build_rules
        self.store, self.human_review, self.confirm_design = store, human_review, confirm_design
        self.prior_wiring = dict(prior_wiring or {})       # 同一个项目上一次的接线（DECISIONS 第 108 条）
        self.exp = None
        if store is not None:                            # 给了数据库就用经验库（评测的 full 配置也用，第 119 条）
            from ..memory.experience import ExperienceBase
            self.exp = ExperienceBase(store)
        from ..orchestrator.scheduler import make_strategy

        def list_price(agent: AgentDef) -> float:
            t = settings.models.get(agent.model_tier)
            return (t.price_in_per_mtok + t.price_out_per_mtok) if t else 1.0
        self.strategy = make_strategy("smart", scheduler_cfg=settings.scheduler, concurrency=2, list_price=list_price)
        self._fails: dict[str, dict[str, int]] = {}      # 模块 → 编码 Agent → 连续没修好的次数
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
    def _design(self, rules: RuleSet, confirm: bool | None = None) -> Design | None:
        """confirm：设计好后要不要停下等人确认；None 按 self.plan（评测路径）。"""
        if confirm is None:
            confirm = self.plan.stop_after_design(self.human_review)
        if self.fixed_design is not None:
            self.rd.write_json("design.json", self.fixed_design.to_dict())
            self.bus.emit("plan_ready", "design", modules=[m.to_dict() for m in self.fixed_design.modules],
                          app=self.fixed_design.app, fixed=True)
            return self.fixed_design
        msgs = designer_messages(self.spec, self.profile, rules, prior_wiring=self.prior_wiring)
        tries = revisions = 0
        k = 0
        while tries < MAX_DESIGN_TRIES:
            k += 1
            resp = self.agents.call("designer", msgs, "design", attempt=k, purpose="设计分层" if k == 1 else "改设计")
            design, problems = design_from_text(resp.text, self.profile)
            if design is None:
                tries += 1
                self.bus.emit("design_rejected", "design", attempt=k, problems=problems[:10])
                msgs = msgs + [{"role": "assistant", "content": resp.text},
                               {"role": "user", "content": designer_retry_message(problems)}]
                continue
            self.rd.write_json("design.json", design.to_dict())
            self.bus.emit("plan_ready", "design", modules=[m.to_dict() for m in design.modules], app=design.app)
            if confirm and self.confirm_design is not None and revisions < MAX_DESIGN_TRIES:
                self.bus.emit("human_wait", "design", step="confirm_design", reason="设计好了，等你确认")
                comment = (self.confirm_design(design) or "").strip()
                if comment:                                # 负责人有意见：带着意见重做
                    revisions += 1
                    self.bus.emit("design_rejected", "design", attempt=k, problems=[f"负责人的意见：{comment}"])
                    msgs = msgs + [{"role": "assistant", "content": resp.text},
                                   {"role": "user", "content": f"负责人看了这个设计，意见如下，请按意见重做设计，"
                                                               f"格式和要求不变：\n{comment}"}]
                    continue
            return design
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
        wrong = self._check_chip()
        if wrong:
            self.bus.emit("verify_finished", m.id, attempt=attempt, scope="board_test", ok=False, failed_layer="skipped")
            return "skipped", "board_test", wrong
        r = self.board.run(proj, self.rd.attempt_dir(m.id, attempt) / "serial.log")
        self._check_wiring(r)
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

    # ---------------------------------------------------------------- 接线（DECISIONS 第 108 条）
    def _use_wiring(self, design: Design) -> None:
        """设计里给了这个项目的接线：后面的编码、测试、生成 hw_config.h、上板都改用它；没给就沿用硬件档案的默认接线。"""
        source = "design" if design.wiring else "default"
        if design.wiring:
            self.profile = self.profile.with_wiring(design.wiring)
        used = design.devices_used(self.profile)
        devices = [{"name": d.name, "part": d.part, "bus": d.bus, "address": d.address}
                   for d in self.profile.devices if d.name in used]
        wiring = {s: f.value for s, f in sorted(self.profile.pins.items())}
        self.rd.write_json("wiring.json", {"source": source, "pins": wiring, "devices": devices})
        self.bus.emit("wiring_ready", "design", source=source, pins=wiring, devices=devices)
        self._wiring_used, self._wiring_checked = used, False

    def _check_chip(self) -> str:
        """第一次上板前识别板子上的芯片（DECISIONS 第 114 条）；和硬件档案对不上返回说明，不烧录。"""
        if getattr(self, "_chip_checked", None) is not None:
            return self._chip_checked
        from ..target.chip_detect import mismatch
        info = self.board.chip() if hasattr(self.board, "chip") else None
        self._chip_checked = ""
        if info is not None:
            self._chip_checked = mismatch(info, str(self.profile.chip.value))
            self.bus.emit("chip_detected", None, target=info.target, description=info.description,
                          flash_mb=info.flash_mb, port=info.port, error=info.error,
                          expected=str(self.profile.chip.value), mismatch=self._chip_checked)
        return self._chip_checked

    def _check_wiring(self, res) -> None:
        """第一次真正跑起来的上板测试：测试程序在每个用例前复位外接器件，器件应答了就说明它确实接在这条 I2C 总线上。
        只能确认 I2C 器件的数据线和地址，确认不了中断等其他引脚。"""
        if getattr(self, "_wiring_checked", True) or not res.started:
            return
        self._wiring_checked = True
        text = "\n".join(res.logs)
        out = []
        for d in self.profile.devices:
            if d.name not in self._wiring_used or d.reset is None or not d.bus.startswith("i2c"):
                continue
            pins = "、".join(f"{s}=GPIO{self.profile.pins[s].value}" for s in (f"{d.bus}.sda", f"{d.bus}.scl")
                            if s in self.profile.pins)
            if f"建不了 {d.bus} 总线" in text:
                out.append({"device": d.name, "part": d.part, "ok": False, "pins": pins, "why": f"建不了 {d.bus} 总线"})
            elif f"复位 {d.name} 失败" in text:
                why = next(ln.split("失败：", 1)[-1] for ln in res.logs if f"复位 {d.name} 失败" in ln)
                out.append({"device": d.name, "part": d.part, "ok": False, "pins": pins, "why": why})
            else:
                out.append({"device": d.name, "part": d.part, "ok": True, "pins": pins,
                            "why": f"在 0x{d.address:02X} 应答了"})
        if out:
            self.bus.emit("wiring_checked", "design", devices=out)
            try:
                info = json.loads((self.rd.path / "wiring.json").read_text(encoding="utf-8"))
                info["checked"] = out
                self.rd.write_json("wiring.json", info)
            except (OSError, ValueError):
                pass

    # ---------------------------------------------------------------- 调度与经验（DECISIONS 第 110 条）
    def _pick_coder(self, m: DesignModule, attempt: int, pinned: str | None | object = _PLAN) -> tuple[str, str]:
        """这一轮编码交给谁。返回 (Agent 名, 统计分组)。分组按任务特征：硬件层还是逻辑层、第一次写还是修改。
        pinned：解释器传入工作流步骤里指定的 Agent（None 表示由调度器挑）；不传按 self.plan。"""
        bucket = f"fw-{m.layer}-{'first' if attempt == 1 else 'repair'}"
        pin = self.plan.coder if pinned is _PLAN else pinned
        if pin:
            self.bus.emit("node_assigned", m.id, agent=pin, role="coder", attempt=attempt, bucket=bucket,
                          scores={"strategy": "workflow" if self.plan.source else "fixed",
                                  "reason": f"{'工作流' if self.plan.source else '评测配置'}指定 {pin}"})
            return pin, bucket
        fails = self._fails.get(m.id, {})
        cands = self.agents.registry.find("coder")
        keep = [a for a in cands if fails.get(a.name, 0) < 2] or cands     # 连续两次没修好就换人（只剩它时照用）
        stats = self.store.agent_stats(bucket) if self.store is not None else {}
        agent, scores = self.strategy.choose(m.id, keep, stats, {})
        dropped = [a.name for a in cands if a not in keep]
        if dropped:
            scores = {**scores, "reason": scores.get("reason", "") + f"；{'、'.join(dropped)} 在这个模块上连续两次没修好，先不用"}
        self.bus.emit("node_assigned", m.id, agent=agent.name, role="coder", attempt=attempt, bucket=bucket, scores=scores)
        return agent.name, bucket

    def _record(self, turn: dict | None, attempt: int, verdict: str) -> None:
        """一轮编码之后检查的结果，记进这个 Agent 在这类任务上的成绩（调度器下次按它打分）。"""
        if turn is None:
            return
        ok = verdict == "pass"
        fails = self._fails.setdefault(turn["module"], {})
        fails[turn["agent"]] = 0 if ok else fails.get(turn["agent"], 0) + 1
        if self.store is None or verdict == "skipped":
            return
        a = self.agents.registry.get(turn["agent"])
        self.store.add_agent_stat(a.name, a.version, turn["bucket"], first_pass=ok and attempt == 1, success=ok,
                                  tokens=turn["tokens"], latency_s=turn["seconds"], cost=turn["cost"])

    def _experience_text(self, m: DesignModule) -> str:
        """同类模块以前修好过的问题（经验库），编码前附在提示词后面；没有就是空字符串。"""
        if self.exp is None:
            return ""
        cat = f"firmware-{m.layer}"
        if not self.exp.all(cat):                          # 没有上板流程的经验时不拿别的流程的来凑
            return ""
        found = self.exp.search(f"{m.title} {' '.join(m.scopes)} {' '.join(m.provides)}", k=3, category=cat)
        if not found:
            return ""
        self.exp.mark_used([e.id for e in found])
        self.bus.emit("experience_used", m.id, ids=[e.id for e in found], where="首次编码", patterns=[e.pattern for e in found])
        return ("\n\n## 过往经验（同类模块以前出过的问题和修好的办法，仅供参考）\n" +
                "\n".join(e.render() for e in found))

    def _save_experience(self, m: DesignModule, layer: str, report: str, diag: dict) -> None:
        if self.exp is None or not diag.get("instruction"):
            return
        lines = [ln.strip() for ln in report.splitlines() if ln.strip().startswith(("失败", "违规", "芯片崩溃"))]
        pattern = "；".join(lines[:3]) or report.strip().splitlines()[0][:200] if report.strip() else layer
        self.exp.save(f"firmware-{m.layer}", layer, pattern[:300], diag["instruction"], self.rd.run_id)

    def _write_both(self, m: DesignModule, rules: RuleSet, deps_h: dict, c_msgs: list[dict], coder: str):
        """第一次编码和写测试。工作流里两者并行时同时进行：测试 Agent 只看接口（锁定的头文件，或设计给出的函数原型）。"""
        writable = set(m.files)
        need_code = {f for f in writable if f.endswith(".c")} or writable     # 第一次要交齐全部 .c 文件
        tester = self.plan.tester or "tester"

        def code():
            return self._ask_files(coder, c_msgs, m.id, writable, need_code, attempt=1, purpose="编码",
                                   tools=self.rule_mode != "none")

        def test(header: str):
            t_msgs = tester_messages(self.spec, self.profile, rules, m, header, deps_h)
            return self._ask_files(tester, t_msgs, m.id, {test_file(m)}, {test_file(m)}, attempt=1, purpose="写测试")
        if self.plan.parallel_write:
            header = self.locked.get(m.header) or (
                "/* 设计给出的接口（编码 Agent 写的头文件以它为准） */\n#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n"
                + "\n".join(p if p.rstrip().endswith(";") else p.rstrip() + ";" for p in m.provides) + "\n")
            with ThreadPoolExecutor(max_workers=2) as ex:
                fc, ft = ex.submit(code), ex.submit(test, header)
                return fc.result(), ft.result()
        c = code()
        return c, test(self.locked.get(m.header) or c[1].files.get(m.header, ""))

    def _module(self, design: Design, m: DesignModule, rules: RuleSet) -> ModuleResult:
        mr = ModuleResult(m.id, m.layer)
        self.result.modules[m.id] = mr
        self.bus.emit("node_ready", m.id, title=m.title, layer=m.layer)
        deps_h = self._dep_headers(design, m)
        c_msgs = coder_messages(self.spec, self.profile, rules, design, m, deps_h,
                                locked_header=self.locked.get(m.header))
        tip = self._experience_text(m)
        if tip and c_msgs and c_msgs[-1].get("role") == "user":
            c_msgs = c_msgs[:-1] + [{**c_msgs[-1], "content": (c_msgs[-1].get("content") or "") + tip}]
        writable = set(m.files)
        coder, bucket = self._pick_coder(m, 1)
        before = (self.result.prompt_tokens + self.result.completion_tokens, self.result.cost, time.time())
        (c_conv, out), (t_conv, tout) = self._write_both(m, rules, deps_h, c_msgs, coder)
        turn = self._turn(m, coder, bucket, before)
        mr.files.update(out.files)
        self._new_facts(out.facts)
        mr.tests.update(tout.files)
        last: tuple[str, str, dict] | None = None             # 上一轮没过的层、报告、诊断（修好后存经验）
        for attempt in range(1, self.max_attempts + 1):
            mr.attempts = attempt
            verdict, layer, report = self._check(design, m, mr, rules, attempt)
            mr.last_report = report
            self._write_snapshot(m, mr, attempt, report)
            self._record(turn, attempt, verdict)
            turn = None
            if verdict == "pass" and last is not None and last[2].get("blame") == "code":
                self._save_experience(m, *last)
            if verdict in ("pass", "skipped"):
                mr.verdict = verdict
                self.bus.emit("node_done" if verdict == "pass" else "node_failed", m.id, verdict=verdict,
                              attempts=attempt, note=report[-300:])
                return mr
            if attempt == self.max_attempts:
                break
            diag = self._diagnose(m, mr, rules, layer, report, attempt)
            last = (layer, report, diag)
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
                t_conv, tout = self._ask_files(self.plan.tester or "tester", t_conv + [{"role": "user", "content": msg}], m.id,
                                               {test_file(m)}, {test_file(m)}, attempt=attempt + 1, purpose="改测试")
                mr.tests.update(tout.files)
            else:
                msg = coder_repair_message(attempt, self.max_attempts, report, diag)
                coder, bucket = self._pick_coder(m, attempt + 1)        # 修改轮重新挑：成绩差的会被换掉
                before = (self.result.prompt_tokens + self.result.completion_tokens, self.result.cost, time.time())
                # 修改轮可以只交改动的文件，但至少要交一个
                c_conv, o = self._ask_files(coder, c_conv + [{"role": "user", "content": msg}], m.id, writable,
                                            None, attempt=attempt + 1, purpose="改代码", tools=self.rule_mode != "none")
                turn = self._turn(m, coder, bucket, before)
                mr.files.update(o.files)
                self._new_facts(o.facts)
        mr.verdict = "fail"
        self.bus.emit("node_failed", m.id, verdict="fail", attempts=mr.attempts, note=mr.last_report[-300:])
        return mr

    def _turn(self, m: DesignModule, agent: str, bucket: str, before: tuple) -> dict:
        tokens, cost, t0 = before
        return {"module": m.id, "agent": agent, "bucket": bucket, "seconds": round(time.time() - t0, 2),
                "tokens": self.result.prompt_tokens + self.result.completion_tokens - tokens,
                "cost": round(self.result.cost - cost, 6)}

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
        if layer == "build":                                 # 编译错误不花钱诊断：看报错落在谁写的文件里
            where = build_error_files(report)
            mine = {Path(p).name for p in mr.tests}
            if where and where <= mine:                      # 报错全在 Agent 写的测试里：交给测试 Agent（第 122 条）
                diag = {"blame": "test", "rule": "", "evidence": f"编译报错都在测试文件 {'、'.join(sorted(where))}",
                        "instruction": "测试文件编译不过。按编译器报错改测试文件；用到的宏必须在平台头文件里真的存在，"
                                       "并自己 #include 对应头文件；外接器件的寄存器地址没有宏，直接写数字"}
                self.bus.emit("diagnosis", m.id, attempt=attempt, **diag)
                return diag
        if layer in ("compliance", "build"):                 # 驱动代码编译不过、合规违规：代码的错
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
                      board=self.profile.board.value, workflow=self.plan.source)
        t0 = time.time()
        if self.workflow is not None:                    # 按工作流逐步执行（DECISIONS 第 117 条）
            return self._run_workflow(res, t0)
        try:
            rules = self._rules_for_flow()
            res.rules_used = [r.id for r in rules.select(None, "approved")]
            design = self._design(rules)
            res.design = design
            if design is not None:
                self._use_wiring(design)
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

    def _run_workflow(self, res: FlowResult, t0: float) -> FlowResult:
        from .fw_interpreter import FirmwareInterpreter
        try:
            FirmwareInterpreter(self, self.workflow).run_main()
        except IdfUnavailable as exc:
            res.error = str(exc)
        except Exception as exc:                         # 和固定路径一样：先把记录写完整再往上抛
            res.verdict, res.error = "error", f"{type(exc).__name__}: {exc}"
            self._finish(res, t0)
            raise
        self._finish(res, t0)
        return res

    def _finish(self, res: FlowResult, t0: float) -> None:
        self.rd.write_json("run.json", {"verdict": res.verdict, "error": res.error, "rule_mode": self.rule_mode,
                                        "workflow": self.plan.source,
                                        "rules_used": res.rules_used,
                                        "modules": {k: v.verdict for k, v in res.modules.items()},
                                        "tokens": res.prompt_tokens + res.completion_tokens,
                                        "cost": round(res.cost, 6),
                                        "seconds": round(time.time() - t0, 1)})
        self.bus.emit("run_finished", None, verdict=res.verdict, tokens=res.prompt_tokens + res.completion_tokens,
                      cost=round(res.cost, 6), error=res.error)

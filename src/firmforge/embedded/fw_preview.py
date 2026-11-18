"""工作流预演（设计器"检查"里用，负责人 2026-11-14 选定）：拿真正的解释器（fw_interpreter.py）把上板工作流空跑几种情况，
记下每种情况实际按什么顺序走了哪些步骤。

不调用模型、不编译、不上板：解释器调用的"干活"方法换成记账的替身，检查结果、诊断结论、审查意见按情况预先定好。
因为走的是解释器本身的代码，预演出错就说明真跑也会出错（预演时的异常当作检查问题报出来）。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field

from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet
from ..orchestrator.workflow import Workflow
from .design import Design, DesignModule
from .roles import AgentOutput


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    layer: str                                   # hardware / logic
    checks: tuple[str, ...]                      # 依次每次检查的结论：pass / fail；用完以后一直是最后一个
    blame: str = "code"                          # 没通过时诊断的结论
    human_review: bool = False                   # 开了"人工确认"
    review: str = "ok"                           # 审查结论：ok / needs_fix
    comment: str = ""                            # 模块做完等人确认时写的意见


SCENARIOS = (
    Scenario("hw_pass", "硬件层模块，第一次就通过", "hardware", ("pass",)),
    Scenario("logic_fix", "逻辑层模块，第一次没过、修好", "logic", ("fail", "pass")),
    Scenario("never", "一直没通过（试到上限）", "hardware", ("fail",)),
    Scenario("hw_wiring", "诊断为硬件或接线问题", "hardware", ("fail",), blame="hardware"),
    Scenario("review_fix", "审查要求修改、改完通过", "hardware", ("pass",), review="needs_fix"),
    Scenario("human", "开了人工确认，模块做完你写了意见", "hardware", ("pass",), human_review=True, comment="改一下"),
)


@dataclass
class Trace:
    scenario: Scenario
    events: list[tuple[str, str]] = field(default_factory=list)   # (步骤 id，没有就是 ""，说明)
    lit: list[str] = field(default_factory=list)                   # 真正起了作用的步骤，按先后（画布上依次点亮）
    verdict: str = ""
    error: str = ""

    def light(self, sid: str) -> None:
        if sid and sid not in self.lit:
            self.lit.append(sid)

    @property
    def step_ids(self) -> list[str]:
        return list(self.lit)


_PROFILE = {"chip": {"value": "esp32c3"}, "chip_variant": {"value": "ESP32-C3"}, "board": {"value": "预演"},
            "framework": {"value": "esp-idf"}, "framework_version": {"value": "v6.1"}}


class _Bus:
    def __init__(self, trace: Trace):
        self.trace = trace

    def emit(self, type_: str, node=None, **data):
        if type_ == "human_wait":
            self.trace.events.append(("", f"停下等你：{data.get('reason', '')}"))
        elif type_ == "review_reverted":
            self.trace.events.append(("", "按审查意见改坏了，退回通过的版本"))
        elif type_ == "human_reverted":
            self.trace.events.append(("", "按你的意见改坏了，退回通过的版本"))


class _Resp:
    def __init__(self, text: str):
        self.text, self.prompt_tokens, self.completion_tokens, self.latency_s = text, 0, 0, 0.0


class _Agents:
    def __init__(self, flow: "_StubFlow"):
        from ..agents.registry import default_registry
        self.flow, self.registry = flow, default_registry()
        self.settings = type("PreviewSettings", (), {"loop": {}})()     # 解释器只用到 loop（max 写成 loop.xxx 时取默认 6）

    def call(self, name, messages, node, *, attempt=1, purpose="", tools=False):
        self.flow._note(f"{purpose or name}（{name}）")
        if purpose == "审查":
            sc = self.flow.sc
            issues = [{"severity": "high", "rule": "预演", "message": "预演里设定要改"}] if sc.review == "needs_fix" else []
            import json
            return _Resp("```json\n" + json.dumps({"verdict": sc.review, "issues": issues}) + "\n```")
        return _Resp("")


class _StubFlow:
    """解释器要用到的 EmbeddedFlow 接口的替身：干活的方法只记账，结论按情况给出。"""

    def __init__(self, sc: Scenario, trace: Trace):
        from .flow import FlowResult
        self.sc, self.trace = sc, trace
        self.bus = _Bus(trace)
        self.agents = _Agents(self)
        self.result = FlowResult(run_dir=None)
        self.rule_mode, self.rules, self.locked, self.spec = "none", RuleSet(), {}, "预演"
        self.profile = HardwareProfile.from_dict(_PROFILE)
        self.human_review = sc.human_review
        self.human, self._human_in, self.build_rules, self.max_attempts = None, object(), True, 1
        self.confirm_module = self._confirm_module
        self.idf, self.board, self.extra_requires = "预演", None, ()      # 集成步骤只看 idf 有没有；真正的检查换成替身
        self._checks = 0
        self._current = ""                      # 正在执行的步骤 id（记账用）

    def _note(self, text: str) -> None:
        self.trace.events.append((self._current, text))
        self.trace.light(self._current)

    def _ensure_board(self) -> bool:                 # 预演不碰真板子（第 141 条的重新找板子在这里不做）
        return self.board is not None

    def _no_board_note(self, what: str) -> str:
        return f"没有配置板子，{what}只做到编译和合规检查"

    # ---- 主流程
    def _rules_for_flow(self):
        self._note("查资料提炼规则" + ("，新规则等你确认" if self.human is not None else ""))
        return self.rules

    def _design(self, rules, confirm=None):
        self._note("设计分层" + ("，设计好后等你确认" if confirm else ""))
        m = DesignModule("m", "预演模块", self.sc.layer, ("m.c", "include/m.h"), provides=("int m_read(void)",))
        return Design([m])

    def _use_wiring(self, design):
        pass

    # ---- 模块
    def _dep_headers(self, design, m):
        return {}

    def _experience_text(self, m):
        return ""

    def _pick_coder(self, m, attempt, pinned=None):
        return (pinned or "调度器挑的编码 Agent"), "x"

    def _turn(self, m, agent, bucket, before):
        return {}

    def _ask_files(self, agent, conv, node, allowed, need, *, attempt, purpose, tools=False):
        self._note(f"{purpose}（{agent}）")
        return list(conv) + [{"role": "assistant", "content": ""}], AgentOutput(files={f: "" for f in sorted(allowed)})

    def _new_facts(self, facts):
        pass

    def _check(self, design, m, mr, rules, attempt):
        v = self.sc.checks[min(self._checks, len(self.sc.checks) - 1)]
        self._checks += 1
        self._note(f"第 {attempt} 次检查：{'通过' if v == 'pass' else '没通过'}")
        return v, "board_test" if m.layer == "hardware" else "host_test", ""

    def _write_snapshot(self, *a):
        pass

    def _record(self, *a):
        pass

    def _save_experience(self, *a):
        pass

    def _diagnose(self, m, mr, rules, layer, report, attempt):
        names = {"code": "代码", "test": "测试", "rule": "规则", "hardware": "硬件或接线"}
        self._note(f"诊断：问题出在{names[self.sc.blame]}")
        return {"blame": self.sc.blame, "instruction": "预演", "evidence": "", "rule": ""}

    def _dispute_rule(self, rules, diag):
        return rules

    def _harden_tests(self, design, m, mr, rules, attempt, t_conv, tester):
        self._note("通过后用变异体检查测试够不够严（预演不上板，跳过）")
        return t_conv, False

    def _restore_before_hardening(self, m, mr, backup):
        pass

    def _recheck_hardware(self, m, mr, rules, layer, report, attempt, diag):
        self._note("接线自检（预演不上板，按测不了处理，维持硬件问题）")
        return diag

    def _wiring_gate(self, design):
        return True                                  # 预演不接线、不上板

    def _check_chip(self):
        return ""

    def _confirm_module(self, m, mr, verdict):
        self._note("等你看结果" + (f"，你写了意见：{self.sc.comment}" if self.sc.comment else "，你点了通过"))
        return self.sc.comment


def preview(wf: Workflow, scenarios=SCENARIOS) -> list[Trace]:
    """用解释器把工作流空跑每种情况。工作流本身执行不了（检查不过）时抛 WorkflowError。"""
    from .fw_interpreter import FirmwareInterpreter
    out = []
    for sc in scenarios:
        tr = Trace(sc)
        flow = _StubFlow(sc, tr)
        it = FirmwareInterpreter(flow, wf)
        orig = it._step

        def step(s, run, _orig=orig, _flow=flow):        # 记下正在执行模块流程里的哪一步，再照常执行
            if it._on(s, run):
                _flow._current = s.id
                if s.kind == "condition":                # 条件本身不干活，但走到了也要点亮
                    tr.light(s.id)
            r = _orig(s, run)
            if s.kind == "parallel" and s.id in tr.lit:  # 并行的两个分支画成两张卡片，一起点亮
                for b in s.branches:
                    tr.light(b)
            return r
        it._step = step

        def check_app(design, mr, module_files, rules, attempt, _flow=flow):    # 集成：不编译、不上板，按通过记
            mr.attempts = attempt
            _flow._note(f"第 {attempt} 次主程序检查：通过（真跑时编译、烧录、复位跑两轮看串口）")
            return "pass", ""
        it._check_app = check_app
        it._deliver = lambda mr, module_files, _flow=flow: _flow._note("把完整工程交付到运行目录的 firmware/")
        _main_ids = {s.id for s in wf.main}

        def emit(type_, node=None, _bus=flow.bus, **data):
            if type_ == "step_started" and node in _main_ids:
                flow._current = node
                if data.get("kind") == "foreach":         # 每个模块各跑：开始跑模块就点亮
                    tr.light(node)
            _bus.__class__.emit(_bus, type_, node, **data)
        flow.bus.emit = emit
        try:
            it.run_main()
            main = Workflow.top_level(wf.main)           # 确认规则、确认设计由前一步处理：前一步说了"等你确认"就点亮它
            for i, s in enumerate(main[1:], 1):
                prev = main[i - 1].id
                if (s.kind == "human" and prev in tr.lit and s.id not in tr.lit
                        and any(sid == prev and ("等你确认" in t or "等你看结果" in t) for sid, t in tr.events)):
                    tr.lit.insert(tr.lit.index(prev) + 1, s.id)
            mr = flow.result.modules.get("m")
            tr.verdict = mr.verdict if mr is not None else (flow.result.error or "没有执行到模块")
        except Exception as exc:                         # noqa: BLE001  预演出错就是真跑也会出错
            tr.error = f"{type(exc).__name__}: {exc}\n" + traceback.format_exc(limit=3)[-600:]
        out.append(tr)
    return out


VERDICT_NAMES = {"pass": "通过", "fail": "没通过", "skipped": "跳过", "hardware": "停下等你查硬件"}


def describe(tr: Trace) -> list[str]:
    """一种情况的预演结果写成几行字：每一步做了什么。"""
    lines = [f"{sid}：{text}" if sid else text for sid, text in tr.events if text]
    if tr.error:
        lines.append(f"出错：{tr.error.splitlines()[0]}")
    else:
        lines.append(f"结果：{VERDICT_NAMES.get(tr.verdict, tr.verdict)}")
    return lines

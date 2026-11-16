"""上板流程的工作流解释器（DECISIONS 第 117 条）：按工作流文件逐步执行，顺序和结构完全由文件决定。

每种步骤一个处理函数，真正干活的（调用 Agent、编译、上板、诊断、调度选人、经验库）都复用 EmbeddedFlow 里的方法，
所以默认工作流 firmware_dev.toml 跑出来和原来的固定流程一样；设计器里改了顺序、加了审查或条件，这里就照着做。
评测（不给工作流）不经过这里，走 EmbeddedFlow 原来的固定路径。

模块流程里的状态（ModuleRun）：
- 已经写过的代码、测试和各自的对话（修改轮接着这段对话）；
- 最近一次检查的结论（pass / fail / skipped）、出问题的层、报告，以及检查了几次（attempt）；
- 上一轮诊断（修好后存经验）、审查结果、是否已经停下（硬件问题等人）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from ..agents.roles import parse_review
from ..grounding.rules import RuleSet
from ..orchestrator.dag import extract_json  # noqa: F401  （改验收条件时也用）
from ..orchestrator.workflow import Step, Workflow
from .design import Design, DesignModule
from .firmware_workflow import firmware_problems
from .roles import (coder_repair_message, human_fix_message, review_fix_message, reviewer_messages, test_file,
                    tester_messages, tester_repair_message)


@dataclass
class ModuleRun:
    m: DesignModule
    mr: object                                     # flow.ModuleResult
    rules: RuleSet
    deps_h: dict
    c_msgs: list
    coder_pin: str | None = None                   # 模块流程里编码步骤指定的 Agent（修正循环也交给它；不指定由调度器挑）
    tester_pin: str | None = None
    c_conv: list | None = None
    t_conv: list | None = None
    turn: dict | None = None
    verdict: str | None = None                     # 最近一次检查：pass / fail / skipped
    layer: str = ""
    report: str = ""
    attempt: int = 0                               # 检查了几次
    last: tuple | None = None                      # 上一轮没过的层、报告、诊断
    review: dict | None = None
    stopped: str = ""                              # 停下的原因（hardware：诊断为硬件问题，等人处理）
    passed_files: dict = field(default_factory=dict)   # 最近一次通过时的代码（审查改坏了退回）
    hardened: bool = False                         # 做过变异体补测试了（第 125 条，每个模块最多一次）
    hardened_backup: dict | None = None            # 补测试前通过的代码（补强后没修好就交付它）
    hardened_tests_backup: dict | None = None      # 补测试前的测试（补的测试本身有错时退回它，第 131 条）


class FirmwareInterpreter:
    def __init__(self, flow, wf: Workflow):
        probs = firmware_problems(wf, flow.agents.registry)
        if probs:
            from ..orchestrator.workflow import WorkflowError
            raise WorkflowError(f"上板工作流 {wf.name} {wf.version} 执行不了：" + "；".join(probs))
        self.flow, self.wf = flow, wf
        self.loop_cfg = flow.agents.settings.loop

    # ------------------------------------------------------------ 公共

    def _on(self, s: Step, run: ModuleRun | None = None) -> bool:
        """步骤要不要执行：enabled_by 和 when 都成立才执行。"""
        if s.enabled_by and not self._cond(s.enabled_by, run):
            return False
        return not s.when or self._cond(s.when, run)

    def _cond(self, name: str, run: ModuleRun | None) -> bool:
        if name == "human_review":
            return bool(self.flow.human_review)
        if run is None:
            return False
        if name == "verify_passed":
            return run.verdict == "pass"
        if name == "hardware_layer":
            return run.m.layer == "hardware"
        if name == "logic_layer":
            return run.m.layer == "logic"
        if name == "attempts_exhausted":
            return run.attempt >= self._max_attempts(run)
        if name == "review_needs_fix":
            return bool(run.review and run.review.get("verdict") == "needs_fix")
        raise ValueError(f"未知条件 {name}")

    # ------------------------------------------------------------ 主流程

    def run_main(self):
        f = self.flow
        res = f.result
        rules = RuleSet() if f.rule_mode == "none" else f.rules
        design: Design | None = None
        main = Workflow.top_level(self.wf.main)
        by = Workflow.by_id(self.wf.main)
        for i, s in enumerate(main):
            if not self._on(s):
                continue
            nxt = main[i + 1] if i + 1 < len(main) else None
            confirm = self._confirm_follows(nxt, by)
            f.bus.emit("step_started", s.id, kind=s.kind, role=s.role)
            if s.kind == "agent" and s.role == "researcher":
                f.human = f._human_in if confirm else None
                f.build_rules = True
                rules = f._rules_for_flow()
            elif s.kind == "agent" and s.role == "designer":
                res.rules_used = [r.id for r in rules.select(None, "approved")]
                design = f._design(rules, confirm=confirm)
                res.design = design
                if design is None:
                    res.error = "设计几次都没有通过检查"
                    return
                f._use_wiring(design)
                if not f._wiring_gate(design):            # 接线一步（第 134 条）：接好、自检对上才写代码
                    return
            elif s.kind == "verify" and s.scope == "all":    # 集成：写主程序、上板跑（第 131 条）
                if design is not None:
                    self.integrate(s, design, rules, confirm)
            elif s.kind in ("human", "condition"):
                pass                                   # 确认规则、确认设计、看集成结果由前一步处理（见 _confirm_follows）
            elif s.kind == "foreach":
                if design is None:
                    res.error = "还没有设计，不能按模块执行"
                    return
                if not res.rules_used:
                    res.rules_used = [r.id for r in rules.select(None, "approved")]
                steps = self.wf.subflows[s.subflow]
                for m in design.order():
                    if any(res.modules.get(d) is None or res.modules[d].verdict not in ("pass", "skipped")
                           for d in m.depends_on):
                        from .flow import ModuleResult
                        res.modules[m.id] = ModuleResult(m.id, m.layer, verdict="skipped", last_report="依赖的模块没有完成")
                        f.bus.emit("node_failed", m.id, verdict="skipped", note="依赖的模块没有完成")
                        continue
                    self.run_module(steps, design, m, rules)
        verdicts = {mr.verdict for mr in res.modules.values()}
        if design is not None:
            res.verdict = "pass" if verdicts == {"pass"} else "incomplete" if verdicts <= {"pass", "skipped"} else "fail"
            if res.verdict == "pass" and res.app is not None:     # 模块都过了，结论还要看主程序上板跑得对不对
                res.verdict = {"pass": "pass", "skipped": "incomplete"}.get(res.app.verdict, "fail")

    def _confirm_follows(self, nxt: Step | None, by: dict) -> bool:
        """紧跟着的是等人确认（或开关打开时才走的条件分支里的等人确认），并且这次要停。"""
        if nxt is None:
            return False
        if nxt.kind == "human":
            return self._on(nxt)
        if nxt.kind == "condition" and self._on(nxt):
            branch = nxt.then if self._cond(nxt.cond, None) else nxt.otherwise
            return any(by[r].kind == "human" and self._on(by[r]) for r in branch if r in by)
        return False

    # ------------------------------------------------------------ 集成（第 131 条）

    def integrate(self, s: Step, design: Design, rules: RuleSet, confirm: bool) -> None:
        """所有模块通过后：编码 Agent 写 app_main.c 和期望的串口输出，平台编译、烧录、复位跑两轮核对；
        没通过就带着报告让它改，最多 max 次。开了人工确认时跑完停下等人看。最后把完整工程交付到运行目录的 firmware/。"""
        from .flow import ModuleResult
        from .integrate import APP_FILE, EXPECT_FILE, app_module, integrator_messages, integrator_repair_message
        f, res = self.flow, self.flow.result
        m = app_module()
        mr = ModuleResult(m.id, m.layer)
        res.app = mr
        if not res.modules or any(x.verdict != "pass" for x in res.modules.values()):
            mr.verdict, mr.last_report = "skipped", "有模块没有通过，不做集成"
            f.bus.emit("node_failed", m.id, verdict="skipped", note=mr.last_report)
            return
        if f.idf is None:                                  # 编译不了就不花钱写主程序；结论记 skipped，不算通过
            mr.verdict, mr.last_report = "skipped", "ESP-IDF 不可用，主程序无法编译和上板运行，没有做集成"
            f.bus.emit("node_failed", m.id, verdict="skipped", note=mr.last_report)
            return
        f.bus.emit("node_ready", m.id, title=m.title, layer=m.layer)
        limit = max(1, min(5, self.wf.resolve_max(s, self.loop_cfg))) if s.max not in (None, "") else 3
        module_files = dict(f.locked)
        headers = {}
        for dm in design.modules:
            files = res.modules[dm.id].files
            module_files.update(files)
            if dm.header in files:
                headers[dm.header] = files[dm.header]
        agent = s.agent or "coder-strong"
        allowed = {APP_FILE, EXPECT_FILE}
        tools = f.rule_mode != "none"
        conv, out = f._ask_files(agent, integrator_messages(f.spec, f.profile, rules, design, headers), m.id, allowed,
                                 allowed, attempt=1, purpose="写主程序", tools=tools)
        mr.files.update(out.files)
        verdict = "fail"
        for attempt in range(1, limit + 1):
            verdict, mr.last_report = self._check_app(design, mr, module_files, rules, attempt)
            if verdict != "fail" or attempt == limit:
                break
            conv, o = f._ask_files(agent, conv + [{"role": "user", "content": integrator_repair_message(attempt, limit, mr.last_report)}],
                                   m.id, allowed, None, attempt=attempt + 1, purpose="改主程序", tools=tools)
            mr.files.update(o.files)
        ask = getattr(f, "confirm_module", None)
        if confirm and ask is not None and verdict in ("pass", "fail"):
            f.bus.emit("human_wait", m.id, step="confirm_app", reason="主程序上板跑完了，等你看结果（串口记录在运行目录里）",
                       verdict=verdict)
            comment = (ask(m, mr, verdict) or "").strip()
            if comment:
                keep = dict(mr.files)
                conv, o = f._ask_files(agent, conv + [{"role": "user", "content": human_fix_message(comment)}], m.id, allowed,
                                       None, attempt=mr.attempts + 1, purpose="按你的意见改", tools=tools)
                mr.files.update(o.files)
                was_pass = verdict == "pass"
                verdict, mr.last_report = self._check_app(design, mr, module_files, rules, mr.attempts + 1)
                if was_pass and verdict != "pass":
                    mr.files.clear()
                    mr.files.update(keep)
                    verdict = "pass"
                    f.bus.emit("human_reverted", m.id, attempt=mr.attempts,
                               note="按你的意见改了以后没通过，退回改之前通过的版本")
        mr.verdict = verdict
        self._deliver(mr, module_files)
        f.bus.emit("node_done" if verdict == "pass" else "node_failed", m.id, verdict=verdict, attempts=mr.attempts,
                   note=mr.last_report[-300:])

    def _check_app(self, design: Design, mr, module_files: dict, rules: RuleSet, attempt: int) -> tuple[str, str]:
        """合规 → 编译整个工程 → 烧录、跑两轮、核对串口。返回 (结论, 报告)；结论 pass / fail / skipped。"""
        from pathlib import Path

        from .. import config
        from ..grounding.compliance import check_code
        from ..target.esp_idf import DEFAULT_REQUIRES, IdfProject
        from .integrate import (APP_FILE, EXPECT_FILE, PHASES, app_module, check_app_output, parse_expectations,
                                window_seconds)
        f, m = self.flow, mr.id
        mr.attempts = attempt

        def done(verdict: str, layer: str, report: str) -> tuple[str, str]:
            f._write_snapshot(app_module(), mr, attempt, report)
            return verdict, report
        expects, probs = parse_expectations(mr.files.get(EXPECT_FILE, ""))
        scopes = {x for dm in design.modules for x in dm.scopes} - {"all"}
        comp = check_code({APP_FILE: mr.files.get(APP_FILE, "")}, rules.select(scopes, "approved") if rules is not None else [])
        ok = comp.ok and not probs
        f.bus.emit("verify_finished", m, attempt=attempt, scope="compliance", ok=ok, failed_layer=None if ok else "compliance")
        if not ok:
            return done("fail", "compliance", "\n".join(([comp.summary()] if not comp.ok else [])
                                                        + [f"{EXPECT_FILE}：{p}" for p in probs]))
        if f.idf is None:
            return done("skipped", "build", "ESP-IDF 不可用，主程序无法编译和上板运行")
        reqs = DEFAULT_REQUIRES + tuple(f.extra_requires)
        proj = IdfProject(Path(config.project_root()) / ".ff_data" / "idf_build" / f"{f.rd.run_id}-app", f.idf,
                          target=str(f.profile.chip.value), requires=reqs)
        proj.write(f.profile, {**module_files, APP_FILE: mr.files[APP_FILE]})
        f.bus.emit("verify_started", m, attempt=attempt, scope="build")
        b = proj.build()
        f.bus.emit("verify_finished", m, attempt=attempt, scope="build", ok=b.ok, failed_layer=None if b.ok else "build")
        if not b.ok:
            errs = "\n".join(d.render() for d in b.errors) or b.log_tail[-1500:]
            return done("fail", "build", "编译失败：\n" + errs)
        if f.board is None:
            return done("skipped", "board_test", "没有配置板子，主程序只做到编译和合规检查")
        wrong = f._check_chip()
        if wrong:
            f.bus.emit("verify_finished", m, attempt=attempt, scope="board_test", ok=False, failed_layer="skipped")
            return done("skipped", "board_test", wrong)
        secs = window_seconds(expects)
        f.bus.emit("verify_started", m, attempt=attempt, scope="board_test")
        outs, why = f.board.run_app(proj, secs, PHASES, f.rd.attempt_dir(m, attempt))
        if why:
            f.bus.emit("verify_finished", m, attempt=attempt, scope="board_test", ok=False, failed_layer="skipped")
            return done("skipped", "board_test", why)
        chk = check_app_output(outs, expects, secs)
        f.bus.emit("verify_finished", m, attempt=attempt, scope="board_test", ok=chk.ok,
                   failed_layer=None if chk.ok else "board_test")
        f.bus.emit("app_checked", m, attempt=attempt, ok=chk.ok, seconds=secs, problems=chk.problems[:10],
                   matched=chk.matched[:12])
        return done("pass" if chk.ok else "fail", "board_test", chk.report())

    def _deliver(self, mr, module_files: dict) -> None:
        """把完整工程（各模块 + 主程序 + hw_config.h，不带测试）写到运行目录的 firmware/，用 idf.py 就能编译烧录。"""
        from ..target.esp_idf import DEFAULT_REQUIRES, IdfProject
        from .integrate import APP_FILE
        f = self.flow
        if APP_FILE not in mr.files:
            return
        dest = f.rd.path / "firmware"
        IdfProject(dest, f.idf, target=str(f.profile.chip.value),
                   requires=DEFAULT_REQUIRES + tuple(f.extra_requires)).write(f.profile, {**module_files, APP_FILE: mr.files[APP_FILE]})
        (dest / "验证报告.txt").write_text(f"主程序检查结论：{mr.verdict}（共 {mr.attempts} 次）\n\n{mr.last_report}\n",
                                         encoding="utf-8")
        f.bus.emit("firmware_ready", "app", path=str(dest), verdict=mr.verdict)

    # ------------------------------------------------------------ 模块流程

    def _max_attempts(self, run: ModuleRun) -> int:
        loop = next((s for s in self._mod_steps if s.kind == "loop"), None)
        return max(1, min(10, self.wf.resolve_max(loop, self.loop_cfg))) if loop is not None else 1

    def run_module(self, steps: tuple[Step, ...], design: Design, m: DesignModule, rules: RuleSet):
        from .flow import ModuleResult
        f = self.flow
        self._mod_steps, self._by = steps, Workflow.by_id(steps)
        f.max_attempts = self._max_attempts(None)          # 修改消息里写"最多几次"
        mr = ModuleResult(m.id, m.layer)
        f.result.modules[m.id] = mr
        f.bus.emit("node_ready", m.id, title=m.title, layer=m.layer)
        deps_h = f._dep_headers(design, m)
        from .roles import coder_messages
        c_msgs = coder_messages(f.spec, f.profile, rules, design, m, deps_h, locked_header=f.locked.get(m.header))
        tip = f._experience_text(m)
        if tip and c_msgs and c_msgs[-1].get("role") == "user":
            c_msgs = c_msgs[:-1] + [{**c_msgs[-1], "content": (c_msgs[-1].get("content") or "") + tip}]
        coder_step = next((s for s in steps if s.kind == "agent" and s.role == "coder"), None)
        tester_step = next((s for s in steps if s.kind == "agent" and s.role == "tester"), None)
        run = ModuleRun(m, mr, rules, deps_h, c_msgs, coder_pin=coder_step.agent if coder_step else None,
                        tester_pin=tester_step.agent if tester_step else None)
        self.design = design
        for s in Workflow.top_level(steps):
            if run.stopped:
                break
            self._step(s, run)
        self._finish_module(run)
        return mr

    def _step(self, s: Step, run: ModuleRun) -> None:
        if not self._on(s, run):
            return
        if s.kind == "parallel":
            self._write_parallel(run)
        elif s.kind == "agent" and s.role == "coder":
            self._code(run, s)
        elif s.kind == "agent" and s.role == "tester":
            self._test(run, s)
        elif s.kind == "agent" and s.role == "reviewer":
            self._review(run, s)
        elif s.kind == "verify":
            self._verify(run)
        elif s.kind == "loop":
            self._repair_loop(run)
        elif s.kind == "human":
            self._human(run, s)
        elif s.kind == "condition":
            for r in (s.then if self._cond(s.cond, run) else s.otherwise):
                if r in self._by and not run.stopped:
                    self._step(self._by[r], run)

    # ---- 写代码、写测试

    def _interface(self, run: ModuleRun) -> str:
        """测试 Agent 看的接口：锁定的头文件 > 编码 Agent 已经写好的头文件 > 设计给出的函数原型。"""
        f, m = self.flow, run.m
        if m.header in f.locked:
            return f.locked[m.header]
        if m.header in run.mr.files:
            return run.mr.files[m.header]
        return ("/* 设计给出的接口（编码 Agent 写的头文件以它为准） */\n#pragma once\n#include <stdint.h>\n#include <stdbool.h>\n"
                + "\n".join(p if p.rstrip().endswith(";") else p.rstrip() + ";" for p in m.provides) + "\n")

    def _first_code(self, run: ModuleRun, pin):
        f, m = self.flow, run.m
        coder, bucket = f._pick_coder(m, 1, pinned=pin)
        before = (f.result.prompt_tokens + f.result.completion_tokens, f.result.cost, time.time())
        writable = set(m.files)
        need = {x for x in writable if x.endswith(".c")} or writable
        conv, out = f._ask_files(coder, run.c_msgs, m.id, writable, need, attempt=1, purpose="编码",
                                 tools=f.rule_mode != "none")
        return conv, out, f._turn(m, coder, bucket, before)

    def _first_test(self, run: ModuleRun, pin, header: str):
        f, m = self.flow, run.m
        msgs = tester_messages(f.spec, f.profile, run.rules, m, header, run.deps_h)
        return f._ask_files(pin or "tester", msgs, m.id, {test_file(m)}, {test_file(m)}, attempt=1, purpose="写测试")

    def _take_code(self, run: ModuleRun, conv, out, turn) -> None:
        run.c_conv, run.turn = conv, turn
        run.mr.files.update(out.files)
        self.flow._new_facts(out.facts)

    def _write_parallel(self, run: ModuleRun) -> None:
        from concurrent.futures import ThreadPoolExecutor
        header = self._interface(run)
        with ThreadPoolExecutor(max_workers=2) as ex:
            fc = ex.submit(self._first_code, run, run.coder_pin)
            ft = ex.submit(self._first_test, run, run.tester_pin, header)
            conv, out, turn = fc.result()
            t_conv, tout = ft.result()
        self._take_code(run, conv, out, turn)
        run.t_conv = t_conv
        run.mr.tests.update(tout.files)

    def _code(self, run: ModuleRun, s: Step) -> None:
        if run.c_conv is None:
            self._take_code(run, *self._first_code(run, s.agent))
        elif run.verdict == "fail":                        # 已经写过、上次没过：按报告改一轮（不再检查，交给后面的步骤）
            self._revise_code(run, coder_repair_message(run.attempt, self._max_attempts(run), run.report), s.agent)

    def _test(self, run: ModuleRun, s: Step) -> None:
        if run.t_conv is None:
            run.t_conv, tout = self._first_test(run, s.agent, self._interface(run))
            run.mr.tests.update(tout.files)

    def _revise_code(self, run: ModuleRun, msg: str, pin=None, purpose: str = "改代码") -> None:
        f, m = self.flow, run.m
        if run.c_conv is None:                             # 还没写过（检查器会拦，这里兜底）：当第一次写
            self._take_code(run, *self._first_code(run, pin if pin is not None else run.coder_pin))
            return
        coder, bucket = f._pick_coder(m, run.attempt + 1, pinned=pin if pin is not None else run.coder_pin)
        before = (f.result.prompt_tokens + f.result.completion_tokens, f.result.cost, time.time())
        run.c_conv, o = f._ask_files(coder, run.c_conv + [{"role": "user", "content": msg}], m.id, set(m.files), None,
                                     attempt=run.attempt + 1, purpose=purpose, tools=f.rule_mode != "none")
        run.turn = f._turn(m, coder, bucket, before)
        run.mr.files.update(o.files)
        f._new_facts(o.facts)

    # ---- 检查与修正

    def _verify(self, run: ModuleRun) -> None:
        f, m, mr = self.flow, run.m, run.mr
        run.attempt += 1
        mr.attempts = run.attempt
        run.verdict, run.layer, run.report = f._check(self.design, m, mr, run.rules, run.attempt)
        mr.last_report = run.report
        f._write_snapshot(m, mr, run.attempt, run.report)
        f._record(run.turn, run.attempt, run.verdict)
        run.turn = None
        if run.verdict == "pass":
            run.passed_files = dict(mr.files)
            if run.last is not None and run.last[2].get("blame") == "code":
                f._save_experience(m, *run.last)
                run.last = None
            # 补测试后重测算一次尝试，之后至少还要留一次修正的机会（第 131 条：以前在倒数第二次补测试，
            # 补的测试编译不过时已经没有机会再改）
            if not run.hardened and run.t_conv and run.attempt + 1 < self._max_attempts(run):
                run.hardened = True
                tests_before = dict(mr.tests)
                run.t_conv, changed = f._harden_tests(self.design, m, mr, run.rules, run.attempt, run.t_conv,
                                                      run.tester_pin or "tester")
                if changed:                                   # 用补强后的测试再测一次原来的代码（算一次尝试）
                    run.hardened_backup = dict(mr.files)
                    run.hardened_tests_backup = tests_before
                    self._verify(run)

    def _repair_loop(self, run: ModuleRun) -> None:
        """诊断 → 按责任交给编码或测试 Agent（硬件问题停下等人，规则问题先退回规则）→ 再检查，直到通过或次数用完。"""
        f, m, mr = self.flow, run.m, run.mr
        limit = self._max_attempts(run)
        while run.verdict == "fail" and run.attempt < limit and not run.stopped:
            diag = f._diagnose(m, mr, run.rules, run.layer, run.report, run.attempt)
            if diag["blame"] == "hardware":                    # 先实测接线，不靠模型判断（第 124 条）
                diag = f._recheck_hardware(m, mr, run.rules, run.layer, run.report, run.attempt, diag)
            run.last = (run.layer, run.report, diag)
            if diag["blame"] == "hardware":
                run.stopped = "hardware"
                f.bus.emit("human_wait", m.id, reason="诊断为硬件或接线问题，请检查板子和接线",
                           evidence=diag["evidence"], instruction=diag["instruction"])
                return
            if diag["blame"] == "rule":
                f.bus.emit("escalated", m.id, level="rule", rule=diag["rule"], note=diag["instruction"])
                run.rules = f._dispute_rule(run.rules, diag)
            if diag["blame"] == "test" and run.t_conv is None:      # 还没写过测试（兜底）：当第一次写
                run.t_conv, tout = self._first_test(run, run.tester_pin, self._interface(run))
                mr.tests.update(tout.files)
            elif diag["blame"] == "test":
                msg = tester_repair_message(run.attempt, limit, run.report, mr.files, diag)
                run.t_conv, tout = f._ask_files(run.tester_pin or "tester", run.t_conv + [{"role": "user", "content": msg}],
                                                m.id, {test_file(m)}, {test_file(m)}, attempt=run.attempt + 1, purpose="改测试")
                mr.tests.update(tout.files)
                self._enforce_removals(run, diag)
            elif diag["blame"] == "design":                    # 验收条件本身有问题：交回设计 Agent（第 134 条第 5 阶段）
                if not self._revise_acceptance(run, diag):
                    msg = tester_repair_message(run.attempt, limit, run.report, mr.files, diag)
                    run.t_conv, tout = f._ask_files(run.tester_pin or "tester",
                                                    (run.t_conv or []) + [{"role": "user", "content": msg}], m.id,
                                                    {test_file(m)}, {test_file(m)}, attempt=run.attempt + 1, purpose="改测试")
                    mr.tests.update(tout.files)
            else:
                self._revise_code(run, coder_repair_message(run.attempt, limit, run.report, diag))
            self._verify(run)

    # ---- 交接核对、退回设计（第 134 条第 5 阶段）

    def _enforce_removals(self, run: ModuleRun, diag: dict) -> None:
        """诊断说要删的用例，核对测试 Agent 真的删了；没删就带着原指令再要一次，还没删就记下来（检查结果会说话）。"""
        want = diag.get("remove_cases") or []
        if not want:
            return
        f, m, mr = self.flow, run.m, run.mr

        def left() -> list[str]:
            names = set(re.findall(r'TEST_CASE\(\s*"((?:[^"\\]|\\.)*)"', mr.tests.get(test_file(m), "")))
            return [w for w in want if w in names]
        still = left()
        if still:
            msg = ("诊断要求删除下面这些用例，你的测试文件里还在（加标签、注释掉一半都不算删）：\n"
                   + "\n".join(f"- {w}" for w in still) + "\n\n请删掉整个 TEST_CASE，输出修改后的完整测试文件。")
            run.t_conv, tout = f._ask_files(run.tester_pin or "tester", run.t_conv + [{"role": "user", "content": msg}],
                                            m.id, {test_file(m)}, {test_file(m)}, attempt=run.attempt + 1,
                                            purpose="改测试（照诊断删用例）")
            mr.tests.update(tout.files)
            still = left()
        f.bus.emit("handoff_checked", m.id, attempt=run.attempt, required=list(want), left=still, ok=not still)

    def _revise_acceptance(self, run: ModuleRun, diag: dict) -> bool:
        """设计 Agent 改这个模块的验收条件（只改验收条件，接口和接线不动），改好后测试按新条件从头写。"""
        import json as _json
        from dataclasses import replace as _replace
        from .design import manual_acceptance
        f, m, mr = self.flow, run.m, run.mr
        ask = ("诊断认为模块 " + m.id + " 的验收条件本身有问题，测试和代码照着它写怎么改都过不了。\n\n"
               f"诊断的依据：{diag.get('evidence', '')}\n诊断的建议：{diag.get('instruction', '')}\n\n"
               "现在的模块：\n```json\n" + _json.dumps(m.to_dict(), ensure_ascii=False, indent=2) + "\n```\n\n"
               "这块板子和这个项目的接线（状态由平台实测给出）：\n" + "\n".join(f.profile.summary_lines()) + "\n\n"
               "只改这个模块的验收条件：每条都要能在这块板上自动测出来，读数只能落在已经实测确认的线上。"
               '只输出一个 ```json 代码块：{"acceptance": ["……", "……"]}')
        conv = [{"role": "system", "content": "你是嵌入式系统的设计工程师，负责把需求拆成可以自动验收的模块。"},
                {"role": "user", "content": f"需求：\n{f.spec}\n\n{ask}"}]
        for k in range(2):
            resp = f.agents.call("designer", conv, m.id, attempt=run.attempt, purpose="改验收条件")
            try:
                acc = extract_json(resp.text).get("acceptance")
                if not isinstance(acc, list) or not acc or not all(isinstance(a, str) and a.strip() for a in acc):
                    raise ValueError("acceptance 要写成非空的字符串列表")
                new_m = _replace(m, acceptance=tuple(a.strip() for a in acc))
                probs = manual_acceptance([new_m])
                if probs:
                    raise ValueError("；".join(probs))
            except (ValueError, AttributeError) as exc:
                conv = conv + [{"role": "assistant", "content": resp.text or ""},
                               {"role": "user", "content": f"没法用：{exc}。请重新输出。"}]
                continue
            run.m = new_m
            if getattr(self, "design", None) is not None:
                i = next(j for j, x in enumerate(self.design.modules) if x.id == m.id)
                self.design.modules[i] = new_m
                self.design.by_id[m.id] = new_m
            f.bus.emit("design_revised", m.id, attempt=run.attempt, old=list(m.acceptance), new=list(new_m.acceptance),
                       reason=diag.get("instruction", ""))
            run.t_conv, tout = self._first_test(run, run.tester_pin, self._interface(run))   # 按新条件从头写测试
            mr.tests.clear()
            mr.tests.update(tout.files)
            return True
        return False

    # ---- 审查、等人

    def _review(self, run: ModuleRun, s: Step) -> None:
        """检查通过后审一遍；needs_fix 时让编码 Agent 改 1 次再检查，改坏了退回通过的版本（审查不能否决通过）。"""
        if run.verdict != "pass" or run.review is not None:
            return
        f, m, mr = self.flow, run.m, run.mr
        msgs = reviewer_messages(f.spec, f.profile, run.rules, m, mr.files, run.report)
        resp = f.agents.call(s.agent or "reviewer", msgs, m.id, attempt=run.attempt, purpose="审查")
        try:
            run.review = parse_review(extract_json(resp.text))
        except ValueError as exc:
            run.review = {"verdict": "ok", "issues": [], "error": str(exc)}
        high = [i for i in run.review["issues"] if i.get("severity") == "high"]
        f.bus.emit("review", m.id, attempt=run.attempt, verdict=run.review["verdict"], issues=run.review["issues"][:10],
                   error=run.review.get("error", ""))
        if run.review["verdict"] != "needs_fix" or not high:
            return
        keep = dict(mr.files)
        self._revise_code(run, review_fix_message(high), purpose="按审查意见改")
        self._verify(run)
        if run.verdict != "pass":                          # 改坏了：退回审查前通过的版本
            mr.files.clear()
            mr.files.update(keep)
            run.verdict, run.passed_files = "pass", keep
            f.bus.emit("review_reverted", m.id, attempt=run.attempt, note="按审查意见改了以后没通过检查，退回改之前的版本")

    def _human(self, run: ModuleRun, s: Step) -> None:
        """看结果确认：写意见就交给编码 Agent 改并重新检查；没写就继续。没有人在场（命令行）时跳过。"""
        f, m, mr = self.flow, run.m, run.mr
        ask = getattr(f, "confirm_module", None)
        if ask is None or run.verdict is None:
            return
        f.bus.emit("human_wait", m.id, step=s.id, reason="模块检查完了，等你看结果", verdict=run.verdict)
        comment = (ask(m, mr, run.verdict) or "").strip()
        if not comment:
            return
        was_pass, keep = run.verdict == "pass", dict(mr.files)
        self._revise_code(run, human_fix_message(comment), purpose="按你的意见改")
        self._verify(run)
        if was_pass and run.verdict != "pass":             # 按意见改坏了一个已经通过的模块：退回改之前的版本
            mr.files.clear()
            mr.files.update(keep)
            run.verdict, run.passed_files = "pass", keep
            f.bus.emit("human_reverted", m.id, attempt=run.attempt,
                       note="按你的意见改了以后没通过检查，退回改之前通过的版本")

    @staticmethod
    def _hardened_tests_at_fault(run: ModuleRun) -> bool:
        """补强以后没通过，是不是补的测试的错：编译错误全在测试文件里，或最近一次诊断判为测试的错。"""
        if run.layer == "build":
            errs = [ln for ln in run.report.splitlines() if re.match(r"^\S+:\d+: (fatal )?error:", ln)]
            return bool(errs) and all(ln.startswith(("main/test/", "main/test_host/", "test/", "test_host/"))
                                      for ln in errs)
        return bool(run.last and run.last[2].get("blame") == "test")

    def _finish_module(self, run: ModuleRun) -> None:
        f, m, mr = self.flow, run.m, run.mr
        if run.stopped == "hardware":
            mr.verdict = "hardware"
            return
        mr.verdict = run.verdict if run.verdict in ("pass", "skipped") else "fail"
        if mr.verdict == "fail" and run.hardened_backup is not None and self._hardened_tests_at_fault(run):
            # 代码在补强前已经通过；补的测试本身有错（编译不过，或诊断判为测试的错）又没改好：
            # 退回补强前的测试和代码，按补强前的检查结果算通过，并写明（第 131 条）
            mr.files.clear()
            mr.files.update(run.hardened_backup)
            mr.tests.clear()
            mr.tests.update(run.hardened_tests_backup or {})
            mr.verdict = "pass"
            why = "补的测试编译不过" if run.layer == "build" else "诊断判为补的测试有错"
            mr.last_report += f"\n\n补强测试失败（{why}），已退回补强前的测试和代码；按补强前的检查结果算通过。"
            f.bus.emit("hardened_reverted", m.id, kept="pass",
                       note=f"补强测试失败（{why}），退回补强前的测试和代码，按补强前的结果算通过")
        elif mr.verdict == "fail":
            f._restore_before_hardening(m, mr, run.hardened_backup)
        if mr.verdict == "pass" and run.passed_files and mr.files != run.passed_files:
            mr.files.clear()
            mr.files.update(run.passed_files)
        f.bus.emit("node_done" if mr.verdict == "pass" else "node_failed", m.id, verdict=mr.verdict,
                   attempts=run.attempt, note=mr.last_report[-300:])

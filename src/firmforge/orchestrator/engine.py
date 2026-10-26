"""多 Agent 执行引擎（ARCHITECTURE §6）。

- 用 asyncio 实现。LLM 调用和判定都是阻塞调用，放到后台线程执行，用两个信号量分别限制并发数；
- 按工作流模板（workflows/module_dev.toml）解释执行七种节点；
- 模块并行与隔离：每个模块的每次尝试都在 nodes/<module>/attempt_<n>/ 里，
  目录内容 = 已完成依赖模块的实现 + 本模块的实现和测试；模块通过后复制进 integration/；
- 全部模块完成后对 integration/ 做集成判定（全部 Agent 测试）；
- 人工节点：开启 human_review 时暂停并发 human_wait 事件；评测模式下不开启；
- 升级阶梯、经验库、知识库（ARCHITECTURE §8、§9）按配置开关启用，用于消融实验。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..agents.protocol import parse_files, write_files
from ..agents.registry import AgentDef, Registry, default_registry
from ..agents import roles
from ..config import ModelTier, Settings, Toolchain
from ..llm import CallLogger, LLMClient, LLMError, LLMResponse, LoggedLLM
from ..memory.experience import ExperienceBase
from ..memory.knowledge import KnowledgeBase
from ..runs import RunDir
from ..store import Store
from ..tasks import Task
from ..tools import ToolContext, ToolPipeline, ToolRegistry, default_registry as default_tools
from ..tools.loop import call_with_tools, followup
from ..tools.pipeline import Approve
from ..verifier import verify
from ..verifier.report import PASS, Report, attempt_score
from .baseline import RunInterrupted
from .dag import Module, Plan, PlanError, extract_json, plan_from_text, single_module_plan, MAX_MODULES
from .events import EventBus
from .loop import ModuleAttempt, ModuleState, failure_points, repair_round
from .scheduler import Strategy, make_strategy, prioritize
from .workflow import Step, Workflow, load_workflow

PLAN_RETRIES = 2              # 规划不合格时最多重做 2 次（ARCHITECTURE §2.7）
HUMAN_ROUNDS = 3              # 人工最多否决 2 次，第 3 次仍不确认就结束运行
DEFAULT_MAX_TOKENS = 8192     # 档位和 Agent 都没配置 max_tokens 时使用


@dataclass(frozen=True)
class MultiConfig:
    name: str
    strategy: str                       # serial | round_robin | random | smart
    review: bool = False
    fixed: dict[str, str] = field(default_factory=dict)   # serial 下各角色固定用的 Agent
    escalation: bool = False            # 升级阶梯
    experience: bool = False            # 经验库（检索与写入）
    knowledge: bool = False             # 知识库（规划时参考；升级第 1 级检索）
    workflow: str = "module_dev"
    tools: bool = True                  # 内置工具（DECISIONS 第 63 条）；full-no-tools 关闭
    kind: str = "multi"


B3 = MultiConfig("b3", "serial", review=False, fixed={"coder": "coder-strong"})
B4 = MultiConfig("b4", "round_robin", review=False)
FULL = MultiConfig("full", "smart", review=True, escalation=True, experience=True, knowledge=True)
FULL_NO_EXP = MultiConfig("full-no-exp", "smart", review=True, escalation=True, experience=False, knowledge=True)
FULL_NO_ESC = MultiConfig("full-no-esc", "smart", review=True, escalation=False, experience=True, knowledge=True)
FULL_NO_REVIEW = MultiConfig("full-no-review", "smart", review=False, escalation=True, experience=True, knowledge=True)
FULL_NO_TOOLS = MultiConfig("full-no-tools", "smart", review=True, escalation=True, experience=True, knowledge=True,
                            tools=False)
MULTI = {c.name: c for c in (B3, B4, FULL, FULL_NO_EXP, FULL_NO_ESC, FULL_NO_REVIEW, FULL_NO_TOOLS)}


@dataclass
class MultiRunResult:
    run_id: str
    run_dir: Path
    task_id: str
    config: str
    verdict: str                     # pass | fail | incomplete | error | interrupted | rejected
    plan: Plan | None = None
    modules: dict[str, ModuleState] = field(default_factory=dict)
    replaced: list[ModuleState] = field(default_factory=list)     # 被重新规划替换掉的模块
    final_report: Report | None = None
    escalations: int = 0
    experiences_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost: float = 0.0
    wall_s: float = 0.0
    error: str | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def final_dir(self) -> Path:
        return self.run_dir / "final"

    @property
    def first_dir(self) -> Path:
        """每个模块第一次尝试合在一起（评测算首次通过率用）。"""
        return self.run_dir / "first"

    @property
    def attempt_count(self) -> int:
        return sum(len(s.attempts) for s in [*self.modules.values(), *self.replaced])

    @property
    def has_code(self) -> bool:
        return any(s.attempts for s in self.modules.values())


class _Interrupt(Exception):
    """后台线程里收到了 KeyboardInterrupt。"""


class _Rejected(Exception):
    """负责人没有确认规划。"""


async def in_thread(fn: Callable, *args, **kwargs):
    """在守护线程里执行阻塞调用。用守护线程而不是线程池：按 Ctrl+C 时不必等正在进行的 LLM 请求返回。"""
    loop = asyncio.get_running_loop()
    fut = loop.create_future()

    def settle(ok: bool, value) -> None:
        if not fut.done():
            fut.set_result(value) if ok else fut.set_exception(value)

    def work() -> None:
        try:
            outcome = (True, fn(*args, **kwargs))
        except KeyboardInterrupt:
            outcome = (False, _Interrupt())
        except BaseException as exc:          # noqa: BLE001 - 原样交回事件循环
            outcome = (False, exc)
        try:
            loop.call_soon_threadsafe(settle, *outcome)
        except RuntimeError:                  # 事件循环已经关闭（运行被中断）
            pass

    threading.Thread(target=work, daemon=True).start()
    return await fut


async def gather_all(*coros):
    """并发执行；任何一个出错就取消其余的，再把错误抛出。"""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class _ParseFailure(Exception):
    def __init__(self, retry: str, detail: str):
        super().__init__(detail)
        self.retry, self.detail = retry, detail


HumanCallback = Callable[[Plan], tuple[bool, str]]
HintCallback = Callable[[str, list[str]], str | None]     # (模块 id, 失败要点) -> 提示；None 表示放弃
MAX_REPLANS = 1               # 每次运行最多重新规划 1 次


class Engine:
    def __init__(self, task: Task, cfg: MultiConfig, *, llm: LLMClient, settings: Settings, runs_root: Path | str,
                 registry: Registry | None = None, store: Store | None = None,
                 subscribers: tuple[Callable[[dict], None], ...] = (), toolchain: Toolchain | None = None,
                 secrets: tuple[str, ...] = (), human_review: bool = False, human: HumanCallback | None = None,
                 hint: HintCallback | None = None, workflow: Workflow | None = None, memory: Store | None = None,
                 knowledge: KnowledgeBase | None = None, tools: ToolRegistry | None = None,
                 approve: Approve | None = None, tier_override: str | None = None, thinking: str | None = None):
        self.task, self.cfg, self.llm, self.settings = task, cfg, llm, settings
        self.registry = registry or default_registry()
        self.store, self.toolchain = store, toolchain
        self.human_review, self.human, self.hint = human_review, human, hint
        self.kb = (knowledge or KnowledgeBase.load()) if cfg.knowledge else None
        mem = memory or store
        self.exp = ExperienceBase(mem) if (cfg.experience and mem is not None) else None
        self.replans = 0
        self.toolbox = (tools or default_tools()) if cfg.tools else None
        if self.toolbox is not None:
            self.registry.check_toolsets(set(self.toolbox.toolsets()) | {t.name for t in self.toolbox.all()})
        self.approve = approve
        self.tier_override, self.thinking = tier_override, thinking     # 这一次运行的覆盖（对话框下方的选择）
        self.workflow = workflow or load_workflow(cfg.workflow)
        self.wf_label = f"工作流 {self.workflow.name} {self.workflow.version}"     # 任务单的「当前位置」
        self.loop_cfg = dict(settings.loop)
        conc = settings.concurrency
        self.llm_limit = int(conc.get("llm", 4))
        self.verify_limit = int(conc.get("verify", 4))
        self.max_modules = MAX_MODULES
        self.strategy: Strategy = make_strategy(cfg.strategy, scheduler_cfg=settings.scheduler,
                                                concurrency=self.llm_limit, list_price=self._list_price,
                                                fixed=cfg.fixed)
        self.rd = RunDir.create(runs_root, task.id)
        self.bus = EventBus(self.rd.run_id, self.rd.events_path)
        for fn in subscribers:
            self.bus.subscribe(fn)
        self.logger = CallLogger(self.rd.path, list(secrets))
        self.result = MultiRunResult(self.rd.run_id, self.rd.path, task.id, cfg.name, "fail")
        self.load: dict[str, int] = {}
        self.usage: dict[tuple[str, str], dict] = {}     # (模块, Agent) -> token / 耗时 / 成本
        self.states: dict[str, ModuleState] = {}
        self.plan: Plan | None = None
        self.integration_dir = self.rd.path / "integration"

    # ------------------------------------------------------------ 公共入口

    def run(self) -> MultiRunResult:
        t0 = time.perf_counter()
        info = {"run_id": self.rd.run_id, "task": self.task.id, "task_root": str(self.task.root),
                "spec": str(self.task.spec_path), "config": self.cfg.name, "strategy": self.cfg.strategy,
                "review": self.cfg.review, "escalation": self.cfg.escalation, "experience": self.exp is not None,
                "knowledge": self.kb is not None, "workflow": f"{self.workflow.name} {self.workflow.version}",
                "tools": sorted(t.name for t in self.toolbox.all()) if self.toolbox else [],
                "tier_override": self.tier_override, "thinking": self.thinking,
                "human_review": self.human_review,
                "agents": {n: {"version": self.registry.get(n).version, "model_tier": self.registry.get(n).model_tier,
                               "model": self._tier(self.registry.get(n)).name} for n in self.registry.names()},
                "loop": self.loop_cfg, "concurrency": {"llm": self.llm_limit, "verify": self.verify_limit},
                "scheduler": dict(self.settings.scheduler), "started_at": time.time()}
        self.rd.write_json("run.json", info)
        if self.store:
            self.store.start_run(self.rd.run_id, self.task.id, self.cfg.name)
        self.bus.emit("run_started", task=self.task.id, config=self.cfg.name, strategy=self.cfg.strategy,
                      model="、".join(sorted({v["model"] for v in info["agents"].values()})))
        interrupted = False
        try:
            interrupted = asyncio.run(self._main())
        except KeyboardInterrupt:
            interrupted = True
        if interrupted:
            self.result.verdict = "interrupted"
        self._finalize(interrupted)
        r = self.result
        r.wall_s = time.perf_counter() - t0
        self.bus.emit("run_finished", verdict=r.verdict, attempts=r.attempt_count, modules=len(self.states),
                      tokens=r.tokens, cost=round(r.cost, 6), wall_s=round(r.wall_s, 2))
        info.update({"finished_at": time.time(), "verdict": r.verdict, "attempts": r.attempt_count,
                     "modules": {m: {"status": s.status, "level": s.level, "coder": s.coder.name if s.coder else None,
                                     "tester": s.tester.name if s.tester else None, "attempts": len(s.attempts),
                                     "delivered": s.delivered().n if s.delivered() else None, "notes": s.notes}
                                 for m, s in self.states.items()},
                     "fallback_plan": bool(self.plan and self.plan.fallback), "replans": self.replans,
                     "replaced_modules": [st.module.id for st in r.replaced],
                     "escalations": r.escalations, "experiences_used": r.experiences_used,
                     "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                     "reasoning_tokens": r.reasoning_tokens, "total_tokens": r.tokens, "cost": r.cost,
                     "wall_s": r.wall_s, "error": r.error})
        self.rd.write_json("run.json", info)
        if self.store:
            self.store.finish_run(self.rd.run_id, r.verdict, r.tokens, r.cost, r.wall_s)
        if interrupted:
            raise RunInterrupted(r)
        return r

    async def _main(self) -> bool:
        """返回 True 表示被中断。"""
        self.sem_llm = asyncio.Semaphore(self.llm_limit)
        self.sem_verify = asyncio.Semaphore(self.verify_limit)
        try:
            for step in Workflow.top_level(self.workflow.main):
                await self._run_main_step(step)
        except (_Interrupt, asyncio.CancelledError):
            return True
        except LLMError as exc:
            self.result.verdict, self.result.error = "error", str(exc)
        except _Rejected as exc:
            self.result.verdict, self.result.error = "rejected", str(exc)
        return False

    # ------------------------------------------------------------ 主流程节点

    async def _run_main_step(self, step: Step) -> None:
        if step.when and not self._cond(step.when, None):
            return
        if step.kind == "agent" and step.role == "planner":
            await self.make_plan()
        elif step.kind == "human":
            if step.enabled_by and not self._cond(step.enabled_by, None):
                return
            await self.confirm_plan()
        elif step.kind == "foreach":
            await self.run_modules(step.subflow)
        elif step.kind == "verify" and step.scope == "all":
            await self.integrate()
        else:
            raise ValueError(f"主流程不支持步骤 {step.id}（{step.kind}）")

    def _cond(self, name: str, state: ModuleState | None) -> bool:
        if name == "human_review":
            return self.human_review
        if state is None:
            return False
        if name == "verify_passed":
            return state.passed
        if name == "signature_repeated":
            sigs = state.signatures()
            return len(sigs) >= 2 and sigs[-1] is not None and sigs[-1] == sigs[-2]
        if name == "attempts_exhausted":
            return len(state.attempts) >= int(self.loop_cfg.get("max_attempts_per_module", 6))
        if name == "review_needs_fix":
            return bool(state.review and state.review.get("verdict") == "needs_fix")
        raise ValueError(f"未知条件 {name}")

    async def make_plan(self, feedback: str | None = None, previous: Plan | None = None) -> Plan:
        agent, scores = self._assign("plan", "planner", "*")
        self.bus.emit("node_assigned", "plan", agent=agent.name, role="planner", scores=scores)
        kb = self.kb.search(self._spec_text()[:2000], k=3) if (self.kb and agent.memory_knowledge) else []
        msgs = roles.planner_messages(self.task, self.max_modules, agent.prompt_name, knowledge=kb, where=self.wf_label)
        if feedback and previous is not None:
            msgs = msgs + [{"role": "assistant", "content": f"```json\n{previous.to_json()}\n```"},
                           {"role": "user", "content": roles.planner_feedback_message(feedback)}]
        problems: list[str] = []
        plan: Plan | None = None
        for k in range(PLAN_RETRIES + 1):
            resp = await self.call(agent, msgs, "plan", attempt=k + 1)
            try:
                plan = plan_from_text(resp.text, self.task, self.max_modules)
            except PlanError as exc:
                problems = exc.problems
                self._finished(agent, "plan", k + 1, resp, ok=True, valid=False, problems=problems)
                msgs = followup(msgs, resp) + [{"role": "user", "content": roles.planner_retry_message(problems)}]
                continue
            self._finished(agent, "plan", k + 1, resp, ok=True, valid=True, modules=[m.id for m in plan.modules])
            break
        if plan is None:
            plan = single_module_plan(self.task)
        self.plan = self.result.plan = plan
        self.rd.write_json("dag.json", plan.to_dict())
        self.bus.emit("plan_ready", "plan", modules=[m.to_dict() for m in plan.modules],
                      critical_path=plan.critical_path(), fallback=plan.fallback,
                      problems=problems if plan.fallback else [])
        return plan

    async def confirm_plan(self) -> None:
        for rnd in range(1, HUMAN_ROUNDS + 1):
            self.bus.emit("human_wait", "confirm_plan", round=rnd, modules=[m.id for m in self.plan.modules])
            if self.human is None:
                ok, comment = True, ""
            else:
                ok, comment = await in_thread(self.human, self.plan)
            self.bus.emit("human_done", "confirm_plan", round=rnd, approved=ok, comment=comment)
            if ok:
                return
            if rnd == HUMAN_ROUNDS:
                raise _Rejected(f"负责人 {HUMAN_ROUNDS} 次都没有确认规划")
            await self.make_plan(feedback=comment or "请重新规划", previous=self.plan)

    # ------------------------------------------------------------ 模块调度（foreach）

    async def run_modules(self, subflow: str) -> None:
        self.states = {m.id: ModuleState(m) for m in self.plan.modules}
        self.result.modules = self.states
        self.integration_dir.mkdir(parents=True, exist_ok=True)
        steps = self.workflow.subflows[subflow]
        running: dict[str, asyncio.Task] = {}
        try:
            while True:
                for mid, st in self.states.items():           # 依赖失败的模块不再执行
                    if st.status == "pending" and any(self.states[d].status in ("failed", "skipped")
                                                      for d in st.module.depends_on):
                        st.status = "skipped"
                        bad = [d for d in st.module.depends_on if self.states[d].status in ("failed", "skipped")]
                        self.bus.emit("node_failed", mid, verdict="skipped", attempts=0,
                                      reason=f"依赖的模块 {', '.join(bad)} 没有完成")
                ready = [mid for mid, st in self.states.items() if st.status == "pending"
                         and all(self.states[d].status == "done" for d in st.module.depends_on)]
                for mid in prioritize(ready, self.plan):          # 重新规划后 self.plan 会变
                    if not self.strategy.parallel and running:
                        break
                    st = self.states[mid]
                    st.status = "running"
                    self.bus.emit("node_ready", mid, title=st.module.title, depends_on=list(st.module.depends_on))
                    running[mid] = asyncio.ensure_future(self.run_module(st, steps))
                if not running:
                    break
                done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                for mid in [m for m, t in running.items() if t in done]:
                    running.pop(mid).result()                 # 有异常就抛出
        finally:
            for t in running.values():
                t.cancel()
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)

    async def run_module(self, st: ModuleState, steps: tuple[Step, ...]) -> None:
        by_id = Workflow.by_id(steps)
        for step in Workflow.top_level(steps):
            await self._run_sub_step(step, st, by_id)
        mid = st.module.id
        delivered = st.delivered()
        if st.stop == "replanned":
            st.status = "replanned"
            self.bus.emit("node_failed", mid, verdict="replanned", attempts=len(st.attempts),
                          reason="已重新规划，由新的模块接替")
            self._record_stats(st)
            return
        st.status = "done" if st.passed else "failed"
        if st.status == "done" and self.exp is not None and st.exp_used:
            self.exp.mark_helped(st.exp_used)
        if st.status == "done":
            self._copy_module(delivered, self.integration_dir, with_tests=True)
            self.bus.emit("node_done", mid, attempt=delivered.n, verdict=delivered.report.verdict,
                          attempts=len(st.attempts))
        else:
            self.bus.emit("node_failed", mid, verdict="fail", attempts=len(st.attempts),
                          best=delivered.n if delivered else None,
                          failed_layer=delivered.report.failed_layer if delivered else None)
        self._record_stats(st)

    async def _run_sub_step(self, step: Step, st: ModuleState, by_id: dict[str, Step]) -> None:
        if step.when and not self._cond(step.when, st):
            return
        if step.kind == "parallel":
            branches = [by_id[b] for b in step.branches]
            if self.strategy.parallel:
                await gather_all(*(self._run_sub_step(b, st, by_id) for b in branches))
            else:
                for b in branches:
                    await self._run_sub_step(b, st, by_id)
        elif step.kind == "condition":
            for sid in (step.then if self._cond(step.cond, st) else step.otherwise):
                await self._run_sub_step(by_id[sid], st, by_id)
        elif step.kind == "agent" and step.role == "coder":
            await self.write_code(st)
        elif step.kind == "agent" and step.role == "tester":
            await self.write_test(st)
        elif step.kind == "agent" and step.role == "reviewer":
            if self.cfg.review:
                await self.review(st)
        elif step.kind == "verify" and step.scope == "module":
            await self.check(st, by="coder+tester")
        elif step.kind == "loop" and step.body == "repair_round":
            limit = self.workflow.resolve_max(step, self.loop_cfg)
            while not self._cond(step.until, st) and len(st.attempts) < limit and st.stop is None:
                await repair_round(self, st)
        elif step.kind == "subflow":
            sub = self.workflow.subflows[step.subflow]
            sub_ids = Workflow.by_id(sub)
            for s in Workflow.top_level(sub):
                await self._run_sub_step(s, st, sub_ids)
        else:
            raise ValueError(f"子流程不支持步骤 {step.id}（{step.kind}）")

    # ------------------------------------------------------------ Agent 调用

    def _tier(self, agent: AgentDef) -> ModelTier:
        return self.settings.tier(self.tier_override or agent.model_tier)

    def _effort(self, agent: AgentDef) -> str | None:
        """思考强度：这次运行的覆盖 > Agent 自己的设置 > 档位默认。"""
        return self.thinking or agent.thinking

    def _tool_pipeline(self, agent: AgentDef, node_id: str) -> tuple[list, ToolPipeline] | None:
        if self.toolbox is None or not agent.toolsets:
            return None
        allowed = self.toolbox.for_agent(agent.toolsets)
        if not allowed:
            return None
        st = self.states.get(node_id)
        module = st.module if st else None
        ctx = ToolContext(task=self.task, module=module, plan=self.plan,
                          dep_files=self._dep_files(module) if (module and self.plan) else {},
                          toolchain=self.toolchain, kb=self.kb, agent=agent.name,
                          scratch_dir=self.rd.path / "tool_scratch" / node_id)
        pipe = ToolPipeline(self.toolbox, allowed=allowed, ctx=ctx, emit=self.bus.emit, node_id=node_id,
                            max_chars=int(self.settings.tools.get("max_chars", 8000)), approve=self.approve,
                            output_dir=self.rd.path / "tool_outputs")
        return allowed, pipe

    def _list_price(self, agent: AgentDef) -> float:
        t = self.settings.models.get(agent.model_tier)
        return (t.price_in_per_mtok + t.price_out_per_mtok) if t else 1.0

    def _assign(self, node_id: str, role: str, task_type: str, exclude: tuple[str, ...] = ()) -> tuple[AgentDef, dict]:
        candidates = [a for a in self.registry.find(role, task_type) if a.name not in exclude]
        if not candidates:
            raise ValueError(f"没有注册角色为 {role} 的 Agent")
        stats = self.store.agent_stats(task_type) if (self.store and role == "coder") else {}
        return self.strategy.choose(node_id, candidates, stats, dict(self.load))

    async def call(self, agent: AgentDef, messages: list[dict], node_id: str, *, attempt: int,
                   purpose: str = "") -> LLMResponse:
        tier = self._tier(agent)
        client = LoggedLLM(self.llm, self.logger, agent.name)
        self.bus.emit("agent_started", node_id, agent=agent.name, role=agent.role, attempt=attempt, purpose=purpose)
        self.load[agent.name] = self.load.get(agent.name, 0) + 1
        params = dict(model=tier.name, temperature=agent.temperature,
                      max_tokens=agent.max_tokens or tier.max_tokens or DEFAULT_MAX_TOKENS,
                      extra_body=tier.extra_body(self._effort(agent)))
        tooling = self._tool_pipeline(agent, node_id)
        try:
            async with self.sem_llm:
                if tooling is None:
                    resp = await in_thread(client.complete, messages, **params)
                else:
                    allowed, pipe = tooling
                    resp = await in_thread(call_with_tools, lambda m, tools: client.complete(m, tools=tools, **params),
                                           messages, allowed, pipe,
                                           max_rounds=int(self.settings.tools.get("max_rounds", 5)))
        except LLMError as exc:
            self.bus.emit("agent_finished", node_id, agent=agent.name, role=agent.role, attempt=attempt,
                          ok=False, error=str(exc))
            raise
        finally:
            self.load[agent.name] -= 1
        cost = tier.cost(resp.prompt_tokens, resp.completion_tokens, ts=time.time(), schedule=self.settings.pricing)
        r = self.result
        r.prompt_tokens += resp.prompt_tokens
        r.completion_tokens += resp.completion_tokens
        r.reasoning_tokens += resp.reasoning_tokens
        r.cost += cost
        u = self.usage.setdefault((node_id, agent.name), {"tokens": 0, "latency_s": 0.0, "cost": 0.0})
        u["tokens"] += resp.prompt_tokens + resp.completion_tokens
        u["latency_s"] += resp.latency_s
        u["cost"] += cost
        return resp

    def _finished(self, agent: AgentDef, node_id: str, attempt: int, resp: LLMResponse, **data) -> None:
        self.bus.emit("agent_finished", node_id, agent=agent.name, role=agent.role, attempt=attempt,
                      tokens=resp.prompt_tokens + resp.completion_tokens, latency_s=round(resp.latency_s, 3), **data)

    async def ask(self, agent: AgentDef, messages: list[dict], node_id: str, *, attempt: int, purpose: str,
                  parse: Callable[[str], tuple[object, dict]], fallback: Callable[[], object] | None = None):
        """调用 Agent 并解析输出；解析失败时带着提示重试 1 次，仍失败就用 fallback（没有则抛出）。"""
        msgs = list(messages)
        for k in range(2):
            resp = await self.call(agent, msgs, node_id, attempt=attempt, purpose=purpose)
            try:
                value, info = parse(resp.text)
            except _ParseFailure as pf:
                self._finished(agent, node_id, attempt, resp, ok=True, parse_error=pf.detail)
                if k == 0:
                    msgs = followup(msgs, resp) + [{"role": "user", "content": pf.retry}]
                    continue
                if fallback is None:
                    raise
                return fallback()
            self._finished(agent, node_id, attempt, resp, ok=True, **info)
            return value
        raise AssertionError("不会到这里")

    # ------------------------------------------------------------ 编码与测试

    def _dep_files(self, module: Module) -> dict[str, str]:
        """已完成依赖模块交付的实现文件（不含它们的测试）。"""
        files: dict[str, str] = {}
        for d in self.plan.transitive_deps(module.id):
            att = self.states[d].delivered()
            if att is not None:
                files.update(att.code)
        return files

    def _code_parser(self, module: Module):
        def parse(text: str):
            pr = parse_files(text, roles.CODER_DIRS)
            files = {p: c for p, c in pr.files.items() if p in module.files}
            rejected = list(pr.rejected) + [(p, f"不属于本模块（本模块的文件：{', '.join(module.files)}）")
                                            for p in pr.files if p not in module.files]
            if not files:
                raise _ParseFailure(
                    "没有在你的回复里找到本模块的文件块。请严格按下面的格式输出本模块的每个完整文件"
                    f"（{', '.join(module.files)}）：\n\n<<<FILE {module.files[0]}\n…完整文件内容…\n>>>FILE",
                    "没有本模块的文件块")
            return (files, rejected), {"files": sorted(files), "rejected": [p for p, _ in rejected]}
        return parse

    def _test_parser(self, module: Module):
        path = f"tests/{module.test_file}"

        def parse(text: str):
            pr = parse_files(text, roles.TESTER_DIRS)
            files = {p: c for p, c in pr.files.items() if p == path}
            rejected = list(pr.rejected) + [(p, f"测试 Agent 只能写 {path}") for p in pr.files if p != path]
            if not files:
                raise _ParseFailure(f"没有在你的回复里找到测试文件块。请严格按下面的格式输出：\n\n"
                                    f"<<<FILE {path}\n…完整文件内容…\n>>>FILE", "没有测试文件块")
            return (files, rejected), {"files": sorted(files), "rejected": [p for p, _ in rejected]}
        return parse

    async def write_code(self, st: ModuleState) -> None:
        m = st.module
        st.coder, scores = self._assign(m.id, "coder", m.task_type)
        self.bus.emit("node_assigned", m.id, agent=st.coder.name, role="coder", scores=scores)
        exps = (self._experiences(st, f"{m.task_type} {m.title} {' '.join(m.acceptance)} {self.task.title}", "首次编码")
                if st.coder.memory_experience else [])
        msgs = roles.coder_messages(self.task, self.plan, m, self._dep_files(m), st.coder.prompt_name,
                                    knowledge=st.knowledge if st.coder.memory_knowledge else (), experiences=exps,
                                    where=self.wf_label)
        files, rejected = await self.ask(st.coder, msgs, m.id, attempt=1, purpose="编写实现",
                                         parse=self._code_parser(m), fallback=lambda: ({}, []))
        st.code.update(files)
        st.rejected = rejected

    async def write_test(self, st: ModuleState) -> None:
        m = st.module
        st.tester, scores = self._assign(m.id, "tester", m.task_type)
        self.bus.emit("node_assigned", m.id, agent=st.tester.name, role="tester", scores=scores)
        msgs = roles.tester_messages(self.task, self.plan, m, st.tester.prompt_name, where=self.wf_label)
        files, _ = await self.ask(st.tester, msgs, m.id, attempt=1, purpose="编写测试",
                                  parse=self._test_parser(m), fallback=lambda: ({}, []))
        st.tests.update(files)

    async def repair_code(self, st: ModuleState, base: ModuleAttempt, diagnosis: dict, n: int) -> None:
        m = st.module
        limit = int(self.loop_cfg.get("max_attempts_per_module", 6))
        keys = " ".join(base.report.layer(base.report.failed_layer).failing_keys()) if base.report.failed_layer else ""
        exps = (self._experiences(st, f"{base.report.failed_layer or ''} {keys}", "修正")
                if keys and st.coder.memory_experience else [])
        msgs = roles.coder_messages(self.task, self.plan, m, self._dep_files(m), st.coder.prompt_name,
                                    knowledge=st.knowledge if st.coder.memory_knowledge else (),
                                    where=self.wf_label, attempt=n)
        msgs.append({"role": "user", "content": roles.coder_repair_message(
            base.report, base.n, limit, diagnosis, base.code, st.rejected, base.dir, experiences=exps)})
        files, rejected = await self.ask(st.coder, msgs, m.id, attempt=n, purpose="按诊断修改实现",
                                         parse=self._code_parser(m), fallback=lambda: ({}, []))
        st.code.update(files)
        st.rejected = rejected

    async def repair_test(self, st: ModuleState, base: ModuleAttempt, diagnosis: dict, n: int) -> None:
        m = st.module
        limit = int(self.loop_cfg.get("max_attempts_per_module", 6))
        src_texts = {**self._dep_files(m), **base.code}
        msgs = roles.tester_messages(self.task, self.plan, m, st.tester.prompt_name, where=self.wf_label)
        msgs.append({"role": "user", "content": roles.tester_repair_message(
            base.report, base.n, limit, diagnosis, base.tests, src_texts, base.dir, self._public_header_text())})
        files, _ = await self.ask(st.tester, msgs, m.id, attempt=n, purpose="按诊断修改测试",
                                  parse=self._test_parser(m), fallback=lambda: ({}, []))
        st.tests.update(files)

    # ------------------------------------------------------------ 经验库、知识库与升级阶梯

    def _spec_text(self) -> str:
        return self.task.spec_path.read_text(encoding="utf-8")

    def _experiences(self, st: ModuleState, query: str, where: str) -> list:
        if self.exp is None:
            return []
        found = self.exp.search(query, k=3, category=st.module.task_type)
        if found:
            ids = [e.id for e in found]
            self.exp.mark_used(ids)
            st.exp_used.extend(ids)
            self.result.experiences_used += len(ids)
            self.bus.emit("experience_used", st.module.id, ids=ids, where=where,
                          patterns=[e.pattern for e in found])
        return found

    async def save_experience(self, st: ModuleState, base: ModuleAttempt, diagnosis: dict) -> None:
        """修正让失败层前进或直接通过：由诊断 Agent 总结一条经验（pattern 用判定器的失败 key）。"""
        if self.exp is None or not base.report.failed_layer:
            return
        layer = base.report.failed_layer
        pattern = " ".join(sorted(set(base.report.layer(layer).failing_keys())))[:300]
        if not pattern:
            return
        agent, _ = self._assign(st.module.id, "diagnoser", st.module.task_type)
        msgs = roles.experience_messages(self.task, st.module, pattern, base.report, diagnosis)

        def parse(text: str):
            try:
                fix = str(extract_json(text).get("fix", "")).strip()
            except PlanError as exc:
                raise _ParseFailure(roles.json_retry_message(str(exc)), str(exc)) from exc
            if not fix:
                raise _ParseFailure(roles.json_retry_message("fix 为空"), "fix 为空")
            return fix, {"purpose_result": "经验"}

        fix = await self.ask(agent, msgs, st.module.id, attempt=base.n, purpose="总结经验", parse=parse,
                             fallback=lambda: diagnosis.get("summary", ""))
        if not fix:
            return
        eid, merged = self.exp.save(st.module.task_type, layer, pattern, fix, self.rd.run_id)
        self.bus.emit("experience_saved", st.module.id, id=eid, merged=merged, layer=layer, pattern=pattern)

    def consult_knowledge(self, st: ModuleState, base: ModuleAttempt) -> list[str]:
        """升级第 1 级：按失败的 key 和模块说明检索知识库，之后的编码上下文都带上。返回条目标题。"""
        if self.kb is None:
            return []
        layer = base.report.failed_layer
        keys = " ".join(base.report.layer(layer).failing_keys()) if layer else ""
        st.knowledge = self.kb.search(f"{layer or ''} {keys} {st.module.title} {' '.join(st.module.acceptance)}", k=3)
        return [c.title for c in st.knowledge]

    async def rewrite(self, st: ModuleState, base: ModuleAttempt) -> bool:
        """升级第 2 级：换一个编码 Agent 从头重写，只给需求、接口和失败要点。没有可换的返回 False。"""
        m = st.module
        excluded = tuple({*st.excluded, *( [st.coder.name] if st.coder else [])})
        if not [a for a in self.registry.find("coder", m.task_type) if a.name not in excluded]:
            return False
        st.excluded = list(excluded)
        st.coder, scores = self._assign(m.id, "coder", m.task_type, exclude=excluded)
        scores = {**scores, "reason": f"升级换 Agent：{scores.get('reason', '')}"}
        self.bus.emit("node_assigned", m.id, agent=st.coder.name, role="coder", scores=scores)
        n = len(st.attempts) + 1
        msgs = roles.coder_messages(self.task, self.plan, m, self._dep_files(m), st.coder.prompt_name,
                                    knowledge=st.knowledge)
        msgs.append({"role": "user", "content": roles.coder_rewrite_message(failure_points(st),
                                                                            base.report.failed_layer)})
        files, rejected = await self.ask(st.coder, msgs, m.id, attempt=n, purpose="换 Agent 从头重写",
                                         parse=self._code_parser(m), fallback=lambda: ({}, []))
        st.code, st.tests = dict(files), dict(base.tests)
        st.rejected = rejected
        return True

    def can_replan(self) -> bool:
        return self.replans < MAX_REPLANS and self.plan is not None

    async def replan(self, st: ModuleState) -> bool:
        """升级第 3 级：回到规划层修改这个模块的接口或拆分方式。已完成或正在进行的其他模块必须原样保留。"""
        if not self.can_replan():
            return False
        self.replans += 1
        failing = st.module.id
        frozen = [mid for mid, s in self.states.items() if mid != failing and s.status in ("done", "running")]
        agent, scores = self._assign("plan", "planner", "*")
        self.bus.emit("node_assigned", "plan", agent=agent.name, role="planner", scores=scores)
        msgs = roles.planner_messages(self.task, self.max_modules, agent.prompt_name, where=self.wf_label)
        msgs += [{"role": "assistant", "content": f"```json\n{self.plan.to_json()}\n```"},
                 {"role": "user", "content": roles.planner_replan_message(failing, failure_points(st), frozen)}]
        for k in range(PLAN_RETRIES + 1):
            resp = await self.call(agent, msgs, "plan", attempt=k + 1, purpose="重新规划")
            try:
                plan = plan_from_text(resp.text, self.task, self.max_modules)
                problems = [f"模块 {mid} 已经完成或正在进行，必须原样保留" for mid in frozen
                            if mid not in plan.by_id or plan.by_id[mid].to_dict() != self.states[mid].module.to_dict()]
                if problems:
                    raise PlanError(problems)
            except PlanError as exc:
                self._finished(agent, "plan", k + 1, resp, ok=True, valid=False, problems=exc.problems)
                msgs = followup(msgs, resp) + [
                               {"role": "user", "content": roles.planner_retry_message(exc.problems)}]
                continue
            self._finished(agent, "plan", k + 1, resp, ok=True, valid=True, modules=[m.id for m in plan.modules])
            self._apply_replan(plan, st, frozen)
            return True
        return False

    def _apply_replan(self, plan: Plan, failing: ModuleState, frozen: list[str]) -> None:
        old = self.plan
        self.rd.write_json(f"dag_before_replan_{self.replans}.json", old.to_dict())
        new_states: dict[str, ModuleState] = {}
        for m in plan.modules:
            if m.id in frozen:
                new_states[m.id] = self.states[m.id]
            else:
                ns = ModuleState(m)
                ns.level = 3                   # 接替的模块再修不好就直接转人工
                new_states[m.id] = ns
        # 规划里删掉的、还没开始的模块直接丢弃；失败的那个模块留作记录
        self.result.replaced.append(failing)
        self.plan = self.result.plan = plan
        self.states = self.result.modules = new_states
        self.rd.write_json("dag.json", plan.to_dict())
        self.bus.emit("plan_ready", "plan", modules=[m.to_dict() for m in plan.modules],
                      critical_path=plan.critical_path(), fallback=False, problems=[], replan=True,
                      replaced=failing.module.id)

    async def ask_human(self, st: ModuleState) -> str | None:
        """升级第 4 级：转人工。没有回调（评测模式）就返回 None。"""
        if self.hint is None:
            return None
        points = failure_points(st)
        self.bus.emit("human_wait", st.module.id, reason="升级到转人工", points=points)
        hint = await in_thread(self.hint, st.module.id, points)
        self.bus.emit("human_done", st.module.id, approved=hint is not None, comment=hint or "")
        return hint

    def _public_header_text(self) -> str:
        return "\n".join(h.read_text(encoding="utf-8") for h in sorted(self.task.include_dir.glob("*.h")))

    # ------------------------------------------------------------ 判定

    async def _verify(self, workdir: Path, layers: list[str] | None = None) -> Report:
        async with self.sem_verify:
            report = await in_thread(verify, workdir, self.task, layers=layers, toolchain=self.toolchain)
        shutil.rmtree(workdir / ".ff_build", ignore_errors=True)
        (workdir / "report.json").write_text(report.for_agent().to_json(), encoding="utf-8")
        return report

    async def check(self, st: ModuleState, *, by: str) -> ModuleAttempt:
        """判定本模块：已完成依赖模块的实现 + 本模块的实现和测试。"""
        m = st.module
        n = len(st.attempts) + 1
        adir = self.rd.attempt_dir(m.id, n)
        adir.mkdir(parents=True, exist_ok=True)
        write_files(adir, {**self._dep_files(m), **st.code, **st.tests})
        (adir / "src").mkdir(exist_ok=True)
        (adir / "tests").mkdir(exist_ok=True)
        self.bus.emit("verify_started", m.id, attempt=n, scope="module")
        report = await self._verify(adir)
        att = ModuleAttempt(n, adir, report, attempt_score(report), dict(st.code), dict(st.tests), by)
        st.attempts.append(att)
        self.bus.emit("verify_finished", m.id, attempt=n, verdict=report.verdict, failed_layer=report.failed_layer,
                      signature=report.signature, score=round(att.score, 3), scope="module")
        if self.store:
            agent = st.coder.name if st.coder else ""
            self.store.add_attempt(self.rd.run_id, m.id, n, agent, report.verdict, report.failed_layer,
                                   report.signature, 0, 0.0)
        return att

    # ------------------------------------------------------------ 诊断与审查

    async def diagnose(self, st: ModuleState, base: ModuleAttempt) -> dict:
        m = st.module
        agent, _ = self._assign(m.id, "diagnoser", m.task_type)
        msgs = roles.diagnoser_messages(self.task, self.plan, m, base.code, base.tests, base.report,
                                        st.signatures(), base.dir, agent.prompt_name, where=self.wf_label)

        def parse(text: str):
            try:
                d = roles.parse_diagnosis(extract_json(text))
            except (PlanError, ValueError) as exc:
                raise _ParseFailure(roles.json_retry_message(str(exc)), str(exc)) from exc
            return d, {"blame": d["blame"]}

        d = await self.ask(agent, msgs, m.id, attempt=base.n, purpose="诊断", parse=parse,
                           fallback=lambda: {"blame": "code", "summary": "诊断输出无法解析，按判定报告直接修改",
                                             "instructions": "", "confidence": 0.0, "unparsed": True})
        st.diagnoses.append({"attempt": base.n, **d})
        (base.dir / "diagnosis.json").write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return d

    async def review(self, st: ModuleState) -> None:
        """客观层全过之后审查；high 级 needs_fix 最多触发 1 次额外编码轮，修改后没通过就退回。"""
        m = st.module
        passed = st.delivered()
        agent, _ = self._assign(m.id, "reviewer", m.task_type)
        msgs = roles.reviewer_messages(self.task, m, passed.code, passed.report, agent.prompt_name,
                                      where=self.wf_label)

        def parse(text: str):
            try:
                r = roles.parse_review(extract_json(text))
            except (PlanError, ValueError) as exc:
                raise _ParseFailure(roles.json_retry_message(str(exc)), str(exc)) from exc
            return r, {"verdict": r["verdict"], "issues": len(r["issues"])}

        st.review = await self.ask(agent, msgs, m.id, attempt=passed.n, purpose="审查", parse=parse,
                                   fallback=lambda: {"verdict": "ok", "issues": [], "unparsed": True})
        (self.rd.path / "nodes" / m.id / "review.json").write_text(
            json.dumps(st.review, ensure_ascii=False, indent=2), encoding="utf-8")
        high = [i for i in st.review["issues"] if str(i.get("severity", "")).lower() == "high"]
        if st.review["verdict"] != "needs_fix" or not high or st.review_fix_used or st.coder is None:
            return
        st.review_fix_used = True
        n = len(st.attempts) + 1
        st.code, st.tests = dict(passed.code), dict(passed.tests)
        msgs = roles.coder_messages(self.task, self.plan, m, self._dep_files(m), st.coder.prompt_name)
        msgs.append({"role": "user", "content": roles.review_fix_message(high, passed.code)})
        files, rejected = await self.ask(st.coder, msgs, m.id, attempt=n, purpose="按审查意见修改",
                                         parse=self._code_parser(m), fallback=lambda: ({}, []))
        st.code.update(files)
        st.rejected = rejected
        att = await self.check(st, by="review")
        if not att.passed:
            # 审查不能阻止通过：交付仍是审查前那次（delivered() 取最后一次通过的尝试）
            st.notes.append(f"审查后的修改（第 {att.n} 次尝试）没有通过判定，退回第 {passed.n} 次")
            self.bus.emit("rollback", m.id, from_attempt=att.n, to_attempt=passed.n,
                          from_score=round(att.score, 3), to_score=round(passed.score, 3),
                          reason="审查后的修改没有通过判定，退回审查前的版本")

    # ------------------------------------------------------------ 集成与交付

    def _copy_module(self, att: ModuleAttempt, dst: Path, *, with_tests: bool) -> None:
        write_files(dst, {**att.code, **(att.tests if with_tests else {})})

    async def integrate(self) -> None:
        if not self.states or not all(s.status == "done" for s in self.states.values()):
            return
        self.bus.emit("verify_started", "integrate", attempt=1, scope="all")
        report = await self._verify(self.integration_dir)
        self.result.final_report = report
        self.bus.emit("verify_finished", "integrate", attempt=1, verdict=report.verdict,
                      failed_layer=report.failed_layer, signature=report.signature, score=round(attempt_score(report), 3),
                      scope="all")

    def _finalize(self, interrupted: bool) -> None:
        r = self.result
        final, first = r.final_dir, r.first_dir
        for d in (final, first):
            if d.exists():
                shutil.rmtree(d)
        if not r.has_code:
            if r.verdict not in ("error", "interrupted", "rejected"):
                r.verdict = "fail"
            return
        if self.integration_dir.is_dir():
            shutil.copytree(self.integration_dir, final, ignore=shutil.ignore_patterns(".ff_build", "report.json"))
        for st in self.states.values():           # 没完成的模块也交付它最好的一版，便于分析
            att = st.delivered()
            if att is not None and st.status != "done":
                self._copy_module(att, final, with_tests=True)
            if st.attempts:
                self._copy_module(st.attempts[0], first, with_tests=True)
        final.mkdir(parents=True, exist_ok=True)
        report = r.final_report
        if report is not None:
            agent_report = report.for_agent()
            (final / "report.json").write_text(agent_report.to_json(), encoding="utf-8")
            md = agent_report.to_markdown(f"{self.task.title}（{self.task.id}）验证报告：多 Agent 集成判定")
            (final / "report.md").write_text(md + self._modules_markdown(), encoding="utf-8")
        else:
            (final / "report.md").write_text(f"# {self.task.title}（{self.task.id}）验证报告\n\n"
                                             "没有做集成判定（有模块没有完成，或运行被中断）。\n"
                                             + self._modules_markdown(), encoding="utf-8")
        if r.verdict in ("error", "interrupted", "rejected"):
            return
        all_done = all(s.status == "done" for s in self.states.values())
        r.verdict = report.for_agent().verdict if (report is not None and all_done) else "fail"

    def _modules_markdown(self) -> str:
        lines = ["", "## 模块", "", "| 模块 | 结果 | 编码 | 测试 | 尝试次数 | 交付第几次 | 备注 |", "|---|---|---|---|---|---|---|"]
        for s in [*self.states.values(), *self.result.replaced]:
            mid = s.module.id
            d = s.delivered()
            notes = "；".join(s.notes).replace("|", "/") or "-"
            lines.append(f"| {mid} | {s.status} | {s.coder.name if s.coder else '-'} | "
                         f"{s.tester.name if s.tester else '-'} | {len(s.attempts)} | {d.n if d else '-'} | {notes} |")
        reviews = [(mid, s.review) for mid, s in self.states.items() if s.review and s.review.get("issues")]
        if reviews:
            lines += ["", "## 审查意见（只作提示，不影响是否通过）", ""]
            for mid, rv in reviews:
                for i in rv["issues"]:
                    lines.append(f"- {mid} [{i.get('severity')}] {i.get('file', '')}:{i.get('line', '')} "
                                 f"{i.get('rule', '')}：{i.get('message', '')}")
        return "\n".join(lines) + "\n"

    def _record_stats(self, st: ModuleState) -> None:
        if not self.store:
            return
        first_pass = bool(st.attempts) and st.attempts[0].passed
        for agent in (st.coder, st.tester):
            if agent is None:
                continue
            u = self.usage.get((st.module.id, agent.name), {"tokens": 0, "latency_s": 0.0, "cost": 0.0})
            self.store.add_agent_stat(agent.name, agent.version, st.module.task_type, first_pass=first_pass,
                                      success=st.status == "done", tokens=u["tokens"], latency_s=u["latency_s"],
                                      cost=u["cost"])


def run_multi(task: Task, cfg: MultiConfig, **kwargs) -> MultiRunResult:
    """多 Agent 运行的唯一入口；命令行、评测都调用它。"""
    return Engine(task, cfg, **kwargs).run()

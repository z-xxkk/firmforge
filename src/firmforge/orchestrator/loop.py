"""每个模块的修正循环（ARCHITECTURE §8）：诊断 → 按 blame 分派 → 再判定，变差则回滚；修不好就升级。

- blame = code：交给编码 Agent；blame = test：交给测试 Agent；
- blame = interface：按 code 处理，并在报告里标注"接口问题"（升级阶梯开启时，反复出现就会走到"重新规划"）；
- blame = spec：在报告里标记"需求歧义"，然后按 code 处理。

升级阶梯（配置开启 escalation 时）：同一签名连续出现 repeat_threshold 次，或本级已修正
rounds_before_escalation 轮，就升一级，并发 escalated 事件：
    1 检索知识库，把相关条目加入编码上下文（本轮照常诊断和修改）
    2 换一个编码 Agent 从头重写：只给需求、接口和失败要点，不给旧代码（DECISIONS 第 32 条）
    3 回到规划层，修改这个模块的接口或拆分方式
    4 转人工；没有人工（评测模式）就记为失败
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..agents.registry import AgentDef
from ..verifier.report import Report
from .dag import Module

if TYPE_CHECKING:
    from .engine import Engine


@dataclass
class ModuleAttempt:
    n: int
    dir: Path
    report: Report
    score: float
    code: dict[str, str]          # 本模块的实现文件
    tests: dict[str, str]         # 本模块的测试文件
    by: str                       # 这次改动来自谁：coder / tester / review

    @property
    def passed(self) -> bool:
        return self.report.failed_layer is None

    @property
    def signature(self) -> str | None:
        return self.report.signature


@dataclass
class ModuleState:
    module: Module
    status: str = "pending"       # pending → ready → running → done | failed | skipped
    coder: AgentDef | None = None
    tester: AgentDef | None = None
    code: dict[str, str] = field(default_factory=dict)       # 下一次判定要用的实现
    tests: dict[str, str] = field(default_factory=dict)      # 下一次判定要用的测试
    rejected: list[tuple[str, str]] = field(default_factory=list)
    attempts: list[ModuleAttempt] = field(default_factory=list)
    diagnoses: list[dict] = field(default_factory=list)
    review: dict | None = None
    notes: list[str] = field(default_factory=list)            # 接口问题、需求歧义、审查修改回退等
    review_fix_used: bool = False
    level: int = 0                    # 当前升级级别 0–4
    level_rounds: int = 0             # 本级已修正的轮数
    knowledge: list = field(default_factory=list)            # 第 1 级检索到的知识库条目
    exp_used: list[int] = field(default_factory=list)        # 用到的经验 id
    excluded: list[str] = field(default_factory=list)        # 第 2 级换下去的编码 Agent
    stop: str | None = None           # 不再继续修正的原因：replanned | gave_up
    human_hint: str | None = None

    @property
    def passed(self) -> bool:
        """有一次尝试通过了判定（审查后的修改没通过时，交付的仍是之前通过的那次）。"""
        return any(a.passed for a in self.attempts)

    def best(self) -> ModuleAttempt | None:
        """得分最高的尝试；同分取较晚的。"""
        return max(self.attempts, key=lambda a: (a.score, a.n)) if self.attempts else None

    def delivered(self) -> ModuleAttempt | None:
        """交付的尝试：最后一次通过的；都没通过就取得分最高的。"""
        passing = [a for a in self.attempts if a.passed]
        return passing[-1] if passing else self.best()

    def signatures(self) -> list[str | None]:
        return [a.signature for a in self.attempts]


def choose_base(state: ModuleState) -> tuple[ModuleAttempt, bool]:
    """下一轮从哪次尝试开始：最近一次比历史最好的差，就退回最好的那次（返回 rolled_back=True）。"""
    last, best = state.attempts[-1], state.best()
    if best is not None and last.score < best.score:
        return best, True
    return last, False


ROUTE = {"code": "coder", "interface": "coder", "spec": "coder", "test": "tester"}
NOTE = {"interface": "接口问题", "spec": "需求歧义"}
LEVELS = {1: "检索知识库", 2: "换 Agent 从头重写", 3: "重新规划", 4: "转人工"}
LAYER_RANK = {name: i for i, name in enumerate(
    ("host_compile", "cross_compile", "resources", "static_check", "unit_tests"))}


def escalation_reason(state: ModuleState, loop_cfg: dict) -> str | None:
    """该升级时返回原因，否则返回 None。"""
    repeat = int(loop_cfg.get("repeat_threshold", 2))
    rounds = int(loop_cfg.get("rounds_before_escalation", 3))
    sigs = state.signatures()
    tail = sigs[-repeat:]
    if len(tail) == repeat and tail[0] is not None and len(set(tail)) == 1:
        return f"同一个错误签名 {tail[0]} 连续出现 {repeat} 次"
    if state.level_rounds >= rounds:
        return f"本级已经修正 {state.level_rounds} 轮仍未通过"
    return None


def progressed(before: ModuleAttempt, after: ModuleAttempt) -> bool:
    """失败层前进了，或者直接通过了。"""
    if after.passed:
        return True
    b, a = before.report.failed_layer, after.report.failed_layer
    return a is not None and b is not None and LAYER_RANK.get(a, -1) > LAYER_RANK.get(b, -1)


def failure_points(state: ModuleState) -> list[str]:
    """给"换 Agent 重写"和"重新规划"用的失败要点摘要。"""
    points = [f"第 {d['attempt']} 次（{d['blame']}）：{d.get('summary', '')}" for d in state.diagnoses]
    last = state.attempts[-1] if state.attempts else None
    if last is not None and last.report.failed_layer:
        points.append(f"最近一次判定卡在 {last.report.failed_layer} 层，签名 {last.signature}")
    return points


async def escalate(engine: "Engine", state: ModuleState, base: ModuleAttempt, reason: str) -> bool:
    """升一级。返回 True 表示本轮已经处理完（不再走诊断修改）。"""
    mid = state.module.id
    state.level = min(4, state.level + 1)
    if state.level == 3 and not engine.can_replan():
        state.level = 4
    state.level_rounds = 0
    engine.result.escalations += 1
    detail = engine.consult_knowledge(state, base) if state.level == 1 else []
    engine.bus.emit("escalated", mid, level=state.level, name=LEVELS[state.level], reason=reason,
                    attempt=state.attempts[-1].n, knowledge=detail)
    if state.level == 1:
        return False
    if state.level == 2:
        if await engine.rewrite(state, base):
            await engine.check(state, by="rewrite")
            return True
        return False                                  # 没有别的编码 Agent 可换：本轮照常修改
    if state.level == 3:
        if await engine.replan(state):
            state.stop = "replanned"
            return True
        state.level = 4
        engine.result.escalations += 1
        engine.bus.emit("escalated", mid, level=4, name=LEVELS[4], reason="重新规划没有成功",
                        attempt=state.attempts[-1].n)
    hint = await engine.ask_human(state)
    if hint is None:
        state.stop = "gave_up"
        state.notes.append("升级到转人工，但没有人工介入（评测模式），记为失败")
        return True
    state.human_hint = hint
    return False


async def repair_round(engine: "Engine", state: ModuleState) -> None:
    """一轮修正：选起点（必要时回滚）→（需要时升级）→ 诊断 → 交给对应 Agent 修改 → 判定。"""
    mid = state.module.id
    base, rolled_back = choose_base(state)
    last = state.attempts[-1]
    if rolled_back:
        engine.bus.emit("rollback", mid, from_attempt=last.n, to_attempt=base.n,
                        from_score=round(last.score, 3), to_score=round(base.score, 3),
                        reason="这次比历史最好的一次差，下一轮从最好的那次开始")
    if engine.cfg.escalation and state.level < 4:
        reason = escalation_reason(state, engine.loop_cfg)
        if reason and await escalate(engine, state, base, reason):
            return
    state.level_rounds += 1
    if state.human_hint is not None:
        hint, state.human_hint = state.human_hint, None
        n = len(state.attempts) + 1
        state.code, state.tests = dict(base.code), dict(base.tests)
        await engine.repair_code(state, base, {"summary": "负责人给出的提示", "instructions": hint}, n)
        await engine.check(state, by="human")
        return
    diagnosis = await engine.diagnose(state, base)
    blame = diagnosis["blame"]
    route = ROUTE[blame]
    if blame in NOTE:
        state.notes.append(f"第 {base.n} 次尝试：{NOTE[blame]}——{diagnosis.get('summary', '')}")
    engine.bus.emit("diagnosis", mid, attempt=base.n, blame=blame, route=route,
                    confidence=diagnosis.get("confidence"), summary=diagnosis.get("summary", ""))
    n = len(state.attempts) + 1
    state.code, state.tests = dict(base.code), dict(base.tests)
    if route == "coder":
        await engine.repair_code(state, base, diagnosis, n)
    else:
        await engine.repair_test(state, base, diagnosis, n)
    after = await engine.check(state, by=route)
    if route == "coder" and progressed(base, after):
        await engine.save_experience(state, base, diagnosis)

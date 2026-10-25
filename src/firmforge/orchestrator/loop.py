"""每个模块的修正循环（ARCHITECTURE §8）：诊断 → 按 blame 分派 → 再判定，变差则回滚。

阶段 3 没有升级阶梯（阶段 4 再加）：
- blame = code：交给编码 Agent；blame = test：交给测试 Agent；
- blame = interface：按 code 处理，并在报告里标注"接口问题"（DECISIONS 第 39 条）；
- blame = spec：在报告里标记"需求歧义"，然后按 code 处理。
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


async def repair_round(engine: "Engine", state: ModuleState) -> None:
    """一轮修正：选起点（必要时回滚）→ 诊断 → 交给对应 Agent 修改 → 判定。"""
    mid = state.module.id
    base, rolled_back = choose_base(state)
    last = state.attempts[-1]
    if rolled_back:
        engine.bus.emit("rollback", mid, from_attempt=last.n, to_attempt=base.n,
                        from_score=round(last.score, 3), to_score=round(base.score, 3),
                        reason="这次比历史最好的一次差，下一轮从最好的那次开始")
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
    await engine.check(state, by=route)

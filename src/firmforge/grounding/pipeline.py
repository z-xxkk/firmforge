"""规则流程：查资料 → 规则判定器 → 退回修改 → 人工确认 → 入库（DECISIONS 第 66 条第 ②③⑧ 步）。

- 可信规则库里已经覆盖的外设不再查（第 66 条第 ⑧ 步：同芯片同版本直接复用）；
- 查资料 Agent 最多跑 max_rounds 轮；每轮被退回的规则带着原因交回去改；
- 同一条规则被退回 3 次，不再交给 Agent，直接放进人工确认清单并标明"多次退回"；
- 人工确认是必经的（第 66 条），没有接人工的回调时，已核对的规则停在"已核对"，不会入库。

每一步都发事件（ARCHITECTURE §2.8），界面据此显示进度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..tools import ToolContext, ToolPipeline, default_registry
from ..tools.loop import call_with_tools
from .profile import HardwareProfile
from .research import SOURCE_TOOLS, parse_rules, research_messages
from .rules import Rule, RuleSet
from .sources import SourceLibrary
from .verifier import MAX_REJECTS, CHECKLIST, RuleResult, RuleVerifier, apply_human, store_trusted

Emit = Callable[..., None]
# 人工确认：(待确认的规则, 多次退回的规则及原因) → {规则编号: ("approve" | "reject", 意见)}
Human = Callable[[list[Rule], list[RuleResult]], dict[str, tuple[str, str]]]
Complete = Callable[[list[dict], list[dict] | None], object]


@dataclass
class RuleBuildResult:
    trusted: list[Rule] = field(default_factory=list)       # 本次新入库的
    reused: list[Rule] = field(default_factory=list)        # 规则库里已有、直接复用的
    pending: list[Rule] = field(default_factory=list)       # 已核对但没有人工确认（不入库）
    rejected: list[RuleResult] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing and not self.conflicts and not self.pending


def covered_scopes(store: RuleSet, scopes: list[str]) -> set[str]:
    """规则库里可信规则已经覆盖完整性清单的外设或器件。"""
    out = set()
    for s in scopes:
        have = {r.kind for r in store.select({s}, "trusted")}           # 含 scope 为 all 的规则，和判定器第 5 步一致
        need = CHECKLIST.get(s, ())
        if need and all(k in have for k in need):
            out.add(s)
    return out


class RuleBuilder:
    def __init__(self, complete: Complete, profile: HardwareProfile, library: SourceLibrary, verifier: RuleVerifier,
                 store: RuleSet, *, emit: Emit | None = None, human: Human | None = None, max_rounds: int = 4,
                 max_tool_rounds: int = 12):
        self.complete, self.profile, self.library, self.verifier = complete, profile, library, verifier
        self.store, self.human, self.max_rounds, self.max_tool_rounds = store, human, max_rounds, max_tool_rounds
        self.emit = emit or (lambda *a, **k: None)

    def _ask_agent(self, msgs: list[dict], out: RuleBuildResult) -> str:
        registry = default_registry()
        allowed = [t for t in registry.all() if t.name in {x.name for x in SOURCE_TOOLS}]
        ctx = ToolContext(agent="researcher", library=self.library)
        pipe = ToolPipeline(registry, allowed=allowed, ctx=ctx, emit=self.emit, node_id="rules")
        resp = call_with_tools(self.complete, msgs, allowed, pipe, max_rounds=self.max_tool_rounds)
        out.prompt_tokens += resp.prompt_tokens
        out.completion_tokens += resp.completion_tokens
        return resp.text

    def run(self, scopes: list[str]) -> RuleBuildResult:
        out = RuleBuildResult()
        done = covered_scopes(self.store, scopes)
        out.reused = [r for r in self.store.select(done, "trusted")]
        todo = [s for s in scopes if s not in done]
        self.emit("rules_started", "rules", scopes=scopes, reused=sorted(done), todo=todo)
        checked: dict[str, Rule] = {}
        rejects: dict[str, int] = {}
        escalated: dict[str, RuleResult] = {}
        feedback: list[RuleResult] | None = None
        missing: list[str] | None = None
        for rnd in range(1, self.max_rounds + 1 if todo else 1):
            out.rounds = rnd
            msgs = research_messages(self.profile, todo, self.library, trusted=self.store, feedback=feedback,
                                     missing=missing)
            text = self._ask_agent(msgs, out)
            rules, parse_errors = parse_rules(text)
            rules = [r for r in rules if r.id not in escalated]
            self.emit("rules_proposed", "rules", round=rnd, count=len(rules), parse_errors=parse_errors[:5])
            report = self.verifier.run(RuleSet(rules + list(checked.values())), scopes=set(todo))
            for r in report.checked_rules():
                checked[r.id] = r
            feedback = []
            for res in report.rejected:
                checked.pop(res.rule.id, None)
                rejects[res.rule.id] = rejects.get(res.rule.id, 0) + 1
                if rejects[res.rule.id] >= MAX_REJECTS:
                    escalated[res.rule.id] = res
                else:
                    feedback.append(res)
                self.emit("rule_rejected", "rules", rule=res.rule.id, times=rejects[res.rule.id],
                          reasons=res.reasons())
            missing, out.conflicts = report.missing, report.conflicts
            self.emit("rules_round_done", "rules", round=rnd, checked=len(checked), rejected=len(report.rejected),
                      missing=missing, conflicts=report.conflicts)
            if not feedback and not missing and not report.conflicts:
                break
        out.rejected = [r for r in feedback or []] + list(escalated.values())
        out.missing = missing or []
        pending = sorted(checked.values(), key=lambda r: r.id)
        if self.human is None or not (pending or escalated):
            out.pending = pending
            if pending:
                self.emit("rules_need_human", "rules", count=len(pending))
            return out
        self.emit("human_wait", "rules", reason="确认规则", rules=[r.id for r in pending],
                  escalated=sorted(escalated))
        decisions = self.human(pending, list(escalated.values()))
        approved, rejected_by_human = apply_human(pending, decisions)
        out.trusted = store_trusted(self.store, approved)
        decided = {r.id for r in approved} | {r.id for r in rejected_by_human}
        out.pending = [r for r in pending if r.id not in decided]
        for r in out.trusted:
            self.emit("rule_trusted", "rules", rule=r.id)
        self.emit("rules_finished", "rules", trusted=len(out.trusted), pending=len(out.pending),
                  rejected=len(out.rejected) + len(rejected_by_human))
        return out

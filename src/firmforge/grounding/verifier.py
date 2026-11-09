"""规则判定器：在编码之前核对查资料 Agent 产出的规则（DECISIONS 第 66 条第 ③ 步）。

固定工作流，每一步的结论都记下来，可审计：
  0 资料登记   出处资料必须已登记；代码类资料的版本必须和硬件档案一致
  1 格式检查   字段齐全、编号合法（程序）
  2 出处核对   引用原文必须出现在指定页（程序）
  3 事实核对   接口查该版本 ESP-IDF 头文件、寄存器查 SVD、引脚查硬件档案（程序）
  4 语义核对   不能机械核对的规则，交给独立的核对 Agent，只看原文那一页（Agent）
  5 冲突与完整性  同一字段不同值、既要求又禁止；按外设清单检查有没有缺类别（程序）
  6 人工确认   必经，只看新规则（人）
  7 入库       确认过的规则标记"可信"并存入规则库（程序）

本模块做一遍 0–5；第 6、7 步由调用方在拿到报告后进行（`apply_human` / `store_trusted`）。
同一条规则被退回 3 次就不再交给查资料 Agent，直接转人工（第 66 条），次数由调用方统计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .idf import IdfTree
from .profile import HardwareProfile
from .rules import KINDS, Rule, RuleSet
from .sources import SourceError, SourceLibrary, quote_found
from .svd import SvdDevice

# 第 5 步完整性清单：硬件档案里用到某类外设或器件时，至少要有这些类别的规则
CHECKLIST: dict[str, tuple[str, ...]] = {
    "i2c": ("api", "pin", "peripheral"),
    "uart": ("api", "pin"),
    "gpio": ("api", "pin"),
    "ledc": ("api", "pin", "peripheral"),
    "rmt": ("api", "pin", "peripheral"),
    "gptimer": ("api", "peripheral"),
    "adc": ("api", "pin", "peripheral"),
    "mpu6050": ("device", "sequence"),
    "max30102": ("device", "sequence"),
}
MAX_REJECTS = 3

SemanticChecker = Callable[[Rule, str], tuple[bool, str]]     # (规则, 原文所在页) → (是否忠实于原文, 理由)


@dataclass
class RuleResult:
    rule: Rule
    steps: list[tuple[int, str, str]] = field(default_factory=list)    # (步骤, ok/wrong/unknown/skip, 说明)

    @property
    def rejected(self) -> bool:
        return any(r == "wrong" for _, r, _ in self.steps)

    def reasons(self) -> list[str]:
        return [f"第 {s} 步：{d}" for s, r, d in self.steps if r == "wrong"]


@dataclass
class VerifyReport:
    results: list[RuleResult]
    conflicts: list[str]
    missing: list[str]              # 完整性缺项：外设或器件 → 缺的规则类别
    profile_problems: list[str]     # 硬件档案自身的问题或和规则冲突

    @property
    def passed(self) -> list[Rule]:
        return [r.rule for r in self.results if not r.rejected]

    @property
    def rejected(self) -> list[RuleResult]:
        return [r for r in self.results if r.rejected]

    def checked_rules(self) -> list[Rule]:
        """通过 0–5 步的规则：待核对的推进到"已核对"，已经更靠后的保持原状。"""
        return [r.advance("checked", "规则判定器通过") if r.status == "draft" else r for r in self.passed]

    @property
    def ok(self) -> bool:
        return not self.rejected and not self.conflicts and not self.missing and not self.profile_problems

    def summary(self) -> str:
        lines = [f"规则 {len(self.results)} 条：通过 {len(self.passed)}，退回 {len(self.rejected)}"]
        for r in self.rejected:
            lines.append(f"- {r.rule.id} 退回：" + "；".join(r.reasons()))
        lines += [f"- 冲突：{c}" for c in self.conflicts]
        lines += [f"- 缺项：{m}" for m in self.missing]
        lines += [f"- 硬件档案：{p}" for p in self.profile_problems]
        return "\n".join(lines)


class RuleVerifier:
    def __init__(self, library: SourceLibrary, profile: HardwareProfile, *, idf: IdfTree | None = None,
                 svd: SvdDevice | None = None, semantic: SemanticChecker | None = None):
        self.library, self.profile, self.idf, self.svd, self.semantic = library, profile, idf, svd, semantic

    # ---- 单条规则 0–4 步
    def check_rule(self, rule: Rule) -> RuleResult:
        res = RuleResult(rule)
        add = lambda step, result, detail: res.steps.append((step, result, detail))     # noqa: E731

        try:
            doc = self.library.get(rule.cite.doc)
        except SourceError as exc:
            add(0, "wrong", str(exc))
            return res
        want = str(self.profile.framework_version.value)
        if doc.kind == "header" and doc.version and doc.version != want:
            add(0, "wrong", f"资料 {doc.id} 是 ESP-IDF {doc.version}，硬件档案要求 {want}")
            return res
        add(0, "ok", f"{doc.id}（{doc.tier}）")

        probs = rule.format_problems()
        if probs:
            add(1, "wrong", "；".join(probs))
            return res
        add(1, "ok", "格式合格")

        try:
            found = quote_found(doc, rule.cite.page, rule.cite.quote)
        except SourceError as exc:
            add(2, "wrong", str(exc))
            return res
        where = f"第 {rule.cite.page} 页" if rule.cite.page else "全文"
        if not found:
            add(2, "wrong", f"{doc.id} {where}找不到引用的原文")
            return res
        add(2, "ok", f"{doc.id} {where}找到原文")

        machine = self._fact_check(rule)
        add(3, *machine)
        if machine[0] == "wrong":
            return res

        if machine[0] == "ok" and rule.kind in ("api", "register", "pin"):
            add(4, "skip", "已由程序核对，不再交给核对 Agent")
        elif self.semantic is None:
            add(4, "unknown", "没有配置核对 Agent，交给人工确认时重点看")
        else:
            page_text = doc.page(rule.cite.page)
            layout = doc.page_layout(rule.cite.page)
            if layout:                                   # 表格的列对齐只在按版面的文字里（DECISIONS 第 98 条）
                page_text += "\n\n【同一页按版面排列，保留表格的列对齐】\n" + layout
            bits = doc.page_bits(rule.cite.page)
            if bits:                                     # 平台按坐标算的位标注，不是原文（DECISIONS 第 99 条）
                page_text += "\n\n【同一页的寄存器位标注，平台按 PDF 坐标算出，不是原文】\n" + bits
            ok, why = self.semantic(rule, page_text)
            add(4, "ok" if ok else "wrong", why)
        return res

    def _fact_check(self, rule: Rule) -> tuple[str, str]:
        c = rule.check
        if not c:
            return "unknown", "这条规则没有机器检查项"
        t = c["type"]
        if t == "api_exists":
            if self.idf is None:
                return "unknown", "没有 ESP-IDF 源码树，无法核对接口"
            ok, why = self.idf.declares(c["header"], c["symbol"])
            return ("ok" if ok else "wrong"), why
        if t == "api_absent":
            if self.idf is None:
                return "unknown", "没有 ESP-IDF 源码树，无法核对"
            where = self.idf.symbol_anywhere(c["symbol"])
            if where:
                return "ok", f"禁止使用 {c['symbol']}（该版本仍保留此接口，属于约定而非缺失）"
            return "ok", f"该版本头文件里已没有 {c['symbol']}"
        if t == "pin_forbidden":
            used = {f.value: s for s, f in self.profile.pins.items()}
            hit = [f"GPIO{g}（{used[g]}）" for g in c["gpio"] if g in used]
            if hit:
                return "ok", "规则本身无误，但硬件档案用到了被禁止的引脚：" + "、".join(hit)
            return "ok", "硬件档案没有用到这些引脚"
        if t == "register_value":
            if self.svd is None:
                return "unknown", "没有 SVD 文件，无法核对寄存器"
            return self.svd.check_field(c["peripheral"], c["register"], c["field"], c["value"])
        return "unknown", f"{t} 类规则在编码前无法机械核对，留到合规检查和上板测试"

    # ---- 整体 0–5 步
    def run(self, rules: RuleSet, scopes: set[str] | None = None, already: RuleSet | list[Rule] | None = None) -> VerifyReport:
        """already：之前已经核对通过的规则，不再逐条核对（省掉重复的核对 Agent 调用），只参与冲突和完整性检查。"""
        results = [self.check_rule(r) for r in rules]
        passed = RuleSet([r.rule for r in results if not r.rejected] + list(already or []))
        conflicts = passed.conflicts()
        prof = list(self.profile.problems())
        for r in passed:
            c = r.check or {}
            if c.get("type") == "pin_forbidden":
                used = {f.value: s for s, f in self.profile.pins.items()}
                prof += [f"{used[g]} 用了 GPIO{g}，违反 {r.id}" for g in c["gpio"] if g in used]
        missing = []
        need = scopes if scopes is not None else self.profile_scopes()
        for scope in sorted(need):
            have = {r.kind for r in passed if scope in r.scope or "all" in r.scope}
            lack = [k for k in CHECKLIST.get(scope, ()) if k not in have]
            if lack:
                missing.append(f"{scope} 缺少这些类别的规则：" + "、".join(_kind_names(lack)))
        return VerifyReport(results, conflicts, missing, prof)

    def profile_scopes(self) -> set[str]:
        """硬件档案里用到的外设和器件，作为完整性检查的范围。"""
        return self.profile.scopes()


def _kind_names(kinds: list[str]) -> list[str]:
    from .rules import KIND_NAMES
    return [KIND_NAMES.get(k, k) for k in kinds if k in KINDS]


# ---- 第 6、7 步

Decision = tuple[str, str]      # ("approve" | "reject", 意见)


def apply_human(rules: list[Rule], decisions: dict[str, Decision]) -> tuple[list[Rule], list[Rule]]:
    """人工确认：已核对的规则逐条通过或驳回；没表态的保持已核对、不进入下一步。"""
    approved, rejected = [], []
    for r in rules:
        verdict, note = decisions.get(r.id, ("", ""))
        if verdict == "approve":
            approved.append(r.advance("approved", note or "人工确认"))
        elif verdict == "reject":
            rejected.append(r.reject(f"人工驳回：{note}" if note else "人工驳回"))
    return approved, rejected


def store_trusted(store: RuleSet, approved: list[Rule]) -> list[Rule]:
    """入库：人工确认过的规则标记可信，之后同芯片同版本直接复用（第 66 条第 ⑧ 步）。"""
    out = []
    for r in approved:
        t = r.advance("trusted")
        store.put(t)
        out.append(t)
    return out

"""结构化规则（DECISIONS 第 66、67、76 条）。

一条规则 = 编号 + 类型 + 适用范围 + 一句话内容 + 出处（资料、页、原文引用）+ 来源等级 + 状态，
可选一个"机器检查"字段，供合规检查程序直接核对（例如某接口在该版本头文件里存在、某引脚不许用）。

状态只能按 待核对 → 已核对 → 人工确认 → 可信 前进；任何一步不过就退回"待核对"并记下原因。
拼进 system prompt 的文本由程序按固定格式生成，一行一条，不交给模型压缩（第 67 条）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .sources import TIERS

KINDS = ("api", "pin", "peripheral", "register", "sequence", "timing", "forbidden", "device")
KIND_NAMES = {"api": "接口用法", "pin": "引脚", "peripheral": "外设约束", "register": "寄存器",
              "sequence": "操作顺序", "timing": "时序", "forbidden": "禁止项", "device": "外接器件"}
STATUSES = ("draft", "checked", "approved", "trusted")
STATUS_NAMES = {"draft": "待核对", "checked": "已核对", "approved": "人工确认", "trusted": "可信"}

# 机器检查的种类与必需字段（合规检查程序据此判定，第 66 条第 ⑥ 步）
CHECK_FIELDS = {
    "api_exists": ("symbol", "header"),          # 该版本头文件里有这个符号
    "api_absent": ("symbol",),                   # 不许用这个符号（例如旧版接口）
    "pin_forbidden": ("gpio",),                  # 不许用这些 GPIO
    "register_value": ("peripheral", "register", "field", "value"),   # 运行时读寄存器比对
    "call_order": ("before", "after"),           # before 必须在 after 之前调用
}

RULE_ID = re.compile(r"^R-[A-Z0-9]+-\d{3}$")


class RuleError(ValueError):
    """规则内容不合法。"""


@dataclass(frozen=True)
class Citation:
    doc: str                    # 资料编号
    page: int | None            # PDF 页码（从 1 开始）；网页、头文件为 None
    section: str = ""           # 章节号或标题，给人看的
    quote: str = ""             # 原文引用，程序要在该页里找到它


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    scope: tuple[str, ...]      # 例如 ("i2c", "mpu6050")
    text: str                   # 一句话内容，进 system prompt
    cite: Citation
    tier: str
    status: str = "draft"
    check: dict | None = None
    notes: tuple[str, ...] = ()     # 退回原因、人工意见

    def format_problems(self) -> list[str]:
        """规则判定器第 1 步：格式检查。"""
        p = []
        if not RULE_ID.match(self.id):
            p.append(f"编号 {self.id!r} 不合格式，应为 R-大写字母或数字-三位数字，例如 R-I2C-003")
        if self.kind not in KINDS:
            p.append(f"类型 {self.kind!r} 未知")
        if not self.scope:
            p.append("缺少适用范围")
        if not self.text.strip():
            p.append("缺少内容")
        elif "\n" in self.text or len(self.text) > 120:
            p.append("内容必须是一行、不超过 120 字")
        if not self.cite.doc:
            p.append("缺少出处资料")
        if not self.cite.quote.strip():
            p.append("缺少原文引用")
        if self.cite.page is not None and self.cite.page < 1:
            p.append("页码必须从 1 开始")
        if self.tier not in TIERS:
            p.append(f"来源等级 {self.tier!r} 未知")
        if self.status not in STATUSES:
            p.append(f"状态 {self.status!r} 未知")
        if self.check is not None:
            t = self.check.get("type")
            if t not in CHECK_FIELDS:
                p.append(f"机器检查类型 {t!r} 未知")
            else:
                miss = [f for f in CHECK_FIELDS[t] if f not in self.check]
                if miss:
                    p.append(f"机器检查 {t} 缺字段：{', '.join(miss)}")
        return p

    def line(self) -> str:
        """拼进 system prompt 的一行。"""
        where = self.cite.doc + (f" §{self.cite.section}" if self.cite.section else "") \
            + (f" p.{self.cite.page}" if self.cite.page else "")
        return f"{self.id} | {self.text} | {where}"

    def advance(self, to: str, note: str = "") -> "Rule":
        order = STATUSES.index
        if to not in STATUSES or order(to) != order(self.status) + 1:
            raise RuleError(f"{self.id}：状态不能从{STATUS_NAMES.get(self.status, self.status)}"
                            f"跳到{STATUS_NAMES.get(to, to)}")
        return replace(self, status=to, notes=self.notes + ((note,) if note else ()))

    def reject(self, reason: str) -> "Rule":
        return replace(self, status="draft", notes=self.notes + (reason,))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scope"], d["notes"] = list(self.scope), list(self.notes)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        try:
            return cls(id=d["id"], kind=d["kind"], scope=tuple(d.get("scope", ())), text=d["text"],
                       cite=Citation(**d["cite"]), tier=d["tier"], status=d.get("status", "draft"),
                       check=d.get("check"), notes=tuple(d.get("notes", ())))
        except (KeyError, TypeError) as exc:
            raise RuleError(f"规则缺字段或字段不对：{exc}") from exc


class RuleSet:
    def __init__(self, rules: list[Rule] | None = None):
        self.rules: dict[str, Rule] = {}
        for r in rules or []:
            self.put(r)

    def put(self, rule: Rule) -> None:
        self.rules[rule.id] = rule

    def __iter__(self):
        return iter(sorted(self.rules.values(), key=lambda r: r.id))

    def __len__(self):
        return len(self.rules)

    def select(self, scopes: set[str] | None = None, min_status: str = "draft") -> list[Rule]:
        """按适用范围筛选（第 67 条：只放和当前模块有关的规则）；scope 含 "all" 的规则总是入选。"""
        floor = STATUSES.index(min_status)
        out = []
        for r in self:
            if STATUSES.index(r.status) < floor:
                continue
            if scopes is None or "all" in r.scope or scopes & set(r.scope):
                out.append(r)
        return out

    def prompt_block(self, scopes: set[str] | None = None, min_status: str = "approved") -> str:
        rules = self.select(scopes, min_status)
        if not rules:
            return ""
        return "\n".join(["## 必须遵守的硬件规则（编号 | 内容 | 出处）"] + [r.line() for r in rules])

    def conflicts(self) -> list[str]:
        """规则判定器第 5 步的一部分：同一个寄存器字段给了不同的值；同一个符号既要求存在又禁止。"""
        out, seen_reg, need, ban = [], {}, {}, {}
        for r in self:
            c = r.check or {}
            if c.get("type") == "register_value":
                key = (c["peripheral"], c["register"], c["field"])
                if key in seen_reg and seen_reg[key][1] != c["value"]:
                    out.append(f"{seen_reg[key][0]} 与 {r.id} 对 {'.'.join(key)} 的值不同")
                seen_reg.setdefault(key, (r.id, c["value"]))
            elif c.get("type") == "api_exists":
                need[c["symbol"]] = r.id
            elif c.get("type") == "api_absent":
                ban[c["symbol"]] = r.id
        for sym in sorted(set(need) & set(ban)):
            out.append(f"{need[sym]} 要求使用 {sym}，{ban[sym]} 却禁止它")
        return out

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps([r.to_dict() for r in self], ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "RuleSet":
        path = Path(path)
        if not path.is_file():
            return cls()
        return cls([Rule.from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))])

"""代码合规检查：编译通过以后，用程序逐条核对代码有没有照规则写（DECISIONS 第 66 条第 ⑥ 步）。

程序判定（error，不过就打回）：
- api_absent 规则：代码里调用了被禁止的接口（例如旧版 ESP-IDF 接口、深度睡眠）；
- pin_forbidden 规则：代码里出现被禁止的 GPIO（GPIO_NUM_19 这类写法）；
- 引脚必须用 hw_config.h 的宏：在引脚字段或 GPIO 函数里直接写数字，算违规（硬件档案改了代码就会错）。
程序提示（warning，不打回）：
- call_order 规则：同一个文件里"后调用的函数"先出现了。只看文字先后，不分析控制流，所以只作提示。

没有机器检查项的规则（例如时序、设计要求）交给 Agent 审查，不在这里判定。
扫描前先去掉注释和字符串，行号保持不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rules import Rule, RuleSet

# 引脚字段与 GPIO 函数的第一个参数：这些位置不许直接写数字
_PIN_FIELD = re.compile(r"\.(?:\w+_io_num|gpio_num|\w+_gpio_num|pin)\s*=\s*(?:\(gpio_num_t\)\s*)?(\d+)\b")
_PIN_CALL = re.compile(r"\b(gpio_(?:set_level|get_level|set_direction|set_pull_mode|reset_pin|"
                       r"set_intr_type|isr_handler_add|pullup_en|pulldown_en|config_by_mask))\s*\(\s*(\d+)\b")
_PIN_MASK = re.compile(r"\(\s*1ULL\s*<<\s*(\d+)\s*\)")
_GPIO_NUM = re.compile(r"\bGPIO_NUM_(\d+)\b")


@dataclass
class Violation:
    rule: str           # 规则编号，或 "PIN-LITERAL" 这类内置检查名
    file: str
    line: int
    message: str
    level: str = "error"

    def render(self) -> str:
        return f"[{self.rule}] {self.file}:{self.line} {self.message}"


@dataclass
class ComplianceReport:
    violations: list[Violation] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)      # 交给 Agent 审查的规则编号

    @property
    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if not self.violations:
            return "代码合规检查通过"
        return "\n".join(("违规 " if v.level == "error" else "提示 ") + v.render() for v in self.violations)


def strip_c(text: str) -> str:
    """去掉注释、字符串和字符常量，换行保留（行号不变）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * text.count("\n", i, j))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            out.append(c + c)
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check_code(files: dict[str, str], rules: RuleSet | list[Rule]) -> ComplianceReport:
    """files：Agent 写的源文件（路径 → 内容），不含程序生成的 hw_config.h。"""
    rep = ComplianceReport()
    code = {p: strip_c(t) for p, t in sorted(files.items()) if p.endswith((".c", ".h"))
            and not p.endswith("hw_config.h")}
    rule_list = list(rules)

    for path, text in code.items():
        for rx, what in ((_PIN_FIELD, "引脚字段"), (_PIN_CALL, "GPIO 函数参数"), (_PIN_MASK, "引脚位掩码")):
            for m in rx.finditer(text):
                num = m.group(m.lastindex)
                rep.violations.append(Violation("PIN-LITERAL", path, _line(text, m.start()),
                                                f"{what}直接写了数字 {num}，请用 hw_config.h 里的 FF_PIN_* 宏"))

    for r in rule_list:
        c = r.check or {}
        t = c.get("type")
        if t == "api_absent":
            rx = re.compile(rf"\b{re.escape(c['symbol'])}\s*\(")
            for path, text in code.items():
                for m in rx.finditer(text):
                    rep.violations.append(Violation(r.id, path, _line(text, m.start()),
                                                    f"调用了被禁止的 {c['symbol']}（{r.text}）"))
        elif t == "pin_forbidden":
            banned = set(c["gpio"])
            for path, text in code.items():
                for m in _GPIO_NUM.finditer(text):
                    if int(m.group(1)) in banned:
                        rep.violations.append(Violation(r.id, path, _line(text, m.start()),
                                                        f"用了被禁止的 GPIO{m.group(1)}（{r.text}）"))
        elif t == "call_order":
            for path, text in code.items():
                a = re.search(rf"\b{re.escape(c['before'])}\s*\(", text)
                b = re.search(rf"\b{re.escape(c['after'])}\s*\(", text)
                if a and b and b.start() < a.start():
                    rep.violations.append(Violation(r.id, path, _line(text, b.start()),
                                                    f"{c['after']} 出现在 {c['before']} 之前（{r.text}）", "warning"))
        elif t not in ("api_exists", "register_value"):
            rep.unchecked.append(r.id)
    return rep

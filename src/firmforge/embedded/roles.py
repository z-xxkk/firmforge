"""ESP-IDF 编码、写测试、诊断 Agent 的消息与输出解析（DECISIONS 第 66、80、81 条）。

隔离规则（沿用旧流程，ARCHITECTURE §5.2）：
- 编码 Agent 只能写设计分给本模块的文件；能看到依赖模块的公共头文件；
- 测试 Agent 看不到实现代码，只看需求、本模块公共头文件、验收标准和同范围的规则；
  修正轮交给它的报告和诊断指令先删去实现代码片段（scrub_for_tester）；
- 评测题的隐藏测试不进任何 Agent 的上下文。

编码 Agent 可以翻手册（第 80 条），但新用到的硬件事实必须写进 facts 代码块，程序转成"待核对"规则。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..agents.brief import Brief
from ..agents.protocol import FILE_BLOCK, OPEN_TAG
from ..agents.roles import scrub_for_tester
from ..grounding.profile import HardwareProfile
from ..grounding.research import parse_rules
from ..grounding.rules import Rule, RuleSet
from .design import Design, DesignModule, design_json
from .prompting import system_prompt

BLAMES = ("code", "test", "rule", "hardware")
BLAME_NAMES = {"code": "代码", "test": "测试", "rule": "规则", "hardware": "硬件或接线"}
_FACTS = re.compile(r"```facts\s*\n(.*?)\n```", re.S)


def board_test_file(m: DesignModule) -> str:
    return f"test/test_{m.id}.c"


def host_test_file(m: DesignModule) -> str:
    return f"test_host/test_{m.id}.c"


def test_file(m: DesignModule) -> str:
    return board_test_file(m) if m.layer == "hardware" else host_test_file(m)


@dataclass
class AgentOutput:
    files: dict[str, str] = field(default_factory=dict)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    facts: list[Rule] = field(default_factory=list)          # 编码 Agent 从手册里新用到的事实（待核对规则）
    fact_errors: list[str] = field(default_factory=list)
    unterminated: int = 0


def parse_output(text: str, allowed: set[str]) -> AgentOutput:
    """只收 allowed 里的文件（设计分给本模块的文件，或本模块的测试文件）。"""
    out = AgentOutput()
    starts = set()
    for m in FILE_BLOCK.finditer(text):
        starts.add(m.start())
        path = m["path"].strip().strip("`'\"").replace("\\", "/").removeprefix("main/")
        if path not in allowed:
            out.rejected.append((path, "不是分给本模块的文件：" + "、".join(sorted(allowed))))
            continue
        body = m["body"]
        out.files[path] = body if body.endswith("\n") else body + "\n"
    out.unterminated = sum(1 for m in OPEN_TAG.finditer(text) if m.start() not in starts)
    fm = _FACTS.search(text)
    if fm:
        out.facts, out.fact_errors = parse_rules("```json\n" + fm.group(1) + "\n```")
    return out


def _deps_block(dep_headers: dict[str, str]) -> str:
    return "\n\n".join(f"`main/{p}`：\n\n```c\n{c.rstrip()}\n```" for p, c in sorted(dep_headers.items())) or "（无）"


def _module_scopes(m: DesignModule) -> set[str]:
    return set(m.scopes)          # 逻辑层为空集：只放 scope 为 all 的规则


def _common_constraints(profile: HardwareProfile, m: DesignModule) -> list[str]:
    return [f"开发框架 {profile.framework.value} {profile.framework_version.value}，目标芯片 {profile.chip.value}",
            "引脚和器件地址只能用 include/hw_config.h 里的 FF_PIN_* / FF_*_ADDR 宏，不能直接写数字",
            "不能修改 include/hw_config.h（程序按硬件档案生成）",
            "只能写这些文件：" + "、".join(f"main/{f}" for f in m.files)]


# ---------------------------------------------------------------- 编码

def coder_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None, design: Design,
                   module: DesignModule, dep_headers: dict[str, str], where: str = "") -> list[dict]:
    system = system_prompt("coder_idf.md", profile, rules, _module_scopes(module))
    layer = "硬件层（受硬件规则约束，要上板测试）" if module.layer == "hardware" else "逻辑层（与硬件无关，在电脑上测试）"
    b = Brief(intent=f"实现模块 {module.id}：{module.title}", location=where or f"编码 › {module.id}")
    b.ref(f"module {module.id}", json.dumps(module.to_dict(), ensure_ascii=False, indent=2), "json")
    b.constraints += _common_constraints(profile, module) + [f"本模块属于{layer}"]
    if module.layer == "logic":
        b.constraints.append("逻辑层不能包含任何 ESP-IDF 驱动头文件或 hw_config.h，只用标准 C（C11）")
    b.attach("需求文档", requirement)
    b.attach("整体设计", design_json(design))
    b.attach("依赖模块的公共头文件（只读）", _deps_block(dep_headers))
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def coder_repair_message(attempt: int, max_attempts: int, problems: str, diagnosis: dict | None = None) -> str:
    lines = [f"第 {attempt} 次没有通过（最多 {max_attempts} 次）。问题如下：", "", problems.strip()]
    if diagnosis and diagnosis.get("instruction"):
        lines += ["", f"诊断 Agent 的修改指令：{diagnosis['instruction']}"]
    lines += ["", "请输出修改后的完整文件（没改动的文件可以不输出）。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 写测试

def tester_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None, module: DesignModule,
                    header: str, dep_headers: dict[str, str], where: str = "") -> list[dict]:
    board = module.layer == "hardware"
    system = system_prompt("tester_board.md" if board else "tester_host.md", profile, rules, _module_scopes(module))
    b = Brief(intent=f"为模块 {module.id} 写{'上板' if board else '电脑上的'}单元测试",
              location=where or f"测试 › {module.id}")
    b.ref(f"module {module.id}", json.dumps(module.to_dict(), ensure_ascii=False, indent=2), "json")
    b.constraints += [f"只能写 main/{test_file(module)}", "你看不到实现代码，只根据需求、接口、验收标准和规则写测试"]
    if board:
        b.constraints.append("读寄存器、计时用平台提供的 ff_test.h；寄存器地址用 ff_regs.h 里的 FF_REG_* 宏")
    b.attach("需求文档", requirement)
    b.attach(f"本模块公共头文件 main/{module.header}", f"```c\n{header.rstrip()}\n```")
    b.attach("依赖模块的公共头文件（只读）", _deps_block(dep_headers))
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def tester_repair_message(attempt: int, max_attempts: int, problems: str, src_files: dict[str, str],
                          diagnosis: dict | None = None) -> str:
    """交给测试 Agent 的报告和诊断指令先删去实现代码片段。"""
    body = scrub_for_tester(problems, src_files)
    instr = scrub_for_tester(diagnosis.get("instruction", ""), src_files) if diagnosis else ""
    lines = [f"第 {attempt} 次没有通过（最多 {max_attempts} 次），诊断认为问题出在测试。", "", body.strip()]
    if instr:
        lines += ["", f"修改指令：{instr}"]
    lines += ["", f"请输出修改后的完整测试文件。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 诊断

def diagnoser_messages(profile: HardwareProfile, rules: RuleSet | None, module: DesignModule, code: dict[str, str],
                       tests: dict[str, str], problems: str, where: str = "") -> list[dict]:
    system = system_prompt("diagnoser_idf.md", profile, rules, _module_scopes(module))
    b = Brief(intent=f"判断模块 {module.id} 没通过的原因出在代码、测试、规则还是硬件", location=where or f"诊断 › {module.id}")
    for p, c in sorted(code.items()):
        b.ref(f"file main/{p}", c, "c")
    for p, c in sorted(tests.items()):
        b.ref(f"test main/{p}", c, "c")
    b.attach("检查结果", problems)
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def parse_diagnosis(data: dict, rules: RuleSet | None = None) -> dict:
    blame = data.get("blame")
    if blame not in BLAMES:
        raise ValueError(f"blame 只能是 {', '.join(BLAMES)}")
    out = {"blame": blame, "instruction": str(data.get("instruction", "")).strip(),
           "evidence": str(data.get("evidence", "")).strip(), "rule": str(data.get("rule", "")).strip()}
    if not out["instruction"]:
        raise ValueError("缺少 instruction（修改指令）")
    if blame == "rule":
        if not out["rule"]:
            raise ValueError("blame 为 rule 时必须写出是哪条规则（rule）")
        if rules is not None and out["rule"] not in rules.rules:
            raise ValueError(f"规则 {out['rule']} 不存在")
    return out

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


def format_reminder(missing: list[str], rejected: list[tuple[str, str]] | None = None) -> str:
    """回复里没找到要交的文件块时，当场提醒格式（便宜模型常把代码写成 ```c 代码块）。"""
    lines = ["你的回复里没有找到这些文件：" + "、".join(f"main/{p}" for p in missing) + "。",
             "程序只认下面这种文件块，不认 ```c 代码块。请把每个文件完整地按这个格式重新输出：", ""]
    for p in missing:
        lines += [f"<<<FILE {p}", "…完整文件内容…", ">>>FILE", ""]
    if rejected:
        lines.append("这些文件块没有被收下：" + "；".join(f"{p}（{why}）" for p, why in rejected[:5]))
    return "\n".join(lines).rstrip()


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
                   module: DesignModule, dep_headers: dict[str, str], where: str = "",
                   locked_header: str | None = None) -> list[dict]:
    """locked_header：本模块的公共头文件已经定好（评测题的接口），编码 Agent 只能照着实现、不能改。"""
    system = system_prompt("coder_idf.md", profile, rules, _module_scopes(module))
    layer = "硬件层（受硬件规则约束，要上板测试）" if module.layer == "hardware" else "逻辑层（与硬件无关，在电脑上测试）"
    b = Brief(intent=f"实现模块 {module.id}：{module.title}", location=where or f"编码 › {module.id}")
    b.ref(f"module {module.id}", json.dumps(module.to_dict(), ensure_ascii=False, indent=2), "json")
    b.constraints += _common_constraints(profile, module) + [f"本模块属于{layer}"]
    if module.layer == "logic":
        b.constraints.append("逻辑层不能包含任何 ESP-IDF 驱动头文件或 hw_config.h，只用标准 C（C11）")
    b.attach("需求文档", requirement)
    b.attach("整体设计", design_json(design))
    if locked_header is not None:
        b.constraints.append(f"main/{module.header} 已经定好，不能修改，也不要输出它")
        b.attach(f"本模块公共头文件 main/{module.header}（锁定）", f"```c\n{locked_header.rstrip()}\n```")
    b.attach("依赖模块的公共头文件（只读）", _deps_block(dep_headers))
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def coder_repair_message(attempt: int, max_attempts: int, problems: str, diagnosis: dict | None = None) -> str:
    lines = [f"第 {attempt} 次没有通过（最多 {max_attempts} 次）。问题如下：", "", problems.strip()]
    if diagnosis and diagnosis.get("instruction"):
        lines += ["", f"诊断 Agent 的修改指令：{diagnosis['instruction']}"]
    lines += ["", "请输出修改后的完整文件（没改动的文件可以不输出）。"]
    return "\n".join(lines)


# ---------------------------------------------------------------- 写测试

# 测试 Agent 的通用检查清单（第 125 条）：只写通用的测试设计常识（等价类、边界、错误路径、可重复性、独立核对），
# 不写从测试题标准答案或隐藏测试里看来的具体知识
TEST_CHECKLIST = """- 接口里每个参数的每一类取值都至少测一次（每个枚举值、每种模式），不只测默认值；
- 边界值和非法值：最小、最大、刚好越界、空指针；非法参数要断言返回规定的错误码，而且不改动已有状态；
- 需求里每一条带数字的要求（频率、个数、时长、精度、范围）都按数字断言，并写清允许的误差；
- 初始化、反初始化、再初始化以后还能正常工作；重复调用、调用顺序不对时的行为符合需求；
- 能独立观察到的东西（寄存器、引脚电平、收到的数据）就自己读出来，和被测函数给出的结果互相核对，不要只信被测函数的返回值；
- 和时间有关的要求（超时、等待、采样率）要测时间本身，不只测"最后有结果"；
- 每个断言都要能在实现写错时失败：想一想"如果实现把这里写错了，这条断言会不会发现"。"""


def tester_mutant_message(survived, total: int) -> str:
    """变异体补测试（第 125 条）：告诉测试 Agent 哪些改动它的测试没发现。只描述改了什么，不给实现代码。"""
    lines = [f"你的测试已经通过。平台把被测代码故意改坏了 {total} 处，分别用你的测试去测，"
             f"下面 {len(survived)} 处改动你的测试没有发现（改坏了测试照样通过）：", ""]
    lines += [f"{i}. {mu.describe}" for i, mu in enumerate(survived, 1)]
    lines += ["", "请补充或加强测试，让这些改动会让测试失败（按需求和接口该有的行为来断言，不要猜实现细节）。"
                  "如果你确定某处改动按需求本来就不影响行为（例如多余的等待），说明理由，不用硬写测试。",
              "请输出修改后的完整测试文件。"]
    return "\n".join(lines)


def platform_headers_block(profile: HardwareProfile, rules: RuleSet | None, scopes) -> str:
    """测试 Agent 能用的宏：hw_config.h 全文 + ff_regs.h 里的寄存器宏名单（基线评测发现它常编出不存在的宏、
    或者忘了 include，测试文件编译不过，第 122 条）。"""
    from ..target.esp_idf import hw_config_header
    from ..target.harness import regs_header
    regs = [ln for ln in regs_header(rules, None, set(scopes) if scopes else None).splitlines() if "FF_REG_" in ln]
    names = sorted({ln.split("FF_REG_", 1)[1].split()[0].rstrip("：") for ln in regs})
    reg_txt = ("\n".join(f"#define FF_REG_{n} ..." for n in names) if names else
               "/* 这个模块没有可用的 FF_REG_* 宏：不要用 FF_REG_ 开头的名字 */")
    return (f"```c\n/* main/include/hw_config.h */\n{hw_config_header(profile).rstrip()}\n```\n\n"
            f"```c\n/* main/include/ff_regs.h（地址由平台按芯片资料生成） */\n{reg_txt}\n```")


def tester_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None, module: DesignModule,
                    header: str, dep_headers: dict[str, str], where: str = "") -> list[dict]:
    board = module.layer == "hardware"
    system = system_prompt("tester_board.md" if board else "tester_host.md", profile, rules, _module_scopes(module))
    b = Brief(intent=f"为模块 {module.id} 写{'上板' if board else '电脑上的'}单元测试",
              location=where or f"测试 › {module.id}")
    b.ref(f"module {module.id}", json.dumps(module.to_dict(), ensure_ascii=False, indent=2), "json")
    b.constraints += [f"只能写 main/{test_file(module)}", "你看不到实现代码，只根据需求、接口、验收标准和规则写测试"]
    b.attach("测试要点（通用检查清单）", TEST_CHECKLIST)
    if board:
        b.constraints.append("读寄存器、计时用平台提供的 ff_test.h；片内外设寄存器地址用 ff_regs.h 里的 FF_REG_* 宏")
        b.constraints.append('用到的宏必须在下面"平台头文件"里真的存在，并且自己 #include 对应的头文件（hw_config.h、ff_regs.h）；'
                             "外接器件（I2C 传感器等）的寄存器没有宏，按需求和规则里写的地址直接写数字")
    b.attach("需求文档", requirement)
    if board:
        b.attach("平台头文件（测试能用的宏只有这些）", platform_headers_block(profile, rules, _module_scopes(module)))
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


def diagnosis_format_reminder(problem: str) -> str:
    """诊断回复不合格时的补交要求：说清哪里不对、要什么格式。"""
    return (f"上一条回复没法用：{problem}。请只输出一个 ```json 代码块，不要别的文字，形如\n"
            '```json\n{"blame": "code", "rule": "", "evidence": "一句话证据", "instruction": "一句话修改指令"}\n```\n'
            f"blame 只能是 {'、'.join(BLAMES)}；instruction 必须写；字符串里不要出现未转义的双引号和换行。")


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


# ---------------------------------------------------------------- 审查（DECISIONS 第 117 条：工作流里可以加的步骤）

def reviewer_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None, module: DesignModule,
                      code: dict[str, str], report: str, where: str = "") -> list[dict]:
    """检查已经通过后再审一遍代码。只给实现代码和检查结果，不给测试代码（审的是实现）。"""
    system = system_prompt("reviewer_idf.md", profile, rules, _module_scopes(module))
    b = Brief(intent=f"审查模块 {module.id} 的实现（检查已全部通过），输出审查 JSON", location=where or f"审查 › {module.id}")
    b.ref(f"module {module.id}", json.dumps(module.to_dict(), ensure_ascii=False, indent=2), "json")
    for p, c in sorted(code.items()):
        b.ref(f"file main/{p}", c, "c")
    b.attach("需求文档", requirement)
    b.attach("检查结果", report)
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


def review_fix_message(issues: list[dict]) -> str:
    lines = ["检查已经通过，但审查 Agent 指出了下面这些必须修改的问题。请修改后输出改动的完整文件（没改动的文件不用输出）。",
             "注意不要破坏已经通过的行为：改完会重新检查，检查不过就退回改之前的版本。", ""]
    for i in issues:
        where = f"{i.get('file', '')}" + (f":{i['line']}" if i.get("line") else "")
        lines.append(f"- [{i.get('severity', '')}] {where} {i.get('rule', '')}：{i.get('message', '')}")
    return "\n".join(lines)


def human_fix_message(comment: str) -> str:
    return ("负责人看了这个模块的代码和检查结果，意见如下。请按意见修改，输出改动的完整文件（没改动的文件不用输出）。\n\n"
            + comment.strip())

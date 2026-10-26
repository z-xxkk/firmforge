"""多 Agent 角色的上下文（ARCHITECTURE §5.2）：每个角色看到什么、能写哪里、输出什么格式。

隔离规则：
- 只读取任务的 spec.md 和 include/*.h，永远不读 hidden_tests/、reference/、mutants/；
- 测试 Agent 的上下文里不能出现 src/*.c 的内容：只给需求、公共头文件和规划里的接口原型；
  交给它的报告摘录和诊断指令都先经过 scrub_for_tester()；
- 给 Agent 看的判定报告一律先经过 Report.for_agent()。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..orchestrator.dag import Module, Plan, public_functions
from ..tasks import Task
from ..verifier.layers import TITLES
from ..verifier.report import Report
from .brief import Brief, Ref
from .context import _budget_text, load_prompt, public_headers, report_details

CODER_DIRS = ("src",)
TESTER_DIRS = ("tests",)
CODE_REMOVED = "（此处是实现代码，已删去）"
# gcc 报错里引用源码的行：" 12 |   x = y;" 和下一行的 "    |   ^~~"
_GCC_QUOTE = re.compile(r"^\s*\d*\s*\|")
_MIN_PIECE = 10               # 更短的片段（如 return 0）太常见，不当作实现代码


def _spec(task: Task) -> str:
    return task.spec_path.read_text(encoding="utf-8").strip()


def _headers_block(task: Task) -> str:
    return "\n\n".join(f"`include/{h.name}`：\n\n```c\n{h.read_text(encoding='utf-8').rstrip()}\n```"
                       for h in public_headers(task)) or "（无）"


def _module_json(m: Module) -> str:
    return json.dumps(m.to_dict(), ensure_ascii=False, indent=2)


def _files_block(files: dict[str, str]) -> str:
    return "\n\n".join(f"`{p}`：\n\n```c\n{c.rstrip()}\n```" for p, c in sorted(files.items())) or "（无）"


def _system(name: str, task: Task, **extra) -> str:
    return load_prompt(name).safe_substitute(forbidden="、".join(task.forbidden_symbols),
                                             budget=_budget_text(task), **extra)


def constraints(task: Task, *extra: str) -> list[str]:
    """任务单的「约束」一节：每个 Agent 看到同一份。"""
    return ["目标芯片 STM32F103（Cortex-M3），C11，裸机，不能用动态内存、VLA、alloca，禁止递归",
            f"禁止使用的符号：{'、'.join(task.forbidden_symbols) or '（无）'}",
            f"资源预算（arm-none-eabi-gcc -Os 下统计，全部模块合计）：{_budget_text(task)}",
            "include/ 下的公共头文件由任务锁定，只读，不能修改", *extra]


def _where(where: str, *parts: str) -> str:
    return " › ".join(x for x in (where, *parts) if x)


def _task_attachments(b: Brief, task: Task) -> Brief:
    return b.attach("需求文档 spec.md", _spec(task)).attach("公共接口头文件（只读，位于 include/）", _headers_block(task))


def _knowledge_text(chunks) -> str:
    return "\n\n".join(c.render() for c in chunks)


def _experience_text(exps) -> str:
    return "之前同类问题是这样修好的，仅供参考：\n" + "\n".join(e.render()for e in exps) if exps else ""


def _user(b: Brief) -> dict:
    return {"role": "user", "content": b.render()}


# ---------------------------------------------------------------- 规划

def planner_messages(task: Task, max_modules: int, prompt: str = "planner.md", knowledge=(), where: str = "") -> list[dict]:
    funcs = "\n".join(f"- `{f}`" for f in public_functions(task)) or "（无）"
    b = Brief(f"把「{task.title}」的需求拆成模块（最多 {max_modules} 个），写明每个模块的职责、文件、接口和依赖，输出规划 JSON。",
              _where(where, "plan（规划）"), constraints=constraints(task))
    b.ref("public_functions 必须由某个模块负责实现的公共函数", funcs)
    _task_attachments(b, task).attach("参考资料（知识库）", _knowledge_text(knowledge))
    return [{"role": "system", "content": load_prompt(prompt).safe_substitute(max_modules=max_modules)}, _user(b)]


def planner_retry_message(problems: list[str]) -> str:
    return Brief("你的规划没有通过检查，请修正后重新输出完整的规划 JSON。",
                 refs=[Ref("plan_check 检查发现的问题", "\n".join(f"- {p}" for p in problems))]).render()


def planner_feedback_message(comment: str) -> str:
    return Brief("负责人没有确认这份规划，请按意见修改后重新输出完整的规划 JSON。",
                 refs=[Ref("human 负责人的意见", comment)]).render()


def planner_replan_message(module_id: str, failures: list[str], frozen: list[str]) -> str:
    """升级阶梯第 3 级：模块反复修不好，回到规划层修改它的接口或拆分方式。"""
    lines = "\n".join(f"- {x}" for x in failures) or "- （没有诊断记录）"
    keep = "、".join(frozen) or "（无）"
    return Brief(f"模块 {module_id} 多次修改仍然没有通过检查。请重新规划：可以修改 {module_id} 的接口或把它拆成几个更小的模块，"
                 "受影响的下游模块可以一起调整。输出完整的规划 JSON。",
                 "升级第 3 级（重新规划）",
                 refs=[Ref(f"failures {module_id} 的失败要点", lines)],
                 constraints=[f"已经完成或正在进行的模块必须原样保留（id、文件、职责、接口都不能变）：{keep}"]).render()


# ---------------------------------------------------------------- 编码

def coder_messages(task: Task, plan: Plan, module: Module, dep_files: dict[str, str],
                   prompt: str = "coder_module.md", knowledge=(), experiences=(), where: str = "",
                   attempt: int = 1) -> list[dict]:
    """首次编码：需求 + 公共头文件 + 完整规划（DECISIONS 第 33 条）+ 本模块 + 依赖模块的头文件。"""
    dep_headers = {p: c for p, c in dep_files.items() if p.endswith(".h")}
    b = Brief(f"编写模块 {module.id}（{module.title}）的实现，输出这些文件：{', '.join(module.files)}。",
              _where(where, "module_impl", f"code（模块 {module.id}，第 {attempt} 次）"), constraints=constraints(task))
    b.ref(f"module {module.id} 你负责的模块", _module_json(module), "json")
    b.ref("plan 完整规划（全部模块的划分、职责与接口）", plan.to_json(), "json")
    _task_attachments(b, task)
    b.attach("已完成的依赖模块的头文件", _files_block(dep_headers) if dep_headers else "")
    b.attach("参考资料（知识库）", _knowledge_text(knowledge)).attach("过往经验", _experience_text(experiences))
    return [{"role": "system", "content": _system(prompt, task)}, _user(b)]


def coder_repair_message(report: Report, attempt: int, max_attempts: int, diagnosis: dict | None,
                         current: dict[str, str], rejected: list[tuple[str, str]] | None = None,
                         workdir: Path | None = None, experiences=()) -> str:
    agent_report = report.for_agent()
    layer = agent_report.failed_layer or "无"
    b = Brief("判定器检查没有通过，请按诊断结论修改，按同样的格式输出需要修改的完整文件。",
              f"repair（修正，第 {attempt}/{max_attempts} 次之后；首个失败层：{layer} {TITLES.get(layer, '—')}）")
    if diagnosis:
        b.ref(f"diagnosis 第 {attempt} 次", f"- 根因：{diagnosis.get('summary', '')}\n- 修改指令：{diagnosis.get('instructions', '')}")
    for path, code in sorted(current.items()):
        b.ref(f"file {path} 本模块当前的代码", code, "c")
    if rejected:
        b.ref("rejected 上一轮被拒绝写入的文件", "\n".join(f"- {p}：{why}" for p, why in rejected))
    b.attach("判定报告", report_details(agent_report, workdir)).attach("过往经验", _experience_text(experiences))
    return b.render()


def coder_rewrite_message(failures: list[str], failed_layer: str | None) -> str:
    """升级阶梯第 2 级：换一个 Agent 从头重写，只给失败要点，不给旧代码（DECISIONS 第 32 条）。"""
    lines = "\n".join(f"- {x}" for x in failures) or "- （没有诊断记录）"
    return Brief("之前另一位工程师写的实现多次修改仍然没有通过检查。请你从头重写本模块，避开下面这些问题，不要沿用之前的思路。",
                 f"升级第 2 级（换 Agent 从头重写；最后卡在 {failed_layer or '未知'} 层）",
                 refs=[Ref("failures 失败要点", lines)]).render()


def human_hint_message(hint: str) -> str:
    return Brief("负责人看过之后给出了提示，请按提示修改，按同样的格式输出需要修改的完整文件。",
                 "升级第 4 级（转人工）", refs=[Ref("human 负责人的提示", hint)]).render()


def experience_messages(task: Task, module: Module, pattern: str, before: Report, diagnosis: dict,
                        prompt: str = "experience.md") -> list[dict]:
    b = Brief(f"按下面的指令修改后，模块 {module.id} 的失败层前进了（或者直接通过了）。请总结经验。",
              f"任务 {task.title} › 模块 {module.id}（类别 {module.task_type}）")
    b.ref("failing_keys 修改前失败的 key", pattern)
    b.ref("diagnosis 诊断与修改指令", f"- 根因：{diagnosis.get('summary', '')}\n- 修改指令：{diagnosis.get('instructions', '')}")
    b.attach("修改前的判定报告", report_details(before.for_agent()))
    return [{"role": "system", "content": load_prompt(prompt).template}, _user(b)]


def review_fix_message(issues: list[dict], current: dict[str, str]) -> str:
    lines = [f"- [{i.get('severity')}] {i.get('file', '')}:{i.get('line', '')} {i.get('rule', '')}：{i.get('message', '')}"
             for i in issues]
    b = Brief("代码审查发现了必须修改的问题。请只修改这些问题，不要改变其他行为，按同样的格式输出需要修改的完整文件。",
              "review（审查之后的修改）", refs=[Ref("review 审查意见", "\n".join(lines))])
    for path, code in sorted(current.items()):
        b.ref(f"file {path} 本模块当前的代码", code, "c")
    return b.render()


# ---------------------------------------------------------------- 测试

def tester_messages(task: Task, plan: Plan, module: Module, prompt: str = "tester.md", where: str = "") -> list[dict]:
    """只有需求、公共头文件和规划（含接口原型）；没有任何实现代码。"""
    b = Brief(f"为模块 {module.id}（{module.title}）编写单元测试，输出 `tests/{module.test_file}`。",
              _where(where, "module_impl", f"test（模块 {module.id}）"), constraints=constraints(task))
    b.ref(f"module {module.id} 你要测试的模块", _module_json(module), "json")
    b.ref("plan 完整规划", plan.to_json(), "json")
    _task_attachments(b, task)
    return [{"role": "system", "content": _system(prompt, task, test_file=module.test_file)}, _user(b)]


def scrub_for_tester(text: str, src_files: dict[str, str], keep: str = "") -> str:
    """删去报错里引用的源码行，以及与实现代码逐字相同的行，测试 Agent 才真正看不到实现。

    keep 里出现过的行（测试 Agent 自己的代码、公共头文件）不删。
    """
    keep_text = " ".join(keep.split())
    # 实现代码按行、按语句切成片段；夹在一行文字中间的片段（如诊断指令里引用的一句代码）也要删
    pieces = set()
    for c in src_files.values():
        for ln in c.splitlines():
            pieces.add(" ".join(ln.split()))
            pieces.update(" ".join(x.split()) for x in re.split(r"[;{}]", ln))
    pieces = sorted((x for x in pieces if len(x) >= _MIN_PIECE and x not in keep_text), key=len, reverse=True)
    out = []
    for ln in text.splitlines():
        if _GCC_QUOTE.match(ln):                  # gcc 报错引用的整行源码
            if not out or out[-1] != CODE_REMOVED:
                out.append(CODE_REMOVED)
            continue
        flat = " ".join(ln.split())
        hits = [x for x in pieces if x in flat]
        if hits:
            for x in hits:
                flat = flat.replace(x, CODE_REMOVED)
            ln = flat
        out.append(ln)
    return "\n".join(out)


def tester_repair_message(report: Report, attempt: int, max_attempts: int, diagnosis: dict | None,
                          current_test: dict[str, str], src_files: dict[str, str],
                          workdir: Path | None = None, headers: str = "") -> str:
    agent_report = report.for_agent()
    layer = agent_report.failed_layer or "无"
    diag = ""
    if diagnosis:
        diag = f"- 根因：{diagnosis.get('summary', '')}\n- 修改指令：{diagnosis.get('instructions', '')}"
    own = "\n".join(current_test.values())
    keep = own + "\n" + headers
    b = Brief("判定器检查没有通过，诊断认为问题出在测试上。请对照需求文档核实期望值后修改，按同样的格式输出完整的测试文件。",
              f"repair（修正测试，第 {attempt}/{max_attempts} 次之后；首个失败层：{layer} {TITLES.get(layer, '—')}）")
    if diag:
        b.ref(f"diagnosis 第 {attempt} 次", scrub_for_tester(diag, src_files, keep=keep))
    for path, code in sorted(current_test.items()):
        b.ref(f"file {path} 你当前的测试代码", code, "c")
    b.attach("判定报告", scrub_for_tester(report_details(agent_report, workdir), src_files, keep=keep))
    return b.render()


# ---------------------------------------------------------------- 审查与诊断

def reviewer_messages(task: Task, module: Module, code: dict[str, str], report: Report,
                      prompt: str = "reviewer.md", where: str = "") -> list[dict]:
    b = Brief(f"审查模块 {module.id} 的实现（客观检查已全部通过），输出审查 JSON。",
              _where(where, "module_impl", f"review（模块 {module.id}）"), constraints=constraints(task))
    b.ref(f"module {module.id}", _module_json(module), "json")
    for path, c in sorted(code.items()):
        b.ref(f"file {path} 实现代码", c, "c")
    _task_attachments(b, task).attach("判定报告", report_details(report.for_agent()))
    return [{"role": "system", "content": load_prompt(prompt).template}, _user(b)]


def diagnoser_messages(task: Task, plan: Plan, module: Module, code: dict[str, str], tests: dict[str, str],
                       report: Report, signatures: list[str | None], workdir: Path | None = None,
                       prompt: str = "diagnoser.md", where: str = "") -> list[dict]:
    history = "、".join(s or "（无）" for s in signatures) or "（无）"
    repeated = len(signatures) >= 2 and signatures[-1] is not None and signatures[-1] == signatures[-2]
    b = Brief(f"模块 {module.id} 第 {len(signatures)} 次没有通过检查，判断错在实现、测试、接口还是需求，给出修改指令，输出诊断 JSON。"
              + ("最近两次签名相同：上次的修改方向不对，这次必须换一个思路。" if repeated else ""),
              _where(where, "module_impl", f"repair › 诊断（模块 {module.id}，第 {len(signatures)} 次之后）"),
              constraints=constraints(task))
    b.ref(f"module {module.id} 出问题的模块", _module_json(module), "json")
    b.ref("plan 完整规划", plan.to_json(), "json")
    for path, c in sorted(code.items()):
        b.ref(f"file {path} 实现代码", c, "c")
    for path, c in sorted(tests.items()):
        b.ref(f"file {path} 单元测试代码", c, "c")
    b.ref("signatures 本模块历次的错误签名（从早到晚）", history)
    _task_attachments(b, task).attach("判定报告", report_details(report.for_agent(), workdir))
    return [{"role": "system", "content": load_prompt(prompt).template}, _user(b)]


BLAMES = ("code", "test", "interface", "spec")


def parse_diagnosis(data: dict) -> dict:
    blame = data.get("blame")
    if blame not in BLAMES:
        raise ValueError(f"blame 必须是 {', '.join(BLAMES)} 之一，收到 {blame!r}")
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return {"blame": blame, "summary": str(data.get("summary", "")),
            "instructions": str(data.get("instructions", "")), "confidence": min(1.0, max(0.0, conf))}


def parse_review(data: dict) -> dict:
    verdict = data.get("verdict")
    if verdict not in ("ok", "needs_fix"):
        raise ValueError(f"verdict 必须是 ok 或 needs_fix，收到 {verdict!r}")
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        raise ValueError("issues 必须是列表")
    clean = [i for i in issues if isinstance(i, dict)]
    return {"verdict": verdict, "issues": clean}


def json_retry_message(error: str) -> str:
    return Brief(f"没能解析你的输出：{error}。请只输出一个符合要求的 ```json 代码块。").render()

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


def _module_block(m: Module) -> str:
    return "```json\n" + json.dumps(m.to_dict(), ensure_ascii=False, indent=2) + "\n```"


def _files_block(files: dict[str, str]) -> str:
    return "\n\n".join(f"`{p}`：\n\n```c\n{c.rstrip()}\n```" for p, c in sorted(files.items())) or "（无）"


def _system(name: str, task: Task, **extra) -> str:
    return load_prompt(name).safe_substitute(forbidden="、".join(task.forbidden_symbols),
                                             budget=_budget_text(task), **extra)


# ---------------------------------------------------------------- 规划

def planner_messages(task: Task, max_modules: int, prompt: str = "planner.md") -> list[dict]:
    funcs = "\n".join(f"- `{f}`" for f in public_functions(task)) or "（无）"
    user = (f"# 任务：{task.title}\n\n## 需求文档\n\n{_spec(task)}\n\n"
            f"## 公共接口头文件（只读，位于 include/）\n\n{_headers_block(task)}\n\n"
            f"## 必须由某个模块负责实现的公共函数\n\n{funcs}\n\n请输出规划 JSON。")
    return [{"role": "system", "content": load_prompt(prompt).safe_substitute(max_modules=max_modules)},
            {"role": "user", "content": user}]


def planner_retry_message(problems: list[str]) -> str:
    return ("你的规划没有通过检查，问题如下：\n" + "\n".join(f"- {p}" for p in problems)
            + "\n\n请修正后重新输出完整的规划 JSON。")


def planner_feedback_message(comment: str) -> str:
    return f"负责人没有确认这份规划，意见如下：\n\n{comment}\n\n请按意见修改后重新输出完整的规划 JSON。"


# ---------------------------------------------------------------- 编码

def coder_messages(task: Task, plan: Plan, module: Module, dep_files: dict[str, str],
                   prompt: str = "coder_module.md") -> list[dict]:
    """首次编码：需求 + 公共头文件 + 完整规划（DECISIONS 第 33 条）+ 本模块 + 依赖模块的头文件。"""
    dep_headers = {p: c for p, c in dep_files.items() if p.endswith(".h")}
    user = (f"# 任务：{task.title}\n\n## 需求文档\n\n{_spec(task)}\n\n"
            f"## 公共接口头文件（只读，位于 include/）\n\n{_headers_block(task)}\n\n"
            f"## 完整规划（全部模块的划分、职责与接口）\n\n```json\n{plan.to_json()}\n```\n\n"
            f"## 你负责的模块：{module.id}\n\n{_module_block(module)}\n\n"
            f"## 已完成的依赖模块的头文件\n\n{_files_block(dep_headers)}\n\n"
            f"请输出本模块的文件：{', '.join(module.files)}。")
    return [{"role": "system", "content": _system(prompt, task)}, {"role": "user", "content": user}]


def coder_repair_message(report: Report, attempt: int, max_attempts: int, diagnosis: dict | None,
                         current: dict[str, str], rejected: list[tuple[str, str]] | None = None,
                         workdir: Path | None = None) -> str:
    agent_report = report.for_agent()
    layer = agent_report.failed_layer or "无"
    parts = [f"判定器检查没有通过（第 {attempt} 次尝试，最多 {max_attempts} 次）。",
             f"首个失败层：{layer}（{TITLES.get(layer, '—')}）"]
    if diagnosis:
        parts.append(f"## 诊断结论\n\n- 根因：{diagnosis.get('summary', '')}\n"
                     f"- 修改指令：{diagnosis.get('instructions', '')}")
    parts.append(f"## 判定报告\n\n{report_details(agent_report, workdir)}")
    parts.append(f"## 本模块当前的代码\n\n{_files_block(current)}")
    if rejected:
        parts.append("上一轮这些文件被拒绝写入：\n" + "\n".join(f"- {p}：{why}" for p, why in rejected))
    parts.append("请按诊断结论修改，按同样的格式输出需要修改的完整文件。")
    return "\n\n".join(parts)


def review_fix_message(issues: list[dict], current: dict[str, str]) -> str:
    lines = [f"- [{i.get('severity')}] {i.get('file', '')}:{i.get('line', '')} {i.get('rule', '')}：{i.get('message', '')}"
             for i in issues]
    return ("代码审查发现了必须修改的问题：\n\n" + "\n".join(lines)
            + f"\n\n## 本模块当前的代码\n\n{_files_block(current)}\n\n"
              "请只修改这些问题，不要改变其他行为，按同样的格式输出需要修改的完整文件。")


# ---------------------------------------------------------------- 测试

def tester_messages(task: Task, plan: Plan, module: Module, prompt: str = "tester.md") -> list[dict]:
    """只有需求、公共头文件和规划（含接口原型）；没有任何实现代码。"""
    user = (f"# 任务：{task.title}\n\n## 需求文档\n\n{_spec(task)}\n\n"
            f"## 公共接口头文件（只读，位于 include/）\n\n{_headers_block(task)}\n\n"
            f"## 完整规划\n\n```json\n{plan.to_json()}\n```\n\n"
            f"## 你要测试的模块：{module.id}\n\n{_module_block(module)}\n\n"
            f"请输出 `tests/{module.test_file}`。")
    return [{"role": "system", "content": _system(prompt, task, test_file=module.test_file)},
            {"role": "user", "content": user}]


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
    parts = [f"判定器检查没有通过（第 {attempt} 次尝试，最多 {max_attempts} 次），诊断认为问题出在测试上。",
             f"首个失败层：{layer}（{TITLES.get(layer, '—')}）"]
    if diagnosis:
        parts.append(f"## 诊断结论\n\n- 根因：{diagnosis.get('summary', '')}\n"
                     f"- 修改指令：{diagnosis.get('instructions', '')}")
    parts.append(f"## 判定报告\n\n{report_details(agent_report, workdir)}")
    own = "\n".join(current_test.values())
    scrubbed = scrub_for_tester("\n\n".join(parts), src_files, keep=own + "\n" + headers)
    return (scrubbed + f"\n\n## 你当前的测试代码\n\n{_files_block(current_test)}\n\n"
            "请对照需求文档核实期望值后修改，按同样的格式输出完整的测试文件。")


# ---------------------------------------------------------------- 审查与诊断

def reviewer_messages(task: Task, module: Module, code: dict[str, str], report: Report,
                      prompt: str = "reviewer.md") -> list[dict]:
    user = (f"# 任务：{task.title}\n\n## 需求文档\n\n{_spec(task)}\n\n"
            f"## 公共接口头文件\n\n{_headers_block(task)}\n\n## 模块\n\n{_module_block(module)}\n\n"
            f"## 实现代码\n\n{_files_block(code)}\n\n## 判定报告\n\n{report_details(report.for_agent())}\n\n"
            "请输出审查 JSON。")
    return [{"role": "system", "content": load_prompt(prompt).template}, {"role": "user", "content": user}]


def diagnoser_messages(task: Task, plan: Plan, module: Module, code: dict[str, str], tests: dict[str, str],
                       report: Report, signatures: list[str | None], workdir: Path | None = None,
                       prompt: str = "diagnoser.md") -> list[dict]:
    history = "、".join(s or "（无）" for s in signatures) or "（无）"
    repeated = len(signatures) >= 2 and signatures[-1] is not None and signatures[-1] == signatures[-2]
    user = (f"# 任务：{task.title}\n\n## 需求文档\n\n{_spec(task)}\n\n"
            f"## 公共接口头文件\n\n{_headers_block(task)}\n\n"
            f"## 完整规划\n\n```json\n{plan.to_json()}\n```\n\n## 出问题的模块：{module.id}\n\n{_module_block(module)}\n\n"
            f"## 实现代码\n\n{_files_block(code)}\n\n## 单元测试代码\n\n{_files_block(tests)}\n\n"
            f"## 判定报告\n\n{report_details(report.for_agent(), workdir)}\n\n"
            f"## 本模块历次的错误签名（从早到晚）\n\n{history}"
            + ("\n\n**最近两次签名相同：上次的修改方向不对，这次必须换一个思路。**" if repeated else "")
            + "\n\n请输出诊断 JSON。")
    return [{"role": "system", "content": load_prompt(prompt).template}, {"role": "user", "content": user}]


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
    return f"没能解析你的输出：{error}。请只输出一个符合要求的 ```json 代码块。"

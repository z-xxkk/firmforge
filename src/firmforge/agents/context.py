"""构建发给 Agent 的上下文。

隔离规则：这里只读取任务的 spec.md 和 include/*.h，永远不读 hidden_tests/、reference/、mutants/。
给 Agent 看的判定报告一律先经过 Report.for_agent()。
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from .. import config
from ..tasks import Task
from ..verifier.layers import TITLES
from ..verifier.report import Report

MAX_DIAGS = 15
LOG_EXCERPT = 2000
# 日志里的绝对路径缩短成相对路径：省 token，也不把宿主机目录结构发给 LLM
_ABS_PREFIX = re.compile(r"(?:[A-Za-z]:)?(?:[/\\][^\s:'\"/\\]+)+[/\\](?=(?:src|tests|include|\.ff_build)[/\\])")


def shorten_paths(text: str, workdir: Path | None = None) -> str:
    """先按已知工作目录逐字替换（路径里有空格也能处理），剩下的再用正则兜底。"""
    if workdir is not None:
        for base in {str(Path(workdir)), str(Path(workdir).resolve())}:
            for sep in ("/", "\\"):
                text = text.replace(base + sep, "")
    return _ABS_PREFIX.sub("", text)


def load_prompt(name: str) -> Template:
    path = config.prompts_dir() / name
    return Template(path.read_text(encoding="utf-8"))


def _budget_text(task: Task) -> str:
    b = task.budget
    parts = []
    if b.flash_bytes is not None:
        parts.append(f"Flash（text+data）≤ {b.flash_bytes} 字节")
    if b.ram_bytes is not None:
        parts.append(f"静态 RAM（data+bss）≤ {b.ram_bytes} 字节")
    if b.stack_bytes is not None:
        parts.append(f"调用链最坏栈用量 ≤ {b.stack_bytes} 字节（禁止递归）")
    return "；".join(parts) if parts else "无"


def public_headers(task: Task) -> list[Path]:
    return sorted(task.include_dir.glob("*.h")) if task.include_dir.is_dir() else []


def coder_messages(task: Task) -> list[dict]:
    """单 Agent（写实现 + 测试）的初始消息：system + user。"""
    system = load_prompt("coder.md").safe_substitute(
        forbidden="、".join(task.forbidden_symbols), budget=_budget_text(task))
    headers = "\n\n".join(f"`include/{h.name}`：\n\n```c\n{h.read_text(encoding='utf-8').rstrip()}\n```"
                          for h in public_headers(task)) or "（无）"
    user = load_prompt("coder_task.md").safe_substitute(
        title=task.title, module=task.id, spec=task.spec_path.read_text(encoding="utf-8").strip(),
        headers=headers)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def report_details(report: Report, workdir: Path | None = None) -> str:
    """把给 Agent 看的报告整理成文字：失败层的诊断 + 日志摘录。"""
    agent_report = report.for_agent()
    lines = []
    for lr in agent_report.layers:
        mark = {"pass": "通过", "fail": "失败", "skipped": "跳过"}.get(lr.status, lr.status)
        metrics = "，".join(f"{k}={v}" for k, v in lr.metrics.items() if not isinstance(v, dict))
        lines.append(f"- {lr.title}（{lr.layer}）：{mark}" + (f"；{metrics}" if metrics else ""))
    failed = agent_report.layer(agent_report.failed_layer) if agent_report.failed_layer else None
    if failed is not None:
        diags = [d for d in failed.diagnostics if d.severity != "info"]
        lines += ["", "问题列表："]
        for d in diags[:MAX_DIAGS]:
            loc = f"{d.file}:{d.line}: " if d.file and d.line else (f"{d.file}: " if d.file else "")
            lines.append(f"- {loc}{d.message}")
        if len(diags) > MAX_DIAGS:
            lines.append(f"- …另有 {len(diags) - MAX_DIAGS} 条")
        for note in failed.notes:
            lines.append(f"- 说明：{note}")
        log = failed.log.strip()
        if log:
            excerpt = shorten_paths(log, workdir)[-LOG_EXCERPT:]
            lines += ["", "原始输出（末尾摘录）：", "```", excerpt, "```"]
    return "\n".join(lines)


def repair_message(report: Report, round_no: int, max_rounds: int,
                   rejected: list[tuple[str, str]] | None = None, workdir: Path | None = None) -> str:
    agent_report = report.for_agent()
    layer = agent_report.failed_layer or "无"
    text = load_prompt("coder_repair.md").safe_substitute(
        round=round_no, max_rounds=max_rounds, failed_layer=layer,
        failed_title=TITLES.get(layer, "—"), details=report_details(agent_report, workdir))
    if rejected:
        text += "\n\n上一轮这些文件被拒绝写入：\n" + "\n".join(f"- {p}：{why}" for p, why in rejected)
    return text


FORMAT_REMINDER = ("没有在你的回复里找到符合格式的文件块。请严格按下面的格式输出每个完整文件：\n\n"
                   "<<<FILE src/文件名.c\n…完整文件内容…\n>>>FILE")

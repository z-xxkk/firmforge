"""system prompt 拼接（DECISIONS 第 67 条）。

    固定开头（角色提示词文件）
    + 硬件档案（程序按固定格式生成）
    + 与当前模块有关的规则（一行一条，按适用范围筛选，只放人工确认过的）

全部由程序拼接，不经模型改写；同一个 Agent 处理同一个模块时开头完全相同，
DeepSeek 的输入缓存能命中（缓存命中的输入价约为未命中的 1/50，tasks_inbox/test_strategy_research.md）。
每一步具体做什么放在用户消息的任务单里（DECISIONS 第 62 条）。
"""

from __future__ import annotations

from ..agents.context import load_prompt
from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet

RULE_FLOOR = "approved"         # 人工确认过的规则才进 system prompt


def system_prompt(role_prompt: str, profile: HardwareProfile, rules: RuleSet | None, scopes: set[str] | None,
                  **subst: str) -> str:
    """role_prompt：prompts/ 下的文件名。scopes 为 None 表示全部规则（例如设计 Agent 要看全局）。"""
    head = load_prompt(role_prompt).safe_substitute(**subst).rstrip()
    parts = [head, "## 硬件档案\n\n" + "\n".join(f"- {line}" for line in profile.summary_lines())]
    block = rules.prompt_block(scopes, RULE_FLOOR) if rules is not None else ""
    parts.append(block or "## 必须遵守的硬件规则\n\n（这部分没有已确认的规则。不确定的硬件事实不要猜，在说明里写出来。）")
    return "\n\n".join(parts) + "\n"


def rule_ids(rules: RuleSet | None, scopes: set[str] | None) -> list[str]:
    """本次放进 system prompt 的规则编号（记进事件，事后能查 Agent 当时看到了哪些规则）。"""
    return [r.id for r in rules.select(scopes, RULE_FLOOR)] if rules is not None else []

"""任务单格式（DECISIONS 第 62 条）：5 节固定顺序；自带标题的附件包进代码块；各角色的消息都用它。"""

import unittest

import helpers
from firmforge.agents import roles
from firmforge.agents.brief import SECTIONS, Brief, fence, parse_sections
from firmforge.agents.context import coder_messages
from firmforge.orchestrator.dag import single_module_plan
from firmforge.tasks import discover_tasks

TASKS = {t.id: t for t in discover_tasks(helpers.TASKS)}


class BriefTest(unittest.TestCase):
    def test_sections_in_order(self):
        b = Brief("做一件事", "工作流 x › 步骤 y", constraints=["只读"])
        b.ref("workflow.step repair", 'id = "repair"\nkind = "loop"', "toml").attach("需求", "# 标题\n\n## 约束\n\n内容")
        text = b.render()
        order = [text.index(f"## {s}") for s in SECTIONS]
        self.assertEqual(order, sorted(order))
        self.assertIn('[workflow.step repair]\n```toml\nid = "repair"', text)
        secs = parse_sections(text)
        self.assertEqual(list(secs), list(SECTIONS))              # 附件里的"## 约束"不会被当成任务单的节
        self.assertEqual(secs["约束"], "- 只读")
        self.assertIn("```markdown", secs["附件"])

    def test_fence_survives_backticks(self):
        self.assertTrue(fence("a\n```\nb").startswith("````"))

    def test_empty_parts_omitted(self):
        text = Brief("只有意图").attach("空", "  ").render()
        self.assertEqual(text, "## 意图\n\n只有意图")

    def test_role_messages_use_brief(self):
        task = TASKS["ringbuf"]
        plan = single_module_plan(task)
        m = plan.modules[0]
        msgs = [roles.planner_messages(task, 4, where="工作流 module_dev 1.0.0"),
                roles.coder_messages(task, plan, m, {}, where="工作流 module_dev 1.0.0"),
                roles.tester_messages(task, plan, m), coder_messages(task)]
        for ms in msgs:
            secs = parse_sections(ms[1]["content"])
            self.assertIn("意图", secs)
            self.assertIn("约束", secs)
            self.assertIn("需求文档 spec.md", secs["附件"])
        self.assertIn("工作流 module_dev 1.0.0 › module_impl › code（模块", parse_sections(msgs[1][1]["content"])["当前位置"])
        self.assertIn(f"[module {m.id}", msgs[2][1]["content"])


if __name__ == "__main__":
    unittest.main()

"""对话（DECISIONS 第 62 条）：需求草稿、头文件检查、@ 引用、追问、会话保存、让 AI 改工作流。全部用 FakeLLM。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.agents.brief import parse_sections
from firmforge.chat import (Conversation, Draft, check_headers, list_conversations, parse_draft,
                            propose_workflow_edit, resolve_refs)
from firmforge.config import ModelTier, Settings
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import dump_workflow, load_workflow

SETTINGS = Settings(models={"strong": ModelTier("fake-strong", 1, 1), "fast": ModelTier("fake-fast", 1, 1)})
HEADER = "#ifndef CNT_H\n#define CNT_H\n#include <stdint.h>\nvoid cnt_add(uint32_t *c);\n#endif\n"
DRAFT_REPLY = ("整理好了，地址在初始化时传入是我推断的。\n\n<<<FILE spec.md\n# 计数器\n\n## 约束\n\n不能用动态内存。\n>>>FILE\n\n"
               f"<<<FILE include/cnt.h\n{HEADER}>>>FILE\n")


class DraftTest(unittest.TestCase):
    def test_parse(self):
        d = parse_draft(DRAFT_REPLY)
        self.assertIn("# 计数器", d.spec)
        self.assertEqual(list(d.headers), ["include/cnt.h"])
        self.assertIsNone(parse_draft("请问寄存器有多少个？"))
        bad = parse_draft("<<<FILE include/../x.h\nx\n>>>FILE\n<<<FILE src/a.c\nx\n>>>FILE")
        self.assertIsNone(bad)

    @unittest.skipUnless(helpers.HAS_CC, "需要主机 gcc")
    def test_check_headers(self):
        self.assertEqual(check_headers(Draft("s", {"include/cnt.h": HEADER})), [])
        probs = check_headers(Draft("s", {"include/bad.h": "void f(int x\n"}))
        self.assertTrue(probs and probs[0].startswith("include/bad.h"))


class ConversationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def conv(self, script, **kw):
        llm = FakeLLM(script)
        return Conversation(llm, SETTINGS, chats_dir=self.dir / "chats", toolchain=helpers.TC, **kw), llm

    def test_question_then_draft_then_task(self):
        c, llm = self.conv(["从机地址要能改吗？", DRAFT_REPLY])
        r = c.send("帮我写一个计数器")
        self.assertIn("地址", r.text)
        self.assertIsNone(c.draft)
        c.send("按你推荐的来")
        secs = parse_sections(llm.calls[1]["messages"][-1]["content"])
        self.assertEqual(secs["意图"], "按你推荐的来")
        self.assertEqual(llm.calls[1]["messages"][1]["role"], "user")          # 带着之前的对话
        if helpers.HAS_CC:
            self.assertTrue(c.draft.ready, c.draft.problems)
            task = c.start_task(self.dir / "drafts")
            self.assertEqual(task.id, "cnt")
            self.assertTrue((task.include_dir / "cnt.h").is_file())
            self.assertFalse(task.hidden_tests_dir.exists() if hasattr(task, "hidden_tests_dir") else False)
        self.assertEqual(c.title, "帮我写一个计数器")
        saved = list_conversations(self.dir / "chats")
        self.assertEqual(saved[0]["title"], "帮我写一个计数器")
        again = Conversation.load(saved[0]["path"], FakeLLM([]), SETTINGS)
        self.assertEqual(len(again.turns), 4)
        self.assertEqual(again.draft.headers, c.draft.headers)

    def test_start_requires_ready_draft(self):
        c, _ = self.conv(["只有问题"])
        c.send("x")
        with self.assertRaises(ValueError):
            c.start_task(self.dir / "drafts")

    def test_followup_attaches_run_and_refs(self):
        run = self.dir / "run1"
        (run / "final" / "src").mkdir(parents=True)
        (run / "final" / "src" / "pdu.c").write_text("int pdu;\n", encoding="utf-8")
        (run / "final" / "report.md").write_text("# 报告\n\n单元测试没过", encoding="utf-8")
        (run / "run.json").write_text(json.dumps({"verdict": "fail", "config": "full", "modules": {
            "pdu": {"status": "failed", "attempts": 6, "delivered": 3, "notes": []}}}), encoding="utf-8")
        (run / "dag.json").write_text(json.dumps({"modules": [{"id": "pdu", "title": "PDU"}]}), encoding="utf-8")
        (run / "events.jsonl").write_text(json.dumps({"type": "diagnosis", "node_id": "pdu",
                                                      "data": {"attempt": 1, "blame": "code", "summary": "越界"}}) + "\n",
                                          encoding="utf-8")
        c, llm = self.conv(["因为越界检查漏了。"])
        c.send("为什么 @pdu 没过？看看 @src/pdu.c 和 @repair", run_dir=run, workflow=load_workflow())
        sent = llm.calls[0]["messages"][-1]["content"]
        secs = parse_sections(sent)
        self.assertIn("[module pdu]", secs["引用"])
        self.assertIn("[file src/pdu.c]", secs["引用"])
        self.assertIn("[workflow.step repair]", secs["引用"])
        self.assertIn("越界", secs["附件"])
        self.assertIn("结论：fail", secs["附件"])
        self.assertEqual(secs["当前位置"], "对话 › 运行结束后追问")

    def test_refs_cannot_escape_final_dir(self):
        run = self.dir / "run2"
        (run / "final").mkdir(parents=True)
        (self.dir / "secret.c").write_text("x", encoding="utf-8")
        self.assertEqual(resolve_refs("@../secret.c", run_dir=run), [])


class WorkflowEditTest(unittest.TestCase):
    def test_ai_edit_checked_before_use(self):
        wf = load_workflow()
        new_text = dump_workflow(wf).replace(f'version = "{wf.version}"', 'version = "9.9.9"')
        good = FakeLLM([f"改好了\n```toml\n{new_text}```"])
        new, problems, sent, _ = propose_workflow_edit(good, SETTINGS, wf, "加一步", selected=["repair"])
        self.assertEqual((new.version, problems), ("9.9.9", []))
        self.assertIn("[workflow.step repair 选中的步骤]", sent)
        broken = new_text.replace('role = "planner"', 'role = "coder"')
        new, problems, _, _ = propose_workflow_edit(FakeLLM([f"```toml\n{broken}```"]), SETTINGS, wf, "x")
        self.assertTrue(any("规划" in p for p in problems))
        new, problems, _, _ = propose_workflow_edit(FakeLLM(["不给代码"]), SETTINGS, wf, "x")
        self.assertIsNone(new)


if __name__ == "__main__":
    unittest.main()

"""阶段 4：升级阶梯、经验库、知识库、消融配置。全部用 FakeLLM，不联网。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge import cli
from firmforge.config import Settings
from firmforge.evaluation.configs import get_config
from firmforge.llm import FakeLLM
from firmforge.memory.bm25 import BM25, tokenize
from firmforge.memory.experience import ExperienceBase, clip_fix
from firmforge.memory.knowledge import KnowledgeBase, split_markdown
from firmforge.orchestrator.engine import FULL, FULL_NO_ESC, FULL_NO_EXP, run_multi
from firmforge.store import Store
from test_multi_agent import (FAST, RB, RB_CODE, RB_PLAN, STRONG, UTIL_H, Team, block, diag, mod, plan_json,
                              public_functions, rb_team)

SETTINGS = Settings(models={"fast": FAST, "strong": STRONG}, concurrency={"llm": 4, "verify": 4},
                    loop={"max_attempts_per_module": 12, "repeat_threshold": 2, "rounds_before_escalation": 3},
                    scheduler={"alpha": 0.3, "beta": 0.2, "gamma": 0.5, "seed": 42})
WRONG_B = block("src/util.h", UTIL_H) + block("src/util.c", '#include "util.h"\nint util_add(int a, int b) { return a - b; }\n')


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def run_m(self, cfg, team, store=None, settings=SETTINGS, **kw):
        llm = FakeLLM(team)
        events = []
        res = run_multi(RB, cfg, llm=llm, settings=settings, runs_root=self.tmp / "runs", store=store,
                        subscribers=(events.append,), **kw)
        return res, llm, events


# ---------------------------------------------------------------- 检索

class Bm25Test(unittest.TestCase):
    def test_tokenize(self):
        t = tokenize("Host_Compile 没包含 stddef")
        self.assertIn("host_compile", t)
        self.assertIn("compile", t)
        self.assertIn("包含", t)                  # 双字
        self.assertIn("没", t)                    # 单字

    def test_search_and_similarity(self):
        b = BM25(["gcc NULL undeclared stddef", "unity expected 5 was 6", "forbidden malloc"])
        self.assertEqual(b.search("NULL undeclared")[0][0], 0)
        self.assertEqual(b.search("没有关系的词"), [])
        self.assertAlmostEqual(b.similarity("gcc NULL undeclared stddef", 0), 1.0)
        self.assertEqual(b.similarity("forbidden malloc", 0), 0.0)


class KnowledgeTest(unittest.TestCase):
    def test_split_and_search(self):
        chunks = split_markdown("x.md", "# 标题\n\n前言不算\n\n## 第一节\n内容一\n\n## 第二节\nCRC 低字节在前\n")
        self.assertEqual([c.title for c in chunks], ["标题 / 第一节", "标题 / 第二节"])
        kb = KnowledgeBase(chunks)
        self.assertEqual(kb.search("CRC 字节")[0].title, "标题 / 第二节")
        self.assertIn("kb/x.md", kb.search("CRC")[0].render())

    def test_project_kb(self):
        kb = KnowledgeBase.load(helpers.ROOT / "kb")
        self.assertGreaterEqual(len({c.source for c in kb.chunks}), 5)            # ROADMAP：首批 5–8 篇
        self.assertIn("stddef", kb.search("NULL undeclared stddef host_compile")[0].text)
        self.assertEqual(KnowledgeBase.load(helpers.ROOT / "没有这个目录").search("x"), [])

    def test_kb_never_mentions_hidden_tests(self):
        from test_baseline_eval import hidden_markers, TASKS
        text = "\n".join(f.read_text(encoding="utf-8") for f in (helpers.ROOT / "kb").glob("*.md"))
        for task in TASKS.values():
            for mark in hidden_markers(task):
                self.assertNotIn(mark, text)


class ExperienceTest(unittest.TestCase):
    def test_save_merge_search_helped(self):
        store = Store(":memory:")
        e = ExperienceBase(store)
        a, merged = e.save("protocol", "host_compile", "gcc:implicit NULL", "包含 <stddef.h>\n第二行\n第三行\n第四行", "r1")
        self.assertFalse(merged)
        self.assertEqual(e.all()[0].fix.count("\n"), 2)                      # 最多 3 行
        b, merged = e.save("protocol", "host_compile", "gcc:implicit NULL", "另一种说法", "r2")
        self.assertEqual((a, merged), (b, True))
        c, merged = e.save("protocol", "unit_tests", "gcc:implicit NULL", "层不同就不合并", "r2")
        self.assertFalse(merged)
        self.assertEqual({x.id for x in e.search("implicit NULL", category="protocol")}, {a, c})
        self.assertEqual(e.search("host_compile implicit NULL", category="protocol")[0].id, a)
        self.assertEqual(len(e.search("implicit NULL", category="driver")), 2)   # 该类别没有就全库找
        e.mark_helped([a, a])
        self.assertEqual(e.all()[0].helped, 1)
        self.assertEqual(clip_fix("  a\n\n b \n"), "a\nb")


# ---------------------------------------------------------------- 升级阶梯

@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class EscalationTest(_Tmp):
    def levels(self, events, node="b"):
        return [e["data"]["level"] for e in events if e["type"] == "escalated" and e["node_id"] == node]

    def test_same_error_climbs_every_level(self):
        """验收第 1 条：同一个错误连续出现，事件流里能看到逐级 escalated。"""
        team = rb_team(coder=lambda m, msgs, n: WRONG_B if m == "b" else RB_CODE[m],
                       planner=lambda m, msgs, n: plan_json(RB_PLAN))      # 重新规划时原样交回也算合格
        res, llm, events = self.run_m(FULL, team)
        self.assertEqual(self.levels(events), [1, 2, 3, 4])
        names = [e["data"]["name"] for e in events if e["type"] == "escalated"]
        self.assertEqual(names, ["检索知识库", "换 Agent 从头重写", "重新规划", "转人工"])
        self.assertEqual(res.escalations, 4)
        # 第 1 级：之后的编码上下文带上知识库条目
        lvl1 = next(e for e in events if e["type"] == "escalated")
        self.assertTrue(lvl1["data"]["knowledge"])
        self.assertTrue(any("参考资料（知识库）" in m[-1]["content"] or "参考资料（知识库）" in m[1]["content"]
                            for m in team.calls("coder", "b")[2:]))
        # 第 2 级：换了编码 Agent，重写消息里没有旧代码
        assigns = [e for e in events if e["type"] == "node_assigned" and e["node_id"] == "b" and e["data"]["role"] == "coder"]
        self.assertEqual(len(assigns), 2 + 1)                 # 首次、换人、重新规划后的新模块
        self.assertIn("升级换 Agent", assigns[1]["data"]["scores"]["reason"])
        self.assertNotEqual(assigns[0]["data"]["agent"], assigns[1]["data"]["agent"])
        rewrite = next(m for m in team.calls("coder", "b") if "从头重写" in m[-1]["content"])
        self.assertNotIn("return a - b", "\n".join(x["content"] for x in rewrite))
        # 第 3 级：重新规划，旧模块被接替
        replan = [e for e in events if e["type"] == "plan_ready" and e["data"].get("replan")]
        self.assertEqual(len(replan), 1)
        self.assertEqual(res.replaced[0].module.id, "b")
        self.assertTrue((res.run_dir / "dag_before_replan_1.json").is_file())
        self.assertIn("必须原样保留", team.calls("planner")[1][-1]["content"])
        # 第 4 级：评测模式没有人工，记为失败；依赖它的 c 跳过
        self.assertEqual(res.modules["b"].status, "failed")
        self.assertEqual(res.modules["c"].status, "skipped")
        self.assertEqual(res.verdict, "fail")
        run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual((run["escalations"], run["replans"], run["replaced_modules"]), (4, 1, ["b"]))

    def test_replan_must_keep_finished_modules(self):
        changed_a = [dict(RB_PLAN[0], title="改了已完成的模块"), *RB_PLAN[1:]]
        team = rb_team(coder=lambda m, msgs, n: WRONG_B if m == "b" else RB_CODE[m],
                       planner=lambda m, msgs, n: plan_json(RB_PLAN if n == 1 else changed_a))
        res, _, events = self.run_m(FULL, team)
        self.assertEqual(len(team.calls("planner")), 1 + 3)            # 首次 + 重新规划 1 次 + 重做 2 次
        self.assertIn("必须原样保留", team.calls("planner")[2][-1]["content"])
        self.assertEqual(self.levels(events), [1, 2, 3, 4])
        self.assertFalse(any(e["data"].get("replan") for e in events if e["type"] == "plan_ready"))
        self.assertEqual(res.replaced, [])

    def test_human_hint_at_last_level(self):
        def coder(m, msgs, n):
            if m != "b":
                return RB_CODE[m]
            return RB_CODE["b"] if "负责人看过之后给出的提示" in msgs[-1]["content"] or "加法" in msgs[-1]["content"] else WRONG_B
        seen = []

        def hint(module_id, points):
            seen.append((module_id, points))
            return "util_add 是加法，不是减法"
        team = rb_team(coder=coder, planner=lambda m, msgs, n: plan_json(RB_PLAN))
        res, _, events = self.run_m(FULL, team, hint=hint)
        self.assertEqual(seen[0][0], "b")
        self.assertTrue(any("unit_tests" in p for p in seen[0][1]))
        self.assertTrue(res.modules["b"].passed)
        self.assertIn(res.verdict, ("pass", "incomplete"))
        self.assertTrue(any(e["type"] == "human_wait" and e["node_id"] == "b" for e in events))

    def test_no_escalation_when_disabled(self):
        team = rb_team(coder=lambda m, msgs, n: WRONG_B if m == "b" else RB_CODE[m])
        res, _, events = self.run_m(FULL_NO_ESC, team)
        self.assertEqual(self.levels(events), [])
        self.assertEqual(len(res.modules["b"].attempts), 12)              # 一直修到次数上限

    def test_cli_shows_escalation(self):
        line = cli.format_event({"ts": 1.0, "type": "escalated", "node_id": "b",
                                 "data": {"level": 2, "name": "换 Agent 从头重写", "reason": "同一个错误签名 x 连续出现 2 次"}}, 0.0)
        self.assertIn("[b] 升级到第 2 级（换 Agent 从头重写）", line)


# ---------------------------------------------------------------- 经验库

@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class ExperienceFlowTest(_Tmp):
    def team(self):
        return rb_team(coder=lambda m, msgs, n: WRONG_B if (m == "b" and n == 1) else RB_CODE[m],
                       diagnoser=lambda m, msgs, n: (diag("code", "加法写成了减法", "util_add 改成 a + b")
                                                     if "总结经验" not in msgs[-1]["content"] and "请总结经验" not in msgs[1]["content"]
                                                     else '```json\n{"fix": "核对运算符是否与需求一致"}\n```'))

    def test_saved_then_used_in_next_run(self):
        """验收第 2 条：连续跑两个同类任务，第一个产生 experience_saved，第二个出现 experience_used。"""
        store = Store(self.tmp / "db.sqlite")
        res1, _, ev1 = self.run_m(FULL, self.team(), store=store)
        saved = [e for e in ev1 if e["type"] == "experience_saved"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["data"]["layer"], "unit_tests")
        exp = ExperienceBase(store).all()
        self.assertEqual(exp[0].fix, "核对运算符是否与需求一致")
        self.assertEqual(exp[0].category, "data_structure")

        team2 = rb_team()
        res2, _, ev2 = self.run_m(FULL, team2, store=store)
        used = [e for e in ev2 if e["type"] == "experience_used"]
        self.assertTrue(used)
        self.assertEqual(used[0]["data"]["where"], "首次编码")
        self.assertGreater(res2.experiences_used, 0)
        self.assertTrue(any("过往经验" in m[1]["content"] for m in team2.calls("coder")))
        self.assertGreaterEqual(ExperienceBase(store).all()[0].helped, 1)      # 用到的模块最后通过了
        store.close()

    def test_disabled_in_no_exp(self):
        store = Store(self.tmp / "db.sqlite")
        _, _, ev = self.run_m(FULL_NO_EXP, self.team(), store=store)
        self.assertFalse(any(e["type"].startswith("experience_") for e in ev))
        self.assertEqual(ExperienceBase(store).all(), [])
        store.close()

    def test_separate_memory_db(self):
        store, memory = Store(self.tmp / "exp.sqlite"), Store(self.tmp / "mem.sqlite")
        self.run_m(FULL, self.team(), store=store, memory=memory)
        self.assertEqual(len(ExperienceBase(memory).all()), 1)
        self.assertEqual(ExperienceBase(store).all(), [])
        store.close()
        memory.close()


class AblationConfigTest(unittest.TestCase):
    def test_switches(self):
        cases = {"full": (True, True, True), "full-no-exp": (True, False, True),
                 "full-no-esc": (False, True, True), "full-no-review": (True, True, False),
                 "b3": (False, False, False), "b4": (False, False, False)}
        for name, (esc, exp, review) in cases.items():
            c = get_config(name)
            self.assertEqual((c.escalation, c.experience, c.review), (esc, exp, review), name)


if __name__ == "__main__":
    unittest.main()

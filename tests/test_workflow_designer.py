"""设计器用到的工作流功能（DECISIONS 第 62 条）：写回 TOML、版本、引擎可执行性检查、放宽后的引擎（任意位置等人确认、指定 Agent）。"""

import dataclasses
import tempfile
import tomllib
import unittest
from pathlib import Path

import helpers
from firmforge.agents.registry import default_registry
from firmforge.orchestrator.engine import B3, FULL
from firmforge.orchestrator.workflow import (Step, dump_workflow, engine_problems, list_versions, load_workflow,
                                             parse_workflow, save_workflow)
from test_multi_agent import RB, _Tmp, rb_team


def with_steps(wf, sub=None, main=None):
    subflows = dict(wf.subflows)
    if sub is not None:
        subflows["module_impl"] = tuple(sub)
    return dataclasses.replace(wf, main=tuple(main) if main is not None else wf.main, subflows=subflows)


class SerializeTest(unittest.TestCase):
    def test_round_trip(self):
        wf = load_workflow()
        again = parse_workflow(tomllib.loads(dump_workflow(wf)), "x")
        self.assertEqual(again, wf)

    def test_pos_note_agent_survive(self):
        wf = load_workflow()
        step = dataclasses.replace(wf.main[0], pos=(12.0, 34.5), note="说明 \"引号\"\n换行", agent="planner")
        wf2 = with_steps(wf, main=(step, *wf.main[1:]))
        back = parse_workflow(tomllib.loads(dump_workflow(wf2)), "x")
        self.assertEqual(back.main[0].pos, (12.0, 34.5))
        self.assertEqual(back.main[0].note, "说明 \"引号\"\n换行")
        self.assertEqual(back.main[0].agent, "planner")

    def test_versions(self):
        with tempfile.TemporaryDirectory() as d:
            wf = load_workflow()
            save_workflow(wf, Path(d))
            save_workflow(dataclasses.replace(wf, version="1.1.0", based_on=wf.version), Path(d))
            self.assertEqual(list_versions(wf.name, Path(d)), ["1.1.0", wf.version])
            self.assertEqual(load_workflow(Path(d) / f"{wf.name}.toml").based_on, wf.version)
            old = Path(d) / "history" / f"{wf.name}-{wf.version}.toml"
            self.assertEqual(load_workflow(old).version, wf.version)


class EngineProblemsTest(unittest.TestCase):
    def setUp(self):
        self.wf = load_workflow()
        self.reg = default_registry()

    def test_default_is_fine(self):
        self.assertEqual(engine_problems(self.wf, self.reg), [])

    def test_problems_are_reported(self):
        sub = list(self.wf.subflows["module_impl"])
        bad = [Step("p", "agent", role="planner"), Step("x", "verify", scope="all"),
               Step("l", "loop", body="repair_round", until="verify_passed"),
               Step("q", "agent", role="coder", agent="tester"), Step("r", "agent", role="coder", agent="nobody"),
               Step("s", "subflow", subflow="module_impl")]
        probs = "\n".join(engine_problems(with_steps(self.wf, sub=sub + bad), self.reg))
        for text in ("只能是编码、测试、审查", "只能检查本模块", "次数上限", "角色是 tester", "没有注册", "嵌套了自己"):
            self.assertIn(text, probs)
        no_plan = with_steps(self.wf, main=[s for s in self.wf.main if s.kind != "agent"])
        self.assertIn("规划步骤", "\n".join(engine_problems(no_plan, self.reg)))

    def test_engine_refuses_bad_workflow(self):
        from firmforge.orchestrator.workflow import WorkflowError
        from firmforge.orchestrator.engine import Engine
        from firmforge.llm import FakeLLM
        from test_multi_agent import SETTINGS
        bad = with_steps(self.wf, sub=[Step("x", "verify", scope="all")])
        with tempfile.TemporaryDirectory() as d, self.assertRaises(WorkflowError):
            Engine(RB, FULL, llm=FakeLLM([]), settings=SETTINGS, runs_root=d, workflow=bad)


class RelaxedEngineTest(_Tmp):
    def wf_with_accept(self, note="审查之后等人验收"):
        wf = load_workflow()
        sub = list(wf.subflows["module_impl"]) + [Step("accept", "human", note=note, enabled_by="human_review")]
        return with_steps(wf, sub=sub)

    def test_module_checkpoint_rejection_sends_comment_to_coder(self):
        asked = []

        def checkpoint(info):
            asked.append(info)
            return (info["node"] != "a" or len([x for x in asked if x["node"] == "a"]) > 1), "请加注释"
        res, llm, events = self.run_m(B3, rb_team(), workflow=self.wf_with_accept(), human_review=True,
                                      human=lambda plan: (True, ""), checkpoint=checkpoint)
        self.assertEqual(res.verdict, "pass")
        a_info = [x for x in asked if x["node"] == "a"][0]
        self.assertEqual((a_info["step"], a_info["note"]), ("accept", "审查之后等人验收"))
        coder_a = [m for r, md, m in llm._script.log if r == "coder" and md == "a"]
        self.assertEqual(len(coder_a), 2)                                   # 意见交给编码 Agent 改了一次
        self.assertIn("请加注释", coder_a[1][-1]["content"])
        self.assertTrue(any("负责人在 accept 没有通过" in n for n in res.modules["a"].notes))

    def test_checkpoint_skipped_without_human_review(self):
        res, llm, events = self.run_m(B3, rb_team(), workflow=self.wf_with_accept(),
                                      checkpoint=lambda info: self.fail("评测模式不该问人"))
        self.assertEqual(res.verdict, "pass")
        self.assertFalse([e for e in events if e["type"] == "human_wait"])

    def test_main_checkpoint_after_modules_can_reject(self):
        wf = load_workflow()
        main = list(wf.main)
        main.insert(main.index(next(s for s in main if s.kind == "verify")),
                    Step("deliver", "human", note="集成前确认", enabled_by="human_review"))
        res, _, events = self.run_m(B3, rb_team(), workflow=with_steps(wf, main=main), human_review=True,
                                    human=lambda plan: (True, ""), checkpoint=lambda info: (False, "先别集成"))
        self.assertEqual(res.verdict, "rejected")
        self.assertIn("先别集成", res.error)

    def test_step_can_pin_an_agent(self):
        wf = load_workflow()
        sub = [dataclasses.replace(s, agent="coder-fast") if s.kind == "agent" and s.role == "coder" else s
               for s in wf.subflows["module_impl"]]
        res, _, events = self.run_m(FULL, rb_team(), workflow=with_steps(wf, sub=sub))
        coders = {e["data"]["agent"] for e in events if e["type"] == "node_assigned" and e["data"].get("role") == "coder"}
        self.assertEqual(coders, {"coder-fast"})
        self.assertIn("工作流指定", [e for e in events if e["type"] == "node_assigned"
                                    and e["data"].get("role") == "coder"][0]["data"]["scores"]["reason"])


if __name__ == "__main__":
    unittest.main()

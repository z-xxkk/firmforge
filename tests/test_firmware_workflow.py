"""上板流程接入编排（DECISIONS 第 110 条，轻量接入）：firmware_dev.toml 决定上板流程走哪些步骤；
编码交给谁由调度器打分；连续没修好就换人；编码和写测试并行；设计后等人确认；经验库。评测（不给工作流）保持原样。"""

import copy
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

import helpers
from firmforge import config
from firmforge.agents.registry import default_registry
from firmforge.embedded.firmware_workflow import firmware_plan, firmware_problems
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import WorkflowError, dump_workflow, engine_problems, load_workflow, parse_workflow
from firmforge.runs import read_events
from firmforge.store import Store
from test_embedded_flow import CONV_BAD, CONV_GOOD, CONV_H, FakeTeam, files
from test_embedded_design import profile
from test_multi_agent import SETTINGS

BASE = tomllib.loads((Path(helpers.ROOT) / "workflows" / "firmware_dev.toml").read_text(encoding="utf-8"))


def variant(fn=None):
    data = copy.deepcopy(BASE)
    if fn is not None:
        fn(data)
    return parse_workflow(data, "测试")


def sub(data):
    return data["subflow"]["board_module"]["steps"]


def drop(steps, sid):
    steps[:] = [s for s in steps if s["id"] != sid]


class WorkflowCheckTest(unittest.TestCase):
    def test_shipped_workflow_is_valid(self):
        wf = load_workflow("firmware_dev")
        self.assertEqual(wf.engine, "firmware")
        self.assertEqual(engine_problems(wf, default_registry()), [])
        p = firmware_plan(wf)
        self.assertEqual((p.build_rules, p.confirm_rules, p.confirm_design, p.parallel_write, p.coder, p.max_attempts),
                         (True, True, "human_review", True, None, 4))
        self.assertEqual(p.source, f"firmware_dev@{wf.version}")
        self.assertTrue(p.stop_after_design(True))
        self.assertFalse(p.stop_after_design(False))
        again = parse_workflow(tomllib.loads(dump_workflow(wf)), "x")      # 设计器保存后读回，engine 不丢
        self.assertEqual(again.engine, "firmware")

    def test_supported_edits_map_to_plan(self):
        p = firmware_plan(variant(lambda d: drop(d["main"], "rules") or drop(d["main"], "confirm_rules")))
        self.assertEqual((p.build_rules, p.confirm_rules), (False, False))
        p = firmware_plan(variant(lambda d: drop(d["main"], "confirm_rules")))
        self.assertEqual((p.build_rules, p.confirm_rules), (True, False))

        def sequential(d):
            drop(sub(d), "write")
        p = firmware_plan(variant(sequential))
        self.assertFalse(p.parallel_write)

        def pin_and_two(d):
            next(s for s in sub(d) if s["id"] == "code")["agent"] = "coder-fast"
            next(s for s in sub(d) if s["id"] == "repair")["max"] = 2
            next(s for s in d["main"] if s["id"] == "confirm_design").pop("enabled_by")
        p = firmware_plan(variant(pin_and_two))
        self.assertEqual((p.coder, p.max_attempts, p.confirm_design), ("coder-fast", 2, "always"))
        self.assertTrue(p.stop_after_design(False))
        p = firmware_plan(variant(lambda d: drop(sub(d), "repair") or drop(d["main"], "confirm_design")))
        self.assertEqual((p.max_attempts, p.confirm_design), (1, ""))

    def test_unsupported_edits_are_rejected(self):
        reg = default_registry()
        cases = {
            "审查": lambda d: sub(d).append({"id": "rev", "kind": "agent", "role": "reviewer"}),
            "条件": lambda d: sub(d).append({"id": "c", "kind": "condition", "if": "verify_passed"}),
            "没有设计": lambda d: drop(d["main"], "design"),
            "各跑不在最后": lambda d: d["main"].append({"id": "x", "kind": "human"}),
            "检查在前": lambda d: sub(d).insert(0, sub(d).pop(3)),
            "两个检查": lambda d: sub(d).append({"id": "check2", "kind": "verify", "scope": "module"}),
            "次数太多": lambda d: next(s for s in sub(d) if s["id"] == "repair").update(max=20),
            "指定错角色": lambda d: next(s for s in sub(d) if s["id"] == "code").update(agent="tester"),
            "主流程放规划": lambda d: d["main"].insert(0, {"id": "p", "kind": "agent", "role": "planner"}),
            "规则在设计后": lambda d: d["main"].insert(3, d["main"].pop(0)),
        }
        for name, fn in cases.items():
            with self.subTest(name):
                self.assertTrue(firmware_problems(variant(fn), reg))
                with self.assertRaises(ValueError):
                    firmware_plan(variant(fn))

    def test_old_engine_refuses_firmware_workflow(self):
        from firmforge.orchestrator.engine import run_multi
        from test_multi_agent import B3, RB
        with self.assertRaises(WorkflowError) as cm:
            run_multi(RB, B3, llm=FakeLLM(lambda m: ""), settings=SETTINGS, runs_root=tempfile.mkdtemp(),
                      workflow=load_workflow("firmware_dev"))
        self.assertIn("上板", str(cm.exception))


class AlwaysBad(FakeTeam):
    """conv 一直写错，用来看调度器换人。"""

    def __call__(self, messages):
        system = messages[0]["content"]
        if "资深嵌入式 C 工程师" in system and "实现模块 imu" not in messages[1]["content"]:
            return files(("conv.c", CONV_BAD), ("include/conv.h", CONV_H))
        return super().__call__(messages)


def run_flow(d, team, wf, store=None, **kw):
    llm = FakeLLM(team)
    flow = EmbeddedFlow(llm=llm, settings=SETTINGS, profile=profile(), spec="# 需求\n读 MPU6050", rules=RuleSet(),
                        library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                        workflow=wf, store=store, **kw)
    res = flow.run()
    return res, read_events(res.run_dir), llm


def assigned(events, mid="conv"):
    return [e["data"] for e in events if e["type"] == "node_assigned" and e["node_id"] == mid]


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class FlowWorkflowTest(unittest.TestCase):
    def test_scheduler_picks_coder_and_learns(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "ff.db")
            res, events, llm = run_flow(d, FakeTeam(), load_workflow("firmware_dev"), store)
            self.assertEqual(res.modules["conv"].verdict, "pass")
            a = assigned(events)
            self.assertEqual([x["attempt"] for x in a], [1, 2])
            self.assertEqual(a[0]["scores"]["strategy"], "smart")
            self.assertEqual(set(a[0]["scores"]["candidates"]), {"coder-fast", "coder-strong"})
            self.assertEqual(a[0]["bucket"], "fw-logic-first")
            self.assertEqual(a[1]["bucket"], "fw-logic-repair")
            stats = store.agent_stats("fw-logic-first")
            self.assertEqual(sum(r["attempts"] for r in stats.values()), 1)          # 第一次写：记 1 次、没通过
            self.assertEqual(sum(r["successes"] for r in stats.values()), 0)
            rep = store.agent_stats("fw-logic-repair")
            self.assertEqual(sum(r["successes"] for r in rep.values()), 1)            # 修改轮：通过
            self.assertEqual(store.agent_stats("fw-hardware-first"), {})            # 没有板子：跳过不记成绩
            run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertTrue(run["workflow"].startswith("firmware_dev@"))
            started = next(e for e in events if e["type"] == "run_started")
            self.assertTrue(started["data"]["workflow"].startswith("firmware_dev@"))
            store.close()

    def test_switches_coder_after_two_failures(self):
        with tempfile.TemporaryDirectory() as d:
            res, events, _ = run_flow(d, AlwaysBad(), load_workflow("firmware_dev"))
            a = assigned(events)
            self.assertEqual(len(a), 4)                                  # 最多 4 次（工作流里的 max）
            self.assertEqual(res.modules["conv"].attempts, 4)
            first = a[0]["agent"]
            self.assertEqual(a[1]["agent"], first)                       # 第 1、2 次是同一个
            self.assertNotEqual(a[2]["agent"], first)                    # 连续两次没修好：换人
            self.assertIn("连续两次没修好", a[2]["scores"]["reason"])

    def test_parallel_write_tester_sees_only_interface(self):
        team = FakeTeam()
        with tempfile.TemporaryDirectory() as d:
            res, events, llm = run_flow(d, team, load_workflow("firmware_dev"))
        self.assertFalse(team.secret_leak)
        test_call = next(c for c in llm.calls if "测试工程师" in c["messages"][0]["content"]
                         and "conv" in c["messages"][-1]["content"])
        self.assertIn("int conv_double(int x);", test_call["messages"][-1]["content"])   # 设计给出的原型
        starts = [e for e in events if e["type"] == "agent_started" and e["node_id"] == "conv"
                  and e["data"]["purpose"] in ("编码", "写测试")]
        self.assertEqual({e["data"]["role"] for e in starts}, {"coder", "tester"})

    def test_pinned_coder_and_attempt_limit(self):
        def edit(d):
            next(s for s in sub(d) if s["id"] == "code")["agent"] = "coder-fast"
            next(s for s in sub(d) if s["id"] == "repair")["max"] = 1
        with tempfile.TemporaryDirectory() as d:
            res, events, llm = run_flow(d, FakeTeam(), variant(edit))
        a = assigned(events)
        self.assertEqual([(x["agent"], x["scores"]["strategy"]) for x in a], [("coder-fast", "workflow")])
        self.assertEqual(res.modules["conv"].attempts, 1)                # 只试一次：第一次写错就结束
        self.assertEqual(res.modules["conv"].verdict, "fail")
        fast = SETTINGS.tier("fast").name
        self.assertTrue(all(c["model"] == fast for c in llm.calls if "资深嵌入式 C 工程师" in c["messages"][0]["content"]))

    def test_confirm_design_with_comment_then_ok(self):
        asked = []

        def confirm(design):
            asked.append([m.id for m in design.modules])
            return "imu 模块改名叫 mpu" if len(asked) == 1 else ""
        team = FakeTeam()
        with tempfile.TemporaryDirectory() as d:
            res, events, llm = run_flow(d, team, load_workflow("firmware_dev"), human_review=True, confirm_design=confirm)
        self.assertEqual(len(asked), 2)
        designer = [c for c in llm.calls if "软件架构师" in c["messages"][0]["content"]]
        self.assertEqual(len(designer), 2)
        self.assertIn("imu 模块改名叫 mpu", designer[1]["messages"][-1]["content"])
        self.assertTrue(any(e["type"] == "human_wait" and e["node_id"] == "design" for e in events))
        with tempfile.TemporaryDirectory() as d:                         # 没开"设计后等我确认"：不停
            run_flow(d, FakeTeam(), load_workflow("firmware_dev"), human_review=False,
                     confirm_design=lambda des: self.fail("不该停下"))

    def test_experience_saved_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "ff.db")
            run_flow(d, FakeTeam(), load_workflow("firmware_dev"), store)        # conv 第 1 次错、按诊断改对 → 存经验
            from firmforge.memory.experience import ExperienceBase
            saved = ExperienceBase(store).all("firmware-logic")
            self.assertEqual(len(saved), 1)
            self.assertIn("应乘以 2", saved[0].fix)
            res, events, llm = run_flow(d, FakeTeam(), load_workflow("firmware_dev"), store)
            coder = next(c for c in llm.calls if "资深嵌入式 C 工程师" in c["messages"][0]["content"]
                         and "实现模块 imu" not in c["messages"][1]["content"])
            self.assertIn("过往经验", coder["messages"][-1]["content"])
            self.assertTrue(any(e["type"] == "experience_used" for e in events))
            store.close()

    def test_eval_path_unchanged(self):
        # 不给工作流（评测）：编码固定 coder-strong，不经调度器，不停下，最多试几次照参数
        with tempfile.TemporaryDirectory() as d:
            res, events, llm = run_flow(d, FakeTeam(), None, max_attempts=2)
            run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        a = assigned(events)
        self.assertEqual({(x["agent"], x["scores"]["strategy"]) for x in a}, {("coder-strong", "fixed")})
        self.assertEqual(res.modules["conv"].verdict, "pass")
        self.assertEqual(run["workflow"], "")


if __name__ == "__main__":
    unittest.main()

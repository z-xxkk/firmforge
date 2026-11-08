"""规则流程：查资料 → 判定 → 退回修改 → 人工确认 → 入库；多次退回转人工；已覆盖的外设直接复用（DECISIONS 第 66 条）。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.grounding.pipeline import RuleBuilder, covered_scopes
from firmforge.grounding.rules import RuleSet
from firmforge.grounding.svd import SvdDevice
from firmforge.grounding.verifier import RuleVerifier
from firmforge.llm import FakeLLM
from firmforge.orchestrator.events import EventBus
from test_rule_verifier import SVD, good_rules, make_idf, make_library, make_profile

GOOD = [r.to_dict() for r in good_rules()]


def reply(rules):
    return "规则如下：\n```json\n" + json.dumps(rules, ensure_ascii=False) + "\n```"


def broken(rule_id, quote="this text is not in the manual"):
    d = next(dict(x) for x in GOOD if x["id"] == rule_id)
    d["cite"] = {**d["cite"], "quote": quote}
    return d


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.events = []
        self.bus = EventBus("t")
        self.bus.subscribe(self.events.append)

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self, script, human=None, store=None):
        llm = FakeLLM(script)
        verifier = RuleVerifier(make_library(), make_profile(**{"i2c0.sda": 8, "i2c0.scl": 9}),
                                idf=make_idf(Path(self.tmp.name) / f"idf{len(self.events)}"), svd=SvdDevice.parse(SVD),
                                semantic=lambda r, p: (True, "一致"))
        b = RuleBuilder(lambda m, tools: llm.complete(m, model="fake", tools=tools), make_profile(), make_library(),
                        verifier, store if store is not None else RuleSet(), emit=self.bus.emit, human=human)
        return b, llm

    def types(self):
        return [e["type"] for e in self.events]

    def test_same_id_different_rule_is_kept_and_not_rechecked(self):
        # 试点里查资料 Agent 第 2 轮给一条新规则用了第 1 轮已有的编号，按编号合并时新规则被悄悄盖掉了
        first = [g for g in GOOD if g["id"] != "R-IMU-002"] + [broken("R-IMU-002")]
        old = next(g for g in GOOD if g["id"] == "R-I2C-002")
        new = {**old, "text": "另一条内容不同的 I2C 规则（同编号）"}
        fixed = next(g for g in GOOD if g["id"] == "R-IMU-002")
        script = [reply(first), reply([fixed, new, old])]          # 第 2 轮：改正的、撞编号的新规则、原样重复的旧规则
        checked = []
        b, _ = self.builder(script, human=lambda pending, esc: {r.id: ("approve", "") for r in pending})
        b.verifier.semantic = lambda r, page: (checked.append(r.text) or True, "一致")
        out = b.run(["i2c", "mpu6050"])
        by_text = {r.text: r.id for r in out.trusted}
        self.assertEqual(by_text[old["text"]], "R-I2C-002")                          # 原来那条还在、编号不变
        self.assertIn(new["text"], by_text)                                         # 新规则没被盖掉
        self.assertNotEqual(by_text[new["text"]], "R-I2C-002")                      # 换了空编号
        self.assertEqual(len(by_text), len(out.trusted))
        self.assertIn("rule_renamed", self.types())
        self.assertEqual(checked.count(old["text"]), 1)                             # 已核对的规则不再重复核对

    def test_second_round_sees_accepted_rules_and_offscope_dropped(self):
        first = [g for g in GOOD if g["id"] != "R-IMU-002"] + [broken("R-IMU-002")]
        offscope = {**next(g for g in GOOD if g["id"] == "R-I2C-002"), "id": "R-MAX-001", "scope": ["max30102"],
                    "text": "不在这次范围内的规则"}
        fixed = next(g for g in GOOD if g["id"] == "R-IMU-002")
        b, llm = self.builder([reply(first), reply([fixed, offscope])],
                              human=lambda pending, esc: {r.id: ("approve", "") for r in pending})
        out = b.run(["i2c", "mpu6050"])
        second = "\n".join(m["content"] or "" for m in llm.calls[1]["messages"])
        self.assertIn("本轮已通过核对的规则（不要再输出）", second)
        self.assertIn("R-I2C-001", second)
        self.assertNotIn("不在这次范围内的规则", {r.text for r in out.trusted})

    def test_feedback_round_then_human(self):
        first = [g for g in GOOD if g["id"] != "R-IMU-002"] + [broken("R-IMU-002")]
        script = [
            {"tool_calls": [{"name": "doc_search", "arguments": {"query": "sleep mode"}}]},      # 先查资料
            reply(first),
            reply([next(g for g in GOOD if g["id"] == "R-IMU-002")]),                         # 第 2 轮改正
        ]
        seen = {}

        def human(pending, escalated):
            seen["pending"] = [r.id for r in pending]
            return {r.id: ("approve", "") for r in pending if r.id != "R-I2C-002"} | {"R-I2C-002": ("reject", "速率按器件定")}

        store = RuleSet()
        b, llm = self.builder(script, human, store)
        out = b.run(["i2c", "mpu6050"])
        self.assertEqual(out.rounds, 2)
        self.assertEqual(seen["pending"], ["R-I2C-001", "R-I2C-002", "R-IMU-001", "R-IMU-002", "R-PIN-001"])
        self.assertEqual(sorted(r.id for r in out.trusted), ["R-I2C-001", "R-IMU-001", "R-IMU-002", "R-PIN-001"])
        self.assertTrue(all(r.status == "trusted" for r in store))
        self.assertNotIn("R-I2C-002", store.rules)
        second_round = llm.calls[-1]["messages"][1]["content"]
        self.assertIn("R-IMU-002", second_round)
        self.assertIn("找不到引用的原文", second_round)                       # 退回原因交回给 Agent
        self.assertTrue(any(c["tools"] for c in llm.calls))                     # 给了查资料工具
        for t in ("rules_started", "rules_proposed", "rule_rejected", "tool_called", "human_wait", "rule_trusted",
                  "rules_finished"):
            self.assertIn(t, self.types())

    def test_three_rejects_go_to_human(self):
        bad = [g for g in GOOD if g["id"] != "R-IMU-002"] + [broken("R-IMU-002")]
        got = {}

        def human(pending, escalated):
            got["escalated"] = [e.rule.id for e in escalated]
            return {}
        b, llm = self.builder([reply(bad)] * 3, human)
        b.max_rounds = 3
        out = b.run(["i2c", "mpu6050"])
        self.assertEqual(got["escalated"], ["R-IMU-002"])
        self.assertEqual(out.trusted, [])
        self.assertEqual(len(out.pending), 4)                  # 人没表态：停在已核对，不入库
        self.assertFalse(out.ok)

    def test_no_human_means_no_trust(self):
        b, _ = self.builder([reply(GOOD)])
        out = b.run(["i2c", "mpu6050"])
        self.assertEqual(out.trusted, [])
        self.assertEqual(len(out.pending), 5)
        self.assertIn("rules_need_human", self.types())

    def test_reuse_covered_scopes(self):
        store = RuleSet([r.advance("checked").advance("approved").advance("trusted") for r in good_rules()])
        self.assertEqual(covered_scopes(store, ["i2c", "mpu6050", "uart"]), {"i2c", "mpu6050"})
        b, llm = self.builder([], store=store)
        out = b.run(["i2c", "mpu6050"])
        self.assertEqual(llm.calls, [])                        # 全部复用，不调用模型
        self.assertEqual(len(out.reused), 5)


if __name__ == "__main__":
    unittest.main()

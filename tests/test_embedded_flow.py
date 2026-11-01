"""嵌入式新流程端到端（假模型）：设计 → 编码、写测试 → 合规 → 测试 → 诊断 → 修正（DECISIONS 第 66 条）。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS
from test_rule_verifier import good_rules

DESIGN = {"modules": [
    {"id": "conv", "title": "换算", "layer": "logic", "scopes": [], "files": ["conv.c", "include/conv.h"],
     "provides": ["int conv_double(int x)"], "acceptance": ["conv_double(21) == 42"]},
    {"id": "imu", "title": "MPU6050 驱动", "layer": "hardware", "scopes": ["i2c", "mpu6050"], "depends_on": ["conv"],
     "files": ["imu.c", "include/imu.h"], "provides": ["int imu_init(void)"]},
], "app": "打印"}

CONV_H = "#pragma once\nint conv_double(int x);\n"
CONV_BAD = '#include "conv.h"\nint conv_double(int x) { return x + 2; }\n'
CONV_GOOD = '#include "conv.h"\nint conv_double(int x) { return x * 2; }\n'
CONV_TEST = """#include "unity.h"
#include "conv.h"
void setUp(void) {}
void tearDown(void) {}
static void test_double_21(void) { TEST_ASSERT_EQUAL_INT(42, conv_double(21)); }
int main(void) { UNITY_BEGIN(); RUN_TEST(test_double_21); return UNITY_END(); }
"""
IMU = ('<<<FILE imu.c\n#include "hw_config.h"\n#include "imu.h"\nint imu_init(void) { return FF_IMU_ADDR; }\n>>>FILE\n'
       "<<<FILE include/imu.h\n#pragma once\nint imu_init(void);\n>>>FILE\n")


def files(*pairs):
    return "".join(f"<<<FILE {p}\n{c}>>>FILE\n" for p, c in pairs)


class FakeTeam:
    def __init__(self):
        self.conv_calls = 0
        self.diagnosed = 0
        self.secret_leak = False

    def __call__(self, messages):
        system, user = messages[0]["content"], messages[-1]["content"]
        if "软件架构师" in system:
            return "```json\n" + json.dumps(DESIGN) + "\n```"
        if "诊断工程师" in system:
            self.diagnosed += 1
            return '```json\n{"blame": "code", "rule": "", "evidence": "Expected 42 Was 23", "instruction": "应乘以 2"}\n```'
        if "资深嵌入式 C 工程师" in system:
            if "实现模块 imu" in messages[1]["content"]:
                return IMU
            self.conv_calls += 1
            body = CONV_BAD if self.conv_calls == 1 else CONV_GOOD
            return files(("conv.c", body), ("include/conv.h", CONV_H))
        if "逻辑层" in system and "测试工程师" in system:
            self.secret_leak |= "x + 2" in user or "x * 2" in user
            return files(("test_host/test_conv.c", CONV_TEST))
        if "硬件层" in system and "测试工程师" in system:
            return files(("test/test_imu.c", '#include "unity.h"\nTEST_CASE("x", "[imu]") {}\n'))
        raise AssertionError("没想到的调用：" + system[:60])


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class FlowTest(unittest.TestCase):
    def test_flow_with_repair_and_skipped_hardware(self):
        team = FakeTeam()
        trusted = RuleSet([r.advance("checked").advance("approved").advance("trusted") for r in good_rules()])
        with tempfile.TemporaryDirectory() as d:
            flow = EmbeddedFlow(llm=FakeLLM(team), settings=SETTINGS, profile=profile(), spec="# 需求\n读 MPU6050",
                                rules=trusted, library=None, verifier=None, runs_root=Path(d), rule_mode="none",
                                idf=None, board=None)
            res = flow.run()
            events = read_events(res.run_dir)
            run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
            snap = (res.run_dir / "nodes" / "conv" / "attempt_2" / "main" / "conv.c").read_text(encoding="utf-8")
        self.assertEqual(res.modules["conv"].verdict, "pass")
        self.assertEqual(res.modules["conv"].attempts, 2)                   # 第 1 次错，诊断后改对
        self.assertEqual(team.diagnosed, 1)
        self.assertFalse(team.secret_leak)                                  # 测试 Agent 没看到实现
        self.assertEqual(res.modules["imu"].verdict, "skipped")            # 没有 ESP-IDF：跳过，不算通过
        self.assertEqual(sorted(res.modules["imu"].files), ["imu.c", "include/imu.h"])
        self.assertEqual(res.verdict, "incomplete")
        self.assertEqual(run["verdict"], "incomplete")
        self.assertIn("x * 2", snap)
        types = [e["type"] for e in events]
        for t in ("run_started", "plan_ready", "node_ready", "agent_started", "verify_finished", "diagnosis",
                  "node_done", "node_failed", "run_finished"):
            self.assertIn(t, types)
        self.assertEqual(res.rules_used, [])                                # 规则模式 none：不给规则

    def test_rules_reach_coder_in_verified_mode(self):
        team = FakeTeam()
        llm = FakeLLM(team)
        trusted = RuleSet([r.advance("checked").advance("approved").advance("trusted") for r in good_rules()])
        with tempfile.TemporaryDirectory() as d:
            flow = EmbeddedFlow(llm=llm, settings=SETTINGS, profile=profile(), spec="x", rules=trusted, library=None,
                                verifier=None, runs_root=Path(d), rule_mode="verified", idf=None, board=None)
            res = flow.run()
        coder_imu = next(c for c in llm.calls if "实现模块 imu" in c["messages"][1]["content"])
        self.assertIn("R-IMU-001 |", coder_imu["messages"][0]["content"])     # 已入库的规则进了 system prompt
        self.assertIn("R-IMU-001", res.rules_used)


if __name__ == "__main__":
    unittest.main()

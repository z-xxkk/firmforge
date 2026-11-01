"""ESP-IDF 编码、写测试、诊断 Agent 的消息与输出解析（DECISIONS 第 66、80、81 条）。"""

import json
import unittest

import helpers  # noqa: F401
from firmforge.embedded.design import parse_design
from firmforge.embedded.roles import (coder_messages, coder_repair_message, diagnoser_messages, parse_diagnosis,
                                      parse_output, test_file, tester_messages, tester_repair_message)
from firmforge.grounding.rules import RuleSet
from test_embedded_design import GOOD, profile
from test_rule_verifier import good_rules

RULES = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
DESIGN = parse_design(GOOD, profile())[0]
IMU, CONV = DESIGN.by_id["imu"], DESIGN.by_id["imu_convert"]
SECRET = "static int imu_secret_impl(void) { return 0x68; }"


class CoderTest(unittest.TestCase):
    def test_messages_select_rules_by_scope(self):
        msgs = coder_messages("读 MPU6050", profile(), RULES, DESIGN, IMU, {"include/dep.h": "int dep(void);"})
        system, user = msgs[0]["content"], msgs[1]["content"]
        self.assertIn("facts", system)                                    # 翻手册要列出处
        self.assertIn("R-IMU-001 |", system)
        self.assertIn("R-I2C-001 |", system)
        self.assertIn("main/imu.c、main/include/imu.h", user)
        self.assertIn("int dep(void);", user)
        self.assertIn("硬件层", user)
        logic = coder_messages("x", profile(), RULES, DESIGN, CONV, {})[0]["content"]
        self.assertNotIn("R-IMU-001", logic)                              # 逻辑层不放器件规则
        self.assertIn("R-PIN-001", logic)                                 # scope all 仍然放
        self.assertIn("只用标准 C", coder_messages("x", profile(), RULES, DESIGN, CONV, {})[1]["content"])

    def test_parse_output_and_facts(self):
        fact = {"id": "R-IMU-101", "kind": "sequence", "scope": ["mpu6050"], "text": "复位后等 100 ms",
                "cite": {"doc": "mpu6050-rm", "page": 41, "section": "", "quote": "wait 100ms"}, "tier": "official_mirror",
                "status": "trusted"}
        text = ("<<<FILE main/imu.c\nint imu;\n>>>FILE\n<<<FILE include/imu.h\nint x;\n>>>FILE\n"
                "<<<FILE include/hw_config.h\n#define X 1\n>>>FILE\n<<<FILE other.c\nx\n"
                "```facts\n" + json.dumps([fact]) + "\n```")
        out = parse_output(text, set(IMU.files))
        self.assertEqual(sorted(out.files), ["imu.c", "include/imu.h"])
        self.assertEqual([p for p, _ in out.rejected], ["include/hw_config.h"])
        self.assertEqual(out.unterminated, 1)
        self.assertEqual([f.id for f in out.facts], ["R-IMU-101"])
        self.assertEqual(out.facts[0].status, "draft")                    # 自称可信也只是待核对
        self.assertIn("诊断 Agent 的修改指令：改成 0x6B", coder_repair_message(2, 6, "WHO_AM_I 错", {"instruction": "改成 0x6B"}))


class TesterTest(unittest.TestCase):
    def test_board_and_host(self):
        board = tester_messages("x", profile(), RULES, IMU, "esp_err_t imu_init(void);", {})
        self.assertIn("ff_reg_read", board[0]["content"])
        self.assertIn("main/test/test_imu.c", board[1]["content"])
        host = tester_messages("x", profile(), RULES, CONV, "float imu_accel_g(int16_t raw, int range_g);", {})
        self.assertIn("不能包含任何 ESP-IDF 头文件", host[0]["content"])
        self.assertEqual(test_file(CONV), "test_host/test_imu_convert.c")
        for m in board + host:                                            # 测试 Agent 看不到实现
            self.assertNotIn("imu_secret_impl", m["content"])

    def test_repair_scrubs_code(self):
        msg = tester_repair_message(2, 6, f"失败，片段：{SECRET}", {"imu.c": SECRET},
                                    {"instruction": f"参考 {SECRET} 改预期值"})
        self.assertNotIn("imu_secret_impl", msg)
        self.assertIn("问题出在测试", msg)


class DiagnoserTest(unittest.TestCase):
    def test_parse(self):
        msgs = diagnoser_messages(profile(), RULES, IMU, {"imu.c": "int a;"}, {"test/test_imu.c": "int b;"}, "寄存器不符")
        self.assertIn("hardware", msgs[0]["content"])
        self.assertIn("[file main/imu.c]", msgs[1]["content"])
        d = parse_diagnosis({"blame": "rule", "rule": "R-IMU-001", "instruction": "地址应为 0x69", "evidence": "AD0 接高"},
                            RULES)
        self.assertEqual(d["blame"], "rule")
        for bad in ({"blame": "luck", "instruction": "x"}, {"blame": "code"}, {"blame": "rule", "instruction": "x"},
                    {"blame": "rule", "rule": "R-NOPE-001", "instruction": "x"}):
            with self.assertRaises(ValueError):
                parse_diagnosis(bad, RULES)


if __name__ == "__main__":
    unittest.main()

"""system prompt 拼接与设计分层 Agent（DECISIONS 第 66 条第 ④ 步、第 67 条）。"""

import json
import unittest

import helpers  # noqa: F401
from firmforge.embedded.design import design_from_text, designer_messages, designer_retry_message, parse_design
from firmforge.embedded.prompting import rule_ids, system_prompt
from firmforge.grounding.rules import RuleSet
from test_rule_verifier import good_rules, make_profile


def approved_rules() -> RuleSet:
    return RuleSet([r.advance("checked").advance("approved") for r in good_rules()]
                   + [list(good_rules())[2]])                 # 再放一条草稿：不应进 system prompt


def profile():
    return make_profile(**{"i2c0.sda": 4, "i2c0.scl": 5})


GOOD = {
    "modules": [
        {"id": "imu", "title": "MPU6050 驱动", "layer": "hardware", "scopes": ["i2c", "mpu6050"],
         "files": ["imu.c", "include/imu.h"], "provides": ["esp_err_t imu_init(void)"],
         "acceptance": ["WHO_AM_I 读回 0x68"]},
        {"id": "imu_convert", "title": "换算", "layer": "logic", "scopes": [],
         "files": ["imu_convert.c", "include/imu_convert.h"], "provides": ["float imu_accel_g(int16_t raw, int range_g)"]},
        {"id": "app_logic", "title": "上层", "layer": "hardware", "scopes": ["i2c"], "depends_on": ["imu", "imu_convert"],
         "files": ["app_logic.c", "include/app_logic.h"]},
    ],
    "app": "每 100 ms 打印一次",
}


class PromptingTest(unittest.TestCase):
    def test_system_prompt(self):
        rs = RuleSet([r.advance("checked").advance("approved") for r in good_rules()])
        s = system_prompt("designer.md", profile(), rs, {"mpu6050"}, max_modules="8")
        self.assertIn("i2c0.sda=GPIO4", s)
        self.assertIn("R-IMU-001 |", s)
        self.assertIn("R-PIN-001 |", s)                     # scope all 总是放进去
        self.assertNotIn("R-I2C-001", s)                    # 与 mpu6050 无关
        self.assertEqual(s, system_prompt("designer.md", profile(), rs, {"mpu6050"}, max_modules="8"))   # 开头完全相同
        self.assertEqual(rule_ids(rs, {"i2c"}), ["R-I2C-001", "R-I2C-002", "R-PIN-001"])
        draft_only = RuleSet(list(good_rules()))
        self.assertIn("没有已确认的规则", system_prompt("designer.md", profile(), draft_only, None))


class DesignTest(unittest.TestCase):
    def test_good_design(self):
        d, problems = parse_design(GOOD, profile())
        self.assertEqual(problems, [])
        self.assertEqual([m.id for m in d.order()], ["imu", "imu_convert", "app_logic"])
        self.assertEqual(d.by_id["imu"].header, "include/imu.h")
        d2, _ = design_from_text("```json\n" + json.dumps(GOOD) + "\n```", profile())
        self.assertEqual(d2.to_dict(), d.to_dict())

    def test_problems(self):
        bad = json.loads(json.dumps(GOOD))
        bad["modules"][0]["scopes"] = ["can"]                                  # 不在硬件档案里
        bad["modules"][1]["scopes"] = ["i2c"]                                  # 逻辑层不能有 scope
        bad["modules"][1]["depends_on"] = ["imu"]                              # 逻辑层依赖硬件层
        bad["modules"][2]["files"] = ["main/app_logic.c", "imu.c", "include/hw_config.h"]
        bad["modules"].append({"id": "Bad-Id", "layer": "middle", "files": ["x.txt"]})
        d, problems = parse_design(bad, profile())
        self.assertIsNone(d)
        text = "\n".join(problems)
        for word in ("can", "逻辑层不能涉及外设", "不能依赖硬件层", "不要带 main/", "同时属于", "hw_config.h",
                     "include/app_logic.h", "编号只能用", "layer 只能是"):
            self.assertIn(word, text)
        self.assertIn("没有通过检查", designer_retry_message(problems))

    def test_cycle_and_missing_dep(self):
        cyc = json.loads(json.dumps(GOOD))
        cyc["modules"][0]["depends_on"] = ["app_logic"]
        self.assertIn("模块依赖成环", parse_design(cyc, profile())[1])
        gone = json.loads(json.dumps(GOOD))
        gone["modules"][2]["depends_on"] = ["nope"]
        self.assertTrue(any("nope 不存在" in p for p in parse_design(gone, profile())[1]))
        self.assertEqual(parse_design({}, profile()), (None, ["缺少 modules 数组"]))
        self.assertTrue(design_from_text("没有 JSON", profile())[1])

    def test_messages(self):
        msgs = designer_messages("读 MPU6050 并打印", profile(), approved_rules())
        self.assertIn("分出硬件层和逻辑层", msgs[0]["content"])
        self.assertIn("R-IMU-002 |", msgs[0]["content"])                       # 设计 Agent 看全部已确认规则
        self.assertIn("读 MPU6050 并打印", msgs[1]["content"])
        self.assertIn("hw_config.h", msgs[1]["content"])

    def test_onchip_scopes(self):
        """片内外设不用接线也能写进 scopes，设计 Agent 被告知可选列表（第 127 条）。"""
        d = {"modules": [
            {"id": "tsens", "title": "片内温度", "layer": "hardware", "scopes": ["temp_sensor"],
             "files": ["tsens.c", "include/tsens.h"]},
            {"id": "volt", "title": "ADC", "layer": "hardware", "scopes": ["adc"], "files": ["volt.c", "include/volt.h"]},
            {"id": "store", "title": "NVS", "layer": "hardware", "scopes": ["nvs"], "files": ["store.c", "include/store.h"]}]}
        _, problems = parse_design(d, profile())
        self.assertEqual(problems, [])
        d["modules"][0]["scopes"] = ["tsens"]               # 名字写错仍要拒
        _, problems = parse_design(d, profile())
        self.assertTrue(any("tsens 不在硬件档案里" in p for p in problems))
        content = designer_messages("x", profile(), None)[1]["content"]
        self.assertIn("temp_sensor", content)
        self.assertIn("nvs", content)

    def test_product_flow_requires(self):
        """产品流程编译带上常用驱动组件，评测仍只用题目自己声明的（第 127 条）。"""
        from firmforge.target.esp_idf import DEFAULT_REQUIRES, PRODUCT_REQUIRES
        self.assertIn("esp_driver_tsens", PRODUCT_REQUIRES)
        self.assertIn("esp_adc", PRODUCT_REQUIRES)
        self.assertIn("nvs_flash", PRODUCT_REQUIRES)
        self.assertNotIn("esp_adc", DEFAULT_REQUIRES)
        import inspect
        from firmforge.embedded import workspace
        self.assertIn("extra_requires=PRODUCT_REQUIRES", inspect.getsource(workspace.make_firmware_flow))


if __name__ == "__main__":
    unittest.main()

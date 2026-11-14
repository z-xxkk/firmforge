"""编译报错落在谁写的文件里，就交给谁改（第 122 条）。

基线评测（results/final_before）里多 Agent 的 90 次编译失败有 82 次报错在 Agent 自己写的测试文件里，
原来一律交给编码 Agent，编码 Agent 不能改测试文件，同一个错一直重复到次数用完。"""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.embedded.design import DesignModule
from firmforge.embedded.flow import EmbeddedFlow, ModuleResult, build_error_files
from firmforge.embedded.roles import platform_headers_block, tester_messages
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS

REPORT_TEST = """编译没过：
main/test/test_mpu6050.c:91: error: 'FF_REG_IMU_WHO_AM_I' undeclared (first use in this function)
main/test/test_mpu6050.c:92:5: error: 'FF_IMU_WHO_AM_I' undeclared (first use in this function)
"""
REPORT_CODE = "main/mpu6050.c:12: error: expected ';' before 'return'\n" + REPORT_TEST
REPORT_FATAL = "main/test/test_mpu6050.c:3:10: fatal error: nope.h: No such file or directory\n"


class BuildErrorFilesTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(build_error_files(REPORT_TEST), {"test_mpu6050.c"})
        self.assertEqual(build_error_files(REPORT_CODE), {"mpu6050.c", "test_mpu6050.c"})
        self.assertEqual(build_error_files(REPORT_FATAL), {"test_mpu6050.c"})
        self.assertEqual(build_error_files("main/x.c:3: warning: unused"), set())
        self.assertEqual(build_error_files(""), set())


def make_flow(d, llm=None):
    return EmbeddedFlow(llm=llm or FakeLLM(lambda m: ""), settings=SETTINGS, profile=profile(), spec="x", rules=RuleSet(),
                        library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None)


class DiagnoseRoutingTest(unittest.TestCase):
    def setUp(self):
        self.m = DesignModule("mpu6050", "MPU6050", "hardware", ("mpu6050.c", "include/mpu6050.h"),
                              provides=("int mpu_init(void)",))
        self.mr = ModuleResult("mpu6050", "hardware")
        self.mr.files = {"mpu6050.c": "", "include/mpu6050.h": ""}
        self.mr.tests = {"test/test_mpu6050.c": ""}

    def test_errors_only_in_tests_go_to_tester_without_model_call(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            f = make_flow(d, FakeLLM(lambda m: calls.append(m) or ""))
            diag = f._diagnose(self.m, self.mr, RuleSet(), "build", REPORT_TEST, 1)
            self.assertEqual(diag["blame"], "test")
            self.assertIn("test_mpu6050.c", diag["evidence"])
            self.assertEqual(calls, [])                                   # 不花钱
            ev = [e for e in read_events(f.rd.path) if e["type"] == "diagnosis"]
            self.assertEqual(ev[0]["data"]["blame"], "test")               # 界面上看得到为什么交给测试 Agent

    def test_errors_in_driver_still_go_to_coder(self):
        with tempfile.TemporaryDirectory() as d:
            f = make_flow(d)
            self.assertEqual(f._diagnose(self.m, self.mr, RuleSet(), "build", REPORT_CODE, 1)["blame"], "code")
            self.assertEqual(f._diagnose(self.m, self.mr, RuleSet(), "build", "链接失败：undefined reference", 1)["blame"],
                             "code")                                      # 看不出文件：照旧算代码
            self.assertEqual(f._diagnose(self.m, self.mr, RuleSet(), "compliance", REPORT_TEST, 1)["blame"], "code")


class TesterSeesPlatformHeadersTest(unittest.TestCase):
    def test_prompt_lists_real_macros(self):
        m = DesignModule("mpu6050", "MPU6050", "hardware", ("mpu6050.c", "include/mpu6050.h"),
                         provides=("int mpu_init(void)",), scopes=("i2c", "mpu6050"))
        msgs = tester_messages("读 MPU6050", profile(), RuleSet(), m, "int mpu_init(void);", {})
        text = msgs[1]["content"]
        self.assertIn("#define FF_PIN_I2C0_SDA", text)                    # hw_config.h 全文
        self.assertIn("#include 对应的头文件", text)
        self.assertIn("不要用 FF_REG_ 开头的名字", text)                    # 这个模块没有寄存器宏，明说
        self.assertNotIn("hidden", text.lower())

    def test_block_lists_register_macros_from_rules(self):
        from firmforge.grounding.rules import Rule
        r = Rule.from_dict({"id": "R-LEDC-009", "kind": "register", "scope": ["ledc"], "text": "x", "tier": "official_manual",
                            "status": "approved", "cite": {"doc": "d", "page": 1, "section": "", "quote": "q"},
                            "check": {"type": "register_value", "peripheral": "LEDC", "register": "CONF", "field": "f",
                                      "value": 1}})
        text = platform_headers_block(profile(), RuleSet([r]), ("ledc",))
        self.assertIn("#define FF_REG_LEDC_CONF", text)


class LoopRoutesTestBuildErrorToTesterTest(unittest.TestCase):
    """整条修正循环：第 1 次检查报"测试文件编译不过"，下一轮该是测试 Agent 改测试，不是编码 Agent 改代码。"""

    def test_fixed_flow_and_workflow(self):
        import json
        from unittest import mock
        from firmforge.embedded.design import Design
        from firmforge.orchestrator.workflow import load_workflow
        for wf in (None, load_workflow("firmware_dev")):
            with self.subTest(workflow=wf.name if wf else "固定流程"), tempfile.TemporaryDirectory() as d:
                seen = []

                def team(messages):
                    system = messages[0]["content"]
                    if "资深嵌入式 C 工程师" in system:
                        seen.append("coder")
                        return "<<<FILE imu.c\nint imu_read(void){return 1;}\n>>>FILE\n"
                    if "测试工程师" in system:
                        seen.append("tester")
                        return '<<<FILE test/test_imu.c\n#include "unity.h"\nTEST_CASE("x", "[imu]") {}\n>>>FILE\n'
                    raise AssertionError("没想到的调用：" + system[:40])
                m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"), provides=("int imu_read(void)",))
                f = EmbeddedFlow(llm=FakeLLM(team), settings=SETTINGS, profile=profile(), spec="x", rules=RuleSet(),
                                 library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                                 design=Design([m]), locked={"include/imu.h": "int imu_read(void);\n"}, build_rules=False,
                                 workflow=wf)
                results = iter([("fail", "build", "main/test/test_imu.c:5: error: 'FF_REG_X' undeclared"),
                                ("pass", "board_test", "")])
                with mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: next(results)):
                    res = f.run()
                self.assertEqual(res.modules["imu"].verdict, "pass")
                first_tester = seen.index("tester")
                self.assertEqual(seen[first_tester + 1:].count("tester"), 1, seen)   # 编译失败后又叫了一次测试 Agent
                self.assertEqual(seen.count("coder"), 1, seen)                      # 编码 Agent 只写了第一次


if __name__ == "__main__":
    unittest.main()

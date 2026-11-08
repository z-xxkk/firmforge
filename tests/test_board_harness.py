"""上板测试：平台辅助文件、结果解析、找不到板子时如实跳过（DECISIONS 第 78、81 条）。"""

import unittest
from pathlib import Path

import helpers  # noqa: F401
from dataclasses import replace

from firmforge.grounding.profile import Device, Fact, HardwareProfile
from firmforge.grounding.rules import Citation, Rule, RuleSet
from firmforge.grounding.svd import SvdDevice
from firmforge.target.board import BoardRunner, find_port
from firmforge.target.harness import BEGIN, END, harness_files, parse_board_output, regs_header
from test_rule_verifier import SVD


def reg_rule(id, field_reg="CTR", periph="I2C0", status="approved"):
    return Rule(id, "register", ("i2c",), "x", Citation("d", 1, "", "q"), "official_manual", status,
                {"type": "register_value", "peripheral": periph, "register": field_reg, "field": "MS_MODE", "value": 1})


OK_LOG = f"""ESP-ROM:esp32s3-20210327
I (25) boot: ESP-IDF v6.1
{BEGIN}
FF_LOG: I2C0 CTR = 0x00000113
/work/main/test/test_imu.c:12:初始化后 I2C0 处于主机模式:PASS
/work/main/test/test_imu.c:30:WHO_AM_I: 读回 0x68:FAIL: Expected 104 Was 255
/work/main/test/test_imu.c:40:跳过的用例:IGNORE
-----------------------
3 Tests 1 Failures 1 Ignored
FAIL
{END}
"""


class HarnessTest(unittest.TestCase):
    def test_regs_header(self):
        svd = SvdDevice.parse(SVD.replace("<name>I2C0</name><registers>",
                                          "<name>I2C0</name><baseAddress>0x60013000</baseAddress><registers>")
                              .replace("<register><name>CTR</name>", "<register><name>CTR</name><addressOffset>0x4</addressOffset>"))
        rs = RuleSet([reg_rule("R-REG-001"), reg_rule("R-REG-002"), reg_rule("R-REG-003", "NOPE"),
                      reg_rule("R-REG-004", "SR", status="draft")])
        h = regs_header(rs, svd)
        self.assertIn("#define FF_REG_I2C0_CTR 0x60013004u  /* R-REG-001 */", h)
        self.assertEqual(h.count("FF_REG_I2C0_CTR"), 1)                    # 同一寄存器只写一次
        self.assertIn("FF_REG_I2C0_NOPE：SVD 里没有这个寄存器", h)
        self.assertNotIn("SR", h)                                          # 草稿规则不进
        files = harness_files(rs, svd)
        self.assertIn(BEGIN, files["ff_test_main.c"])
        self.assertIn("unity_run_test_by_index", files["ff_test_main.c"])
        self.assertIn("ff_reg_read", files["include/ff_test.h"])

    def test_parse_ok_and_fail(self):
        r = parse_board_output(OK_LOG)
        self.assertTrue(r.started)
        self.assertFalse(r.hang)
        self.assertEqual([(c.name, c.status) for c in r.cases],
                         [("初始化后 I2C0 处于主机模式", "pass"), ("WHO_AM_I: 读回 0x68", "fail"), ("跳过的用例", "ignore")])
        self.assertEqual(r.failed[0].message, "Expected 104 Was 255")
        self.assertEqual(r.summary, (3, 1, 1))
        self.assertEqual(r.logs, ["I2C0 CTR = 0x00000113"])
        self.assertEqual(r.verdict(), "fail")
        self.assertIn("失败：WHO_AM_I: 读回 0x68", r.report())

    def test_lost_end_marker_but_summary_present(self):
        r = parse_board_output(f"{BEGIN}\nmain/t.c:3:a:PASS\n1 Tests 0 Failures 0 Ignored\nOK\n_END\n")
        self.assertFalse(r.hang)                         # 结束标记丢了几个字节，汇总行仍可判定结束
        self.assertTrue(r.ok)

    def test_one_boot_per_case(self):
        # 每个用例单独开机（DECISIONS 第 91 条）：中间夹着重启的启动日志，开始标记只有一次，汇总在最后
        boot = "ESP-ROM:esp32c3-api1-20210207\nrst:0x3 (RTC_SW_SYS_RST),boot:0xc (SPI_FAST_FLASH_BOOT)\nI (71) main_task: Calling app_main()\n"
        out = (boot + "FF_TESTS_BEGIN\nRunning a...\nt.c:3:a:PASS\n" + boot + "Running b...\nt.c:9:b:FAIL: Expected 1 Was 0\n"
               + boot + "\n-----------------------\n2 Tests 1 Failures 0 Ignored\nFAIL\nFF_TESTS_END\n")
        r = parse_board_output(out)
        self.assertEqual([(c.name, c.status) for c in r.cases], [("a", "pass"), ("b", "fail")])
        self.assertEqual(r.summary, (2, 1, 0))
        self.assertFalse(r.crash or r.hang)
        self.assertIn("unity_get_test_count", harness_files(None, None)["ff_test_main.c"])

    def test_reset_external_devices_before_each_case(self):
        # 芯片软重启不复位外接器件，上一个用例的配置会留下来（DECISIONS 第 96 条）：每个用例前按档案软复位
        prof = HardwareProfile.load(Path(__file__).resolve().parents[1] / "hardware" / "esp32c3_supermini" / "profile.json")
        main = harness_files(None, None, profile=prof)["ff_test_main.c"]
        self.assertIn("ff_reset_one(bus, FF_IMU_ADDR, 0x6B, 0x80", main)
        self.assertIn("ff_reset_one(bus, FF_PPG_ADDR, 0x09, 0x40", main)
        self.assertIn(".sda_io_num = FF_PIN_I2C0_SDA", main)
        self.assertLess(main.index("ff_reset_devices();"), main.index("unity_run_test_by_index"))
        self.assertIn("i2c_del_master_bus(bus);", main)                 # 复位完删掉总线，用例自己建
        # 不给档案、或档案里没写复位方法：不生成复位代码
        self.assertNotIn("ff_reset_devices", harness_files(None, None)["ff_test_main.c"])
        bare = replace(prof, devices=tuple(replace(d, reset=None) for d in prof.devices))
        self.assertNotIn("ff_reset_devices", harness_files(None, None, profile=bare)["ff_test_main.c"])

    def test_reset_field_checked(self):
        prof = HardwareProfile.load(Path(__file__).resolve().parents[1] / "hardware" / "esp32c3_supermini" / "profile.json")
        self.assertEqual(prof.problems(), [])
        back = HardwareProfile.from_dict(prof.to_dict())               # 存了再读，复位方法不丢
        self.assertEqual(back.devices[0].reset, prof.devices[0].reset)
        bad = replace(prof, devices=(Device("imu", "MPU6050", "i2c0", 0x68, reset=Fact({"reg": 0x6B})),))
        self.assertTrue(any("reset" in p for p in bad.problems()))
        no_addr = replace(prof, devices=(Device("led", "LED", "gpio", None,
                                                reset=Fact({"reg": 1, "value": 1, "wait_ms": 1})),))
        self.assertTrue(any("I2C" in p for p in no_addr.problems()))

    def test_unescape_chinese_names(self):
        raw = f"{BEGIN}\n./main/t.c:9:\\xE6\\xB8\\x85\\xE9\\x9B\\xB6 ok:PASS\n1 Tests 0 Failures 0 Ignored\n{END}"
        r = parse_board_output(raw)
        self.assertEqual(r.cases[0].name, "清零 ok")
        self.assertTrue(r.ok)

    def test_crash_hang_and_not_started(self):
        crash = parse_board_output(f"{BEGIN}\nGuru Meditation Error: Core  0 panic'ed (LoadProhibited)\nBacktrace: 0x4200")
        self.assertIn("Guru Meditation", crash.crash)
        self.assertTrue(crash.hang)
        self.assertFalse(crash.ok)
        self.assertIn("芯片崩溃", crash.report())
        none = parse_board_output("rst:0x1 (POWERON),boot:0x8\n")
        self.assertFalse(none.started)
        self.assertIn("没有看到测试开始标记", none.report())
        empty = parse_board_output(f"{BEGIN}\n0 Tests 0 Failures 0 Ignored\n{END}")
        self.assertFalse(empty.ok)                                         # 一个用例都没跑不算通过
        self.assertIn("一个用例都没有跑", empty.report())
        good = parse_board_output(f"{BEGIN}\nmain/t.c:3:a:PASS\n1 Tests 0 Failures 0 Ignored\n{END}")
        self.assertTrue(good.ok)
        self.assertEqual(good.verdict(), "pass")


class RunnerTest(unittest.TestCase):
    def test_find_port_and_skip(self):
        self.assertEqual(find_port("/dev/ttyX", {}), "/dev/ttyX")
        self.assertEqual(find_port(None, {"FF_SERIAL_PORT": "COM5"}), "COM5")
        runner = BoardRunner(idf=None, port="/nonexistent/ttyACM9")
        res = runner.run(project=None)
        self.assertEqual(res.verdict(), "skipped")                        # 板子没连：跳过，绝不判通过
        self.assertIn("找不到板子的串口", res.report())


if __name__ == "__main__":
    unittest.main()


class HostTestTest(unittest.TestCase):
    MOD_H = "#pragma once\n#include <stdint.h>\nfloat imu_accel_g(int16_t raw, int range_g);\n"
    MOD_C = '#include "imu_convert.h"\nfloat imu_accel_g(int16_t raw, int range_g) { return raw * range_g / 32768.0f; }\n'
    TEST = """#include "unity.h"
#include "imu_convert.h"
void setUp(void) {}
void tearDown(void) {}
static void test_one_g(void) { TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, imu_accel_g(16384, 2)); }
static void test_wrong_on_purpose(void) { TEST_ASSERT_FLOAT_WITHIN(0.001f, 2.0f, imu_accel_g(16384, 2)); }
int main(void) { UNITY_BEGIN(); RUN_TEST(test_one_g); RUN_TEST(test_wrong_on_purpose); return UNITY_END(); }
"""

    @unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
    def test_host_run(self):
        import tempfile
        from firmforge.target.host_test import run_host_tests
        with tempfile.TemporaryDirectory() as d:
            files = {"imu_convert.c": self.MOD_C, "include/imu_convert.h": self.MOD_H,
                     "test_host/test_imu_convert.c": self.TEST}
            r = run_host_tests(files, "test_host/test_imu_convert.c", Path(d) / "w")
            self.assertTrue(r.compiled, r.compile_log)
            self.assertEqual(r.passed, ["test_one_g"])
            self.assertEqual([n for n, _ in r.failed], ["test_wrong_on_purpose"])
            self.assertEqual(r.verdict(), "fail")
            broken = run_host_tests({**files, "imu_convert.c": "int x = ;\n"}, "test_host/test_imu_convert.c", Path(d) / "w")
            self.assertFalse(broken.compiled)
            self.assertIn("测试编译失败", broken.report())

    def test_skip_without_cc(self):
        import tempfile
        from firmforge.config import Toolchain
        from firmforge.target.host_test import run_host_tests
        with tempfile.TemporaryDirectory() as d:
            r = run_host_tests({}, "t.c", Path(d), Toolchain.from_env({"FF_CC": "/nonexistent/gcc"}))
        self.assertEqual(r.verdict(), "skipped")

"""代码合规检查（DECISIONS 第 66 条第 ⑥ 步）。"""

import unittest

import helpers  # noqa: F401
from firmforge.grounding.compliance import check_code, strip_c
from firmforge.grounding.rules import Citation, Rule, RuleSet


def R(id, check, text="x", kind="api"):
    return Rule(id=id, kind=kind, scope=("i2c",), text=text, cite=Citation("d", None, "", "q"),
                tier="official_code", status="trusted", check=check)


RULES = RuleSet([
    R("R-I2C-002", {"type": "api_absent", "symbol": "i2c_driver_install"}, "不许用旧接口 i2c_driver_install()"),
    R("R-ALL-001", {"type": "api_absent", "symbol": "esp_deep_sleep_start"}, "不许进入深度睡眠", "forbidden"),
    R("R-PIN-001", {"type": "pin_forbidden", "gpio": [19, 20]}, "不许用 USB 引脚", "pin"),
    R("R-IMU-003", {"type": "call_order", "before": "i2c_new_master_bus", "after": "i2c_master_bus_add_device"},
      "先建总线再挂设备", "sequence"),
    R("R-IMU-002", None, "上电后先清 SLEEP 位", "sequence"),
])

GOOD = """#include "hw_config.h"
#include "driver/i2c_master.h"
/* 旧写法 i2c_driver_install() 已废弃，这里不用 */
void imu_init(void) {
    i2c_master_bus_config_t c = { .sda_io_num = FF_PIN_I2C0_SDA, .scl_io_num = FF_PIN_I2C0_SCL };
    i2c_new_master_bus(&c, &bus);
    i2c_master_bus_add_device(bus, &dev_cfg, &dev);
    printf("esp_deep_sleep_start() is not called\\n");
}
"""

BAD = """#include "driver/i2c.h"
void imu_init(void) {
    i2c_master_bus_add_device(bus, &dev_cfg, &dev);
    i2c_master_bus_config_t c = { .sda_io_num = 4, .scl_io_num = (gpio_num_t)5 };
    i2c_new_master_bus(&c, &bus);
    i2c_driver_install(I2C_NUM_0, I2C_MODE_MASTER, 0, 0, 0);
    gpio_set_level(48, 1);
    io.pin_bit_mask = (1ULL << 7);
    gpio_reset_pin(GPIO_NUM_19);
    esp_deep_sleep_start();
}
"""


class ComplianceTest(unittest.TestCase):
    def test_good_code_passes(self):
        rep = check_code({"main/imu.c": GOOD, "main/include/hw_config.h": "#define FF_PIN_I2C0_SDA 4"}, RULES)
        self.assertTrue(rep.ok, rep.summary())
        self.assertEqual(rep.violations, [])                      # 注释、字符串里的名字不算
        self.assertEqual(rep.unchecked, ["R-IMU-002"])            # 没有机器检查项的交给 Agent 审查

    def test_bad_code(self):
        rep = check_code({"main/imu.c": BAD}, RULES)
        found = sorted((v.rule, v.line, v.level) for v in rep.violations)
        self.assertEqual(found, [
            ("PIN-LITERAL", 4, "error"), ("PIN-LITERAL", 4, "error"),            # .sda_io_num = 4、.scl_io_num = 5
            ("PIN-LITERAL", 7, "error"), ("PIN-LITERAL", 8, "error"),            # gpio_set_level(48)、1ULL << 7
            ("R-ALL-001", 10, "error"), ("R-I2C-002", 6, "error"),
            ("R-IMU-003", 3, "warning"), ("R-PIN-001", 9, "error")])
        self.assertFalse(rep.ok)
        self.assertIn("违规 [R-I2C-002] main/imu.c:6", rep.summary())
        self.assertIn("提示 [R-IMU-003]", rep.summary())

    def test_strip_keeps_lines(self):
        src = 'a /* x\ny */ b // c\n"str // no" \'q\' d'
        out = strip_c(src)
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertNotIn("str", out)
        self.assertIn("d", out)


if __name__ == "__main__":
    unittest.main()

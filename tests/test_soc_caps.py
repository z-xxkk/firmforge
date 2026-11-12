"""芯片能力从 ESP-IDF 源码读（DECISIONS 第 114 条）。"""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.target.esp_idf import SDKCONFIG_DEFAULTS, idf_path, sdkconfig_for
from firmforge.target.soc_caps import BUILTIN, _eval_mask, caps_file, list_targets, load_caps, parse_caps, sdkconfig_defaults

HAS_IDF = caps_file(idf_path(), "esp32c3").is_file()

SAMPLE = """
#define SOC_GPIO_PIN_COUNT              (40)
#define SOC_GPIO_VALID_GPIO_MASK        (0xFFFFFFFFFFULL & ~(0ULL | BIT24 | BIT28 | BIT29 | BIT30 | BIT31))
#define SOC_GPIO_VALID_OUTPUT_GPIO_MASK (SOC_GPIO_VALID_GPIO_MASK & ~(0ULL | BIT34 | BIT35 | BIT36 | BIT37 | BIT38 | BIT39))
#define SOC_I2C_NUM            (2U)
#define SOC_UART_NUM           (3)
#define SOC_RMT_SUPPORTED      1   // 注释
#define SOC_TWAI_SUPPORTED     0
"""


class ParseTest(unittest.TestCase):
    def test_parse_sample(self):
        c = parse_caps("x", SAMPLE)
        self.assertEqual(len(c.gpios), 35)
        self.assertNotIn(24, c.gpios)
        self.assertEqual(c.input_only, (34, 35, 36, 37, 38, 39))
        self.assertIn("rmt", c.peripherals)
        self.assertIn("i2c", c.peripherals)
        self.assertNotIn("twai", c.peripherals)
        self.assertFalse(c.usb_serial_jtag)

    def test_mask_only_arithmetic(self):
        self.assertEqual(_eval_mask("BIT3 | 0x1ULL", {}), 9)
        self.assertIsNone(_eval_mask("__import__('os')", {}))
        self.assertIsNone(_eval_mask("SOC_UNKNOWN", {}))

    def test_generated_sdkconfig(self):
        self.assertIn("USB_SERIAL_JTAG", sdkconfig_defaults(BUILTIN["esp32c3"], 4))
        self.assertIn("4MB", sdkconfig_defaults(BUILTIN["esp32c3"], 4))
        self.assertIn("UART_DEFAULT", sdkconfig_defaults(BUILTIN["esp32"]))

    def test_existing_chips_unchanged(self):
        for t in ("esp32", "esp32s3", "esp32c3"):                      # 评测编译结果不变
            self.assertEqual(sdkconfig_for(t), SDKCONFIG_DEFAULTS[t])

    def test_builtin_when_no_idf(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_caps("esp32c3", Path(d)), BUILTIN["esp32c3"])
            self.assertIsNone(load_caps("esp32p4", Path(d)))
            self.assertEqual(list_targets(Path(d)), [])


@unittest.skipUnless(HAS_IDF, "本机没有 ESP-IDF v6.1")
class RealIdfTest(unittest.TestCase):
    def test_builtin_matches_idf(self):
        for t, b in BUILTIN.items():
            c = load_caps(t)
            self.assertEqual((c.gpios, c.input_only, c.usb_serial_jtag, set(c.peripherals)),
                             (b.gpios, b.input_only, b.usb_serial_jtag, set(b.peripherals)), t)

    def test_new_chip_from_idf(self):
        self.assertIn("esp32p4", list_targets(idf_path()))
        p4 = load_caps("esp32p4")
        self.assertEqual(len(p4.gpios), 55)
        self.assertTrue(p4.usb_serial_jtag)
        self.assertIn("CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y", sdkconfig_for("esp32p4"))
        with self.assertRaises(ValueError):
            sdkconfig_for("esp99")


if __name__ == "__main__":
    unittest.main()

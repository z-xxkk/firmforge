"""ESP-IDF 编译接入：hw_config.h 生成、报错解析、工程生成、ESP-IDF 不可用时如实报错（DECISIONS 第 66、76–78 条）。"""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.target.esp_idf import (IdfEnv, IdfProject, IdfUnavailable, hw_config_header, idf_path,
                                      parse_diagnostics)
from test_rule_verifier import make_profile


class HwConfigTest(unittest.TestCase):
    def test_macros(self):
        h = hw_config_header(make_profile(**{"i2c0.sda": 4, "i2c0.scl": 5, "uart1.tx": 10}))
        self.assertIn("#define FF_PIN_I2C0_SDA 4", h)
        self.assertIn("#define FF_PIN_UART1_TX 10", h)
        self.assertIn("#define FF_IMU_ADDR 0x68  /* MPU6050 */", h)
        self.assertTrue(h.startswith("/* FirmForge"))


class DiagnosticsTest(unittest.TestCase):
    def test_only_main_dir(self):
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            log = (f"{proj}/main/app_main.c:12:5: error: implicit declaration of function 'i2c_driver_install'\n"
                   f"{proj}/main/app_main.c:12:5: error: implicit declaration of function 'i2c_driver_install'\n"
                   "../main/mpu6050.c:30: warning: unused variable 'x'\n"
                   "/home/u/esp/esp-idf-v6.1/components/freertos/x.c:1:1: warning: from ESP-IDF itself\n"
                   "ninja: build stopped: subcommand failed.\n")
            diags = parse_diagnostics(log, proj)
        self.assertEqual([d.render() for d in diags],
                         ["main/app_main.c:12: error: implicit declaration of function 'i2c_driver_install'",
                          "main/mpu6050.c:30: warning: unused variable 'x'"])


class _FakeEnv:
    root = Path("/nonexistent")
    version = "v6.1"


class ProjectTest(unittest.TestCase):
    def test_write_project(self):
        with tempfile.TemporaryDirectory() as d:
            proj = IdfProject(Path(d) / "p", _FakeEnv(), requires=("esp_driver_i2c",))
            prof = make_profile(**{"i2c0.sda": 4})
            proj.write(prof, {"app_main.c": "void app_main(void) {}", "drv/mpu6050.c": "int x;",
                              "include/mpu6050.h": "#pragma once"})
            main = Path(d) / "p" / "main"
            cm = (main / "CMakeLists.txt").read_text(encoding="utf-8")
            self.assertIn('SRCS "app_main.c" "drv/mpu6050.c"', cm)
            self.assertIn("PRIV_REQUIRES esp_driver_i2c", cm)
            self.assertIn("FF_PIN_I2C0_SDA 4", (main / "include" / "hw_config.h").read_text(encoding="utf-8"))
            self.assertIn("CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y",
                          (Path(d) / "p" / "sdkconfig.defaults").read_text(encoding="utf-8"))
            proj.write(prof, {"app_main.c": "void app_main(void) {}"})          # 上一次的源文件清掉
            self.assertFalse((main / "drv" / "mpu6050.c").exists())
            self.assertIn('SRCS "app_main.c"\n', (main / "CMakeLists.txt").read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):
                proj.write(prof, {"../evil.c": "x"})


class UnavailableTest(unittest.TestCase):
    def test_missing_idf(self):
        with self.assertRaises(IdfUnavailable) as cm:
            IdfEnv.load(Path("/nonexistent/esp-idf"))
        self.assertIn("FF_IDF_PATH", str(cm.exception))
        self.assertEqual(idf_path({"FF_IDF_PATH": "/a", "IDF_PATH": "/b"}), Path("/a"))
        self.assertEqual(idf_path({"IDF_PATH": "/b"}), Path("/b"))


if __name__ == "__main__":
    unittest.main()

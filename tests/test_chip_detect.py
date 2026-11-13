"""插上板子自动识别芯片（DECISIONS 第 114 条）：esptool 的输出解析、对不上时不烧录。"""

import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.runs import read_events
from firmforge.target.chip_detect import ChipInfo, chip_target, mismatch, parse_esptool

V5 = """esptool v5.6.0
Connected to ESP32-C3 on /dev/ttyACM0:
Chip type:          ESP32-C3 (QFN32) (revision v0.4)
Features:           Wi-Fi, BT 5 (LE), Single Core, 160MHz, Embedded Flash 4MB (XMC)
Crystal frequency:  40MHz
USB mode:           USB-Serial/JTAG
MAC:                aa:bb:cc:dd:ee:ff

Detected flash size: 4MB
Hard resetting via RTS pin...
"""
# 负责人的 ESP32-C3 SuperMini 实测输出（2026-11-13，esptool v5.6.0）
REAL_C3 = """Connecting...
Detecting chip type... ESP32-C3
Connected to ESP32-C3 on /dev/ttyACM0:
Chip type:          ESP32-C3 AZ (QFN32) (revision v1.1)
Features:           Wi-Fi, BT 5 (LE), Single Core, 160MHz, Embedded Flash 4MB (XMC)
Crystal frequency:  40MHz
USB mode:           USB-Serial/JTAG

Flash Memory Information:
=========================
Manufacturer: 46
Device: 4016
Detected flash size: 4MB

Hard resetting via RTS pin...
"""
V4 = "Chip is ESP32-S3 (QFN56) (revision v0.2)\nDetected flash size: 8MB\n"
FAIL = "esptool v5.6.0\nA fatal error occurred: Failed to connect to Espressif device: No serial data received.\n"


class ParseTest(unittest.TestCase):
    def test_v5_output(self):
        i = parse_esptool(V5, "/dev/ttyACM0")
        self.assertEqual((i.target, i.flash_mb, i.port), ("esp32c3", 4, "/dev/ttyACM0"))
        self.assertIn("QFN32", i.description)
        self.assertIn("Wi-Fi", i.features)

    def test_real_board_output(self):
        i = parse_esptool(REAL_C3)
        self.assertEqual((i.target, i.description, i.flash_mb), ("esp32c3", "ESP32-C3 AZ (QFN32) (revision v1.1)", 4))
        self.assertEqual(mismatch(i, "esp32c3"), "")

    def test_old_output_and_failure(self):
        self.assertEqual((parse_esptool(V4).target, parse_esptool(V4).flash_mb), ("esp32s3", 8))
        f = parse_esptool(FAIL)
        self.assertFalse(f.ok)
        self.assertIn("Failed to connect", f.error)

    def test_names_and_mismatch(self):
        self.assertEqual(chip_target("ESP32-C61"), "esp32c61")
        c3 = parse_esptool(V5)
        self.assertEqual(mismatch(c3, "esp32c3"), "")
        self.assertIn("不往这块板子上烧", mismatch(c3, "esp32s3"))
        self.assertEqual(mismatch(ChipInfo(error="x"), "esp32s3"), "")          # 没识别出来：不下结论


class RetryTest(unittest.TestCase):
    def test_failed_detection_is_retried(self):
        # 第一次识别失败（串口被占用）不记住，下次再试；识别出来以后就不再跑 esptool（代码审查发现的问题）
        from unittest import mock
        from firmforge.target import board as bmod
        with tempfile.NamedTemporaryFile() as port:
            r = bmod.BoardRunner.__new__(bmod.BoardRunner)
            r.idf, r.port, r._chip = type("I", (), {"python": "py"})(), port.name, None
            answers = [parse_esptool(FAIL), parse_esptool(V5)]
            with mock.patch.object(bmod, "detect_chip", side_effect=lambda *a: answers.pop(0)) as det:
                self.assertFalse(r.chip().ok)
                self.assertEqual(r.chip().target, "esp32c3")
                self.assertEqual(r.chip().target, "esp32c3")
            self.assertEqual(det.call_count, 2)


class FakeBoard:
    def __init__(self, info):
        self.info, self.runs = info, 0

    def chip(self):
        return self.info

    def run(self, *a, **k):
        self.runs += 1
        raise AssertionError("芯片对不上时不该烧录")


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class FlowChipTest(unittest.TestCase):
    def test_wrong_chip_not_flashed_once_detected(self):
        from test_project_wiring import flow, WiredTeam
        with tempfile.TemporaryDirectory() as d:
            f, _ = flow(d, WiredTeam())
            f.board = FakeBoard(parse_esptool(V5))
            f.profile = f.profile.__class__(**{**f.profile.__dict__,
                                               "chip": f.profile.chip.__class__("esp32s3")})
            self.assertIn("不往这块板子上烧", f._check_chip())
            self.assertIn("不往这块板子上烧", f._check_chip())                       # 只识别一次
            ev = [e for e in read_events(f.rd.path) if e["type"] == "chip_detected"]
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["data"]["target"], "esp32c3")


if __name__ == "__main__":
    unittest.main()

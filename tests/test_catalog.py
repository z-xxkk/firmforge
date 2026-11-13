"""知识库目录（DECISIONS 第 114 条）：固定层级、按登记信息归类、搜索、驳回错的规则。"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.grounding.catalog import build_catalog, chip_title, peripheral_of, reject_rule

ROOT = Path(helpers.ROOT)


class RealCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = build_catalog(ROOT)

    def test_fixed_order_and_no_errors(self):
        self.assertEqual(self.cat.errors, [])
        self.assertEqual([c.title for c in self.cat.root.children][:4], ["芯片", "开发框架", "外接器件", "开发板"])
        chips = [c.title for c in self.cat.root.find(("chip",)).children]
        self.assertIn("ESP32-C3", chips)

    def test_placement(self):
        where = {e.id: e.path for e in self.cat.all_entries()}
        self.assertEqual(where["esp32c3-ds"], ("芯片", "ESP32-C3"))                  # 芯片手册归芯片
        self.assertEqual(where["R-GPIO-001"][:2], ("芯片", "ESP32-C3"))              # 引用芯片手册的规则
        self.assertEqual(where["idf-driver-i2c-master-h"], ("开发框架", "ESP-IDF v6.1", "I2C"))
        self.assertEqual(where["mpu6050-rm"], ("外接器件", "运动传感器", "MPU6050"))   # 类别写在硬件档案里
        self.assertEqual(where["board-measured"], ("开发板", "ESP32-C3 SuperMini"))   # 实测记录归板子
        self.assertEqual(where["C-001"], ("开发板", "ESP32-C3 SuperMini"))
        mpu_rules = [e for e in self.cat.all_entries() if e.kind == "rule" and "mpu6050" in e.data.get("scope", [])]
        self.assertTrue(mpu_rules and all(e.path[0] == "外接器件" for e in mpu_rules))   # 写了器件型号的规则归器件
        # 同一份资料几块板子都登记了：只出现一次，记下每块板子
        mpu = [e for e in self.cat.all_entries() if e.id == "mpu6050-rm"]
        self.assertEqual(len(mpu), 1)
        self.assertGreaterEqual(len(mpu[0].boards), 3)

    def test_chip_info_from_idf_and_order(self):
        c3 = self.cat.root.find(("chip", "esp32c3"))
        self.assertEqual(len(c3.info["gpios"]), 22)
        idf = self.cat.root.find(("framework", "idf"))
        keys = [c.key for c in idf.children]
        self.assertEqual(keys[:2], ["gpio", "i2c"])                                 # 外设按 soc_caps 的顺序

    def test_search(self):
        hits = self.cat.search("who_am_i")
        self.assertTrue(any(e.id == "C-001" for e in hits))
        self.assertEqual(self.cat.search("   "), [])

    def test_helpers(self):
        self.assertEqual(chip_title("esp32c3"), "ESP32-C3")
        self.assertEqual(chip_title("esp32"), "ESP32")
        self.assertEqual(peripheral_of(["all", "i2c", "gpio"]), "gpio")
        self.assertEqual(peripheral_of(["all"]), "其他")


class RejectTest(unittest.TestCase):
    def test_reject_moves_back_to_draft(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rules.json"
            shutil.copy(ROOT / "hardware" / "esp32c3_supermini" / "rules.json", p)
            with self.assertRaises(ValueError):
                reject_rule(p, "R-GPIO-001", "  ")                                   # 没写理由
            r = reject_rule(p, "R-GPIO-001", "页码写错了")
            self.assertEqual(r["status"], "draft")
            saved = {x["id"]: x for x in json.loads(p.read_text(encoding="utf-8"))}
            self.assertTrue(any("驳回" in n and "页码写错了" in n for n in saved["R-GPIO-001"]["notes"]))
            with self.assertRaises(ValueError):
                reject_rule(p, "R-NOPE-001", "x")


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class PageRejectTest(unittest.TestCase):
    def test_reject_in_page(self):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.knowledge_page import KnowledgePage, is_rejected
        from firmforge.gui.widgets import set_theme
        QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeCatalogTest")
        set_theme(th.current(False))
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d)
            shutil.copytree(ROOT / "hardware" / "esp32c3_supermini", proj / "hardware" / "esp32c3_supermini")
            page = KnowledgePage(proj)
            e = next(x for x in page.catalog.all_entries() if x.id == "R-GPIO-001")
            page.reveal(e)
            self.assertFalse(page.reject_row.isHidden())
            self.assertFalse(page.reject_current())                                   # 没写理由：不驳回
            page.comment.setText("页码写错了")
            got = []
            page.rules_changed.connect(lambda: got.append(1))
            self.assertTrue(page.reject_current())
            e2 = next(x for x in page.catalog.all_entries() if x.id == "R-GPIO-001")
            self.assertTrue(is_rejected(e2.data))
            self.assertIs(page.current, e2)
            self.assertTrue(page.reject_row.isHidden())                               # 驳回过的不能再驳回
            self.assertEqual(got, [1])


if __name__ == "__main__":
    unittest.main()

"""规则判定器：七步核对、SVD 与 ESP-IDF 头文件事实核对、人工确认与入库（DECISIONS 第 66 条第 ③ 步）。"""

import gzip
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import helpers  # noqa: F401
from firmforge.grounding.idf import IdfTree
from firmforge.grounding.profile import Device, Fact, HardwareProfile
from firmforge.grounding.rules import Citation, Rule, RuleSet
from firmforge.grounding.sources import Document, SourceLibrary
from firmforge.grounding.svd import SvdDevice
from firmforge.grounding.verifier import RuleVerifier, apply_human, store_trusted

SVD = """<?xml version="1.0"?>
<device><name>ESP32-S3</name><peripherals>
  <peripheral><name>I2C0</name><registers>
    <register><name>CTR</name><access>read-write</access><fields>
      <field><name>MS_MODE</name><bitOffset>4</bitOffset><bitWidth>1</bitWidth></field>
      <field><name>SCL_FORCE_OUT</name><lsb>1</lsb><msb>2</msb></field>
    </fields></register>
    <register><name>SR</name><fields>
      <field><name>BUS_BUSY</name><bitRange>[4:4]</bitRange><access>read-only</access></field>
    </fields></register>
  </registers></peripheral>
  <peripheral derivedFrom="I2C0"><name>I2C1</name></peripheral>
</peripherals></device>"""

I2C_MASTER_H = """#pragma once
typedef struct i2c_master_bus_t *i2c_master_bus_handle_t;
esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *bus_config, i2c_master_bus_handle_t *ret_bus_handle);
esp_err_t i2c_master_transmit_receive(i2c_master_dev_handle_t i2c_dev, const uint8_t *write_buffer, size_t w);
"""


def make_idf(root: Path) -> IdfTree:
    inc = root / "components" / "esp_driver_i2c" / "include" / "driver"
    inc.mkdir(parents=True)
    (inc / "i2c_master.h").write_text(I2C_MASTER_H, encoding="utf-8")
    (root / "tools" / "cmake").mkdir(parents=True)
    (root / "tools" / "cmake" / "version.cmake").write_text(
        "set(IDF_VERSION_MAJOR 6)\nset(IDF_VERSION_MINOR 1)\nset(IDF_VERSION_PATCH 0)\n", encoding="utf-8")
    return IdfTree(root)


def make_profile(**pins) -> HardwareProfile:
    return HardwareProfile(chip=Fact("esp32s3"), chip_variant=Fact("ESP32-S3FH4R2"), board=Fact("esp32s3_supermini"),
                           framework=Fact("esp-idf"), framework_version=Fact("v6.1"),
                           pins={k: Fact(v) for k, v in pins.items()},
                           devices=(Device("imu", "MPU6050", "i2c0", 0x68),))


def make_library() -> SourceLibrary:
    lib = SourceLibrary(Path("/nonexistent"))
    lib.add(Document("idf-i2c-master-h", "i2c_master.h", "header", "official_code", version="v6.1",
                     pages=(I2C_MASTER_H,)))
    lib.add(Document("idf-old-h", "i2c.h", "header", "official_code", version="v5.5", pages=("i2c_driver_install(",)))
    lib.add(Document("esp32s3-ds", "ESP32-S3 Datasheet", "pdf", "official_manual",
                     url="https://documentation.espressif.com/esp32-s3_datasheet_en.pdf",
                     pages=("cover", "GPIO19 and GPIO20 are used by USB D- and D+.",
                            "I2C supports standard mode 100 kbit/s and fast mode 400 kbit/s.")))
    lib.add(Document("mpu6050-ps", "MPU-6000/6050 Product Specification", "pdf", "official_manual",
                     url="https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Datasheet1.pdf",
                     pages=("The slave address of the MPU-60X0 is b110100X which is 7 bits long. "
                            "The LSB bit of the 7 bit address is determined by the logic level on pin AD0.",
                            "The device will come up in sleep mode upon power-up.")))
    return lib


def R(id, kind, scope, text, doc, page, quote, check=None, tier="official_manual", status="draft"):
    return Rule(id=id, kind=kind, scope=tuple(scope), text=text, cite=Citation(doc, page, "", quote), tier=tier,
                status=status, check=check)


def good_rules() -> RuleSet:
    return RuleSet([
        R("R-I2C-001", "api", ["i2c"], "I2C 总线用 i2c_new_master_bus() 创建", "idf-i2c-master-h", None,
          "esp_err_t i2c_new_master_bus(", {"type": "api_exists", "symbol": "i2c_new_master_bus",
                                            "header": "driver/i2c_master.h"}, tier="official_code"),
        R("R-PIN-001", "pin", ["all"], "不许使用 GPIO19、GPIO20（USB）", "esp32s3-ds", 2,
          "GPIO19 and GPIO20 are used by USB", {"type": "pin_forbidden", "gpio": [19, 20]}),
        R("R-I2C-002", "peripheral", ["i2c"], "I2C 速率不超过 400 kHz", "esp32s3-ds", 3,
          "fast mode 400 kbit/s"),
        R("R-IMU-001", "device", ["mpu6050"], "MPU6050 地址 0x68（AD0 接低）", "mpu6050-ps", 1,
          "The slave address of the MPU-60X0 is b110100X"),
        R("R-IMU-002", "sequence", ["mpu6050"], "上电后先清 PWR_MGMT_1 的 SLEEP 位再读数据", "mpu6050-ps", 2,
          "come up in sleep mode upon power-up"),
    ])


class SvdTest(unittest.TestCase):
    def test_parse_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s3.svd.gz"
            p.write_bytes(gzip.compress(SVD.encode()))
            svd = SvdDevice.load(p)
        self.assertEqual(svd.check_field("I2C0", "CTR", "MS_MODE", 1)[0], "ok")
        self.assertEqual(svd.check_field("I2C1", "CTR", "MS_MODE", 1)[0], "ok")          # 派生外设
        self.assertEqual(svd.fields[("I2C0", "CTR", "SCL_FORCE_OUT")].width, 2)
        self.assertEqual(svd.check_field("I2C0", "CTR", "MS_MODE", 2)[0], "wrong")       # 1 位放不下 2
        self.assertEqual(svd.check_field("I2C0", "SR", "BUS_BUSY", 1)[0], "wrong")       # 只读
        self.assertEqual(svd.check_field("I2C0", "CTR", "NOPE", 1)[0], "unknown")        # SVD 不全：无法核对
        self.assertEqual(svd.check_field("SPI9", "X", "Y", 1)[0], "unknown")


class IdfTreeTest(unittest.TestCase):
    def test_declares(self):
        with tempfile.TemporaryDirectory() as d:
            idf = make_idf(Path(d))
            self.assertEqual(idf.version(), "v6.1")
            self.assertTrue(idf.declares("driver/i2c_master.h", "i2c_new_master_bus")[0])
            self.assertTrue(idf.declares("driver/i2c_master.h", "i2c_master_bus_handle_t")[0])
            self.assertFalse(idf.declares("driver/i2c_master.h", "i2c_master_write_read_device")[0])
            self.assertFalse(idf.declares("driver/i2c.h", "i2c_driver_install")[0])
            self.assertEqual(len(idf.symbol_anywhere("i2c_master_transmit_receive")), 1)
            with self.assertRaises(FileNotFoundError):
                IdfTree(Path(d) / "components")


class VerifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.idf = make_idf(Path(self.tmp.name))
        self.svd = SvdDevice.parse(SVD)
        self.seen_pages = []

    def tearDown(self):
        self.tmp.cleanup()

    def semantic(self, rule, page):
        self.seen_pages.append((rule.id, page))
        if "自动开始测量" in rule.text:
            return False, "原文说上电处于睡眠模式，规则说法相反"
        return True, "规则与原文一致"

    def verifier(self, **pins):
        return RuleVerifier(make_library(), make_profile(**pins), idf=self.idf, svd=self.svd, semantic=self.semantic)

    def test_all_good(self):
        rep = self.verifier(**{"i2c0.sda": 8, "i2c0.scl": 9}).run(good_rules())
        self.assertTrue(rep.ok, rep.summary())
        self.assertEqual(len(rep.passed), 5)
        steps = {r.rule.id: [s[1] for s in r.steps] for r in rep.results}
        self.assertEqual(steps["R-I2C-001"], ["ok", "ok", "ok", "ok", "skip"])        # 程序已核对，不交给 Agent
        self.assertEqual(steps["R-IMU-002"][4], "ok")                                 # 语义核对
        lib = make_library()
        for rid, page in self.seen_pages:                                             # 核对 Agent 只看原文那一页
            cite = good_rules().rules[rid].cite
            self.assertEqual(page, lib.get(cite.doc).page(cite.page))
        self.assertTrue(all(r.status == "checked" for r in rep.checked_rules()))

    def test_semantic_check_sees_layout_text(self):
        # 核对 Agent 也要看到按版面的文字，否则核对不了"某一位在哪一列"（DECISIONS 第 98 条）
        lib = make_library()
        ps = lib.get("mpu6050-ps")
        lib.docs["mpu6050-ps"] = replace(ps, layout=("", "SLEEP   B6   列对齐的版面文字"), bits=("", "PWR_MGMT_1（0x6B）：B6 = SLEEP"))
        v = RuleVerifier(lib, make_profile(**{"i2c0.sda": 8, "i2c0.scl": 9}), idf=self.idf, svd=self.svd,
                         semantic=self.semantic)
        v.run(RuleSet([good_rules().rules["R-IMU-002"]]))
        page = dict(self.seen_pages)["R-IMU-002"]
        self.assertIn("The device will come up in sleep mode", page)
        self.assertIn("列对齐的版面文字", page)
        self.assertIn("B6 = SLEEP", page)                                   # 位标注也给核对 Agent

    def test_each_step_rejects(self):
        rules = RuleSet([
            R("R-X-001", "device", ["mpu6050"], "x", "no-such-doc", 1, "q"),                                 # 第 0 步
            R("R-X-002", "api", ["i2c"], "旧接口", "idf-old-h", None, "i2c_driver_install(", tier="official_code"),  # 版本不符
            R("bad", "api", ["i2c"], "x", "esp32s3-ds", 2, "GPIO19"),                                         # 第 1 步
            R("R-X-003", "device", ["mpu6050"], "地址 0x69", "mpu6050-ps", 1, "address is 0x69"),             # 第 2 步
            R("R-X-004", "device", ["mpu6050"], "x", "mpu6050-ps", 7, "x"),                                   # 页码越界
            R("R-X-005", "api", ["i2c"], "用 i2c_master_write_read_device()", "idf-i2c-master-h", None,
              "i2c_new_master_bus(", {"type": "api_exists", "symbol": "i2c_master_write_read_device",
                                      "header": "driver/i2c_master.h"}, tier="official_code"),          # 第 3 步
            R("R-X-006", "register", ["i2c"], "x", "esp32s3-ds", 3, "fast mode",
              {"type": "register_value", "peripheral": "I2C0", "register": "CTR", "field": "MS_MODE",
               "value": 3}),                                                                             # 第 3 步 SVD
            R("R-X-007", "sequence", ["mpu6050"], "上电后自动开始测量", "mpu6050-ps", 1,
              "The slave address"),                                                                      # 第 4 步
        ])
        rep = self.verifier().run(rules, scopes=set())
        bad_step = {r.rule.id: next(s for s, res, _ in r.steps if res == "wrong") for r in rep.rejected}
        self.assertEqual(bad_step, {"R-X-001": 0, "R-X-002": 0, "bad": 1, "R-X-003": 2, "R-X-004": 2,
                                    "R-X-005": 3, "R-X-006": 3, "R-X-007": 4})
        self.assertIn("找不到引用的原文", rep.summary())

    def test_unknown_when_svd_missing_field(self):
        rules = RuleSet([R("R-REG-001", "register", ["i2c"], "x", "esp32s3-ds", 3, "fast mode",
                           {"type": "register_value", "peripheral": "I2C0", "register": "CTR", "field": "NOPE",
                            "value": 1})])
        rep = RuleVerifier(make_library(), make_profile(), svd=self.svd).run(rules, scopes=set())
        self.assertFalse(rep.rejected)                       # SVD 不全时是"无法核对"，不是判错
        self.assertEqual([s[1] for s in rep.results[0].steps][3:], ["unknown", "unknown"])

    def test_conflicts_missing_and_profile(self):
        rules = good_rules()
        rules.put(R("R-I2C-003", "api", ["i2c"], "不许用 i2c_new_master_bus", "idf-i2c-master-h", None,
                    "i2c_new_master_bus(", {"type": "api_absent", "symbol": "i2c_new_master_bus"},
                    tier="official_code"))
        rules.rules.pop("R-IMU-002")
        rep = self.verifier(**{"i2c0.sda": 19, "i2c0.scl": 9}).run(rules)
        self.assertFalse(rep.ok)
        self.assertTrue(any("i2c_new_master_bus" in c for c in rep.conflicts))
        self.assertTrue(any(m.startswith("mpu6050") and "操作顺序" in m for m in rep.missing))
        self.assertTrue(any("GPIO19" in p and "R-PIN-001" in p for p in rep.profile_problems))

    def test_human_and_store(self):
        rep = self.verifier(**{"i2c0.sda": 8, "i2c0.scl": 9}).run(good_rules())
        checked = rep.checked_rules()
        approved, rejected = apply_human(checked, {"R-I2C-001": ("approve", ""), "R-IMU-001": ("reject", "AD0 接高")})
        self.assertEqual([r.id for r in approved], ["R-I2C-001"])
        self.assertEqual(rejected[0].status, "draft")
        self.assertIn("人工驳回：AD0 接高", rejected[0].notes)
        store = RuleSet()
        trusted = store_trusted(store, approved)
        self.assertEqual(trusted[0].status, "trusted")
        self.assertEqual(store.prompt_block({"i2c"}, "trusted").count("R-I2C-001"), 1)


if __name__ == "__main__":
    unittest.main()

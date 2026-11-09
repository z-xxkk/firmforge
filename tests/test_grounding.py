"""硬件档案、资料登记与引用核对、结构化规则（DECISIONS 第 66–67 条）。"""

import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.grounding.profile import Device, Fact, HardwareProfile, ProfileError
from firmforge.grounding.rules import Citation, Rule, RuleError, RuleSet
from firmforge.grounding.sources import (Document, SourceError, SourceLibrary, is_official, load_document,
                                         load_manifest, normalize, quote_found)


def profile(**pins):
    return HardwareProfile(
        chip=Fact("esp32s3", "measured", "esptool chip_id"), chip_variant=Fact("ESP32-S3FH4R2", "document"),
        board=Fact("esp32s3_supermini"), framework=Fact("esp-idf"), framework_version=Fact("v6.1"),
        pins={k: Fact(v) for k, v in pins.items()},
        reserved_pins={19: "USB D-", 20: "USB D+", 45: "启动配置引脚"},
        devices=(Device("imu", "MPU6050", "i2c0", 0x68),))


def rule(id="R-I2C-001", **kw):
    base = dict(id=id, kind="api", scope=("i2c",), text="I2C 总线用 i2c_new_master_bus() 创建",
                cite=Citation("idf-i2c-master-h", None, "i2c_master.h", "esp_err_t i2c_new_master_bus("),
                tier="official_code", check={"type": "api_exists", "symbol": "i2c_new_master_bus",
                                             "header": "driver/i2c_master.h"})
    base.update(kw)
    return Rule(**base)


class ProfileTest(unittest.TestCase):
    def test_round_trip_and_summary(self):
        p = profile(**{"i2c0.sda": 8, "i2c0.scl": 9})
        with tempfile.TemporaryDirectory() as d:
            p.save(Path(d) / "hw.json")
            q = HardwareProfile.load(Path(d) / "hw.json")
        self.assertEqual(q, p)
        text = "\n".join(q.summary_lines())
        self.assertIn("i2c0.scl=GPIO9", text)
        self.assertIn("GPIO19（USB D-）", text)
        many = HardwareProfile.from_dict({**p.to_dict(), "reserved_pins": {"26": "Flash", "27": "Flash", "28": "Flash",
                                                                          "30": "Flash", "43": "串口"}})
        self.assertIn("不能用的引脚：GPIO26–28、GPIO30（Flash）；GPIO43（串口）", many.summary_lines())
        self.assertIn("MPU6050，总线 i2c0，地址 0x68", text)

    def test_scopes(self):
        p = profile(**{"i2c0.sda": 8, "uart1.tx": 10, "pwm.out": 12, "imu.int": 7, "led": 48})
        self.assertEqual(p.scopes(), {"i2c", "uart", "ledc", "gpio", "mpu6050"})

    def test_problems(self):
        p = profile(**{"i2c0.sda": 8, "i2c0.scl": 8, "led": 19})
        probs = p.problems()
        self.assertTrue(any("GPIO8 同时分给了" in x for x in probs))
        self.assertTrue(any("保留引脚 GPIO19" in x for x in probs))
        self.assertEqual(profile(**{"i2c0.sda": 8}).problems(), [])

    def test_bad_origin_and_missing_field(self):
        with self.assertRaises(ProfileError):
            Fact(1, "guess")
        with self.assertRaises(ProfileError):
            HardwareProfile.from_dict({"chip": "esp32s3"})


class SourcesTest(unittest.TestCase):
    def test_official_whitelist(self):
        self.assertTrue(is_official("https://documentation.espressif.com/esp32-s3_datasheet_en.pdf"))
        self.assertTrue(is_official("https://github.com/espressif/esp-idf/blob/v6.1/components/x.h"))
        self.assertFalse(is_official("https://github.com/someone/espressif-copy"))
        self.assertFalse(is_official("https://www.espboards.dev/esp32/esp32-s3-super-mini/"))
        self.assertFalse(is_official("https://espressif.com.evil.example/x"))
        with self.assertRaises(SourceError):          # 标成官方，网址却不在白名单里
            Document("x", "x", "html", "official_manual", url="https://www.espboards.dev/x")
        Document("x", "x", "html", "community", url="https://www.espboards.dev/x")
        Document("mpu", "PS-MPU-6000A rev 3.1", "pdf", "official_mirror",       # 停产器件：官方文档的非官方镜像
                 url="https://cdn.sparkfun.com/datasheets/Components/General%20IC/PS-MPU-6000A.pdf")

    def test_quote_found(self):
        doc = Document("trm", "TRM", "pdf", "official_manual",
                       pages=("封面", "GPIO45 is a strap-\nping pin   that selects VDD_SPI voltage.\nTable 2-3",
                              "第三页：上电时 GPIO0 与 GPIO46 共同决定启动模式。"))
        self.assertTrue(quote_found(doc, 2, "GPIO45 is a strapping pin that selects VDD_SPI voltage"))
        self.assertTrue(quote_found(doc, 2, "GPIO45 is a strapping pin … VDD_SPI voltage"))
        self.assertFalse(quote_found(doc, 2, "VDD_SPI voltage … GPIO45"))          # 省略段顺序反了
        self.assertFalse(quote_found(doc, 3, "GPIO45 is a strapping pin"))         # 页码不对
        self.assertTrue(quote_found(doc, 3, "上电时 GPIO0 与 GPIO46 共同决定启动模式"))
        self.assertTrue(quote_found(doc, None, "GPIO0 与 GPIO46"))                # 不指定页：整份资料
        self.assertFalse(quote_found(doc, 2, "   "))
        with self.assertRaises(SourceError):
            quote_found(doc, 9, "x")
        self.assertEqual(normalize("Ａ１  –\tb"), "A1 - b")
        # 表格行尾本来就有的连字符不能被当成断行连字符吃掉（实测 ESP32-C3 数据手册 p.21 "GPIO18 USB_D-\n26 GPIO19"）
        table = Document("ds", "DS", "pdf", "official_manual", pages=("25 GPIO18 USB_D-\n26 GPIO19 USB_D+\n",))
        self.assertTrue(quote_found(table, 1, "GPIO18 USB_D-"))
        self.assertTrue(quote_found(table, 1, "GPIO18 USB_D- 26 GPIO19"))

    def test_layout_text_keeps_table_columns(self):
        # 逐字文本丢了表格的列位置（实测 max30102-ds p.12：看不出 DIE_TEMP_RDY 在 B1 列），按版面的文字保留（DECISIONS 第 98 条）
        plain = "Interrupt \nStatus 2\nDIE_ \nTEMP_RDY 0x01 0x00 R"
        layout = ("   REGISTER   B7   B6   B5   B4   B3   B2      B1        B0    REG\n"
                  "     Interrupt                              DIE_               0x01\n"
                  "     Status 2                            TEMP_RDY")
        doc = Document("max", "MAX30102", "pdf", "official_manual", pages=(plain,), layout=(layout,), bits=("B1",))
        self.assertEqual(doc.page_layout(1), layout)
        self.assertEqual(doc.page_layout(None), "")
        self.assertTrue(quote_found(doc, 1, "DIE_ TEMP_RDY 0x01 0x00 R"))           # 逐字文本里的原文照样认
        self.assertTrue(quote_found(doc, 1, "B2 B1 B0 REG"))                        # 只在按版面文字里的原文也认
        self.assertFalse(quote_found(Document("max", "M", "pdf", "official_manual", pages=(plain,)), 1, "B2 B1 B0 REG"))
        with tempfile.TemporaryDirectory() as d:                                  # 存了再读不丢
            lib = SourceLibrary(Path(d))
            lib.add(doc)
            lib.save()
            self.assertTrue((Path(d) / "cache" / "max.layout.json").is_file())
            self.assertEqual(SourceLibrary.load(Path(d)).get("max").layout, (layout,))
            self.assertEqual(SourceLibrary.load(Path(d)).get("max").bits, ("B1",))

    def test_bit_notes_from_cell_coordinates(self):
        # 按每格文字的坐标标出单个位在第几位（DECISIONS 第 99 条）；坐标取自 max30102-ds 第 12 页的寄存器表
        from firmforge.grounding.sources import bit_notes
        f = 8.07
        head = [(66.5, 625.2, f, "REGISTER")] + [(x, 625.2, f, f" B{b}") for b, x in
                                                  zip(range(7, -1, -1), (133.9, 175.9, 219.0, 262.0, 306.0, 350.0, 393.8, 440.4))]
        status2 = [(72.1, 576.0, f, "Interrupt"), (390.0, 576.0, f, "DIE_"), (471.7, 570.5, f, " 0x01"),
                   (506.1, 570.5, f, " 0x00"), (72.4, 565.0, f, "Status 2"), (377.0, 565.0, f, "TEMP_RDY")]
        self.assertEqual(bit_notes(head + status2), ["Interrupt Status 2（0x01）：B1 = DIE_TEMP_RDY"])
        # 一整行连成一段文字（MPU6050 寄存器表的样子）：格子没分开，不标
        whole = [(48.0, 576.0, f, "3A 58 INT_STATUS  R - MOT_INT  -  FIFO_OFLOW_INT  I2C_MST_INT  -  -  DATA_RDY_INT"),
                 (471.7, 576.0, f, " 0x3A")]
        self.assertEqual(bit_notes(head + whole), [])
        # 同一寄存器两个字段落到同一位：对不准，整行不标
        clash = status2 + [(395.0, 576.0, f, "OTHER")]
        self.assertNotIn("B1 = DIE_TEMP_RDY", "\n".join(bit_notes(head + clash)))
        # 多位字段只列名字，不说占哪几位
        multi = [(72.1, 540.0, f, "FIFO Write"), (300.0, 540.0, f, " FIFO_WR_PTR[4:0]"), (471.7, 540.0, f, " 0x04")]
        self.assertIn("多位字段 FIFO_WR_PTR[4:0]", bit_notes(head + multi)[0])
        # 位标注不是原文，不能当引用
        doc = Document("max", "M", "pdf", "official_manual", pages=("x",), bits=("Interrupt Status 2（0x01）：B1 = DIE_TEMP_RDY",))
        self.assertFalse(quote_found(doc, 1, "B1 = DIE_TEMP_RDY"))

    def test_library_round_trip_and_load_text(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "i2c_master.h"
            f.write_text("esp_err_t i2c_new_master_bus(const i2c_master_bus_config_t *cfg);\n", encoding="utf-8")
            doc = load_document(f, id="idf-i2c-master-h", title="i2c_master.h", kind="header",
                                tier="official_code", version="v6.1")
            lib = SourceLibrary(Path(d) / "lib")
            lib.add(doc)
            lib.save()
            again = SourceLibrary.load(Path(d) / "lib")
            self.assertEqual(again.get("idf-i2c-master-h"), doc)
            self.assertTrue(quote_found(again.get("idf-i2c-master-h"), None, "i2c_new_master_bus(const"))
            with self.assertRaises(SourceError):
                lib.add(Document("idf-i2c-master-h", "x", "header", "official_code", sha256="other"))
            with self.assertRaises(SourceError):
                load_document(Path(d) / "nope.pdf", id="n", title="n", kind="pdf", tier="community")


class ManifestTest(unittest.TestCase):
    def test_manifest_checks_fingerprint_and_caches(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "raw"
            raw.mkdir()
            body = "#define I2C_NUM_0 0\n"
            (raw / "a.h").write_text(body, encoding="utf-8")
            sha = hashlib.sha256(body.encode()).hexdigest()
            man = Path(d) / "sources.toml"
            man.write_text(f'''[[doc]]
id = "a"
title = "A"
kind = "header"
tier = "official_code"
url = "https://raw.githubusercontent.com/espressif/esp-idf/v6.1/a.h"
version = "v6.1"
file = "a.h"
sha256 = "{sha}"
''', encoding="utf-8")
            lib = load_manifest(man, raw, Path(d) / "cache")
            self.assertTrue(quote_found(lib.get("a"), None, "I2C_NUM_0"))
            (raw / "a.h").unlink()                                     # 原件删了也能用缓存
            self.assertEqual(load_manifest(man, raw, Path(d) / "cache").get("a"), lib.get("a"))
            man.write_text(man.read_text(encoding="utf-8").replace(sha, "0" * 64), encoding="utf-8")
            (raw / "a.h").write_text(body, encoding="utf-8")
            with self.assertRaises(SourceError):                       # 指纹不符
                load_manifest(man, raw, Path(d) / "cache")


class RulesTest(unittest.TestCase):
    def test_format_problems(self):
        self.assertEqual(rule().format_problems(), [])
        bad = rule(id="I2C-1", kind="magic", scope=(), text="两行\n内容", tier="blog",
                   cite=Citation("", 0, "", ""), check={"type": "api_exists", "symbol": "x"})
        probs = "\n".join(bad.format_problems())
        for word in ("编号", "类型", "适用范围", "一行", "出处资料", "原文引用", "页码", "来源等级", "缺字段"):
            self.assertIn(word, probs)

    def test_status_order(self):
        r = rule()
        r = r.advance("checked")
        with self.assertRaises(RuleError):
            r.advance("trusted")                      # 不能跳过人工确认
        r = r.advance("approved", "负责人确认")
        back = r.reject("出处原文找不到")
        self.assertEqual(back.status, "draft")
        self.assertEqual(back.notes, ("负责人确认", "出处原文找不到"))

    def test_select_prompt_and_conflicts(self):
        rs = RuleSet([
            rule(status="approved"),
            rule("R-PIN-001", kind="pin", scope=("all",), text="不许使用 GPIO19、GPIO20（USB）", status="approved",
                 cite=Citation("esp32s3-ds", 12, "2.3", "GPIO19 … USB"), tier="official_manual",
                 check={"type": "pin_forbidden", "gpio": [19, 20]}),
            rule("R-UART-001", scope=("uart",), status="draft"),
            rule("R-I2C-002", text="不许用旧接口 i2c_driver_install()", status="approved",
                 check={"type": "api_absent", "symbol": "i2c_new_master_bus"}),
        ])
        ids = [r.id for r in rs.select({"i2c"}, "approved")]
        self.assertEqual(ids, ["R-I2C-001", "R-I2C-002", "R-PIN-001"])       # UART 无关、草稿不入选
        block = rs.prompt_block({"i2c"})
        self.assertIn("R-PIN-001 | 不许使用 GPIO19、GPIO20（USB） | esp32s3-ds §2.3 p.12", block)
        self.assertNotIn("R-UART-001", block)
        self.assertEqual(rs.prompt_block({"spi"}, "trusted"), "")
        self.assertTrue(any("i2c_new_master_bus" in c for c in rs.conflicts()))

    def test_register_conflict_and_round_trip(self):
        reg = {"type": "register_value", "peripheral": "I2C0", "register": "CTR", "field": "MS_MODE"}
        rs = RuleSet([rule("R-REG-001", kind="register", check={**reg, "value": 1}),
                      rule("R-REG-002", kind="register", check={**reg, "value": 0})])
        self.assertEqual(len(rs.conflicts()), 1)
        with tempfile.TemporaryDirectory() as d:
            rs.save(Path(d) / "rules.json")
            again = RuleSet.load(Path(d) / "rules.json")
        self.assertEqual(list(again), list(rs))
        self.assertEqual(len(RuleSet.load(Path("/nonexistent/rules.json"))), 0)


if __name__ == "__main__":
    unittest.main()

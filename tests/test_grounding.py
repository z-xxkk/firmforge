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

"""上板评测题目录的完整性（DECISIONS 第 82–84 条）：不需要 ESP-IDF 和板子，只检查格式和合规预期。"""

import json
import unittest
from pathlib import Path

import helpers
from firmforge.embedded.board_task import BoardTaskError, check_without_board, discover_board_tasks, judge_rules, load_board_task
from firmforge.grounding.profile import HardwareProfile
from firmforge.grounding.rules import Rule, RuleSet

ROOT = Path(helpers.ROOT)
TASKS = ROOT / "tasks_board"
PROFILE = HardwareProfile.load(ROOT / "hardware" / "esp32c3_supermini" / "profile.json")
JUDGE = judge_rules(TASKS, PROFILE)
EXPECTED = {"gpio_edge_count", "rmt_pulse_meter", "ledc_pwm", "gptimer_tick", "uart_loopback", "i2c_bus_scan",
            "mpu6050_drv_idf", "mpu6050_drdy", "max30102_fifo", "max30102_temp", "imu_uart_logger",
            "adc_volt", "chip_temp", "nvs_config", "spi_loopback"}      # 后 4 道：2026-11-15 新外设留出题（第 126 条）


class BoardTasksTest(unittest.TestCase):
    def setUp(self):
        self.tasks = discover_board_tasks(TASKS)

    def test_all_present_and_well_formed(self):
        self.assertEqual({t.id for t in self.tasks}, EXPECTED)
        for t in self.tasks:
            with self.subTest(task=t.id):
                self.assertTrue(t.spec_path.is_file())
                self.assertTrue(t.public_files())
                self.assertTrue(t.reference_files())
                self.assertTrue(t.hidden_files())
                self.assertGreaterEqual(len(t.mutants), 3)
                for m in t.mutants:
                    self.assertNotEqual(t.mutant_files(m)[m.replaces], t.reference_files()[m.replaces], m.file)
                missing = [w for w in t.wiring if w not in t.task_profile(PROFILE).pins]   # 题目可以自带接线
                self.assertEqual(missing, [], "接线信号不在硬件档案里")
                spec = t.spec_path.read_text(encoding="utf-8")
                for h in t.public_files():
                    self.assertIn(Path(h).name, spec)                        # 需求里写了接口头文件
                for hidden in t.hidden_files().values():
                    self.assertNotIn(hidden.strip()[:200], spec)              # 需求里没有隐藏测试内容

    def test_compliance_expectations_without_build(self):
        for t in self.tasks:
            with self.subTest(task=t.id):
                r = check_without_board(t, PROFILE, JUDGE, idf=None, build_root=Path("/nonexistent"))
                self.assertEqual(r.problems, [])
                for v in r.variants:
                    self.assertEqual(v.compliance_ok, v.expect != "compliance", f"{v.name}：{v.problems}")
                    self.assertIsNone(v.build_ok)                              # 没有 ESP-IDF 时不编译

    def test_source_manifests_are_valid(self):
        # 资料清单每一项的等级、类型、网址白名单都合格（不需要原件；第 89 条加的资料曾因等级标错导致读不进来）
        import tomllib
        from firmforge.grounding.sources import Document
        for manifest in sorted((ROOT / "hardware").glob("*/sources.toml")):
            for d in tomllib.loads(manifest.read_text(encoding="utf-8")).get("doc", []):
                with self.subTest(manifest=manifest.parent.name, doc=d.get("id")):
                    Document(d["id"], d["title"], d["kind"], d["tier"], d.get("url", ""), d.get("version", ""))
                    self.assertTrue(d.get("file") and d.get("sha256"))

    def test_bad_task_toml(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "task.toml").write_text('id = "x"\ntitle = "x"\ncategory = "toy"\ndifficulty = 1\n', encoding="utf-8")
            with self.assertRaises(BoardTaskError):
                load_board_task(d)
            (Path(d) / "task.toml").write_text('id = "x"\ntitle = "x"\ncategory = "driver"\ndifficulty = 1\n'
                                               '[[mutants]]\nfile = "a.c"\nreplaces = "b.c"\nexpect_fail = "luck"\n',
                                               encoding="utf-8")
            with self.assertRaises(BoardTaskError):
                load_board_task(d)


class TaskPinsTest(unittest.TestCase):
    """题目自带接线（第 126 条）：复用 GPIO0–GPIO1 跳线的题用自己的信号名，生成的 hw_config.h 只有这些信号。"""

    def test_spi_task_profile(self):
        from firmforge.embedded.board_eval import task_design
        from firmforge.target.esp_idf import hw_config_header
        t = load_board_task(TASKS / "spi_loopback")
        p = t.task_profile(PROFILE)
        self.assertEqual({k: f.value for k, f in p.pins.items()}, {"spi2.mosi": 0, "spi2.miso": 1, "spi2.sclk": 6})
        self.assertEqual(p.wiring_problems(dict(t.pins), set()), [])   # 板子档案的自洽检查（problems）不适用于项目接线：
        from firmforge.target.harness import main_source        # I2C 器件还在档案里但这道题没接总线，复位代码要跳过它们
        self.assertNotIn("ff_reset_devices();", main_source(p))
        h = hw_config_header(p)
        self.assertIn("#define FF_PIN_SPI2_MOSI 0", h)
        self.assertNotIn("FF_PIN_UART1_TX", h)                  # 默认接线里同一个 GPIO 的信号不再出现
        self.assertEqual(task_design(t).wiring, t.pins)
        old = load_board_task(TASKS / "uart_loopback")
        self.assertIs(old.task_profile(PROFILE), PROFILE)        # 没写 [pins] 的老题不变
        self.assertEqual(task_design(old).wiring, {})


if __name__ == "__main__":
    unittest.main()

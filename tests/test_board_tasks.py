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
EXPECTED = {"gpio_edge_count", "ledc_pwm", "gptimer_tick", "uart_loopback", "i2c_bus_scan",
            "mpu6050_drv_idf", "mpu6050_drdy", "max30102_fifo", "max30102_temp", "imu_uart_logger"}


class BoardTasksTest(unittest.TestCase):
    def setUp(self):
        self.tasks = discover_board_tasks(TASKS)

    def test_all_eleven_present_and_well_formed(self):
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
                missing = [w for w in t.wiring if w not in PROFILE.pins]
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


if __name__ == "__main__":
    unittest.main()

"""任务加载与工具链配置。"""

import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.config import Toolchain
from firmforge.tasks import DEFAULT_FORBIDDEN_SYMBOLS, TaskError, discover_tasks, load_task


class TaskLoadTest(unittest.TestCase):
    def test_all_tasks_load(self):
        tasks = discover_tasks(helpers.TASKS)
        self.assertTrue({"ringbuf", "crc16_modbus", "debounce", "median_filter", "soft_timer",
                         "uart_frame"} <= {t.id for t in tasks})
        for t in tasks:
            with self.subTest(task=t.id):
                self.assertTrue(t.spec_path.is_file())
                self.assertTrue(list(t.include_dir.glob("*.h")))
                self.assertTrue(list(t.hidden_tests_dir.glob("test_*.c")))
                self.assertTrue(list(t.reference_dir.glob("*.c")))
                self.assertGreaterEqual(len(t.mutants), 2)
                for m in t.mutants:
                    self.assertTrue((t.root / m.file).is_file(), m.file)
                    self.assertTrue((t.reference_dir / m.replaces).is_file(), m.replaces)

    def test_spec_never_mentions_hidden_tests(self):
        for t in discover_tasks(helpers.TASKS):
            spec = t.spec_path.read_text(encoding="utf-8")
            for f in t.hidden_tests_dir.glob("*.c"):
                self.assertNotIn(f.stem, spec)

    def _write(self, tmp, text):
        d = Path(tmp) / "t"
        d.mkdir()
        (d / "task.toml").write_text(text, encoding="utf-8")
        return d

    def test_forbidden_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(tmp, 'id="x"\ntitle="x"\ncategory="protocol"\ndifficulty=1\n'
                                 '[rules]\nextra_forbidden_symbols=["memcpy"]\nallowed_symbols=["putchar"]\n')
            t = load_task(d)
            self.assertIn("memcpy", t.forbidden_symbols)
            self.assertNotIn("putchar", t.forbidden_symbols)
            self.assertIn("malloc", t.forbidden_symbols)
            self.assertIsNone(t.budget.flash_bytes)

    def test_bad_category_and_mutant_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(tmp, 'id="x"\ntitle="x"\ncategory="nope"\ndifficulty=1\n')
            with self.assertRaises(TaskError):
                load_task(d)
        with tempfile.TemporaryDirectory() as tmp:
            d = self._write(tmp, 'id="x"\ntitle="x"\ncategory="protocol"\ndifficulty=1\n'
                                 '[[mutants]]\nfile="m.c"\nreplaces="a.c"\nexpect_fail="unit_tests"\n')
            with self.assertRaises(TaskError):
                load_task(d)

    def test_default_forbidden_list(self):
        for sym in ("malloc", "free", "printf", "snprintf", "sscanf", "fopen", "exit", "system",
                    "longjmp", "signal", "fork", "socket"):
            self.assertIn(sym, DEFAULT_FORBIDDEN_SYMBOLS)
        self.assertEqual(len(DEFAULT_FORBIDDEN_SYMBOLS), len(set(DEFAULT_FORBIDDEN_SYMBOLS)))
        for sym in ("memcpy", "memset", "strlen"):          # 普通内存 / 字符串函数允许使用
            self.assertNotIn(sym, DEFAULT_FORBIDDEN_SYMBOLS)


class ToolchainTest(unittest.TestCase):
    def test_env_override(self):
        tc = Toolchain.from_env({"FF_CC": "my-gcc", "FF_CPPCHECK": "/opt/cppcheck"})
        self.assertEqual(tc.cc, "my-gcc")
        self.assertEqual(tc.cppcheck, "/opt/cppcheck")
        self.assertEqual(tc.cross_cc, "arm-none-eabi-gcc")

    def test_missing_tool(self):
        tc = Toolchain(cc="ff-definitely-not-a-compiler")
        self.assertFalse(tc.available("cc"))
        self.assertIsNone(tc.resolve("cc"))


if __name__ == "__main__":
    unittest.main()

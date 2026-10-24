"""任务自检与命令行。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge import cli
from firmforge.tasks import discover_tasks, load_task
from firmforge.verifier import check_task
from firmforge.verifier.report import FAIL, PASS, SKIPPED, LayerResult, Report
from firmforge.verifier.selfcheck import MUTANT_OK, MUTANT_UNVERIFIABLE, MUTANT_WRONG, _classify


def _run_cli(*argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class ClassifyTest(unittest.TestCase):
    def _rep(self, *pairs, notes=None):
        layers = []
        for name, status in pairs:
            lr = LayerResult(name, name, status)
            if status == SKIPPED:
                lr.notes = notes or ["未找到 arm-none-eabi-gcc"]
            layers.append(lr)
        return Report(layers)

    def test_ok(self):
        r = self._rep(("host_compile", PASS), ("static_check", FAIL))
        self.assertEqual(_classify(r, "static_check"), MUTANT_OK)

    def test_wrong_layer(self):
        r = self._rep(("host_compile", FAIL))
        self.assertEqual(_classify(r, "hidden_tests"), MUTANT_WRONG)

    def test_not_caught_at_all(self):
        r = self._rep(("host_compile", PASS), ("hidden_tests", PASS))
        self.assertEqual(_classify(r, "hidden_tests"), MUTANT_WRONG)

    def test_expected_layer_missing_tool(self):
        r = self._rep(("cross_compile", SKIPPED), ("resources", SKIPPED), ("hidden_tests", PASS))
        self.assertEqual(_classify(r, "resources"), MUTANT_UNVERIFIABLE)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class CheckTaskTest(unittest.TestCase):
    def test_all_tasks(self):
        for task in discover_tasks(helpers.TASKS):
            with self.subTest(task=task.id):
                res = check_task(task)
                self.assertTrue(res.reference_ok, res.reference.to_json())
                self.assertEqual(res.reference.layer("hidden_tests").status, "pass")
                for m in res.mutants:
                    self.assertNotEqual(m.status, MUTANT_WRONG,
                                        f"{m.mutant.file} 期望 {m.mutant.expect_fail}，实际 {m.actual}")
                    if m.mutant.expect_fail in ("hidden_tests", "host_compile", "static_check"):
                        self.assertEqual(m.status, MUTANT_OK, m.mutant.file)
                if helpers.HAS_CROSS:
                    self.assertTrue(all(m.status == MUTANT_OK for m in res.mutants))
                    self.assertIsNotNone(res.measured["flash"])


class CliTest(unittest.TestCase):
    def test_usage_error_exit_3(self):
        self.assertEqual(_run_cli("no-such-command")[0], 3)
        self.assertEqual(_run_cli()[0], 3)
        self.assertEqual(_run_cli("verify", "/no/such/dir", "--task", str(helpers.TASKS / "ringbuf"))[0], 3)
        self.assertEqual(_run_cli("check-task")[0], 3)

    def test_tasks_lists_all(self):
        code, out, _ = _run_cli("tasks", "--dir", str(helpers.TASKS))
        self.assertEqual(code, 0)
        for tid in ("ringbuf", "crc16_modbus", "debounce", "median_filter", "soft_timer", "uart_frame"):
            self.assertIn(tid, out)

    def test_doctor_runs(self):
        code, out, _ = _run_cli("doctor")
        self.assertIn(code, (0, 2))
        self.assertIn("gcc", out)

    @unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
    def test_verify_exit_code_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = helpers.copy_fixture("syntax_error", Path(tmp))
            js = Path(tmp) / "r.json"
            code, out, _ = _run_cli("verify", str(wd), "--task", str(helpers.TASKS / "ringbuf"), "--json", str(js))
            self.assertEqual(code, 1)
            self.assertIn("[FAIL]", out)
            data = json.loads(js.read_text(encoding="utf-8"))
            self.assertEqual(data["failed_layer"], "host_compile")

    @unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
    def test_verify_layers_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = helpers.copy_fixture("good", Path(tmp))
            code, out, _ = _run_cli("verify", str(wd), "--task", str(helpers.TASKS / "ringbuf"),
                                    "--layers", "host_compile")
            self.assertEqual(code, 0)
            self.assertNotIn("unit_tests", out)
            code, _, _ = _run_cli("verify", str(wd), "--task", str(helpers.TASKS / "ringbuf"),
                                  "--layers", "hidden_tests")
            self.assertEqual(code, 3)

    def test_load_task_error_is_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = _run_cli("check-task", tmp)
            self.assertEqual(code, 3)
            self.assertIn("task.toml", err)


if __name__ == "__main__":
    unittest.main()

"""判定器集成测试：用 tests/fixtures/ 的样例在真实编译器上跑。本机缺工具的测试显示 skipped。"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from firmforge.config import Toolchain
from firmforge.tasks import load_task
from firmforge.verifier import layers as L
from firmforge.verifier import verify
from firmforge.verifier.layers import BuildContext

RINGBUF = load_task(helpers.TASKS / "ringbuf")


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_fixture(self, name, **kw):
        return verify(helpers.copy_fixture(name, self.tmp), RINGBUF, **kw)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class FixtureLayerTest(_TmpCase):
    def test_good_has_no_failure(self):
        r = self.run_fixture("good")
        self.assertIsNone(r.failed_layer)
        self.assertIn(r.verdict, ("pass", "incomplete"))
        ut = r.layer("unit_tests")
        self.assertEqual(ut.status, "pass")
        self.assertEqual(ut.metrics["total"], 5)
        if helpers.TC.available("gcov"):
            self.assertIsNotNone(ut.metrics["coverage_pct"])

    def test_syntax_error_fails_l1(self):
        r = self.run_fixture("syntax_error")
        self.assertEqual(r.failed_layer, "host_compile")
        self.assertEqual(r.layer("unit_tests").status, "skipped")
        self.assertIn("前一层失败，未运行", r.layer("unit_tests").notes)

    def test_signature_stable_when_error_moves(self):
        a = self.run_fixture("syntax_error")
        b = self.run_fixture("syntax_error_moved")
        la = a.layer("host_compile").diagnostics[0].line
        lb = b.layer("host_compile").diagnostics[0].line
        self.assertNotEqual(la, lb)
        self.assertEqual(a.signature, b.signature)

    def test_signature_changes_when_error_changes(self):
        a = self.run_fixture("syntax_error")
        b = self.run_fixture("uses_malloc")
        self.assertNotEqual(a.signature, b.signature)

    def test_malloc_fails_l4(self):
        r = self.run_fixture("uses_malloc")
        self.assertEqual(r.failed_layer, "static_check")
        keys = [d.key for d in r.layer("static_check").diagnostics]
        self.assertIn("forbidden:malloc", keys)

    def test_failing_test_fails_l5(self):
        r = self.run_fixture("test_failure")
        self.assertEqual(r.failed_layer, "unit_tests")
        keys = r.layer("unit_tests").failing_keys()
        self.assertIn("test:test_starts_empty:FAIL", keys)
        self.assertEqual(r.layer("unit_tests").metrics["failed"], 1)

    def test_infinite_loop_times_out(self):
        with mock.patch.dict(os.environ, {"FF_TEST_TIMEOUT": "2"}):
            r = self.run_fixture("infinite_loop")
        self.assertEqual(r.failed_layer, "unit_tests")
        self.assertIn("timeout:test_ringbuf", r.layer("unit_tests").failing_keys())
        self.assertIsNone(r.layer("unit_tests").metrics["coverage_pct"])

    @unittest.skipUnless(helpers.HAS_CROSS, "本机没有 arm-none-eabi 工具链")
    def test_over_budget_fails_l3(self):
        r = self.run_fixture("over_budget")
        self.assertEqual(r.failed_layer, "resources")
        self.assertIn("budget:flash", r.layer("resources").failing_keys())

    def test_build_dir_cleared_each_run(self):
        wd = helpers.copy_fixture("good", self.tmp)
        stale = wd / ".ff_build" / "stale.txt"
        stale.parent.mkdir(exist_ok=True)
        stale.write_text("x", encoding="utf-8")
        verify(wd, RINGBUF, layers=["host_compile"])
        self.assertFalse(stale.exists())

    def test_no_sources_fails(self):
        (self.tmp / "src").mkdir()
        r = verify(self.tmp, RINGBUF, layers=["host_compile"])
        self.assertEqual(r.failed_layer, "host_compile")

    def test_no_tests_fails_l5(self):
        wd = helpers.copy_fixture("good", self.tmp)
        (wd / "tests" / "test_ringbuf.c").unlink()
        r = verify(wd, RINGBUF, layers=["unit_tests"])
        self.assertEqual(r.failed_layer, "unit_tests")
        self.assertIn("no_tests", r.layer("unit_tests").failing_keys())

    def test_sanitizer_catches_overflow(self):
        if os.environ.get("FF_SANITIZE") == "0":
            self.skipTest("FF_SANITIZE=0")
        wd = helpers.copy_fixture("good", self.tmp)
        (wd / "tests" / "test_ringbuf.c").write_text(
            '#include "unity.h"\n#include "ringbuf.h"\n'
            "static void test_overflow(void) { volatile uint8_t a[2]; volatile int i = 2; a[i] = 1; }\n"
            "int main(void) { UNITY_BEGIN(); RUN_TEST(test_overflow); return UNITY_END(); }\n",
            encoding="utf-8")
        r = verify(wd, RINGBUF, layers=["unit_tests"])
        keys = r.layer("unit_tests").failing_keys()
        # 数组越界可能先被 UBSan 抓到，也可能被 ASan 抓到
        self.assertTrue(any(k.startswith(("crash:test_ringbuf:asan:", "crash:test_ringbuf:ubsan:"))
                            for k in keys), keys)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class HiddenIsolationTest(_TmpCase):
    def test_hidden_not_run_by_default(self):
        r = self.run_fixture("good")
        self.assertIsNone(r.layer("hidden_tests"))

    def test_hidden_layer_requires_flag(self):
        wd = helpers.copy_fixture("good", self.tmp)
        with self.assertRaises(ValueError):
            verify(wd, RINGBUF, layers=["hidden_tests"])

    def test_hidden_layer_runs_and_agent_view_drops_it(self):
        r = self.run_fixture("good", include_hidden=True)
        hidden = r.layer("hidden_tests")
        self.assertEqual(hidden.status, "pass")
        self.assertGreater(hidden.metrics["total"], 10)
        agent_json = r.for_agent().to_json()
        self.assertNotIn("hidden_tests", agent_json)
        self.assertNotIn("test_ringbuf_hidden", agent_json)

    def test_hidden_failures_do_not_leak_into_agent_report(self):
        r = self.run_fixture("test_failure", include_hidden=True, stop_on_fail=False)
        self.assertEqual(r.layer("hidden_tests").status, "fail")
        agent = r.for_agent()
        self.assertEqual(agent.failed_layer, "unit_tests")
        for name in ("test_full_at_exact_capacity", "test_init_gives_empty_buffer", "hidden"):
            self.assertNotIn(name, agent.to_json())


class MissingToolTest(_TmpCase):
    def test_missing_host_cc_is_skipped_not_pass(self):
        tc = Toolchain(cc="ff-no-such-cc")
        r = self.run_fixture("good", toolchain=tc, layers=["host_compile", "unit_tests"])
        self.assertEqual(r.layer("host_compile").status, "skipped")
        self.assertEqual(r.layer("unit_tests").status, "skipped")
        self.assertEqual(r.verdict, "incomplete")
        self.assertIn("FF_CC", r.layer("host_compile").notes[0])

    @unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
    def test_missing_cross_and_cppcheck(self):
        tc = Toolchain(cross_cc="ff-no-such-cc", cppcheck="ff-no-cppcheck")
        r = self.run_fixture("good", toolchain=tc)
        self.assertEqual(r.layer("cross_compile").status, "skipped")
        self.assertEqual(r.layer("resources").status, "skipped")
        sc = r.layer("static_check")
        self.assertFalse(sc.complete)
        self.assertEqual(r.verdict, "incomplete")


@unittest.skipUnless(helpers.HAS_CC and helpers.HAS_HOST_SIZE, "本机没有 gcc 或 size")
class ResourcesLayerTest(_TmpCase):
    """用主机 gcc 和 size 冒充交叉工具链，检查 L3 的统计与预算逻辑。"""

    def _ctx(self, name, budget_task=RINGBUF):
        wd = helpers.copy_fixture(name, self.tmp)
        build = wd / ".ff_build"
        cross = build / "cross"
        cross.mkdir(parents=True)
        tc = Toolchain(cross_size="size")
        ctx = BuildContext(wd, budget_task, tc, build)
        for src in ctx.sources():
            subprocess.run(["gcc", "-Os", "-std=c11", "-fstack-usage", *ctx.include_flags(), "-c",
                            str(src), "-o", f"{src.stem}.o"], cwd=cross, check=True)
            ctx.cross_objs.append(cross / f"{src.stem}.o")
        ctx.cross_ok = True
        return ctx

    def test_over_budget_detected(self):
        lr = L.resources(self._ctx("over_budget"))
        self.assertEqual(lr.status, "fail")
        self.assertIn("budget:flash", lr.failing_keys())
        self.assertGreaterEqual(lr.metrics["flash"], 2048)
        self.assertIn(L.STACK_NOTE, lr.notes)

    def test_dynamic_stack_detected(self):
        ctx = self._ctx("good")
        vla = ctx.src_dir / "vla.c"
        vla.write_text("int vla_sum(int n) { volatile int a[n]; a[0] = n; return a[0]; }\n", encoding="utf-8")
        subprocess.run(["gcc", "-Os", "-std=c11", "-fstack-usage", "-c", str(vla), "-o", "vla.o"],
                       cwd=ctx.build_dir / "cross", check=True)
        ctx.cross_objs.append(ctx.build_dir / "cross" / "vla.o")
        lr = L.resources(ctx)
        self.assertIn("budget:dynamic_stack:vla_sum", lr.failing_keys())

    def test_skipped_without_cross_objects(self):
        ctx = BuildContext(self.tmp, RINGBUF, Toolchain(), self.tmp / ".ff_build")
        self.assertEqual(L.resources(ctx).status, "skipped")


if __name__ == "__main__":
    unittest.main()

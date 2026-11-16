"""集成（第 131 条）：所有模块通过后写主程序、编译、上板复位跑两轮核对串口；补测试的两条修正见 test_mutation_feedback。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.embedded.design import Design, DesignModule
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.embedded.fw_interpreter import FirmwareInterpreter
from firmforge.embedded.integrate import (APP_FILE, EXPECT_FILE, Expect, check_app_output, integrator_messages,
                                          parse_expectations, window_seconds)
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import load_workflow
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS

EXPECT = json.dumps({"expect": [
    {"pattern": r"温度 [-0-9.]+", "every_s": 2, "what": "每 2 秒打印温度"},
    {"pattern": "上次保存的最高温度", "phase": "after_reset"},
]}, ensure_ascii=False)


def log(lines: list[tuple[int, str]], boot: bool = True) -> str:
    head = "ESP-ROM:esp32c3-api1-20210207\nrst:0x15 (USB_UART_CHIP_RESET),boot:0xc (SPI_FAST_FLASH_BOOT)\n" if boot else ""
    return head + "".join(f"I ({ms}) app: {text}\n" for ms, text in lines)


def periodic(start_ms: int, n: int, every_ms: int = 2000) -> list[tuple[int, str]]:
    return [(start_ms + i * every_ms, f"温度 {30 + i}.5 ℃，最高 {30 + i}.5") for i in range(n)]


class ExpectationTest(unittest.TestCase):
    def test_parse(self):
        ex, probs = parse_expectations(EXPECT)
        self.assertEqual(probs, [])
        self.assertEqual([e.phase for e in ex], ["first", "after_reset"])
        self.assertEqual(ex[0].every_s, 2.0)
        for bad in ("不是 json", "{}", '{"expect": [{"pattern": "("}]', '{"expect": [{"pattern": "a", "phase": "x"}]}',
                    '{"expect": [{"pattern": "a", "every_s": 0}]}', '{"expect": [{"pattern": "a", "min_count": true}]}'):
            with self.subTest(bad):
                self.assertTrue(parse_expectations(bad)[1])

    def test_window(self):
        self.assertEqual(window_seconds([Expect("a")]), 12.0)
        self.assertEqual(window_seconds([Expect("a", every_s=5)]), 21.0)
        self.assertEqual(window_seconds([Expect("a", every_s=10)]), 36.0)

    def test_good_run(self):
        ex, _ = parse_expectations(EXPECT)
        outs = {"first": log([(300, "没有历史记录")] + periodic(320, 7)),
                "after_reset": log([(300, "上次保存的最高温度 36.5")] + periodic(320, 7))}
        chk = check_app_output(outs, ex, 12.0)
        self.assertTrue(chk.ok, chk.report())
        self.assertEqual(chk.matched[0]["count"], 7)

    def test_failures_are_specific(self):
        ex, _ = parse_expectations(EXPECT)
        good_after = log([(300, "上次保存的最高温度 36.5")] + periodic(320, 7))
        cases = {
            "崩溃": ({"first": log(periodic(320, 3)) + "Guru Meditation Error: Core 0 panic'ed", "after_reset": good_after},
                   "芯片崩溃"),
            "重启": ({"first": log(periodic(320, 3)) + log(periodic(320, 4)), "after_reset": good_after}, "自己重启"),
            "次数不够": ({"first": log(periodic(320, 2)), "after_reset": good_after}, "只出现 2 次"),
            "间隔太长": ({"first": log(periodic(320, 5, every_ms=4000)), "after_reset": good_after}, "最大间隔"),
            "复位后没打印": ({"first": log(periodic(320, 7)), "after_reset": log(periodic(320, 7))}, "上次保存的最高温度"),
            "没有日志": ({"first": "ESP-ROM\nrst:0x15\nhello\n", "after_reset": good_after}, "没有看到程序的日志"),
        }
        for name, (outs, want) in cases.items():
            with self.subTest(name):
                chk = check_app_output(outs, ex, 12.0)
                self.assertFalse(chk.ok)
                self.assertTrue(any(want in p for p in chk.problems), chk.problems)

    def test_prompt_has_spec_headers_and_no_tests(self):
        m = DesignModule("tsens", "温度", "hardware", ("tsens.c", "include/tsens.h"), scopes=("temp_sensor",))
        msgs = integrator_messages("每 2 秒读温度", profile(), RuleSet(), Design([m], app="循环读温度"),
                                   {"include/tsens.h": "esp_err_t tsens_read(float *c);"})
        text = msgs[1]["content"]
        for want in ("每 2 秒读温度", "循环读温度", "esp_err_t tsens_read", EXPECT_FILE, "after_reset"):
            self.assertIn(want, text)
        self.assertNotIn("TEST_CASE", text)


class IntegrateInFlowTest(unittest.TestCase):
    """解释器里的集成步骤：模块都过了才做；没通过带着报告改；结论影响整次运行；交付完整工程。"""

    def run_flow(self, checks, app_checks, *, module_pass=True, idf=True, human_review=False, comment="", expects=None):
        seen = []

        def team(messages):
            system, last = messages[0]["content"], messages[-1]["content"] or ""
            if "主程序" in last or "app_main" in last or "主程序" in (messages[1]["content"] or "")[:200]:
                seen.append(("integrator", last))
                n = sum(1 for k, _ in seen if k == "integrator")
                exp = (expects or [EXPECT])[min(n, len(expects or [EXPECT])) - 1]
                return (f"<<<FILE {APP_FILE}\nvoid app_main(void) {{ /* 第 {n} 版 */ }}\n>>>FILE\n"
                        f"<<<FILE {EXPECT_FILE}\n{exp}\n>>>FILE\n")
            if "资深嵌入式 C 工程师" in system:
                seen.append(("coder", last))
                return "<<<FILE imu.c\nint imu_read(void) { return 1; }\n>>>FILE\n<<<FILE include/imu.h\nint imu_read(void);\n>>>FILE\n"
            if "测试工程师" in system:
                return '<<<FILE test/test_imu.c\n#include "unity.h"\nTEST_CASE("t", "[imu]") {}\n>>>FILE\n'
            if "诊断工程师" in system:
                return '```json\n{"blame": "code", "rule": "", "evidence": "e", "instruction": "改"}\n```'
            if "审查" in system or "reviewer" in system.lower():
                return '```json\n{"verdict": "ok", "issues": []}\n```'
            raise AssertionError(system[:40])
        m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"), provides=("int imu_read(void)",))
        mod_iter = iter(checks)
        app_iter = iter(app_checks)
        answers = []

        def check(self, *a, **k):
            return next(mod_iter, checks[-1])

        def check_app(self, design, mr, module_files, rules, attempt):
            mr.attempts = attempt
            answers.append(dict(mr.files))
            return next(app_iter, app_checks[-1] if app_checks else ("pass", ""))
        from dataclasses import replace
        settings = replace(SETTINGS, loop={**(SETTINGS.loop or {}), "mutants": 0})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", check), \
                mock.patch.object(FirmwareInterpreter, "_check_app", check_app):
            f = EmbeddedFlow(llm=FakeLLM(team), settings=settings, profile=profile(), spec="每 2 秒读 IMU", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none",
                             idf=object() if idf else None, board=None, design=Design([m], app="循环读 IMU"),
                             build_rules=False, workflow=load_workflow("firmware_review" if human_review else "firmware_dev"),
                             human_review=human_review, confirm_design=lambda design: "",
                             confirm_module=lambda mm, mr, v: comment if mm.id == "app" else "")
            res = f.run()
            ev = read_events(res.run_dir)
            firmware = res.run_dir / "firmware" / "main"
            delivered = sorted(p.relative_to(firmware).as_posix() for p in firmware.rglob("*")) if firmware.is_dir() else []
            run_json = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        return res, seen, ev, answers, delivered, run_json

    PASS = ("pass", "board_test", "共 1 个用例，通过 1")
    FAIL = ("fail", "board_test", "失败")

    def test_pass_delivers_full_project(self):
        res, seen, ev, answers, delivered, run_json = self.run_flow([self.PASS], [("pass", "上板运行：通过")])
        self.assertEqual(res.verdict, "pass")
        self.assertEqual(res.app.verdict, "pass")
        self.assertEqual(run_json["app"], "pass")
        self.assertIn(APP_FILE, delivered)
        self.assertIn("imu.c", delivered)
        self.assertIn("include/hw_config.h", delivered)
        self.assertFalse(any(p.startswith("test") for p in delivered))         # 交付的工程不带测试
        self.assertNotIn(EXPECT_FILE, delivered)
        self.assertTrue(any(e["type"] == "firmware_ready" for e in ev))
        msg = next(t for k, t in seen if k == "integrator")
        self.assertIn("int imu_read(void);", msg)                              # 拿到模块的公共头文件

    def test_fail_then_fixed_with_report(self):
        res, seen, ev, answers, *_ = self.run_flow([self.PASS], [("fail", "上板运行：没通过\n- 只出现 2 次"), ("pass", "")])
        self.assertEqual(res.verdict, "pass")
        self.assertEqual(res.app.attempts, 2)
        repair = [t for k, t in seen if k == "integrator"][1]
        self.assertIn("只出现 2 次", repair)
        self.assertIn("第 2 版", answers[1][APP_FILE])

    def test_never_passes_run_fails(self):
        res, seen, *_ = self.run_flow([self.PASS], [("fail", "x")])
        self.assertEqual(res.verdict, "fail")
        self.assertEqual(res.app.attempts, 3)                                   # 工作流里 max = 3

    def test_skipped_counts_as_incomplete(self):
        res, *_ = self.run_flow([self.PASS], [("skipped", "没有板子")])
        self.assertEqual(res.verdict, "incomplete")

    def test_not_run_when_module_failed_or_no_idf(self):
        res, seen, ev, answers, delivered, _ = self.run_flow([self.FAIL], [("pass", "")])
        self.assertEqual(res.app.verdict, "skipped")
        self.assertEqual(answers, [])
        self.assertFalse(any(k == "integrator" for k, _ in seen))             # 不花钱写主程序
        self.assertEqual(delivered, [])
        res, seen, *_ = self.run_flow([self.PASS], [("pass", "")], idf=False)
        self.assertEqual(res.app.verdict, "skipped")
        self.assertEqual(res.verdict, "incomplete")
        self.assertFalse(any(k == "integrator" for k, _ in seen))

    def test_expectations_locked(self):
        """第 140 条：期望第一次交上来就锁定；没写理由的改动不算数，写了理由的照用但记下来给负责人看。"""
        loose = json.dumps({"expect": [{"pattern": r"温度 [-0-9.]+", "every_s": 2}]}, ensure_ascii=False)
        res, seen, ev, answers, delivered, _ = self.run_flow([self.PASS], [("fail", "x"), ("pass", "")],
                                                             expects=[EXPECT, loose])
        self.assertEqual(json.loads(answers[1][EXPECT_FILE]), json.loads(EXPECT))          # 没写理由：退回原来的期望
        ch = [e["data"] for e in ev if e["type"] == "expect_changed"]
        self.assertEqual((ch[0]["accepted"], ch[0]["removed"]), (False, ["上次保存的最高温度"]))
        self.assertIn("锁定", [t for k, t in seen if k == "integrator"][1])
        why = json.dumps({"expect": json.loads(loose)["expect"], "change_reason": "需求没要求复位后打印"}, ensure_ascii=False)
        res, seen, ev, answers, delivered, _ = self.run_flow([self.PASS], [("fail", "x"), ("pass", "")],
                                                             expects=[EXPECT, why])
        self.assertEqual(json.loads(answers[1][EXPECT_FILE])["change_reason"], "需求没要求复位后打印")
        ch = [e["data"] for e in ev if e["type"] == "expect_changed"]
        self.assertEqual((ch[0]["accepted"], ch[0]["reason"]), (True, "需求没要求复位后打印"))

    def test_human_comment_revises_and_rechecks(self):
        res, seen, ev, answers, *_ = self.run_flow([self.PASS], [("pass", ""), ("pass", "")], human_review=True,
                                                   comment="温度保留一位小数")
        waits = [e for e in ev if e["type"] == "human_wait" and e["node_id"] == "app"]
        self.assertEqual(len(waits), 1)
        self.assertTrue(any("温度保留一位小数" in t for k, t in seen if k == "integrator"))
        self.assertEqual(len(answers), 2)
        res, seen, ev, answers, *_ = self.run_flow([self.PASS], [("pass", ""), ("fail", "坏了")], human_review=True,
                                                   comment="随便改改")
        self.assertEqual(res.app.verdict, "pass")                               # 改坏了退回通过的版本
        self.assertIn("第 1 版", res.app.files[APP_FILE])
        self.assertTrue(any(e["type"] == "human_reverted" and e["node_id"] == "app" for e in ev))


class CheckAppTest(unittest.TestCase):
    """真正的 _check_app：合规 → 编译整个工程（不带测试）→ 烧录、复位跑两轮 → 核对。编译和板子换成假的。"""

    def run_check(self, outs, why=""):
        from firmforge.embedded.flow import ModuleResult
        from firmforge.target.esp_idf import BuildResult, IdfProject
        written = {}

        class Board:
            def chip(self):
                return None

            def run_app(self, proj, seconds, phases, log_dir=None):
                self.seconds = seconds
                return outs, why
        m = DesignModule("imu", "IMU", "hardware", ("imu.c", "include/imu.h"))
        with tempfile.TemporaryDirectory() as d:
            f = EmbeddedFlow(llm=FakeLLM([]), settings=SETTINGS, profile=profile(), spec="x", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=object(),
                             board=Board(), design=Design([m]), build_rules=False, workflow=load_workflow("firmware_dev"))
            it = FirmwareInterpreter(f, f.workflow)
            mr = ModuleResult("app", "hardware", files={APP_FILE: '#include "imu.h"\nvoid app_main(void) {}\n',
                                                        EXPECT_FILE: EXPECT})
            with mock.patch("firmforge.config.project_root", return_value=Path(d)), \
                    mock.patch.object(IdfProject, "write", lambda self, prof, files: written.update(files)), \
                    mock.patch.object(IdfProject, "build", lambda self: BuildResult(True)):
                verdict, report = it._check_app(Design([m]), mr, {"imu.c": "int x;", "include/imu.h": "int x;"},
                                                RuleSet(), 1)
            ev = read_events(f.rd.path)
        return verdict, report, written, ev

    def test_pass_and_project_has_no_tests(self):
        outs = {"first": log(periodic(320, 7)), "after_reset": log([(300, "上次保存的最高温度 36.5")] + periodic(320, 7))}
        verdict, report, written, ev = self.run_check(outs)
        self.assertEqual(verdict, "pass", report)
        self.assertEqual(sorted(written), [APP_FILE, "imu.c", "include/imu.h"])
        self.assertEqual([e["data"]["scope"] for e in ev if e["type"] == "verify_finished"],
                         ["compliance", "build", "board_test"])
        self.assertTrue(any(e["type"] == "app_checked" and e["data"]["ok"] for e in ev))

    def test_wrong_output_fails_and_no_board_skips(self):
        verdict, report, *_ = self.run_check({"first": log(periodic(320, 7)), "after_reset": log(periodic(320, 7))})
        self.assertEqual(verdict, "fail")
        self.assertIn("上次保存的最高温度", report)
        verdict, report, *_ = self.run_check({}, why="找不到板子的串口")
        self.assertEqual(verdict, "skipped")


class BoardEraseTest(unittest.TestCase):
    def test_app_run_erases_flash_first(self):
        """第 140 条：烧完主程序先擦 NVS 分区、不复位，第一轮才是真正的第一次上电。"""
        from firmforge.target.board import BoardRunner
        calls = []

        class Idf:
            python, env = "python", {}

            def idf(self, args, cwd, timeout):
                calls.append(args)
                return mock.Mock(returncode=1, stdout="", stderr="stop here")
        Idf.idf = lambda self, args, cwd, timeout: (calls.append(args), mock.Mock(returncode=0, stdout="", stderr=""))[1]
        Idf.root = "/idf"
        b = BoardRunner.__new__(BoardRunner)
        b.idf, b.port, b.run_timeout, b._chip = Idf(), "/dev/null", 60, None
        runs = []

        def fake_run(args, **k):
            runs.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("firmforge.target.board.Path.exists", return_value=True), \
                mock.patch("firmforge.target.board.subprocess.run", fake_run):
            b.run_app(mock.Mock(root="."), 1, ("first",))
        self.assertEqual(calls[0][-1], "flash")
        erase = runs[0]
        self.assertIn("parttool.py", erase[1])
        self.assertEqual(erase[2:4], ["--esptool-args", "after=no_reset"])               # 擦完不复位
        self.assertIn("--partition-name=nvs", erase)
        self.assertIn("-c", runs[1])                                                     # 然后才读串口（这次复位是第一次上电）


class BoardRunAppTest(unittest.TestCase):
    def test_no_port_is_skipped(self):
        from firmforge.target.board import BoardRunner
        b = BoardRunner.__new__(BoardRunner)
        b.idf, b.port, b.run_timeout, b._chip = None, None, 60, None
        outs, why = b.run_app(None, 12, ("first", "after_reset"))
        self.assertEqual(outs, {})
        self.assertIn("找不到板子的串口", why)


if __name__ == "__main__":
    unittest.main()

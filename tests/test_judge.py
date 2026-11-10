"""隐藏测试判定 judge_on_board 本身（变异测试发现它几乎没有直接的测试：评测运行器的测试都换成了假判定）。

用假的 ESP-IDF 工程和假板子，不需要 ESP-IDF 和板子：检查每一层的判定、跳过的条件、
公共头文件一律用题目自带的、隐藏测试和测试主程序都编进去。
"""

import unittest
from pathlib import Path

import helpers
from firmforge.embedded import board_task
from firmforge.embedded.board_task import discover_board_tasks, hidden_case_count, judge_on_board, judge_rules, load_board_task
from firmforge.grounding.profile import HardwareProfile
from firmforge.target.esp_idf import BuildResult, Diagnostic
from firmforge.target.harness import BEGIN, END, BoardTestResult, CaseResult, parse_board_output

ROOT = Path(helpers.ROOT)
TASKS = ROOT / "tasks_board"
PROFILE = HardwareProfile.load(ROOT / "hardware" / "esp32c3_supermini" / "profile.json")
JUDGE = judge_rules(TASKS, PROFILE)
TASK = load_board_task(TASKS / "ledc_pwm")


class FakeProject:
    """代替 IdfProject：记下写进去的文件，按预设返回编译结果。"""
    made: list["FakeProject"] = []
    build_result = BuildResult(True)

    def __init__(self, root, idf, *, target="", requires=(), whole_archive=False, **kw):
        self.root, self.idf, self.target, self.requires, self.whole_archive = root, idf, target, requires, whole_archive
        self.files: dict[str, str] = {}
        self.built = False
        FakeProject.made.append(self)

    def write(self, profile, files):
        self.files = dict(files)

    def build(self):
        self.built = True
        return FakeProject.build_result


class FakeBoard:
    def __init__(self, result: BoardTestResult):
        self.result, self.ran = result, []

    def run(self, proj, log_path):
        self.ran.append((proj, log_path))
        return self.result


class JudgeOnBoardTest(unittest.TestCase):
    def setUp(self):
        self.saved = board_task.IdfProject
        board_task.IdfProject = FakeProject
        FakeProject.made = []
        FakeProject.build_result = BuildResult(True)
        self.total = hidden_case_count(TASK)
        self.ref = TASK.reference_files()

    def tearDown(self):
        board_task.IdfProject = self.saved

    def judge(self, files, *, idf=object(), board=None, rules=JUDGE):
        return judge_on_board(TASK, PROFILE, rules, idf, board, files, Path("/nonexistent/build"), Path("/nonexistent/x.log"))

    def board_with(self, *cases, **kw):
        r = BoardTestResult(cases=[CaseResult(n, s) for n, s in cases], started=True, **kw)
        return FakeBoard(r)

    def test_hidden_cases_counted(self):
        self.assertGreater(self.total, 0)
        for t in discover_board_tasks(TASKS):
            self.assertGreater(hidden_case_count(t), 0, t.id)

    def test_pass_when_all_hidden_cases_pass(self):
        board = self.board_with(("a", "pass"), ("b", "pass"))
        j = self.judge(self.ref, board=board)
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed, j.cases_total, j.failed_cases),
                         ("pass", "", 2, self.total, []))
        proj = FakeProject.made[0]
        self.assertTrue(proj.whole_archive)                   # TEST_CASE 靠构造函数登记，必须保留全部目标文件
        self.assertEqual(proj.target, str(PROFILE.chip.value))
        for extra in TASK.requires:
            self.assertIn(extra, proj.requires)
        for p in TASK.hidden_files():                          # 隐藏测试编进去了
            self.assertIn(p, proj.files)
        self.assertTrue(any("app_main" in c for c in proj.files.values()))   # 测试主程序
        self.assertEqual(board.ran[0][0], proj)

    def test_board_failure_is_board_test_layer(self):
        j = self.judge(self.ref, board=self.board_with(("a", "pass"), ("b", "fail"), ("c", "fail")))
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed, j.failed_cases), ("fail", "board_test", 1, ["b", "c"]))

    def test_crash_or_hang_is_not_pass(self):
        for kw in ({"crash": "Guru Meditation Error"}, {"hang": True}):
            with self.subTest(kw=kw):
                j = self.judge(self.ref, board=self.board_with(("a", "pass"), **kw))
                self.assertEqual((j.verdict, j.failed_layer), ("fail", "board_test"))

    def test_board_skipped_is_skipped(self):
        j = self.judge(self.ref, board=FakeBoard(BoardTestResult(skipped="找不到串口")))
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed), ("skipped", "skipped", 0))
        self.assertIn("找不到串口", j.report)

    def test_no_board_or_no_idf_is_skipped_never_pass(self):
        j = self.judge(self.ref, board=None)
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed, j.cases_total), ("skipped", "skipped", 0, self.total))
        self.assertTrue(FakeProject.made[0].built)            # 没有板子也先编译
        FakeProject.made = []
        j = self.judge(self.ref, idf=None, board=self.board_with(("a", "pass")))
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed), ("skipped", "skipped", 0))
        self.assertEqual(FakeProject.made, [])
        self.assertEqual(j.cases_total, self.total)

    def test_build_failure(self):
        FakeProject.build_result = BuildResult(False, [Diagnostic("main/pwm_out.c", 3, "error", "缺分号"),
                                                       Diagnostic("main/pwm_out.c", 9, "warning", "没用的变量")],
                                               log_tail="很长的日志")
        board = self.board_with(("a", "pass"))
        j = self.judge(self.ref, board=board)
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed, j.failed_cases),
                         ("fail", "build", 0, ["编译失败"]))
        self.assertIn("缺分号", j.report)
        self.assertNotIn("没用的变量", j.report)               # 只报错误，不报警告
        self.assertEqual(board.ran, [])                         # 编译不过不上板
        FakeProject.build_result = BuildResult(False, [], log_tail="链接失败：undefined reference")
        self.assertIn("链接失败", self.judge(self.ref).report)

    def test_compliance_failure_stops_before_build(self):
        bad = None
        for t in discover_board_tasks(TASKS):
            m = next((m for m in t.mutants if m.expect_fail == "compliance"), None)
            if m is not None:
                bad = (t, t.mutant_files(m))
                break
        self.assertIsNotNone(bad, "需要至少一个 expect_fail = compliance 的变异体")
        t, files = bad
        j = judge_on_board(t, PROFILE, JUDGE, object(), self.board_with(("a", "pass")), files, Path("/x"), None)
        self.assertEqual((j.verdict, j.failed_layer, j.cases_passed), ("fail", "compliance", 0))
        self.assertEqual(j.cases_total, hidden_case_count(t))
        self.assertTrue(j.report)
        self.assertEqual(FakeProject.made, [])
        # 不给判定规则时不做合规检查（照常编译、上板）
        j = judge_on_board(t, PROFILE, None, object(), self.board_with(("a", "pass")), files, Path("/x"), None)
        self.assertEqual(j.verdict, "pass")

    def test_public_header_always_from_task(self):
        # Agent 改了公共头文件也不算：编进去的是题目自带的
        (hpath, htext), = TASK.public_files().items()
        files = {**self.ref, hpath: "/* Agent 改坏的接口 */\n"}
        self.judge(files, board=self.board_with(("a", "pass")))
        self.assertEqual(FakeProject.made[0].files[hpath], htext)


class CrashLineTest(unittest.TestCase):
    def test_crash_line_is_exactly_the_crash_line(self):
        out = (f"boot log\n{BEGIN}\nmain/t.c:1:a:PASS\nprev line x\n"
               f"Guru Meditation Error: Core  0 panic'ed (Load access fault). {'y' * 300}\nBacktrace: 0x1\n")
        r = parse_board_output(out)
        self.assertTrue(r.crash.startswith("Guru Meditation Error"))
        self.assertEqual(len(r.crash), 200)
        self.assertEqual(r.verdict(), "fail")

    def test_ff_log_keeps_text_after_marker(self):
        r = parse_board_output(f"{BEGIN}\nFF_LOG: a=1 FF_LOG: b=2\n1 Tests 0 Failures 0 Ignored\nOK\n{END}\n")
        self.assertEqual(r.logs, ["a=1 FF_LOG: b=2"])


if __name__ == "__main__":
    unittest.main()

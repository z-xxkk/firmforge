"""故障注入：网络断开、模型乱回、板子中途拔掉、运行被强制结束、记录文件写坏、没有写入权限……

检查两件事：程序不崩（或者崩之前把记录写完整、提示说清楚原因和怎么办），以及不会把故障当成通过、
也不会把平台自己的故障记成 Agent 没通过（CLAUDE.md 硬性规则 4）。
全部用假模型、假串口，不联网、不花钱、不需要板子。
"""

import http.client
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

import helpers  # noqa: F401
from firmforge.embedded.board_eval import EvalDataError, evaluate, load_rows
from firmforge.grounding.rules import RuleSet
from firmforge.llm import LLMError, OpenAICompatClient
from firmforge.runs import read_events
from firmforge.target import harness
from firmforge.target.board import BoardRunner
from test_board_eval import TASK, FakeTeam, make_ctx

sys.path.insert(0, str(Path(__file__).resolve().parent))
MSGS = [{"role": "user", "content": "hi"}]
NETWORK_DOWN = "LLM 请求失败（已重试 3 次）：网络错误：[Errno -3] Temporary failure in name resolution"


def client(opener):
    return OpenAICompatClient("k", "http://x", opener=opener, sleep=lambda s: None, max_retries=1)


def raising(exc):
    def op(req, timeout):
        raise exc
    return op


def body(raw: bytes):
    return lambda req, timeout: (200, raw, {})


class LLMClientFaultTest(unittest.TestCase):
    def test_network_faults_become_llm_error(self):
        # 回复读到一半断开、SSL 中断、连接被重置：都要变成 LLMError（会重试），不能是没人接的异常
        for exc in (http.client.IncompleteRead(b"{"), ssl.SSLError("bad record"), ConnectionResetError("reset"),
                    http.client.RemoteDisconnected("closed"), TimeoutError("timed out")):
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaises(LLMError) as cm:
                    client(raising(exc)).complete(MSGS, model="m")
                self.assertIn("网络错误", str(cm.exception))

    def test_malformed_replies_become_llm_error(self):
        for raw in (b"\xff\xfe", b"<html>502 Bad Gateway</html>", b'{"choices": []}', b"[1]",
                    b'{"choices": [{"message": "a"}]}', b'{"choices": [{"message": null}]}'):
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(LLMError):
                    client(body(raw)).complete(MSGS, model="m")

    def test_null_fields_are_tolerated(self):
        # 有的兼容接口把没有的字段写成 null：内容为空、token 记 0，不报错
        r = client(body(b'{"choices": [{"message": {"content": null}}], "usage": {"prompt_tokens": null}}')).complete(
            MSGS, model="m")
        self.assertEqual((r.text, r.prompt_tokens, r.completion_tokens), ("", 0, 0))

    def test_auth_error_says_what_to_do(self):
        with self.assertRaises(LLMError) as cm:
            client(raising(urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None))).complete(MSGS, model="m")
        self.assertIn("API key", str(cm.exception))
        with self.assertRaises(LLMError):
            client(body(b'{"data": "x"}')).list_models()


class _Idf:
    python = sys.executable
    env = None

    def idf(self, args, cwd, timeout):
        return subprocess.CompletedProcess(args, 0, "烧录完成", "")


class _Proj:
    root = Path(".")


class BoardUnpluggedTest(unittest.TestCase):
    def _run(self, rc: int, out: str, log: Path | None = None):
        import firmforge.target.board as B
        saved = B.subprocess.run
        B.subprocess.run = lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, rc, out, "serial.serialutil.SerialException: device reports readiness to read but returned no data")
        try:
            return BoardRunner(_Idf(), port=__file__).run(_Proj(), log)
        finally:
            B.subprocess.run = saved

    def test_unplugged_mid_test_is_skipped_not_failed(self):
        # 读串口读到一半板子被拔掉：测试没跑完，不是代码的错，记 skipped 并说明原因（串口原文照样留下）
        partial = f"{harness.BEGIN}\nmain/t.c:1:case_a:PASS\n"
        with tempfile.TemporaryDirectory() as d:
            r = self._run(1, partial, Path(d) / "serial.log")
            self.assertEqual(r.verdict(), "skipped")
            self.assertIn("板子可能被拔掉", r.report())
            self.assertIn("case_a", (Path(d) / "serial.log").read_text(encoding="utf-8"))

    def test_real_hang_still_fails(self):
        # 读串口的程序正常退出（等到超时）却没有结束标记：是程序卡死，照常判没通过
        r = self._run(0, f"{harness.BEGIN}\nmain/t.c:1:case_a:PASS\n")
        self.assertEqual(r.verdict(), "fail")
        self.assertTrue(r.hang)

    def test_crash_then_port_lost_still_fails(self):
        # 芯片崩溃后重启导致串口断开：崩溃信息已经读到，照常判没通过
        r = self._run(1, f"{harness.BEGIN}\nGuru Meditation Error: Core  0 panic'ed (Load access fault)\n")
        self.assertEqual(r.verdict(), "fail")
        self.assertTrue(r.crash)


class _Dropping(FakeTeam):
    """前 after 次调用正常，之后断网。"""

    def __init__(self, after: int):
        super().__init__()
        self.n, self.after = 0, after

    def __call__(self, messages):
        self.n += 1
        if self.n > self.after:
            raise LLMError(NETWORK_DOWN)
        return super().__call__(messages)


class EvalFaultTest(unittest.TestCase):
    def test_network_down_mid_eval(self):
        # 评测中途断网：停下并说明；断网那次不记成 Agent 没通过；运行目录记录完整（能回放）；汇总照常写出；
        # 恢复网络后同一个输出目录再跑，接着跑没跑完的
        for cfg in ("single", "none"):
            with self.subTest(cfg=cfg), tempfile.TemporaryDirectory() as d:
                out, said = Path(d) / "out", []
                ctx = make_ctx(d, _Dropping(1 if cfg == "single" else 3), RuleSet(), [])
                with self.assertRaises(LLMError):
                    evaluate([TASK], [cfg], 2, out, ctx, progress=said.append)
                rows = load_rows(out)
                self.assertTrue(all(r["verdict"] == "pass" for r in rows), rows)
                self.assertTrue((out / "summary.md").is_file())
                self.assertTrue(any("出错停止" in s and "接着跑" in s for s in said), said)
                for rd in (Path(d) / "runs").iterdir():
                    info = json.loads((rd / "run.json").read_text(encoding="utf-8"))
                    kinds = [e["type"] for e in read_events(rd)]
                    self.assertIn("run_finished", kinds)
                    if info["verdict"] == "error":
                        self.assertIn("网络错误", info["error"])
                ctx2 = make_ctx(d, FakeTeam(), RuleSet(), [])
                rows = evaluate([TASK], [cfg], 2, out, ctx2, progress=lambda s: None)
                self.assertEqual(sorted(r["run"] for r in rows), [1, 2])

    def test_model_never_follows_format(self):
        # 模型一直回空或乱码（还想写到 main/ 外面）：每种配置都如实记"没交出代码"，不崩
        for reply in ("", "```json\n{not json\n```\n<<<FILE ../../etc/passwd\nx\n>>>FILE\n"):
            with self.subTest(reply=reply[:10]), tempfile.TemporaryDirectory() as d:
                ctx = make_ctx(d, lambda m, r=reply: r, RuleSet(), [])
                rows = evaluate([TASK], ["single", "none", "unverified"], 1, Path(d) / "out", ctx,
                                progress=lambda s: None)
                self.assertEqual([(r["verdict"], r["failed_layer"]) for r in rows], [("fail", "no_code")] * 3)
                self.assertFalse(list(Path(d).rglob("passwd")))

    def test_truncated_runs_jsonl_resumes(self):
        # 上次被强制结束，runs.jsonl 最后一行只写了一半：去掉这一行并说明，那一次重跑
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            evaluate([TASK], ["single"], 1, out, make_ctx(d, FakeTeam(), RuleSet(), []), progress=lambda s: None)
            with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
                f.write('{"task": "mpu6050_drv_idf", "config": "single", "ru')
            said = []
            rows = evaluate([TASK], ["single"], 2, out, make_ctx(d, FakeTeam(), RuleSet(), []), progress=said.append)
            self.assertTrue(any("不完整" in s for s in said), said)
            self.assertEqual(sorted(r["run"] for r in rows), [1, 2])
            self.assertEqual(len(load_rows(out)), 2)

    def test_broken_rule_cache_is_reported_not_regenerated(self):
        # 未核对规则缓存坏了：明确报错，不悄悄重新生成（同一轮评测规则要一致，重新生成也要花钱）
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            (out / "unverified_rules").mkdir(parents=True)
            (out / "unverified_rules" / f"{TASK.id}.json").write_text('{"rules": [', encoding="utf-8")
            team = FakeTeam()
            with self.assertRaises(EvalDataError) as cm:
                evaluate([TASK], ["unverified"], 1, out, make_ctx(d, team, RuleSet(), []), progress=lambda s: None)
            self.assertIn("删掉它会重新生成", str(cm.exception))
            self.assertFalse(any("资料核查员" in s for s in team.seen))
            self.assertIn("读不了", (out / "summary.md").read_text(encoding="utf-8"))

    def test_truncated_events_still_replay(self):
        # 运行被强制结束，events.jsonl 最后一行只写了一半：前面的照常回放
        with tempfile.TemporaryDirectory() as d:
            evaluate([TASK], ["single"], 1, Path(d) / "out", make_ctx(d, FakeTeam(), RuleSet(), []),
                     progress=lambda s: None)
            rd = next((Path(d) / "runs").iterdir())
            full = read_events(rd)
            with (rd / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write('{"type": "agent_star')
            self.assertEqual(len(read_events(rd)), len(full))


class CliFaultTest(unittest.TestCase):
    def test_unwritable_output_dir_is_a_clear_error(self):
        from firmforge import cli
        if os.geteuid() == 0:
            self.skipTest("root 不受目录权限限制")
        with tempfile.TemporaryDirectory() as d:
            ro = Path(d) / "ro"
            ro.mkdir()
            ro.chmod(0o500)
            try:
                saved = cli.cmd_check_board_tasks

                def boom(args):
                    (ro / "x").mkdir()
                cli_args = ["check-board-tasks"]
                cli.cmd_check_board_tasks = boom
                parser_saved = cli.build_parser

                def parser():
                    p = parser_saved()
                    for a in p._subparsers._group_actions[0].choices.values():
                        if a.prog.endswith("check-board-tasks"):
                            a.set_defaults(func=boom)
                    return p
                cli.build_parser = parser
                import contextlib
                import io
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = cli.main(cli_args)
                self.assertEqual(code, cli.EXIT_FAIL)
                self.assertIn("错误：", err.getvalue())
                self.assertIn("Permission denied", err.getvalue())
            finally:
                cli.cmd_check_board_tasks = saved
                cli.build_parser = parser_saved
                ro.chmod(0o700)


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class GuiFaultTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui import theme as th
        from firmforge.gui.widgets import set_theme
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeFaultTest")
        set_theme(th.current(False))                  # 页面画图要用主题色（主窗口里由它设好）

    def setUp(self):
        import monkey_pages as mp
        from firmforge import llm as llm_mod
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        self.sandbox = mp.make_sandbox()
        self.saved = (llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port,
                      sys.excepthook)
        self.errors: list[str] = []
        sys.excepthook = lambda t, v, tb: self.errors.append(f"{t.__name__}: {v}")

        def no_idf(*a, **k):
            raise esp_idf.IdfUnavailable("测试：当作没装 ESP-IDF")
        esp_idf.IdfEnv.load = staticmethod(no_idf)
        board_mod.find_port = lambda *a, **k: None
        self.llm_mod = llm_mod

    def tearDown(self):
        from firmforge.gui.runner import BOARD_USERS
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        (self.llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port,
         sys.excepthook) = self.saved
        BOARD_USERS.clear()
        for p in self.sandbox.rglob("*"):
            if p.is_dir():
                p.chmod(0o700)
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_network_down_in_chat_run(self):
        # 对话页上板运行时断网：提示是模型调用失败、怎么办；运行目录完整，能回放
        from test_gui_monkey import wait_for
        from firmforge.gui.chat_page import ChatPage
        from firmforge.llm import FakeLLM

        def reply(messages):
            raise LLMError(NETWORK_DOWN)
        self.llm_mod.OpenAICompatClient.from_settings = staticmethod(lambda s: FakeLLM(reply))
        page = ChatPage(self.sandbox)
        page.start_embedded(task_id="ledc_pwm", config_name="none")
        info = wait_for(page.backend)
        self.assertIn("模型调用失败", info["error"])
        self.assertIn("API key", info["error"])
        self.app.processEvents()
        runs = [p for p in (self.sandbox / ".ff_runs").iterdir() if "ledc_pwm-none" in p.name]
        self.assertEqual(len(runs), 1)
        self.assertEqual(json.loads((runs[0] / "run.json").read_text(encoding="utf-8"))["verdict"], "error")
        page.start_replay(runs[0])
        page.replay.stop() if hasattr(page.replay, "stop") else None
        self.assertEqual(self.errors, [])

    def test_stopped_run_with_truncated_logs(self):
        # 运行被强制结束：events.jsonl 和 llm_calls.jsonl 最后一行只写了一半，回放和看任务单都不出错
        from firmforge.gui.chat_page import ChatPage
        rd = sorted((self.sandbox / ".ff_runs").glob("*-eval-*"))
        if not rd:
            self.skipTest("项目里没有可回放的运行记录")
        rd = rd[-1]
        for name in ("events.jsonl", "llm_calls.jsonl"):
            if (rd / name).is_file():
                with (rd / name).open("a", encoding="utf-8") as f:
                    f.write('{"n": 9')
        page = ChatPage(self.sandbox)
        page.start_replay(rd)
        page.show_brief(("call", "coder", "x"), switch=False)
        self.assertEqual(self.errors, [])

    def test_export_to_unwritable_folder(self):
        # 导出代码到没有写权限的文件夹：弹出说明，不是没人接的异常
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from firmforge.gui.chat_page import ChatPage
        rd = sorted((self.sandbox / ".ff_runs").glob("*-eval-*"))
        if not rd:
            self.skipTest("项目里没有可回放的运行记录")
        if os.geteuid() == 0:
            self.skipTest("root 不受目录权限限制")
        ro = self.sandbox / "只读 文件夹"
        ro.mkdir()
        ro.chmod(0o500)
        page = ChatPage(self.sandbox)
        page.run_dir = rd[-1]
        shown = []
        saved = (QFileDialog.getExistingDirectory, QMessageBox.warning, QMessageBox.information)
        QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(ro))
        QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(("warning", a[1], a[2])))
        QMessageBox.information = staticmethod(lambda *a, **k: shown.append(("info", a[1], a[2])))
        try:
            page.export_code()
        finally:
            QFileDialog.getExistingDirectory, QMessageBox.warning, QMessageBox.information = saved
        self.assertEqual(self.errors, [])
        self.assertTrue(shown)
        self.assertIn(shown[0][1], ("导出失败", "还没有代码"))

    def test_export_to_path_with_chinese_and_spaces(self):
        from firmforge.gui import board_view as bv
        rd = [p for p in sorted((self.sandbox / ".ff_runs").glob("*-eval-*")) if bv.final_attempts(p)]
        if not rd:
            self.skipTest("项目里没有带代码的运行记录")
        dest = self.sandbox / "导出 的 代码"
        files = bv.export_run(rd[-1], dest)
        self.assertTrue(files)
        self.assertTrue(all(f.is_file() and str(f).startswith(str(dest)) for f in files))

    def test_broken_rule_library_is_shown(self):
        # 规则库文件坏了：硬件页明说读不了，不能显示成"0 条已确认"让人以为是空的
        from firmforge.gui.hardware_page import HardwarePage
        page = HardwarePage(self.sandbox)
        rules = self.sandbox / page.hw / "rules.json"
        rules.write_text('[{"id": "R-1", ', encoding="utf-8")
        page.reload()
        self.assertIn("读不了", page.rules_sub.text())
        self.assertEqual(self.errors, [])

    def test_truncated_eval_results_page(self):
        # 评测页读到最后一行写坏的 runs.jsonl：照常显示前面的结果
        from firmforge.gui.eval_page import EvalPage
        out = self.sandbox / "results" / "board_full"
        if not (out / "runs.jsonl").is_file():
            self.skipTest("没有上一轮评测数据")
        with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write('{"task": "x", "con')
        page = EvalPage(self.sandbox)
        page.out_combo.setEditText("results/board_full")
        page.refresh()
        self.assertEqual(page.out_dir, out)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

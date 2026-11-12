"""界面的随机操作测试（猴子 + 不变量）和后台运行（对话页上板运行、硬件页提炼规则、评测页）的测试。

猴子测试每次只跑几个种子、每个几十步，保证全部测试几分钟内跑完；要长跑用 tests/monkey_designer.py、tests/monkey_pages.py。
后台运行用假模型、"找不到 ESP-IDF 和板子"的假环境，在项目副本里跑，不联网、不花钱、不动真实的规则库。
"""

import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import helpers

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False

sys.path.insert(0, str(Path(__file__).resolve().parent))


def wait_for(backend, timeout_ms: int = 60000) -> dict:
    """等后台运行结束，返回它 finished 发出的信息。"""
    from PySide6.QtCore import QEventLoop, QTimer
    got = {}
    loop = QEventLoop()
    backend.finished.connect(lambda info: (got.update(info), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    if backend.worker is not None and backend.worker.isFinished():
        QApplication.processEvents()
    if not got:
        loop.exec()
    return got


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class MonkeyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication, QSettings
        cls.app = QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeMonkeyTest")
        QSettings().clear()

    def test_designer_monkey(self):
        # 设计器：固定种子的随机拖拽、拉线、断开、删除、撤销、改名，每一步检查 10 条不变量
        import monkey_designer as md
        from firmforge.gui.app import MainWindow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        w.resize(1440, 900)
        w.show()
        try:
            for seed in (101, 202, 303):
                md.reset(w)
                found = md.Monkey(w, seed).run(60)
                self.assertEqual(found, [], "\n".join(found))
        finally:
            w.close()

    def test_pages_monkey(self):
        # 其他几页：随机点按钮、选下拉框、填输入框、切标签、拖分隔条、改窗口大小；不能出异常、面板不能超出窗口
        import monkey_pages as mp
        sandbox = mp.make_sandbox()
        cwd = os.getcwd()
        env = {k: os.environ.get(k) for k in ("FF_HOME", "HOME")}
        os.chdir(sandbox)
        os.environ["FF_HOME"] = os.environ["HOME"] = str(sandbox)
        hook = sys.excepthook
        errors = mp.Errors()
        from firmforge.gui.app import MainWindow
        try:
            for seed in (7, 8):
                w = MainWindow(sandbox)
                w.apply_theme()
                w.resize(1440, 900)
                w.show()
                found = mp.run_seed(w, seed, 50, errors)
                mp.stop_backends(w)
                w.close()
                self.assertEqual(found, [], "\n".join(found))
        finally:
            mp.restore_world()
            sys.excepthook = hook
            os.chdir(cwd)
            for k, v in env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            shutil.rmtree(sandbox, ignore_errors=True)


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class MonkeyFoundTest(unittest.TestCase):
    """猴子测试找到的 bug（2026-11-10），每个一条回归测试。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        from firmforge.gui.app import MainWindow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        w.resize(1440, 900)
        w.show()
        return w

    def test_line_from_branch_to_own_parallel(self):
        # 从分支 code 拉线到它自己的并行 write：成环，不做；以前程序报错
        w = self.window()
        d = w.designer
        before = d.wf
        d.connect_steps("code", "write")
        self.assertEqual(d.wf, before)
        w.close()

    def test_no_op_leaves_no_undo_entry(self):
        # 实际没改动的操作不留撤销记录；以前要按两次 Ctrl+Z 才撤回真正的改动
        w = self.window()
        d = w.designer
        d.add_step("verify")
        after_add = d.wf
        n = len(d.undo)
        d.connect_steps("check", "check")                 # 拉线到自己
        d._step_dropped("check", 0, 0, ("after", "test", ""), True)    # check 本来就在那儿（并行之后）
        d.code.setPlainText(d.code.toPlainText())
        d._apply_code()
        self.assertEqual(len(d.undo), n)
        d.do_undo()
        self.assertNotEqual(d.wf, after_add)              # 一次撤销就撤回了加的那一步
        w.close()

    def test_stale_selection(self):
        # 选中的步骤被撤销掉以后，双击步骤箱加步骤不报错（以前 KeyError）
        w = self.window()
        d = w.designer
        d.add_step("verify")
        sid = d.selected
        d.do_undo()
        self.assertIsNone(d._get(sid))
        self.assertIsNone(d.selected)
        d.add_step("human")
        self.assertIsNotNone(d._get(d.selected))
        w.close()

    def test_ask_ai_twice_and_failure(self):
        # 连点两次"让 AI 改"：第二次被拦下；模型调用失败时提示，不报错
        from unittest import mock
        from firmforge.gui.runner import _Worker, shutdown_workers
        w = self.window()
        d = w.designer
        warned = []
        with mock.patch("firmforge.gui.designer_page.QMessageBox.information", lambda *a, **k: warned.append(a[1])), \
                mock.patch("firmforge.gui.designer_page.QMessageBox.warning", lambda *a, **k: warned.append(a[1])):
            slow = _Worker(lambda: __import__("time").sleep(0.5))
            d._ai = slow
            slow.start()
            d.ask_ai("加一步")
            self.assertIn("AI 还在改", warned)
            slow.wait()
            bad = _Worker(lambda: None)                   # 没有结果（比如被停掉了）
            bad.start()
            bad.wait()
            d._ai_done(bad)
            self.assertIn("AI 没有改成", warned)
        shutdown_workers()
        w.close()

    def test_close_with_running_backend(self):
        # 还有运行没结束时关窗口：先问一声，确定后停掉、等后台线程结束，不会崩溃
        from unittest import mock
        from firmforge.gui.runner import LIVE_WORKERS, _Worker
        w = self.window()
        stop = []

        class Busy:
            def stop(self):
                stop.append(1)
        w.evalp.backend = Busy()
        job = _Worker(lambda: __import__("time").sleep(0.3))
        job.start()
        self.assertIn("评测页的评测", w.busy())
        with mock.patch("PySide6.QtWidgets.QMessageBox.question", lambda *a, **k: __import__("PySide6.QtWidgets",
                        fromlist=["QMessageBox"]).QMessageBox.No):
            w.close()
        self.assertTrue(w.isVisible())                    # 选了"否"：不关
        with mock.patch("PySide6.QtWidgets.QMessageBox.question", lambda *a, **k: __import__("PySide6.QtWidgets",
                        fromlist=["QMessageBox"]).QMessageBox.Yes):
            w.close()
        self.assertEqual(stop, [1])
        self.assertFalse(any(x.isRunning() for x in LIVE_WORKERS))
        w.evalp.backend = None

    def test_settings_worker_without_result(self):
        # 设置页的后台任务被中途停掉、没有结果时：提示，不报错
        from unittest import mock
        w = self.window()
        sp = w.settings
        shown = []
        with mock.patch("firmforge.gui.settings_page.QMessageBox.information", lambda *a, **k: shown.append(a[2])):
            import subprocess  # noqa: F401
            from firmforge.gui import settings_page
            with mock.patch.object(settings_page, "_Worker") as W:
                inst = W.return_value
                inst.error, inst.result = "", None
                sp._selftest()
                cb = inst.finished.connect.call_args[0][0]
                cb()
        self.assertTrue(any("没有拿到结果" in s for s in shown))
        w.close()


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class TryRunTest(unittest.TestCase):
    """猴子测试找到的：对话页选着"上板"时，设计器"试运行"拿了上板题的编号去 tasks/ 里找，报错。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_try_run_while_chat_is_on_board_flow(self):
        from PySide6.QtWidgets import QInputDialog
        from firmforge.gui.app import MainWindow
        from firmforge.orchestrator.workflow import load_workflow
        w = MainWindow(helpers.ROOT)
        w.apply_theme()
        started = []
        w.chat.start_run = lambda task: started.append(task.id)
        w.chat.flow_combo.setCurrentIndex(w.chat.flow_combo.findData("embedded"))
        w.chat.task_combo.setCurrentIndex(w.chat.task_combo.findData("ledc_pwm"))     # 上板题
        saved = QInputDialog.getItem
        QInputDialog.getItem = staticmethod(lambda *a, **k: ("crc16_modbus", True))
        try:
            w._try_run(load_workflow())
        finally:
            QInputDialog.getItem = saved
        self.assertEqual(started, ["crc16_modbus"])
        self.assertFalse(w.chat.embedded)                    # 切到了纯逻辑流程
        w.close()


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class BackendTest(unittest.TestCase):
    """对话页、硬件页、评测页的后台运行（覆盖率最低的一块）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import monkey_pages as mp
        from firmforge import llm as llm_mod
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        self.sandbox = mp.make_sandbox()
        self.saved = (llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port)
        self.replies: list = []

        def no_idf(*a, **k):
            raise esp_idf.IdfUnavailable("测试：当作没装 ESP-IDF")
        esp_idf.IdfEnv.load = staticmethod(no_idf)
        board_mod.find_port = lambda *a, **k: None
        from firmforge.llm import FakeLLM
        llm_mod.OpenAICompatClient.from_settings = staticmethod(lambda s: FakeLLM(self._reply))

    def tearDown(self):
        from firmforge import llm as llm_mod
        from firmforge.target import board as board_mod
        from firmforge.target import esp_idf
        from firmforge.gui.runner import BOARD_USERS
        llm_mod.OpenAICompatClient.from_settings, esp_idf.IdfEnv.load, board_mod.find_port = self.saved
        BOARD_USERS.clear()
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def _reply(self, messages):
        system = messages[0]["content"]
        for key, text in self.replies:
            if key in system:
                return text
        return "不按格式的回复"

    def test_embedded_task_single_without_idf(self):
        # 对话页选评测题 + 单 Agent：没有 ESP-IDF 时编不了，结论如实是没通过（不会假装通过），板子用完会释放
        from firmforge.gui.runner import BOARD_USERS, EmbeddedBackend
        ref = (self.sandbox / "tasks_board" / "ledc_pwm" / "reference" / "pwm_out.c").read_text(encoding="utf-8")
        self.replies = [("资深嵌入式 C 工程师", f"<<<FILE pwm_out.c\n{ref}>>>FILE\n")]
        b = EmbeddedBackend()
        events = []
        b.event.connect(events.append)
        b.start(project=self.sandbox, hw="hardware/esp32c3_supermini", runs_dir=self.sandbox / ".ff_runs",
                task_id="ledc_pwm", config_name="single")
        self.assertIn(b, BOARD_USERS)
        info = wait_for(b)
        self.assertNotIn(b, BOARD_USERS)
        self.assertEqual(info["judge"]["config"], "single")
        self.assertNotEqual(info["judge"]["verdict"], "pass")
        self.assertTrue(any(e["type"] == "run_started" for e in events))

    def test_embedded_spec_asks_for_rule_review(self):
        # 写需求 + 规则库：规则库缺规则时查资料，核对后等人确认（ask("rules")），确认后流程继续
        import tomllib  # noqa: F401
        from firmforge.gui.runner import EmbeddedBackend
        rule = {"id": "R-UART-001", "kind": "api", "scope": ["uart"], "text": "UART 先用 uart_driver_install() 装驱动，再收发",
                "cite": {"doc": "idf-driver-uart-h", "page": None, "section": "",
                         "quote": "esp_err_t uart_driver_install(uart_port_t uart_num, int rx_buffer_size"},
                "tier": "official_code", "check": {"type": "api_exists", "symbol": "uart_driver_install",
                                                   "header": "driver/uart.h"}}
        self.replies = [("资料核查员", "```json\n" + json.dumps([rule], ensure_ascii=False) + "\n```"),
                        ("规则核对员", '```json\n{"faithful": true, "reason": "一致"}\n```')]
        b = EmbeddedBackend()
        asked = []
        b.ask.connect(lambda kind, payload: (asked.append((kind, payload)),
                                             b.answer({r["id"]: ("reject", "测试") for r in payload["pending"]})))
        b.start(project=self.sandbox, hw="hardware/esp32c3_supermini", runs_dir=self.sandbox / ".ff_runs",
                spec="读一下温度", name="t", rule_mode="verified")
        info = wait_for(b, 120000)
        self.assertTrue(asked and asked[0][0] == "rules")
        self.assertIn("R-UART-001", [r["id"] for r in asked[0][1]["pending"]])
        self.assertTrue(asked[0][1]["pending"][0]["steps"])                # 带着核对的每一步
        self.assertIn(info.get("verdict"), ("fail", "incomplete", "error", "pass"))
        rules = json.loads((self.sandbox / "hardware" / "esp32c3_supermini" / "rules.json").read_text(encoding="utf-8"))
        self.assertNotIn("R-UART-001", [r["id"] for r in rules])           # 驳回了，没有入库

    def test_rules_backend_and_eval_backend_errors(self):
        # 硬件页提炼规则：查不到东西也能正常结束并报告；评测页：没有 ESP-IDF 时开始不了，并说明原因
        from firmforge.embedded.workspace import WorkspaceError
        from firmforge.gui.runner import BOARD_USERS, EvalBackend, RulesBackend
        r = RulesBackend()
        r.start(project=self.sandbox, hw="hardware/esp32c3_supermini", scopes=["uart"], runs_dir=self.sandbox / ".ff_runs")
        info = wait_for(r, 120000)
        self.assertEqual(info["error"], "")
        self.assertIn("missing", info)
        e = EvalBackend()
        with self.assertRaises(WorkspaceError) as cm:
            e.start(project=self.sandbox, hw="hardware/esp32c3_supermini", task_ids=["ledc_pwm"], configs=["single"],
                    runs=1, out_dir=self.sandbox / "results" / "x", max_cost=1.0)
        self.assertIn("ESP-IDF", str(cm.exception))
        self.assertNotIn(e, BOARD_USERS)                                   # 开始不了也要释放板子
        with self.assertRaises(WorkspaceError):
            e.start(project=self.sandbox, hw="hardware/esp32c3_supermini", task_ids=["ledc_pwm"], configs=["single"],
                    runs=1, out_dir=self.sandbox / "results" / "x", max_cost=0)


if __name__ == "__main__":
    unittest.main()

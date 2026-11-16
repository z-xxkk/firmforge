"""模块没通过时（第 139 条）：先按运行记录讲清经过和放行风险，再问负责人接着修、人工放行或放弃。
放行要写理由并再确认一次；结论如实记"人工放行"，依赖它的模块和集成照常往下走。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.embedded.design import Design, DesignModule
from firmforge.embedded.explain import explain_failure, release_risks
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import load_workflow
from firmforge.runs import read_events
from test_embedded_design import profile
from test_multi_agent import SETTINGS

A = DesignModule("adc_reader", "ADC", "hardware", ("adc_reader.c", "include/adc_reader.h"),
                 provides=("int adc_read(void)",), scopes=("adc",), acceptance=("读数在 2800–3100 mV",))
B = DesignModule("logger", "记录", "hardware", ("logger.c", "include/logger.h"), provides=("int log_it(void)",),
                 scopes=("adc",), depends_on=("adc_reader",), acceptance=("每秒记一次",))
PASS = ("pass", "board_test", "共 1 个用例，通过 1")
FAIL = ("fail", "board_test", "失败：读数在范围内（./main/test/t.c:9） Expected TRUE Was FALSE")


def team(seen):
    def reply(messages):
        system, last = messages[0]["content"], messages[-1]["content"] or ""
        if "资深嵌入式 C 工程师" in system:
            seen.append(("coder", last))
            mod = "logger" if "logger" in (messages[1]["content"] or "")[:400] else "adc_reader"
            return f"<<<FILE {mod}.c\nint x_{len(seen)}(void) {{ return 1; }}\n>>>FILE\n"
        if "测试工程师" in system:
            seen.append(("tester", last))
            return '<<<FILE test/test_adc_reader.c\n#include "unity.h"\nTEST_CASE("t", "[a]") {}\n>>>FILE\n' \
                   '<<<FILE test/test_logger.c\n#include "unity.h"\nTEST_CASE("t", "[a]") {}\n>>>FILE\n'
        if "诊断工程师" in system:
            seen.append(("diag", last))
            return '```json\n{"blame": "code", "rule": "", "evidence": "读数不对", "instruction": "改换算"}\n```'
        if "审查" in system:
            return '```json\n{"verdict": "ok", "issues": []}\n```'
        raise AssertionError(system[:40])
    return reply


class FlowTest(unittest.TestCase):
    def run_flow(self, checks, answers, modules=(A,)):
        seen, asked = [], []
        it = iter(checks)

        def confirm_failed(m, payload):
            asked.append(payload)
            return answers[len(asked) - 1]
        from dataclasses import replace
        settings = replace(SETTINGS, loop={**(SETTINGS.loop or {}), "mutants": 0})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: next(it, checks[-1])):
            f = EmbeddedFlow(llm=FakeLLM(team(seen)), settings=settings, profile=profile(), spec="读 ADC 并记录",
                             rules=RuleSet(), library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None,
                             board=None, design=Design(list(modules)), build_rules=False,
                             workflow=load_workflow("firmware_dev"), confirm_failed=confirm_failed)
            res = f.run()
            ev = read_events(res.run_dir)
        return res, seen, asked, ev

    def test_explain_and_risks_before_asking(self):
        res, seen, asked, ev = self.run_flow([FAIL], [{"action": "give_up"}])
        self.assertEqual(res.modules["adc_reader"].verdict, "fail")
        p = asked[0]
        self.assertEqual(p["attempts"], 4)
        self.assertTrue(any(ln.startswith("第 1 次：") and "没通过" in ln and "读数在范围内" in ln for ln in p["explain"]))
        self.assertTrue(any("诊断：错在代码" in ln for ln in p["explain"]))
        self.assertTrue(any("读数在 2800–3100 mV" in ln for ln in p["risks"]))           # 没被证明的验收条件
        self.assertTrue(any("人工放行" in ln and "不算通过" in ln for ln in p["risks"]))
        self.assertTrue(any(e["type"] == "human_wait" and e["data"].get("step") == "failed" for e in ev))

    def test_repair_more_with_hint_and_strong_coder(self):
        res, seen, asked, ev = self.run_flow([FAIL, FAIL, FAIL, FAIL, FAIL, PASS],
                                             [{"action": "repair", "hint": "用曲线拟合校准"}])
        self.assertEqual(res.modules["adc_reader"].verdict, "pass")
        self.assertEqual(len(asked), 1)
        rr = [e["data"] for e in ev if e["type"] == "repair_resumed"][0]
        self.assertEqual((rr["attempts"], rr["hint"], rr["coder"]), (3, "用曲线拟合校准", "coder-strong"))
        self.assertTrue(any("负责人的提示：用曲线拟合校准" in t for k, t in seen if k in ("coder", "diag")))

    def test_release_lets_dependents_continue(self):
        res, seen, asked, ev = self.run_flow([FAIL, FAIL, FAIL, FAIL, PASS],
                                             [{"action": "release", "reason": "是验收条件的问题"}], modules=(A, B))
        self.assertEqual(res.modules["adc_reader"].verdict, "released")
        self.assertIn("是验收条件的问题", res.modules["adc_reader"].last_report)
        self.assertEqual(res.modules["logger"].verdict, "pass")                              # 依赖它的模块照常做
        self.assertIn(res.verdict, ("released", "incomplete"))                               # 不会记成通过
        self.assertNotEqual(res.verdict, "pass")
        rel = [e["data"] for e in ev if e["type"] == "module_released"][0]
        self.assertEqual(rel["reason"], "是验收条件的问题")

    def test_no_callback_behaves_as_before(self):
        seen, it = [], iter([FAIL])
        from dataclasses import replace
        settings = replace(SETTINGS, loop={**(SETTINGS.loop or {}), "mutants": 0})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: FAIL):
            f = EmbeddedFlow(llm=FakeLLM(team(seen)), settings=settings, profile=profile(), spec="x", rules=RuleSet(),
                             library=None, verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None,
                             design=Design([A]), build_rules=False, workflow=load_workflow("firmware_dev"))
            self.assertEqual(f.run().modules["adc_reader"].verdict, "fail")


class ExplainTest(unittest.TestCase):
    def test_code_never_changed_is_called_out(self):
        with tempfile.TemporaryDirectory() as d:
            rd = Path(d)
            for n in (1, 2):
                p = rd / "nodes" / "adc_reader" / f"attempt_{n}"
                (p / "main").mkdir(parents=True)
                (p / "main" / "adc_reader.c").write_text("int a;", encoding="utf-8")
                (p / "report.txt").write_text("编译失败：\nmain/test/t.c:3: error: 'X' undeclared", encoding="utf-8")
            ev = [{"node_id": "adc_reader", "type": "verify_finished",
                   "data": {"attempt": n, "scope": "build", "ok": False, "failed_layer": "build"}} for n in (1, 2)]
            ev.insert(1, {"node_id": "adc_reader", "type": "diagnosis", "data": {"attempt": 1, "blame": "test",
                                                                                 "instruction": "改测试"}})
            lines = explain_failure(ev, rd, A)
        self.assertIn("第 1 次：编译没通过——main/test/t.c:3: error: 'X' undeclared", lines)   # 跳过"编译失败："标题行
        self.assertTrue(any("代码一直没变" in ln for ln in lines))
        self.assertTrue(any("每次诊断都判为测试的问题" in ln for ln in lines))
        risks = release_risks(A, None, Design([A, B]), None, "失败：x")
        self.assertTrue(any("一次都没通过" in r for r in risks))
        self.assertTrue(any("logger" in r for r in risks))


try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "需要 PySide6（在 firmforge-gui 镜像里跑）")
class CardTest(unittest.TestCase):
    def test_release_needs_reason_and_confirmation(self):
        from PySide6.QtCore import QCoreApplication
        from firmforge.gui.chat_page import ChatPage
        from firmforge.gui.theme import current
        from firmforge.gui.widgets import set_theme
        QApplication.instance() or QApplication([])
        QCoreApplication.setOrganizationName("FirmForgeFailedTest")
        set_theme(current(False))
        with tempfile.TemporaryDirectory() as d:
            page = ChatPage(Path(d))
            answers = []

            class Backend:
                def answer(self, v):
                    answers.append(v)
            page.backend = Backend()
            payload = {"module": "adc_reader", "attempts": 4, "explain": ["第 1 次：上板测试没通过——失败：x"],
                       "risks": ["这个模块一次都没通过检查", "　- 读数在 2800–3100 mV"]}

            def button(text):
                return next(b for b in page.findChildren(QPushButton) if b.text() == text)
            page.on_ask("failed", payload)
            text = "\n".join(w.text() for w in page.findChildren(QLabel))
            self.assertIn("从第一次失败到现在的经过", text)
            self.assertIn("读数在 2800–3100 mV", text)
            button("人工放行…").click()
            self.assertEqual(answers, [])                                     # 没写理由：不放行
            page.input.setPlainText("代码是对的，是验收条件的问题")
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.No):
                button("人工放行…").click()
            self.assertEqual(answers, [])                                     # 确认框点了"否"
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes) as q:
                button("人工放行…").click()
                self.assertIn("读数在 2800–3100 mV", q.call_args[0][2])       # 确认框里再列一遍风险
            self.assertEqual(answers, [{"action": "release", "reason": "代码是对的，是验收条件的问题"}])
            page.on_ask("failed", payload)
            page.input.setPlainText("换成曲线拟合")
            [b for b in page.findChildren(QPushButton) if b.text() == "接着修"][-1].click()
            self.assertEqual(answers[-1], {"action": "repair", "hint": "换成曲线拟合"})
            page.backend = None
            page.deleteLater()


if __name__ == "__main__":
    unittest.main()

"""错误签名与报告结构。"""

import json
import unittest

import helpers  # noqa: F401
from firmforge.verifier.report import FAIL, PASS, SKIPPED, Diagnostic, LayerResult, Report
from firmforge.verifier.signature import compile_key, layer_signature, normalize_message


class SignatureTest(unittest.TestCase):
    def test_normalize_strips_path_line_address_pid(self):
        msg = "/home/u/w/src/a.c:12:5: ==4242== bad access at 0x7ffd1234 in C:\\x\\y\\b.c:7"
        self.assertEqual(normalize_message(msg), "a.c: ==?== bad access at 0x? in b.c")

    def test_keeps_identifiers(self):
        self.assertIn("'ringbuf_put'", normalize_message("implicit declaration of function 'ringbuf_put'"))

    def test_compile_key_independent_of_dir_and_line(self):
        a = compile_key("/a/src/x.c", "error", "expected ';' before 'return'")
        b = compile_key("/tmp/other/src/x.c", "error", "expected ';' before 'return'")
        self.assertEqual(a, b)
        self.assertEqual(compile_key("x.c", "fatal error", "m"), compile_key("x.c", "error", "m"))

    def test_layer_signature_order_and_duplicates(self):
        s1 = layer_signature("unit_tests", ["test:b:FAIL", "test:a:FAIL"])
        s2 = layer_signature("unit_tests", ["test:a:FAIL", "test:b:FAIL", "test:a:FAIL"])
        self.assertEqual(s1, s2)
        self.assertRegex(s1, r"^unit_tests:[0-9a-f]{12}$")
        self.assertNotEqual(s1, layer_signature("unit_tests", ["test:c:FAIL"]))


def _layer(name, status, complete=True):
    return LayerResult(name, name, status, complete=complete)


class ReportTest(unittest.TestCase):
    def test_verdict_pass(self):
        self.assertEqual(Report([_layer("host_compile", PASS)]).verdict, "pass")

    def test_verdict_fail_wins(self):
        r = Report([_layer("host_compile", FAIL), _layer("cross_compile", SKIPPED)])
        self.assertEqual(r.verdict, "fail")
        self.assertEqual(r.failed_layer, "host_compile")

    def test_skipped_or_incomplete_is_never_pass(self):
        self.assertEqual(Report([_layer("a", PASS), _layer("b", SKIPPED)]).verdict, "incomplete")
        self.assertEqual(Report([_layer("a", PASS, complete=False)]).verdict, "incomplete")
        self.assertEqual(Report([]).verdict, "incomplete")

    def test_for_agent_drops_hidden(self):
        hidden = _layer("hidden_tests", FAIL)
        hidden.diagnostics.append(Diagnostic("error", "test_secret:FAIL", "test:test_secret:FAIL"))
        r = Report([_layer("unit_tests", PASS), hidden])
        agent = r.for_agent()
        self.assertEqual([x.layer for x in agent.layers], ["unit_tests"])
        self.assertNotIn("test_secret", agent.to_json())
        self.assertNotIn("hidden_tests", agent.to_json())

    def test_json_roundtrip_and_log_truncation(self):
        lr = _layer("unit_tests", FAIL)
        lr.log = "x" * 10000
        lr.signature = "unit_tests:abc"
        lr.diagnostics.append(Diagnostic("error", "m", "k", "tests/t.c", 3))
        d = json.loads(Report([lr], 1.5).to_json())
        self.assertEqual(d["verdict"], "fail")
        self.assertEqual(d["signature"], "unit_tests:abc")
        self.assertLess(len(d["layers"][0]["log"]), 4100)
        back = Report.from_dict(d)
        self.assertEqual(back.layers[0].diagnostics[0].line, 3)


if __name__ == "__main__":
    unittest.main()

"""工具输出解析器：不需要任何外部工具。"""

import unittest

import helpers
from firmforge.verifier import layers as L


class GccParserTest(unittest.TestCase):
    def test_error_and_warning(self):
        out = ("/w/src/a.c: In function 'f':\n"
               "/w/src/a.c:12:5: error: expected ';' before 'return'\n"
               "/w/src/a.c:3:9: warning: unused variable 'x' [-Wunused-variable]\n"
               "/w/src/a.c:4:1: note: declared here\n")
        diags = L.parse_gcc(out)
        self.assertEqual(len(diags), 2)
        self.assertEqual(diags[0].severity, "error")
        self.assertEqual(diags[0].line, 12)
        self.assertEqual(diags[0].message, "expected ';' before 'return'")
        self.assertEqual(diags[0].key, "a.c: error: expected ';' before 'return'")
        self.assertEqual(diags[1].severity, "warning")

    def test_fatal_error_and_windows_path(self):
        out = "C:\\work\\src\\a.c:1:10: fatal error: foo.h: No such file or directory\n"
        d = L.parse_gcc(out)[0]
        self.assertEqual(d.severity, "error")
        self.assertEqual(d.file, "C:\\work\\src\\a.c")
        self.assertTrue(d.key.startswith("a.c: error: foo.h"))

    def test_link_errors(self):
        out = ("/usr/bin/ld: /tmp/x.o: in function `main':\n"
               "t.c:(.text+0x1a): undefined reference to `ringbuf_put'\n"
               "/usr/bin/ld: b.o: multiple definition of `helper'; a.o: first defined here\n"
               "collect2: error: ld returned 1 exit status\n")
        keys = [d.key for d in L.parse_gcc(out)]
        self.assertIn("link:undefined:ringbuf_put", keys)
        self.assertIn("link:multiple:helper", keys)


class UnityParserTest(unittest.TestCase):
    OUT = ("/w/tests/test_rb.c:20:test_a:PASS\n"
           "/w/tests/test_rb.c:41:test_wrap:FAIL: Expected 3 Was 0\n"
           "/w/tests/test_rb.c:50:test_skip:IGNORE\n"
           "/w/tests/test_rb.c:60:test_bare:FAIL\n"
           "\n-----------------------\n4 Tests 2 Failures 1 Ignored\nFAIL\n")

    def test_parse(self):
        r = L.parse_unity(self.OUT)
        self.assertEqual(r.passed, ["test_a"])
        self.assertEqual(r.failed[0], ("test_wrap", "/w/tests/test_rb.c", 41, "Expected 3 Was 0"))
        self.assertEqual(r.failed[1][0], "test_bare")
        self.assertEqual(r.failed[1][3], "")
        self.assertEqual(r.ignored, ["test_skip"])
        self.assertEqual(r.summary, (4, 2, 1))

    def test_no_summary(self):
        self.assertIsNone(L.parse_unity("x.c:1:test_a:PASS\n").summary)

    def test_sanitizer_lines(self):
        out = ("==123==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd at pc 0x55\n"
               "src/a.c:12:7: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented\n")
        lines = L.sanitizer_lines(out)
        self.assertEqual(len(lines), 2)
        self.assertEqual(L.sanitizer_kind(lines[0]), "asan:stack-buffer-overflow")
        self.assertTrue(L.sanitizer_kind(lines[1]).startswith("ubsan:signed integer overflow"))
        self.assertNotIn("2147483647", L.sanitizer_kind(lines[1]))


class BinutilsParserTest(unittest.TestCase):
    def test_size_berkeley(self):
        out = ("   text\t   data\t    bss\t    dec\t    hex\tfilename\n"
               "    240\t      0\t      0\t    240\t     f0\tringbuf.o\n"
               "    512\t    512\t      4\t   1028\t    404\tcrc.o\n")
        self.assertEqual(L.parse_size(out), [("ringbuf.o", 240, 0, 0), ("crc.o", 512, 512, 4)])

    def test_su_gcc_and_clang(self):
        text = ("ringbuf.c:3:6:ringbuf_init\t0\tstatic\n"
                "ringbuf.c:40:8:ringbuf_write\t16\tstatic\n"
                "vla.c:5:f\t32\tdynamic,bounded\n")
        rows = L.parse_su(text)
        self.assertEqual(rows[0], ("ringbuf_init", 0, "static"))
        self.assertEqual(rows[1], ("ringbuf_write", 16, "static"))
        self.assertEqual(rows[2], ("f", 32, "dynamic,bounded"))

    def test_nm_undefined(self):
        out = "                 U malloc\n                 U memcpy\n"
        self.assertEqual(L.parse_nm_undefined(out), ["malloc", "memcpy"])

    def test_cppcheck(self):
        out = ("/w/src/a.c:10:error:arrayIndexOutOfBounds:Array 'b[4]' accessed at index 4\n"
               "/w/src/a.c:3:style:unusedFunction:The function 'g' is never used.\n")
        rows = L.parse_cppcheck(out)
        self.assertEqual(rows[0][2:4], ("error", "arrayIndexOutOfBounds"))
        self.assertEqual(rows[1][2], "style")

    def test_gcov_picks_named_section(self):
        out = ("File '/usr/include/x86_64-linux-gnu/bits/stdio.h'\nLines executed:0.00% of 4\n\n"
               "File '/w/src/ringbuf.c'\nLines executed:87.50% of 16\nCreating 'ringbuf.c.gcov'\n")
        self.assertEqual(L.parse_gcov(out, "ringbuf.c"), (87.5, 16))
        self.assertIsNone(L.parse_gcov(out, "other.c"))


class CallgraphParserTest(unittest.TestCase):
    """样本来自 arm-none-eabi-gcc 13.2 -fcallgraph-info=su 的真实输出（tests/data/）。"""

    def load(self, name):
        return (helpers.ROOT / "tests" / "data" / name).read_text(encoding="utf-8")

    def test_recursion_found_at_O0(self):
        nodes, edges = L.parse_callgraph([self.load("callgraph_O0.ci")])
        self.assertEqual(sorted(L.find_cycles(nodes, edges)), [["a", "b"], ["rec"]])
        self.assertTrue(nodes["memcpy"].external)
        self.assertEqual(nodes["api"].stack, 56)
        self.assertEqual(nodes["vla"].qualifier, "dynamic")

    def test_Os_hides_recursion(self):
        # -Os 把尾递归优化掉了：这正是递归检查要另用 -O0 的原因
        nodes, edges = L.parse_callgraph([self.load("callgraph_Os.ci")])
        self.assertEqual(L.find_cycles(nodes, edges), [])

    def test_worst_chain(self):
        nodes, edges = L.parse_callgraph([self.load("callgraph_O0.ci")])
        total, path, externals, indirect = L.worst_stack_chain(nodes, edges)
        self.assertEqual((total, path), (72, ["api", "helper"]))   # 56 + 16
        self.assertEqual(externals, {"memcpy", "ext_hook"})
        self.assertFalse(indirect)

    def test_indirect_call_flagged(self):
        nodes, edges = L.parse_callgraph([self.load("callgraph_indirect.ci")])
        total, _path, _ext, indirect = L.worst_stack_chain(nodes, edges)
        self.assertTrue(indirect)
        self.assertEqual(total, 32)                                    # top(0) → mid(32)

    def test_merge_prefers_definition(self):
        a = 'node: { title: "f" label: "f\\nx.c:1:1" shape : ellipse }\n'
        b = 'node: { title: "f" label: "f\\ny.c:1:1\\n24 bytes (static)" }\n'
        nodes, _ = L.parse_callgraph([a, b])
        self.assertEqual(nodes["f"].stack, 24)


if __name__ == "__main__":
    unittest.main()

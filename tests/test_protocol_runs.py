"""文件块协议、运行目录、事件总线、SQLite 存储。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401
from firmforge.agents.protocol import check_path, parse_files, write_files
from firmforge.orchestrator.events import EventBus
from firmforge.runs import RunDir, copy_snapshot, read_events
from firmforge.store import Store

DIRS = ("src", "tests")


class ProtocolTest(unittest.TestCase):
    def test_parse_multiple_blocks(self):
        text = ("说明\n<<<FILE src/a.c\nint a;\n>>>FILE\n中间的话\n"
                "<<<FILE tests/test_a.c\n#include \"unity.h\"\n>>>FILE\n")
        r = parse_files(text, DIRS)
        self.assertEqual(r.files, {"src/a.c": "int a;\n", "tests/test_a.c": "#include \"unity.h\"\n"})
        self.assertEqual(r.rejected, [])

    def test_rejects_paths_outside_whitelist(self):
        cases = ["../evil.c", "/etc/passwd.c", "C:\\x\\a.c", "include/ringbuf.h", "hidden_tests/t.c",
                 "src/sub/a.c", "src/a.py", "tests/../src/a.c", "src/a b.c"]
        text = "".join(f"<<<FILE {p}\nx\n>>>FILE\n" for p in cases)
        r = parse_files(text, DIRS)
        self.assertEqual(r.files, {})
        self.assertEqual([p for p, _ in r.rejected], cases)

    def test_tester_whitelist(self):
        self.assertIsNone(check_path("src/a.c", ("tests",))[0])
        self.assertEqual(check_path("tests\\test_a.c", ("tests",))[0], "tests/test_a.c")

    def test_unterminated_block(self):
        r = parse_files("<<<FILE src/a.c\nint a;\n", DIRS)
        self.assertEqual(r.files, {})
        self.assertEqual(r.unterminated, 1)

    def test_closing_tag_must_start_line(self):
        r = parse_files("<<<FILE src/a.c\nconst char *s = \">>>FILE\";\n>>>FILE\n", DIRS)
        self.assertIn('">>>FILE"', r.files["src/a.c"])

    def test_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_files(Path(tmp), {"src/a.c": "int a;\n"})
            self.assertEqual((Path(tmp) / "src" / "a.c").read_text(encoding="utf-8"), "int a;\n")


class RunsEventsStoreTest(unittest.TestCase):
    def test_run_dir_names_are_unique(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = RunDir.create(tmp, "ringbuf", now=1730000000)
            b = RunDir.create(tmp, "ringbuf", now=1730000000)
            self.assertNotEqual(a.run_id, b.run_id)
            self.assertTrue(a.run_id.endswith("-ringbuf"))
            self.assertEqual(a.attempt_dir("m", 2).relative_to(a.path).as_posix(), "nodes/m/attempt_2")

    def test_copy_snapshot_skips_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "a"
            (src / "src").mkdir(parents=True)
            (src / "src" / "x.c").write_text("x", encoding="utf-8")
            (src / ".ff_build").mkdir()
            copy_snapshot(src, Path(tmp) / "b")
            self.assertTrue((Path(tmp) / "b" / "src" / "x.c").exists())
            self.assertFalse((Path(tmp) / "b" / ".ff_build").exists())

    def test_event_bus_writes_and_pushes(self):
        with tempfile.TemporaryDirectory() as tmp:
            seen = []
            bus = EventBus("r1", Path(tmp) / "events.jsonl", clock=lambda: 100.0)
            bus.subscribe(seen.append)
            bus.emit("run_started", task="t")
            bus.emit("verify_finished", "m", attempt=1, verdict="fail")
            events = read_events(tmp)
            self.assertEqual([e["type"] for e in events], ["run_started", "verify_finished"])
            self.assertEqual(events[1]["node_id"], "m")
            self.assertEqual(events[1]["data"]["verdict"], "fail")
            self.assertEqual(seen, events)
            with self.assertRaises(ValueError):
                bus.emit("not_a_type")

    def test_store(self):
        s = Store(":memory:")
        s.start_run("r1", "ringbuf", "b2")
        s.add_attempt("r1", "ringbuf", 1, "coder-strong", "fail", "host_compile", "host_compile:abc", 100, 1.5)
        s.finish_run("r1", "pass", 300, 0.01, 12.0)
        run = s.query("SELECT * FROM runs")[0]
        self.assertEqual((run["verdict"], run["total_tokens"]), ("pass", 300))
        att = s.query("SELECT * FROM node_attempts")[0]
        self.assertEqual(att["failed_layer"], "host_compile")
        s.close()


if __name__ == "__main__":
    unittest.main()

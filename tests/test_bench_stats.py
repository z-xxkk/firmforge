"""性能测试与延迟统计（DECISIONS 第 65 条）。用假模型，不花钱。"""

import json
import tempfile
import unittest
from pathlib import Path

import helpers
from firmforge.evaluation.bench import bench_scheduling, bench_verify, to_markdown
from firmforge.evaluation.stats import collect, to_markdown as stats_md
from firmforge.tasks import load_task

RB = load_task(helpers.TASKS / "ringbuf")


@unittest.skipUnless(helpers.HAS_CC and helpers.HAS_CROSS and helpers.HAS_CPPCHECK, "需要完整工具链（在 Docker 里跑）")
class BenchTest(unittest.TestCase):
    def test_parallel_beats_serial_and_respects_limit(self):
        rows = bench_scheduling(RB, sizes=(3,), limits=(1, 3), latency_s=0.3, toolchain=helpers.TC)
        by = {(r.strategy, r.llm_limit): r for r in rows}
        self.assertEqual({r.verdict for r in rows}, {"pass"})
        self.assertEqual(by[("serial", 1)].peak_agents, 1)
        self.assertLessEqual(by[("smart", 1)].peak_agents, 1)                 # 并发上限说到做到
        self.assertGreater(by[("smart", 3)].peak_agents, 1)
        self.assertLess(by[("smart", 3)].wall_s, by[("serial", 1)].wall_s)
        ver = bench_verify(RB, n=2, limits=(1, 2), toolchain=helpers.TC)
        self.assertEqual([r.verify_limit for r in ver], [1, 2])
        self.assertIn("## 判定吞吐", to_markdown(rows, ver, 0.3))


class StatsTest(unittest.TestCase):
    def test_collect(self):
        with tempfile.TemporaryDirectory() as d:
            rd = Path(d) / "r1"
            rd.mkdir()
            calls = [{"agent": "coder-fast", "model": "m1", "ok": True, "latency_s": x, "prompt_tokens": 10,
                      "completion_tokens": 5} for x in (1.0, 2.0, 3.0)]
            calls.append({"agent": "coder-fast", "model": "m1", "ok": False, "latency_s": None})
            (rd / "llm_calls.jsonl").write_text("\n".join(json.dumps(c) for c in calls), encoding="utf-8")
            ev = [{"ts": 1.0, "type": "verify_started", "node_id": "a", "data": {"attempt": 1, "scope": "module"}},
                  {"ts": 3.5, "type": "verify_finished", "node_id": "a", "data": {"attempt": 1, "scope": "module"}},
                  {"ts": 4.0, "type": "tool_result", "node_id": "a", "data": {"tool": "kb_search", "ok": True, "latency_s": 0.2}},
                  {"ts": 4.1, "type": "tool_result", "node_id": "a", "data": {"tool": "kb_search", "ok": False, "latency_s": 0.4}}]
            (rd / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ev), encoding="utf-8")
            s = collect(Path(d))
            self.assertEqual(s["runs"], 1)
            self.assertEqual(len(s["agents"]["coder-fast"]), 3)                # 失败的调用不算延迟
            self.assertEqual(s["verify"], [2.5])
            md = stats_md(s)
            self.assertIn("| coder-fast | 3 | 2.0 | 2.8 | 3.0 | 15 |", md)
            self.assertIn("| kb_search | 2 | 50% | 0.30 |", md)


if __name__ == "__main__":
    unittest.main()

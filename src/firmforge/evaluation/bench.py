"""性能测试 `firmforge bench`（DECISIONS 第 65 条）：不花钱，用假模型（每次调用固定延迟）测调度能力。

1 调度吞吐：同一批互不依赖的模块（1、2、4、8 个），串行调度（b3 的 serial 策略）和智能并行（smart 策略）
  在不同的 LLM 并发上限下各跑一次。两边都关掉审查、升级、经验库，只差"怎么排"。
  记录：总用时、每分钟完成的模块数、同时在干活的 Agent 最多几个、平均每次调用等了多久才轮到。
2 判定吞吐：同一批模块目录，判定并发 1、2、4 下真跑编译和测试（需要工具链，在 Docker 里跑）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from ..agents.protocol import write_files
from ..config import ModelTier, Settings, Toolchain
from ..llm import FakeLLM
from ..orchestrator.dag import public_functions
from ..orchestrator.engine import MultiConfig, run_multi
from ..tasks import Task
from ..verifier import verify

SERIAL = MultiConfig("bench-serial", "serial", fixed={"coder": "coder-strong"})
PARALLEL = MultiConfig("bench-parallel", "smart")


def _block(path: str, content: str) -> str:
    return f"<<<FILE {path}\n{content if content.endswith(chr(10)) else content + chr(10)}>>>FILE\n"


def _modules(task: Task, n: int) -> list[dict]:
    first = {"id": "m0", "title": "公共函数", "task_type": task.category,
             "files": [f"src/{f.name}" for f in sorted(task.reference_dir.glob("*.[ch]"))],
             "provides": public_functions(task), "interface": {}, "depends_on": [], "acceptance": ["要点"]}
    rest = [{"id": f"m{i}", "title": f"工具 {i}", "task_type": task.category,
             "files": [f"src/u{i}.c", f"src/u{i}.h"], "provides": [],
             "interface": {f"src/u{i}.h": [f"int u{i}(int x);"]}, "depends_on": [], "acceptance": ["要点"]}
            for i in range(1, n)]
    return [first, *rest]


def fake_team(task: Task, n: int):
    """规划出 n 个互不依赖的模块；编码给正确代码；测试给冒烟测试。"""
    import re
    mods = _modules(task, n)
    by_id = {m["id"]: m for m in mods}
    ref_code = "".join(_block(f"src/{f.name}", f.read_text(encoding="utf-8")) for f in sorted(task.reference_dir.glob("*.[ch]")))
    header = sorted(h.name for h in task.include_dir.glob("*.h") if not h.name.startswith("hal_"))[0]

    def reply(messages):
        system, user = messages[0]["content"], messages[-1]["content"]
        m = re.search(r"\[module (\w+)", user) or re.search(r"\[module (\w+)", messages[1]["content"])
        mid = m.group(1) if m else None
        if system.startswith("你是嵌入式软件架构师"):
            return "```json\n" + json.dumps({"modules": mods}, ensure_ascii=False) + "\n```"
        if system.startswith("你是嵌入式软件的测试工程师"):
            h = header if mid == "m0" else f"u{mid[1:]}.h"
            test = (f'#include "unity.h"\n#include "{h}"\nstatic void t(void) {{ TEST_ASSERT_TRUE(1); }}\n'
                    "int main(void) { UNITY_BEGIN(); RUN_TEST(t); return UNITY_END(); }\n")
            return _block(f"tests/test_{mid}.c", test)
        if system.startswith("你是一名资深嵌入式 C 工程师"):
            if mid == "m0":
                return ref_code
            i = mid[1:]
            return (_block(f"src/u{i}.h", f"#ifndef U{i}_H\n#define U{i}_H\nint u{i}(int x);\n#endif\n")
                    + _block(f"src/u{i}.c", f'#include "u{i}.h"\nint u{i}(int x) {{ return x + {i}; }}\n'))
        return '```json\n{"verdict": "ok", "issues": []}\n```'
    return reply, by_id


@dataclass
class SchedRow:
    strategy: str
    modules: int
    llm_limit: int
    wall_s: float
    modules_per_min: float
    peak_agents: int                # 同时在调用模型的 Agent 最多几个
    utilization: float              # 并发名额的利用率 = 模型调用总时长 /（总用时 × 并发上限）
    verdict: str


class CountingLLM(FakeLLM):
    """在模型调用这一层数并发（引擎的 agent_started 事件发生在排队之前，不能用来数）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._lock = threading.Lock()
        self.inflight = 0
        self.peak = 0
        self.busy_s = 0.0

    def complete(self, *a, **kw):
        with self._lock:
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        t0 = time.perf_counter()
        try:
            return super().complete(*a, **kw)
        finally:
            with self._lock:
                self.inflight -= 1
                self.busy_s += time.perf_counter() - t0


def bench_scheduling(task: Task, *, sizes=(1, 2, 4, 8), limits=(1, 2, 4, 8), latency_s: float = 2.0,
                     toolchain: Toolchain | None = None, runs_root: Path | None = None) -> list[SchedRow]:
    rows = []
    tier = ModelTier("bench-fake", 1.0, 1.0)
    for n in sizes:
        for cfg in (SERIAL, PARALLEL):
            for limit in (limits if cfg is PARALLEL else (1,)):
                settings = Settings(models={"strong": tier, "fast": tier}, concurrency={"llm": limit, "verify": 4},
                                    loop={"max_attempts_per_module": 2})
                reply, _ = fake_team(task, n)
                llm = CountingLLM(reply, latency_s=latency_s)
                tmp = Path(tempfile.mkdtemp(dir=runs_root))
                t0 = time.perf_counter()
                try:
                    res = run_multi(task, replace(cfg, tools=False), llm=llm, settings=settings, runs_root=tmp,
                                    toolchain=toolchain)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                wall = time.perf_counter() - t0
                rows.append(SchedRow(cfg.strategy, n, limit, round(wall, 2), round(n / wall * 60, 2), llm.peak,
                                     round(llm.busy_s / (wall * limit), 2), res.verdict))
    return rows


@dataclass
class VerifyRow:
    verify_limit: int
    modules: int
    wall_s: float
    per_module_s: float


def bench_verify(task: Task, *, n: int = 8, limits=(1, 2, 4), toolchain: Toolchain | None = None) -> list[VerifyRow]:
    reply, by_id = fake_team(task, n)
    rows = []
    with tempfile.TemporaryDirectory() as d:
        dirs = []
        for mid in by_id:
            wd = Path(d) / mid
            files = {}
            from ..agents.protocol import parse_files
            code = reply([{"role": "system", "content": "你是一名资深嵌入式 C 工程师"}, {"role": "user", "content": f"[module {mid}]"}])
            test = reply([{"role": "system", "content": "你是嵌入式软件的测试工程师"}, {"role": "user", "content": f"[module {mid}]"}])
            files.update(parse_files(code, ("src",)).files)
            files.update(parse_files(test, ("tests",)).files)
            if mid != "m0":
                files.update(parse_files(reply([{"role": "system", "content": "你是一名资深嵌入式 C 工程师"},
                                                {"role": "user", "content": "[module m0]"}]), ("src",)).files)
            write_files(wd, files)
            dirs.append(wd)
        for limit in limits:
            t0 = time.perf_counter()
            with ThreadPoolExecutor(limit) as ex:
                list(ex.map(lambda w: verify(w, task, toolchain=toolchain), dirs))
            wall = time.perf_counter() - t0
            rows.append(VerifyRow(limit, len(dirs), round(wall, 2), round(wall / len(dirs), 2)))
    return rows


def to_markdown(sched: list[SchedRow], ver: list[VerifyRow], latency_s: float) -> str:
    lines = ["# FirmForge 性能测试", "",
             f"假模型每次调用固定延迟 {latency_s:g} 秒；模块之间互不依赖；审查、升级、经验库、工具都关闭，只比调度。", "",
             "## 调度吞吐", "",
             "| 调度 | 模块数 | LLM 并发上限 | 总用时 (s) | 模块 / 分钟 | 同时调用模型的 Agent 最多 | 并发名额利用率 | 结论 |",
             "|---|---|---|---|---|---|---|---|"]
    names = {"serial": "串行", "smart": "智能并行"}
    for r in sched:
        lines.append(f"| {names.get(r.strategy, r.strategy)} | {r.modules} | {r.llm_limit} | {r.wall_s} | "
                     f"{r.modules_per_min} | {r.peak_agents} | {r.utilization:.0%} | {r.verdict} |")
    if ver:
        lines += ["", "## 判定吞吐（真跑编译和测试）", "", "| 判定并发 | 模块数 | 总用时 (s) | 平均每个模块 (s) |", "|---|---|---|---|"]
        lines += [f"| {r.verify_limit} | {r.modules} | {r.wall_s} | {r.per_module_s} |" for r in ver]
    return "\n".join(lines) + "\n"

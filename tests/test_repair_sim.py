"""修正循环的故障注入演练（DECISIONS 第 123 条）：不调用模型、不编译、不上板，几秒跑几百种情况。

做法参照 MAS-FIRE（Jia 等，2026，多 Agent 系统故障注入）和基于状态机的性质测试（Hypothesis 的 rule-based stateful
testing）：用一个"模拟世界"代替编译器和板子——驱动代码、测试代码各自可能"编译不过 / 逻辑错 / 正确"；假 Agent 按随机种子
修好、改坏、乱回、不交文件；真实的分派代码（_diagnose、修正循环、工作流解释器）照常运行。每一步之后检查不变量：

  I1 一定会停：检查次数不超过上限；
  I2 编译报错只在测试文件里 → 下一个动手的是测试 Agent；报错在驱动里 → 编码 Agent；
  I3 诊断说"测试的错" → 下一个动手的是测试 Agent；说"代码的错" → 编码 Agent；
  I4 动手的 Agent 收到的修改要求里带着上一次的报错（反馈真的送到了）；
  I5 最终判"通过"时，最后一次检查真的是通过；
  I6 不抛异常；结论只能是 pass / fail / hardware / skipped；
  I7 判没过时，机会一定用完了；
  I8 最后一次检查之后不再叫 Agent（不白花钱）。

基线评测里"测试编译不过却一直交给编码 Agent"的错误（第 122 条修掉的）在这里几秒就会被 I2 抓住。
命令行可以跑更多种子：python tests/test_repair_sim.py --seeds 0-999
"""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401
from firmforge.embedded.design import Design, DesignModule
from firmforge.embedded.flow import EmbeddedFlow
from firmforge.grounding.rules import RuleSet
from firmforge.llm import FakeLLM
from firmforge.orchestrator.workflow import load_workflow
from test_embedded_design import profile
from test_multi_agent import SETTINGS

DRIVER, TEST = "imu.c", "test/test_imu.c"


class World:
    """被测代码和测试代码的真实状态，以及各个假 Agent 的脾气（都由种子决定）。"""

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        r = self.rng
        self.driver = r.choice(["ok", "compile", "logic", "logic"])
        self.test = r.choice(["ok", "ok", "compile", "wrong"])
        self.p_coder_fix, self.p_tester_fix = r.uniform(0.2, 0.9), r.uniform(0.2, 0.9)
        self.p_break = r.uniform(0.0, 0.2)              # Agent 改着改着又改坏
        self.p_diag_right = r.uniform(0.4, 1.0)
        self.p_garbage = r.uniform(0.0, 0.3)            # Agent 乱回（不交文件、JSON 坏掉）
        self.log: list[tuple] = []                       # ("check", verdict, layer, report) / ("agent", 角色, 收到的最后一条)
        self.checks = 0

    # ---- 假 Agent
    def __call__(self, messages):
        system, last = messages[0]["content"], messages[-1]["content"] or ""
        r = self.rng
        if "诊断工程师" in system:
            self.log.append(("diag",))
            if r.random() < self.p_garbage:
                return r.choice(["", "我觉得是代码的问题", '```json\n{"blame": "code", "evidence": "坏"掉"}\n```'])
            truth = "code" if self.driver != "ok" else "test"
            blame = truth if r.random() < self.p_diag_right else r.choice(["code", "test", "hardware", "rule"])
            return "```json\n" + json.dumps({"blame": blame, "rule": "", "evidence": "e", "instruction": f"修{blame}"},
                                            ensure_ascii=False) + "\n```"
        role = "coder" if "资深嵌入式 C 工程师" in system else "tester" if "测试工程师" in system else None
        if role is None:
            raise AssertionError("没想到的调用：" + system[:40])
        self.log.append(("agent", role, last))
        if r.random() < self.p_garbage:
            return r.choice(["好的，我改好了。", "```c\nint x;\n```"])          # 不按格式交文件
        if role == "coder":
            if self.driver != "ok" and r.random() < self.p_coder_fix:
                self.driver = "ok"
            elif r.random() < self.p_break:
                self.driver = r.choice(["compile", "logic"])
            return f"<<<FILE {DRIVER}\nint imu_read(void){{return {self.rng.randint(0, 9)};}}\n>>>FILE\n"
        if self.test != "ok" and r.random() < self.p_tester_fix:
            self.test = "ok"
        elif r.random() < self.p_break:
            self.test = r.choice(["compile", "wrong"])
        return f'<<<FILE {TEST}\n#include "unity.h"\nTEST_CASE("x", "[imu]") {{}}\n>>>FILE\n'

    # ---- 模拟的检查（代替编译和上板）
    def check(self, *a, **k):
        self.checks += 1
        errs = []
        if self.test == "compile":
            errs.append(f"main/{TEST}:12: error: 'FF_REG_IMU_X' undeclared (first use in this function)")
        if self.driver == "compile":
            errs.append(f"main/{DRIVER}:3: error: expected ';' before 'return'")
        if errs:
            res = ("fail", "build", "编译没过：\n" + "\n".join(errs))
        elif self.driver == "logic" or self.test == "wrong":
            res = ("fail", "board_test", "失败：读数不对（test_imu.c:20）Expected 1 Was 0\n共 2 个用例，通过 1，失败 1")
        else:
            res = ("pass", "board_test", "共 2 个用例，通过 2，失败 0")
        self.log.append(("check",) + res)
        return res


def run_world(seed: int, workflow: bool, max_attempts: int = 4) -> tuple[World, object]:
    w = World(seed)
    m = DesignModule("imu", "IMU", "hardware", (DRIVER, "include/imu.h"), provides=("int imu_read(void)",))
    with tempfile.TemporaryDirectory() as d, mock.patch.object(EmbeddedFlow, "_check", lambda self, *a, **k: w.check()):
        f = EmbeddedFlow(llm=FakeLLM(w), settings=SETTINGS, profile=profile(), spec="x", rules=RuleSet(), library=None,
                         verifier=None, runs_root=Path(d), rule_mode="none", idf=None, board=None, design=Design([m]),
                         locked={"include/imu.h": "int imu_read(void);\n"}, build_rules=False, max_attempts=max_attempts,
                         workflow=load_workflow("firmware_dev") if workflow else None)
        res = f.run()
    return w, res


def violations(w: World, res, max_attempts: int) -> list[str]:
    out = []
    mr = res.modules.get("imu")
    verdict = mr.verdict if mr is not None else res.error
    if verdict not in ("pass", "fail", "hardware", "skipped"):
        out.append(f"I6 结论不对：{verdict!r}（{res.error}）")
    checks = [e for e in w.log if e[0] == "check"]
    if len(checks) > max_attempts:
        out.append(f"I1 检查了 {len(checks)} 次，超过上限 {max_attempts}")
    if verdict == "pass" and (not checks or checks[-1][1] != "pass"):
        out.append("I5 判了通过，最后一次检查却没过")
    if verdict == "fail" and len(checks) != max_attempts:
        out.append(f"I7 判没过时只检查了 {len(checks)} 次，没用完 {max_attempts} 次机会")
    if checks and verdict in ("pass", "fail"):
        last = max(i for i, e in enumerate(w.log) if e[0] == "check")
        after = [e for e in w.log[last + 1:] if e[0] in ("agent", "diag")]
        if after:
            out.append(f"I8 最后一次检查之后还叫了 {len(after)} 次 Agent（白花钱）")
    log = w.log
    for i, e in enumerate(log):
        if e[0] != "check" or e[1] == "pass":
            continue
        _, _, layer, report = e
        rest = log[i + 1:]
        nxt = next((x for x in rest if x[0] in ("agent", "check")), None)
        if nxt is None or nxt[0] != "agent":
            continue                                             # 停下了（次数用完、硬件问题）
        who, msg = nxt[1], nxt[2]
        diag_seen = any(x[0] == "diag" for x in rest[:rest.index(nxt)])
        if layer == "build":
            in_test, in_drv = TEST in report, DRIVER in report.replace(TEST, "")
            want = "tester" if (in_test and not in_drv) else "coder"
            if who != want:
                out.append(f"I2 第 {sum(1 for x in log[:i + 1] if x[0] == 'check')} 次编译报错在"
                           f"{'测试' if want == 'tester' else '驱动'}里，下一个动手的却是 {who}")
        elif not diag_seen:
            out.append("I3 上板没过，没经过诊断就有人动手")
        if "error:" in report and "error:" not in msg and "上一条回复" not in msg:
            out.append(f"I4 {who} 收到的修改要求里没有上一次的编译报错")
    return out


def run_many(seeds, max_attempts: int = 4) -> dict[str, list[str]]:
    bad: dict[str, list[str]] = {}
    for workflow in (False, True):
        for s in seeds:
            try:
                w, res = run_world(s, workflow, max_attempts)
                v = violations(w, res, max_attempts)
            except Exception as exc:                             # noqa: BLE001  I6：不许抛异常
                v = [f"I6 抛了异常：{type(exc).__name__}: {exc}"]
            if v:
                bad[f"{'工作流' if workflow else '固定流程'} 种子 {s}"] = v
    return bad


class RepairLoopSimTest(unittest.TestCase):
    def test_invariants_hold_for_many_random_worlds(self):
        bad = run_many(range(120))
        self.assertEqual(bad, {}, "\n".join(f"{k}：{'；'.join(v)}" for k, v in list(bad.items())[:10]))

    def test_simulation_catches_the_old_routing_bug(self):
        """演练本身有效：把第 122 条修掉的错误装回去，I2 必须报出来。"""
        def old(self, m, mr, rules, layer, report, attempt):
            return {"blame": "code", "instruction": "", "evidence": layer, "rule": ""} if layer in ("build", "compliance") \
                else orig(self, m, mr, rules, layer, report, attempt)
        orig = EmbeddedFlow._diagnose
        with mock.patch.object(EmbeddedFlow, "_diagnose", old):
            bad = run_many(range(40))
        self.assertTrue(any("I2" in x for v in bad.values() for x in v), bad)


if __name__ == "__main__":
    if "--seeds" in sys.argv:
        a, b = sys.argv[sys.argv.index("--seeds") + 1].split("-")
        bad = run_many(range(int(a), int(b) + 1))
        for k, v in bad.items():
            print(k, "；".join(v))
        print(f"{'没发现问题' if not bad else f'{len(bad)} 种情况违反不变量'}（种子 {a}-{b}，固定流程和工作流各跑一遍）")
        sys.exit(1 if bad else 0)
    unittest.main()

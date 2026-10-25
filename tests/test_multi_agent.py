"""阶段 3：多 Agent 编排。规划校验、注册表、工作流模板、调度策略、执行引擎、修正循环、b5、评测与命令行。

全部用 FakeLLM（按角色分派预设回复），不联网。
"""

import contextlib
import io
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from firmforge import cli
from firmforge.agents import roles
from firmforge.agents.registry import AgentDef, Registry, RegistryError, default_registry
from firmforge.config import ModelTier, Settings
from firmforge.evaluation.configs import ConfigError, parse_configs
from firmforge.evaluation.metrics import read_csv
from firmforge.evaluation.runner import evaluate, full_budget
from firmforge.llm import FakeLLM
from firmforge.orchestrator.baseline import B5, RunInterrupted, run_baseline
from firmforge.orchestrator.dag import (Plan, PlanError, declared_functions, extract_json, parse_plan,
                                        public_functions, single_module_plan)
from firmforge.orchestrator.engine import B3, B4, FULL, MultiConfig, run_multi
from firmforge.orchestrator.scheduler import RoundRobin, Random, Serial, Smart, prioritize
from firmforge.orchestrator.workflow import WorkflowError, load_workflow, parse_workflow
from firmforge.runs import read_events
from firmforge.store import Store
from firmforge.tasks import discover_tasks
from test_baseline_eval import FAKE_HAL_I2C, FAKE_HAL_ONEWIRE, FAKE_HAL_SD, FAKE_HAL_SPI, block, hidden_markers

FAST = ModelTier("fake-fast", 2.0, 8.0)
STRONG = ModelTier("fake-strong", 9.0, 27.0)
SETTINGS = Settings(models={"fast": FAST, "strong": STRONG}, concurrency={"llm": 4, "verify": 4},
                    loop={"max_attempts_per_module": 6}, scheduler={"alpha": 0.3, "beta": 0.2, "gamma": 0.5, "seed": 42})
TASKS = {t.id: t for t in discover_tasks(helpers.TASKS)}


# ---------------------------------------------------------------- 假 Agent 团队

def role_of(messages):
    sysmsg = messages[0]["content"]
    for marker, role in (("你是嵌入式软件架构师", "planner"), ("你是嵌入式软件的测试工程师", "tester"),
                         ("你是嵌入式 C 代码审查专家", "reviewer"), ("你是嵌入式软件的测试与调试专家", "diagnoser"),
                         ("你是一名资深嵌入式 C 工程师", "coder")):
        if sysmsg.startswith(marker):
            return role
    raise AssertionError("认不出角色：" + sysmsg[:40])


def module_of(messages):
    m = re.search(r"## (?:你负责的模块|你要测试的模块|出问题的模块)：(\w+)", messages[1]["content"])
    if m:
        return m.group(1)
    m = re.search(r'## 模块\n\n```json\n\{\n  "id": "(\w+)"', messages[1]["content"])
    return m.group(1) if m else None


class Team:
    """按角色（和模块）分派回复。handler(module, messages, n) -> 文本，n 是该角色在该模块上的第几次调用。"""

    def __init__(self, **handlers):
        self.handlers = handlers
        self.log = []
        self._lock = threading.Lock()
        self._count = {}

    def __call__(self, messages):
        role, mod = role_of(messages), module_of(messages)
        with self._lock:
            k = (role, mod)
            self._count[k] = self._count.get(k, 0) + 1
            n = self._count[k]
            self.log.append((role, mod, messages))
        h = self.handlers.get(role)
        if h is None:
            raise AssertionError(f"没有为 {role} 准备回复")
        return h(mod, messages, n)

    def calls(self, role, mod=None):
        return [m for r, md, m in self.log if r == role and (mod is None or md == mod)]


def plan_json(modules):
    return "```json\n" + json.dumps({"modules": modules}, ensure_ascii=False) + "\n```"


def mod(mid, files, *, provides=(), interface=None, deps=(), task_type="data_structure", acceptance=("要点",)):
    return {"id": mid, "title": mid, "task_type": task_type, "files": list(files), "provides": list(provides),
            "interface": interface or {}, "depends_on": list(deps), "acceptance": list(acceptance)}


def ref(task, name):
    return (task.reference_dir / name).read_text(encoding="utf-8")


def unity_test(header, body="TEST_ASSERT_TRUE(1);", extra=""):
    return (f'#include "unity.h"\n#include "{header}"\n{extra}\n'
            f"static void test_it(void) {{ {body} }}\n"
            "int main(void) { UNITY_BEGIN(); RUN_TEST(test_it); return UNITY_END(); }\n")


def diag(blame, summary="根因", instructions="改这里"):
    return "```json\n" + json.dumps({"blame": blame, "summary": summary, "instructions": instructions,
                                     "confidence": 0.7}, ensure_ascii=False) + "\n```"


OK_REVIEW = '```json\n{"verdict": "ok", "issues": []}\n```'

# ringbuf 拆成三个模块：a（公共函数）与 b（工具函数）互不依赖，c 依赖两者
RB = TASKS["ringbuf"]
RB_PLAN = [
    mod("a", ["src/ringbuf.c"], provides=public_functions(RB)),
    mod("b", ["src/util.c", "src/util.h"], interface={"src/util.h": ["int util_add(int a, int b);"]}),
    mod("c", ["src/glue.c", "src/glue.h"], deps=["a", "b"],
        interface={"src/glue.h": ["size_t glue_total(const ringbuf_t *rb, int extra);"]}),
]
UTIL_H = "#ifndef UTIL_H\n#define UTIL_H\nint util_add(int a, int b);\n#endif\n"
UTIL_C = '#include "util.h"\nint util_add(int a, int b) { return a + b; }\n'
GLUE_H = '#ifndef GLUE_H\n#define GLUE_H\n#include "ringbuf.h"\nsize_t glue_total(const ringbuf_t *rb, int extra);\n#endif\n'
GLUE_C = ('#include "glue.h"\n#include "util.h"\n'
          "size_t glue_total(const ringbuf_t *rb, int extra) { return (size_t)util_add((int)ringbuf_count(rb), extra); }\n")
RB_CODE = {"a": block("src/ringbuf.c", ref(RB, "ringbuf.c")),
           "b": block("src/util.h", UTIL_H) + block("src/util.c", UTIL_C),
           "c": block("src/glue.h", GLUE_H) + block("src/glue.c", GLUE_C)}
RB_TESTS = {
    "a": unity_test("ringbuf.h", "uint8_t s[4]; ringbuf_t rb; TEST_ASSERT_TRUE(ringbuf_init(&rb, s, 4));"
                    " TEST_ASSERT_TRUE(ringbuf_put(&rb, 7)); TEST_ASSERT_EQUAL_UINT32(1, ringbuf_count(&rb));"),
    "b": unity_test("util.h", "TEST_ASSERT_EQUAL_INT(5, util_add(2, 3));"),
    "c": unity_test("glue.h", "uint8_t s[4]; ringbuf_t rb; ringbuf_init(&rb, s, 4); ringbuf_put(&rb, 1);"
                    " TEST_ASSERT_EQUAL_UINT32(3, glue_total(&rb, 2));"),
}


def rb_team(**over):
    handlers = {
        "planner": lambda m, msgs, n: plan_json(RB_PLAN),
        "coder": lambda m, msgs, n: RB_CODE[m],
        "tester": lambda m, msgs, n: block(f"tests/test_{m}.c", RB_TESTS[m]),
        "diagnoser": lambda m, msgs, n: diag("code"),
        "reviewer": lambda m, msgs, n: OK_REVIEW,
    }
    handlers.update(over)
    return Team(**handlers)


def single_plan_reply(task):
    """把整个任务作为一个模块：文件 = 参考实现的文件名。"""
    files = [f"src/{f.name}" for f in sorted(task.reference_dir.glob("*.[ch]"))]
    return plan_json([mod(task.id, files, provides=public_functions(task), task_type=task.category)])


def ref_code_reply(task):
    return "".join(block(f"src/{f.name}", f.read_text(encoding="utf-8")) for f in sorted(task.reference_dir.glob("*.[ch]")))


def smoke_tester_reply(task, module_id):
    header = sorted(h.name for h in task.include_dir.glob("*.h") if not h.name.startswith("hal_"))[0]
    fake = ""
    if (task.include_dir / "hal_i2c.h").is_file():
        fake = FAKE_HAL_I2C
    if (task.include_dir / "hal_spi.h").is_file():
        fake = FAKE_HAL_SPI
    if (task.include_dir / "hal_onewire.h").is_file():
        fake = FAKE_HAL_ONEWIRE
    if (task.include_dir / "hal_sd_spi.h").is_file():
        fake = FAKE_HAL_SD
    return block(f"tests/test_{module_id}.c", unity_test(header, extra=fake))


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def run_m(self, cfg, team, task=RB, store=None, **kw):
        llm = FakeLLM(team)
        events = []
        res = run_multi(task, cfg, llm=llm, settings=SETTINGS, runs_root=self.tmp / "runs", store=store,
                        subscribers=(events.append,), **kw)
        return res, llm, events


# ---------------------------------------------------------------- 规划结果校验

class DagTest(unittest.TestCase):
    def test_valid_plan(self):
        plan = parse_plan({"modules": RB_PLAN}, RB)
        self.assertEqual([m.id for m in plan.modules], ["a", "b", "c"])
        self.assertEqual(plan.topo_order()[-1], "c")
        self.assertEqual(plan.transitive_deps("c"), ["a", "b"])
        self.assertEqual(plan.critical_path(), {"a": 2, "b": 2, "c": 1})
        self.assertEqual(plan.by_id["b"].interface, {"src/util.h": ("int util_add(int a, int b);",)})
        self.assertEqual(prioritize(["c", "b", "a"], plan), ["a", "b", "c"])

    def problems(self, modules, task=RB):
        with self.assertRaises(PlanError) as cm:
            parse_plan({"modules": modules}, task)
        return "\n".join(cm.exception.problems)

    def test_rejections(self):
        pub = public_functions(RB)
        self.assertIn("循环依赖", self.problems([mod("a", ["src/a.c"], provides=pub, deps=["b"]),
                                               mod("b", ["src/b.c"], deps=["a"])]))
        self.assertIn("id a 重复", self.problems([mod("a", ["src/a.c"], provides=pub), mod("a", ["src/b.c"])]))
        self.assertIn("同时属于", self.problems([mod("a", ["src/a.c"], provides=pub), mod("b", ["src/a.c"])]))
        self.assertIn("ringbuf_clear 没有模块负责", self.problems([mod("a", ["src/a.c"], provides=pub[:-1])]))
        self.assertIn("被多个模块负责", self.problems([mod("a", ["src/a.c"], provides=pub),
                                                     mod("b", ["src/b.c"], provides=pub[:1])]))
        self.assertIn("不是公共头文件里的函数", self.problems([mod("a", ["src/a.c"], provides=pub + ["helper"])]))
        self.assertIn("只允许写在 src/", self.problems([mod("a", ["include/a.c"], provides=pub)]))
        self.assertIn("不允许包含 ..", self.problems([mod("a", ["src/../a.c"], provides=pub)]))
        self.assertIn("必须列在它自己的 files 里", self.problems(
            [mod("a", ["src/a.c"], provides=pub, interface={"src/a.h": ["void f(void);"]})]))
        self.assertIn("依赖的 z 不存在", self.problems([mod("a", ["src/a.c"], provides=pub, deps=["z"])]))
        self.assertIn("超过上限", self.problems([mod("a", ["src/a.c"], provides=pub)]
                                               + [mod(f"m{i}", [f"src/m{i}.c"]) for i in range(5)]))
        self.assertIn("task_type", self.problems([mod("a", ["src/a.c"], provides=pub, task_type="gui")]))
        self.assertIn("id 'A-1' 不合法", self.problems([mod("A-1", ["src/a.c"], provides=pub)]))
        self.assertIn("acceptance 不能为空", self.problems([mod("a", ["src/a.c"], provides=pub, acceptance=())]))

    def test_hal_functions_not_required(self):
        t = TASKS["mpu6050_drv"]
        self.assertEqual(public_functions(t), ["mpu6050_init", "mpu6050_read"])
        parse_plan({"modules": [mod("drv", ["src/mpu6050.c"], provides=public_functions(t), task_type="driver")]}, t)

    def test_declared_functions_ignores_structs_and_macros(self):
        text = ("#define MAX(a, b) ((a) > (b) ? (a) : (b))\n"
                "typedef struct { uint8_t a; void (*cb)(int x); } s_t;\n"
                "typedef void (*handler_t)(int);\n/* void not_this(void); */\n"
                "bool  first(s_t *s,\n            int x);\nvoid second(void);\n")
        self.assertEqual(declared_functions(text), ["first", "second"])

    def test_extract_json(self):
        self.assertEqual(extract_json('说明\n```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('前面 {"a": 2} 后面'), {"a": 2})
        with self.assertRaises(PlanError):
            extract_json("没有 JSON")
        with self.assertRaises(PlanError):
            extract_json("```json\n{坏的\n```")

    def test_single_module_fallback(self):
        p = single_module_plan(TASKS["modbus_slave"])
        self.assertTrue(p.fallback)
        self.assertEqual(p.modules[0].provides, ("modbus_slave_init", "modbus_slave_handle"))
        self.assertTrue(p.to_dict()["fallback"])


# ---------------------------------------------------------------- 注册表与工作流模板

class RegistryTest(unittest.TestCase):
    def test_default_agents(self):
        r = default_registry()
        self.assertEqual(sorted(r.names()), ["coder-fast", "coder-strong", "diagnoser", "planner", "reviewer", "tester"])
        self.assertEqual([a.name for a in r.find("coder", "driver")], ["coder-fast", "coder-strong"])
        tiers = {n: r.get(n).model_tier for n in r.names()}        # DECISIONS 第 31、37 条
        self.assertEqual(tiers, {"coder-fast": "fast", "coder-strong": "strong", "diagnoser": "strong",
                                 "planner": "strong", "reviewer": "fast", "tester": "strong"})
        for n in r.names():
            self.assertTrue((Path(helpers.ROOT) / r.get(n).prompt).is_file(), n)

    def test_versions_and_unregister(self):
        r = Registry()
        r.register(AgentDef("x", "coder", "1.2.0", "fast", "prompts/coder_module.md", task_types=("driver",)))
        r.register(AgentDef("x", "coder", "1.10.0", "strong", "prompts/coder_module.md", task_types=("driver",)))
        self.assertEqual(r.get("x").version, "1.10.0")
        self.assertEqual(r.get("x", "1.2.0").model_tier, "fast")
        self.assertEqual(r.find("coder", "protocol"), [])
        r.unregister("x")
        with self.assertRaises(RegistryError):
            r.get("x")

    def test_bad_definition(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "bad.toml").write_text('name = "x"\nrole = "boss"\nversion = "1"\nmodel_tier = "fast"\n'
                                              'prompt = "p.md"\n', encoding="utf-8")
            with self.assertRaises(RegistryError):
                Registry().load_dir(d)


class WorkflowTest(unittest.TestCase):
    def test_default_template(self):
        w = load_workflow()
        self.assertEqual([s.kind for s in w.main], ["agent", "human", "foreach", "verify"])
        sub = w.subflows["module_impl"]
        self.assertEqual([s.id for s in w.top_level(sub)], ["write", "check", "repair", "review_gate"])
        self.assertEqual(w.resolve_max(w.by_id(sub)["repair"], {"max_attempts_per_module": 4}), 4)

    def test_validation(self):
        base = {"main": [{"id": "p", "kind": "agent", "role": "planner"}]}
        parse_workflow(base)
        for bad in ({"main": [{"id": "p", "kind": "magic"}]},
                    {"main": [{"id": "p", "kind": "agent", "role": "planner", "when": "__import__('os')"}]},
                    {"main": [{"id": "p", "kind": "parallel", "branches": ["nope"]}]},
                    {"main": [{"id": "p", "kind": "foreach", "subflow": "missing"}]},
                    {"main": [{"id": "p", "kind": "loop", "body": "anything"}]}):
            with self.assertRaises(WorkflowError):
                parse_workflow(bad)


# ---------------------------------------------------------------- 调度策略

def agents():
    return [AgentDef("coder-fast", "coder", "1", "fast", "p.md"), AgentDef("coder-strong", "coder", "1", "strong", "p.md")]


class SchedulerTest(unittest.TestCase):
    def test_serial_round_robin_random(self):
        cands = agents()
        self.assertEqual(Serial({"coder": "coder-strong"}).choose("m", cands, {}, {})[0].name, "coder-strong")
        self.assertEqual(Serial().choose("m", cands, {}, {})[0].name, "coder-fast")
        self.assertFalse(Serial().parallel)
        rr = RoundRobin()
        self.assertEqual([rr.choose("m", cands, {}, {})[0].name for _ in range(3)],
                         ["coder-fast", "coder-strong", "coder-fast"])
        a = [Random(7).choose("m", cands, {}, {})[0].name for _ in range(1)]
        b = [Random(7).choose("m", cands, {}, {})[0].name for _ in range(1)]
        self.assertEqual(a, b)
        only = Smart().choose("m", cands[:1], {}, {})
        self.assertEqual(only[1]["reason"], "该角色只有这一个 Agent")

    def test_smart_formula(self):
        price = {"coder-fast": 10.0, "coder-strong": 36.0}
        s = Smart(alpha=0.3, beta=0.2, gamma=0.5, concurrency=4, list_price=lambda a: price[a.name])
        # 没有历史：成功率都是 0.5，按标价比较成本，便宜的胜出
        agent, d = s.choose("m", agents(), {}, {})
        self.assertEqual(agent.name, "coder-fast")
        self.assertIn("标价", d["cost_basis"])
        self.assertAlmostEqual(d["candidates"]["coder-fast"]["score"], round(0.5 - 0.3 * 10 / 36, 4), places=4)
        # 有历史：fast 3 次成功 0 次，strong 3 次成功 3 次
        stats = {"coder-fast": {"attempts": 3, "successes": 0, "total_cost": 0.3},
                 "coder-strong": {"attempts": 3, "successes": 3, "total_cost": 1.2}}
        agent, d = s.choose("m", agents(), stats, {"coder-strong": 2})
        self.assertEqual(agent.name, "coder-strong")
        c = d["candidates"]["coder-strong"]
        explore = (__import__("math").log(7) / 4) ** 0.5
        self.assertAlmostEqual(c["score"], round(4 / 5 - 0.3 * 1.0 - 0.2 * 0.5 + 0.5 * explore, 4), places=4)
        self.assertEqual(d["cost_basis"], "实测平均成本")
        self.assertEqual(d["N"], 6)
        self.assertIn("得分最高 coder-strong", d["reason"])


# ---------------------------------------------------------------- 执行引擎

@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class EngineTest(_Tmp):
    def test_full_run_three_modules(self):
        store = Store(self.tmp / "db.sqlite")
        res, llm, events = self.run_m(FULL, rb_team(), store=store)
        self.assertIn(res.verdict, ("pass", "incomplete"))         # 本机缺交叉工具链时为 incomplete
        self.assertEqual([m.id for m in res.plan.modules], ["a", "b", "c"])
        dag = json.loads((res.run_dir / "dag.json").read_text(encoding="utf-8"))
        self.assertEqual(dag["modules"][1]["interface"], {"src/util.h": ["int util_add(int a, int b);"]})
        for f in ("src/ringbuf.c", "src/util.c", "src/glue.c", "tests/test_a.c", "tests/test_c.c", "report.md"):
            self.assertTrue((res.final_dir / f).is_file(), f)
        self.assertIn("## 模块", (res.final_dir / "report.md").read_text(encoding="utf-8"))
        # c 的工作目录里有依赖模块的实现
        self.assertTrue((res.run_dir / "nodes" / "c" / "attempt_1" / "src" / "util.c").is_file())
        self.assertFalse((res.run_dir / "nodes" / "a" / "attempt_1" / "src" / "util.c").exists())
        types = [e["type"] for e in events]
        self.assertEqual(types[0], "run_started")
        self.assertEqual(types[-1], "run_finished")
        self.assertIn("plan_ready", types)
        self.assertNotIn("human_wait", types)                 # 没开 human_review
        assigned = [e for e in events if e["type"] == "node_assigned" and e["data"]["role"] == "coder"]
        self.assertEqual(len(assigned), 3)
        self.assertIn("candidates", assigned[0]["data"]["scores"])     # smart 的得分明细
        self.assertEqual(read_events(res.run_dir), events)
        reviews = [e for e in events if e["type"] == "agent_finished" and e["data"].get("role") == "reviewer"]
        self.assertEqual(len(reviews), 3)
        integ = [e for e in events if e["type"] == "verify_finished" and e["data"].get("scope") == "all"]
        self.assertEqual(len(integ), 1)
        rows = store.query("SELECT agent, task_type, attempts, successes FROM agent_stats")
        self.assertEqual(sum(r["attempts"] for r in rows if r["agent"].startswith("coder")), 3)
        run = json.loads((res.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["modules"]["c"]["status"], "done")
        self.assertGreater(res.tokens, 0)
        store.close()

    def test_dependencies_and_parallelism(self):
        def slow_coder(m, msgs, n):
            time.sleep(0.4)
            return RB_CODE[m]
        res, _, events = self.run_m(B4, rb_team(coder=slow_coder))
        self.assertIn(res.verdict, ("pass", "incomplete"))

        def ts(typ, node, role=None):
            return [e["ts"] for e in events if e["type"] == typ and e["node_id"] == node
                    and (role is None or e["data"].get("role") == role)]
        # a、b 没有依赖关系：两者的编码同时进行
        a_start, b_start = ts("agent_started", "a", "coder")[0], ts("agent_started", "b", "coder")[0]
        a_end, b_end = ts("agent_finished", "a", "coder")[0], ts("agent_finished", "b", "coder")[0]
        self.assertLess(max(a_start, b_start), min(a_end, b_end))
        # 同一模块里编码和写测试同时进行
        self.assertLess(ts("agent_started", "a", "tester")[0], a_end)
        # c 要等 a、b 都完成才开始
        self.assertGreaterEqual(ts("node_ready", "c")[0], max(ts("node_done", "a")[0], ts("node_done", "b")[0]))
        # round_robin：编码 Agent 轮流
        coders = [e["data"]["agent"] for e in events if e["type"] == "node_assigned" and e["data"]["role"] == "coder"]
        self.assertEqual(sorted(coders[:2]), ["coder-fast", "coder-strong"])

    def test_serial_runs_one_thing_at_a_time(self):
        res, _, events = self.run_m(B3, rb_team())
        coders = {e["data"]["agent"] for e in events if e["type"] == "node_assigned" and e["data"]["role"] == "coder"}
        self.assertEqual(coders, {"coder-strong"})                 # B3 固定用 coder-strong
        in_flight, peak = 0, 0
        for e in events:
            if e["type"] == "agent_started":
                in_flight += 1
                peak = max(peak, in_flight)
            elif e["type"] == "agent_finished":
                in_flight -= 1
        self.assertEqual(peak, 1)
        self.assertFalse(any(e["data"].get("role") == "reviewer" for e in events if e["type"] == "agent_started"))

    def test_planner_retry_then_fallback(self):
        bad = plan_json([mod("a", ["src/a.c"])])                   # 公共函数没人负责
        team = rb_team(planner=lambda m, msgs, n: bad if n == 1 else plan_json(RB_PLAN))
        res, llm, events = self.run_m(B4, team)
        self.assertEqual(len(team.calls("planner")), 2)
        self.assertIn("没有通过检查", team.calls("planner")[1][-1]["content"])
        self.assertFalse(res.plan.fallback)

        team = rb_team(planner=lambda m, msgs, n: "我不会输出 JSON",
                       coder=lambda m, msgs, n: block("src/ringbuf.c", ref(RB, "ringbuf.c")),
                       tester=lambda m, msgs, n: block(f"tests/test_{m}.c", RB_TESTS["a"]))
        res, _, events = self.run_m(B4, team)
        self.assertEqual(len(team.calls("planner")), 3)            # 1 次 + 重做 2 次
        self.assertTrue(res.plan.fallback)
        self.assertEqual([m.id for m in res.plan.modules], ["ringbuf"])
        ready = next(e for e in events if e["type"] == "plan_ready")
        self.assertTrue(ready["data"]["fallback"])
        self.assertIn(res.verdict, ("pass", "incomplete"))

    def test_tester_never_sees_implementation(self):
        """验收第 2 条：测试 Agent 收到的消息里没有任何 src/*.c 的内容，修正轮也一样。"""
        wrong = unity_test("util.h", "TEST_ASSERT_EQUAL_INT(6, util_add(2, 3));")      # 期望值写错
        team = rb_team(tester=lambda m, msgs, n: block(f"tests/test_{m}.c", wrong if (m == "b" and n == 1)
                                                       else RB_TESTS[m]),
                       diagnoser=lambda m, msgs, n: diag("test", "测试期望值错了",
                                                         "util_add(2, 3) 按需求应为 5；return a + b;"))
        res, llm, events = self.run_m(FULL, team)
        self.assertIn(res.verdict, ("pass", "incomplete"))
        d = next(e for e in events if e["type"] == "diagnosis")
        self.assertEqual((d["node_id"], d["data"]["blame"], d["data"]["route"]), ("b", "test", "tester"))
        tester_calls = team.calls("tester")
        self.assertEqual(len(team.calls("tester", "b")), 2)
        code_lines = [ln.strip() for f in RB.reference_dir.glob("*.c") for ln in f.read_text(encoding="utf-8").splitlines()
                      if len(ln.strip()) > 20 and ln.startswith("    ")]
        code_lines += ["return a + b;", "util_add((int)ringbuf_count(rb), extra)"]
        for msgs in tester_calls:
            text = "\n".join(m["content"] for m in msgs)
            for ln in code_lines:
                self.assertNotIn(ln, text)
        repair = team.calls("tester", "b")[1][-1]["content"]
        self.assertIn("按需求应为 5", repair)
        self.assertIn(roles.CODE_REMOVED, repair)          # 诊断指令里引用的实现代码被删去了
        self.assertIn("TEST_ASSERT_EQUAL_INT(6", repair)   # 自己的测试代码照常给出

    def test_scrub_for_tester(self):
        report_text = ("src/util.c:2:5: error: 'x' undeclared\n    2 | int util_add(int a, int b) { return x; }\n"
                       "      |                                     ^\n测试失败")
        out = roles.scrub_for_tester(report_text, {"src/util.c": UTIL_C}, keep="")
        self.assertNotIn("return x", out)
        self.assertIn("测试失败", out)
        self.assertIn("src/util.c:2:5", out)

    def test_code_blame_goes_to_coder_and_rollback(self):
        broken_syntax = block("src/util.c", "int util_add(int a, int b) { return a + b }\n")
        wrong_logic = block("src/util.h", UTIL_H) + block("src/util.c", '#include "util.h"\n'
                                                           "int util_add(int a, int b) { return a - b; }\n")

        def coder(m, msgs, n):
            if m != "b":
                return RB_CODE[m]
            return {1: wrong_logic, 2: broken_syntax}.get(n, RB_CODE["b"])
        team = rb_team(coder=coder)
        res, llm, events = self.run_m(B4, team)
        st = res.modules["b"]
        self.assertEqual(len(st.attempts), 3)
        self.assertEqual(st.attempts[0].report.failed_layer, "unit_tests")
        self.assertEqual(st.attempts[1].report.failed_layer, "host_compile")
        self.assertTrue(st.attempts[2].passed)
        rb = [e for e in events if e["type"] == "rollback" and e["node_id"] == "b"]
        self.assertEqual(len(rb), 1)
        self.assertEqual((rb[0]["data"]["from_attempt"], rb[0]["data"]["to_attempt"]), (2, 1))
        # 第 3 次从第 1 次（得分更高）开始：给编码 Agent 看的是第 1 次的代码和报告
        third = team.calls("coder", "b")[2][-1]["content"]
        self.assertIn("return a - b;", third)
        self.assertNotIn("return a + b }", third)
        self.assertEqual([e["data"]["route"] for e in events if e["type"] == "diagnosis"], ["coder", "coder"])
        # 诊断结果写进被诊断的那次尝试的目录
        self.assertTrue((st.attempts[0].dir / "diagnosis.json").is_file())

    def test_interface_and_spec_blame_noted(self):
        wrong_logic = block("src/util.h", UTIL_H) + block("src/util.c", '#include "util.h"\n'
                                                           "int util_add(int a, int b) { return a - b; }\n")
        team = rb_team(coder=lambda m, msgs, n: wrong_logic if (m == "b" and n == 1) else RB_CODE[m],
                       diagnoser=lambda m, msgs, n: diag("interface", "接口原型不够明确"))
        res, _, events = self.run_m(B4, team)
        self.assertEqual(next(e for e in events if e["type"] == "diagnosis")["data"]["route"], "coder")
        self.assertIn("接口问题", res.modules["b"].notes[0])
        self.assertIn("接口问题", (res.final_dir / "report.md").read_text(encoding="utf-8"))

    def test_attempts_exhausted_and_dependents_skipped(self):
        bad = block("src/util.h", UTIL_H) + block("src/util.c", "int util_add(int a, int b) { return a + b }\n")
        team = rb_team(coder=lambda m, msgs, n: bad if m == "b" else RB_CODE[m])
        res, _, events = self.run_m(B4, team)
        self.assertEqual(len(res.modules["b"].attempts), 6)       # loop.max_attempts_per_module
        self.assertEqual(res.modules["b"].status, "failed")
        self.assertEqual(res.modules["c"].status, "skipped")
        self.assertEqual(res.verdict, "fail")
        skipped = next(e for e in events if e["type"] == "node_failed" and e["node_id"] == "c")
        self.assertEqual(skipped["data"]["verdict"], "skipped")
        self.assertTrue((res.final_dir / "src" / "util.c").is_file())       # 没通过的模块也交付最好的一版
        self.assertFalse(any(e["data"].get("scope") == "all" for e in events if e["type"] == "verify_started"))

    def test_review_fix_once_and_rollback(self):
        needs_fix = ('```json\n{"verdict": "needs_fix", "issues": [{"severity": "high", "file": "src/util.c", '
                     '"line": 1, "rule": "整数溢出", "message": "a + b 可能溢出"}]}\n```')
        broken = block("src/util.c", "int util_add(int a, int b) { return a + b }\n")
        team = rb_team(reviewer=lambda m, msgs, n: needs_fix if m == "b" else OK_REVIEW,
                       coder=lambda m, msgs, n: broken if (m == "b" and n == 2) else RB_CODE[m])
        res, _, events = self.run_m(FULL, team)
        st = res.modules["b"]
        self.assertEqual(len(team.calls("reviewer", "b")), 1)
        self.assertEqual(len(team.calls("coder", "b")), 2)        # 只多 1 次编码轮
        self.assertIn("整数溢出", team.calls("coder", "b")[1][-1]["content"])
        self.assertEqual(st.status, "done")                         # 审查不能阻止通过
        self.assertEqual(st.delivered().n, 1)
        self.assertTrue(any(e["type"] == "rollback" and e["node_id"] == "b" for e in events))
        self.assertIn(res.verdict, ("pass", "incomplete"))
        self.assertIn("可能溢出", (res.final_dir / "report.md").read_text(encoding="utf-8"))
        self.assertTrue((res.run_dir / "nodes" / "b" / "review.json").is_file())

    def test_human_review(self):
        seen = []

        def human(plan):
            seen.append([m.id for m in plan.modules])
            return (len(seen) > 1, "请把 b 并进 a")
        team = rb_team()
        res, _, events = self.run_m(B4, team, human_review=True, human=human)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(team.calls("planner")), 2)
        self.assertIn("请把 b 并进 a", team.calls("planner")[1][-1]["content"])
        types = [e["type"] for e in events]
        self.assertEqual(types.count("human_wait"), 2)
        self.assertLess(types.index("human_done"), types.index("node_ready"))

        res, _, _ = self.run_m(B4, rb_team(), human_review=True, human=lambda p: (False, "不行"))
        self.assertEqual(res.verdict, "rejected")

    def test_llm_error_and_interrupt(self):
        events = []                                      # 预设回复是空的：规划 Agent 调用就失败（LLMError）
        res = run_multi(RB, B4, llm=FakeLLM([]), settings=SETTINGS, runs_root=self.tmp / "runs",
                        subscribers=(events.append,))
        self.assertEqual(res.verdict, "error")
        self.assertIn("用完", res.error)
        self.assertEqual(events[-1]["type"], "run_finished")

        def interrupt(m, msgs, n):
            raise KeyboardInterrupt
        with self.assertRaises(RunInterrupted) as cm:
            self.run_m(B4, rb_team(tester=interrupt))
        run = json.loads((cm.exception.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["verdict"], "interrupted")

    def test_diagnoser_unparsable_falls_back_to_code(self):
        wrong_logic = block("src/util.h", UTIL_H) + block("src/util.c", '#include "util.h"\n'
                                                           "int util_add(int a, int b) { return a - b; }\n")
        team = rb_team(coder=lambda m, msgs, n: wrong_logic if (m == "b" and n == 1) else RB_CODE[m],
                       diagnoser=lambda m, msgs, n: "看不出来")
        res, _, events = self.run_m(B4, team)
        self.assertEqual(len(team.calls("diagnoser", "b")), 2)    # 带着提示重试 1 次
        self.assertEqual(next(e for e in events if e["type"] == "diagnosis")["data"]["blame"], "code")
        self.assertTrue(res.modules["b"].passed)

    def test_coder_files_limited_to_module(self):
        team = rb_team(coder=lambda m, msgs, n: RB_CODE[m] + (block("src/glue.c", "/* 抢别人的文件 */") if m == "b" else ""))
        res, _, events = self.run_m(B4, team)
        fin = next(e for e in events if e["type"] == "agent_finished" and e["node_id"] == "b"
                   and e["data"].get("role") == "coder")
        self.assertEqual(fin["data"]["rejected"], ["src/glue.c"])
        self.assertIn(res.verdict, ("pass", "incomplete"))


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class ModbusDagTest(_Tmp):
    def test_modbus_three_module_chain_passes_hidden_tests(self):
        """验收第 1 条的离线版：Modbus 拆成 crc → frame → slave 三个模块，交付物用隐藏测试判定。"""
        from firmforge.evaluation.runner import judge
        t = TASKS["modbus_slave"]
        plan = [mod("crc", ["src/modbus_crc.c", "src/modbus_crc.h"], task_type="protocol",
                    interface={"src/modbus_crc.h": ["uint16_t modbus_crc16(const uint8_t *data, size_t len);"]}),
                mod("frame", ["src/modbus_frame.c", "src/modbus_frame.h"], deps=["crc"], task_type="protocol",
                    interface={"src/modbus_frame.h": ["bool modbus_frame_crc_ok(const uint8_t *frame, size_t len);"]}),
                mod("slave", ["src/modbus_slave.c"], deps=["frame"], provides=public_functions(t), task_type="protocol")]
        code = {m["id"]: "".join(block(f, ref(t, Path(f).name)) for f in m["files"]) for m in plan}
        headers = {"crc": "modbus_crc.h", "frame": "modbus_frame.h", "slave": "modbus_slave.h"}
        team = Team(planner=lambda m, msgs, n: plan_json(plan), coder=lambda m, msgs, n: code[m],
                    tester=lambda m, msgs, n: block(f"tests/test_{m}.c", unity_test(headers[m])),
                    reviewer=lambda m, msgs, n: OK_REVIEW, diagnoser=lambda m, msgs, n: diag("code"))
        res, _, events = self.run_m(FULL, team, task=t)
        self.assertEqual(len(res.plan.modules), 3)
        order = [e["node_id"] for e in events if e["type"] == "node_done"]
        self.assertEqual(order, ["crc", "frame", "slave"])
        # 编码 Agent 看到依赖模块的头文件和完整规划（DECISIONS 第 33 条）
        slave_ctx = team.calls("coder", "slave")[0][1]["content"]
        self.assertIn("bool   modbus_frame_crc_ok", slave_ctx)
        self.assertIn('"id": "crc"', slave_ctx)
        report = judge(res.final_dir, t)
        expected = "pass" if helpers.HAS_CROSS and helpers.HAS_CPPCHECK else "incomplete"
        self.assertEqual(report.verdict, expected, report.failed_layer)


@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class HiddenIsolationStage3Test(_Tmp):
    def test_nothing_hidden_reaches_any_agent(self):
        """所有角色的消息、运行目录里的所有文件，都不含隐藏测试的内容；测试 Agent 也看不到参考实现。"""
        for task in TASKS.values():
            with self.subTest(task=task.id):
                team = Team(planner=lambda m, msgs, n, t=task: single_plan_reply(t),
                            coder=lambda m, msgs, n, t=task: ref_code_reply(t),
                            tester=lambda m, msgs, n, t=task: smoke_tester_reply(t, m),
                            reviewer=lambda m, msgs, n: OK_REVIEW, diagnoser=lambda m, msgs, n: diag("code"))
                res, llm, _ = self.run_m(FULL, team, task=task)
                sent = llm.all_text_sent()
                files = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                                  for p in res.run_dir.rglob("*") if p.is_file())
                for mark in hidden_markers(task):
                    self.assertNotIn(mark, sent)
                    self.assertNotIn(mark, files)
                tester_text = "\n".join(m["content"] for msgs in team.calls("tester") for m in msgs)
                header_text = "\n".join(h.read_text(encoding="utf-8") for h in task.include_dir.glob("*.h"))
                for f in task.reference_dir.glob("*.c"):
                    # 函数签名的续行本来就在公共头文件里，不算实现
                    body = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                            if ln.startswith("    ") and len(ln.strip()) > 20 and ln.strip() not in header_text]
                    for ln in body[:8]:
                        self.assertNotIn(ln, tester_text)
                self.assertIn(res.verdict, ("pass", "incomplete"), res.error)


# ---------------------------------------------------------------- b5 与评测

@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class B5AndEvalTest(_Tmp):
    def test_b5_restarts_from_scratch_until_budget(self):
        broken = (block("src/ringbuf.c", "int broken(void) { return 0 }\n")
                  + block("tests/test_ringbuf.c", RB_TESTS["a"]))
        llm = FakeLLM(lambda msgs: broken)
        res = run_baseline(RB, B5, llm=llm, model=STRONG, runs_root=self.tmp / "runs", token_budget=1)
        self.assertEqual(len(res.attempts), 1)               # 第 1 次后已超预算
        llm = FakeLLM(lambda msgs: broken)
        res = run_baseline(RB, B5, llm=llm, model=STRONG, runs_root=self.tmp / "runs2", token_budget=10 ** 9)
        self.assertEqual(len(res.attempts), B5.max_attempts)
        # 每次都是同一份初始消息：不带上一次的代码和报错
        first = llm.calls[0]["messages"]
        self.assertTrue(all(c["messages"] == first for c in llm.calls))
        with self.assertRaises(ValueError):
            run_baseline(RB, B5, llm=llm, model=STRONG, runs_root=self.tmp / "runs3")

    def test_b5_stops_on_pass(self):
        good = block("src/ringbuf.c", ref(RB, "ringbuf.c")) + block("tests/test_ringbuf.c", RB_TESTS["a"])
        broken = block("src/ringbuf.c", "int broken(void) { return 0 }\n") + block("tests/test_ringbuf.c", RB_TESTS["a"])
        res = run_baseline(RB, B5, llm=FakeLLM([broken, good, good]), model=STRONG, runs_root=self.tmp / "r",
                           token_budget=10 ** 9)
        self.assertEqual(len(res.attempts), 2)
        self.assertIsNone(res.attempts[1].report.failed_layer)

    def test_parse_configs_b5_order(self):
        self.assertEqual([c.name for c in parse_configs("b1,b2,b3,b4,full,b5")], ["b1", "b2", "b3", "b4", "full", "b5"])
        for bad in ("b5", "b5,full", "b1,b1"):
            with self.assertRaises(ConfigError):
                parse_configs(bad)

    def test_full_budget(self):
        rows = [{"task": "t", "config": "full", "tokens": 100}, {"task": "t", "config": "full", "tokens": 300},
                {"task": "u", "config": "full", "tokens": 5}, {"task": "t", "config": "b2", "tokens": 9}]
        self.assertEqual(full_budget(rows, "t"), 200)
        self.assertEqual(full_budget(rows, "x"), 0)

    def test_eval_multi_and_b5(self):
        def reply(msgs):
            if role_of(msgs) == "coder" and msgs[0]["content"].find("负责规划中的") < 0:   # b5 的单 Agent
                return block("src/ringbuf.c", ref(RB, "ringbuf.c")) + block("tests/test_ringbuf.c", RB_TESTS["a"])
            return rb_team()(msgs)
        out = evaluate([RB], parse_configs("b3,full,b5"), 1, self.tmp / "out", llm=FakeLLM(reply), settings=SETTINGS)
        rows = read_csv(out.csv_path)
        self.assertEqual([r["config"] for r in rows], ["b3", "full", "b5"])
        self.assertEqual(rows[1]["attempts"], "3")                   # 3 个模块各 1 次
        expected = "True" if helpers.HAS_CROSS and helpers.HAS_CPPCHECK else "False"
        self.assertEqual(rows[1]["final_pass"], expected)
        self.assertEqual(rows[1]["first_pass"], expected)
        summary = out.summary_path.read_text(encoding="utf-8")
        self.assertIn("每通过一题", summary)
        runs = [json.loads((d / "run.json").read_text(encoding="utf-8")) for d in (self.tmp / "out" / "runs").iterdir()]
        b5_run = next(r for r in runs if r["config"] == "b5")
        self.assertEqual(b5_run["token_budget"], int(rows[1]["tokens"]))


# ---------------------------------------------------------------- 命令行

@unittest.skipUnless(helpers.HAS_CC, "本机没有 gcc")
class CliStage3Test(_Tmp):
    def _cli(self, *argv, llm=None):
        out, err = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(cli.config, "load_settings", return_value=SETTINGS)]
        if llm is not None:
            patches.append(mock.patch.object(cli, "make_llm", return_value=llm))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_run_full_then_replay(self):
        code, out, _ = self._cli("run", "--task", str(RB.root), "--config", "full", "--runs-dir", str(self.tmp / "runs"),
                                 "--db", str(self.tmp / "db"), llm=FakeLLM(rb_team()))
        self.assertIn(code, (0, 2))
        self.assertIn("规划完成：3 个模块：a、b、c（依赖 a, b）", out)
        self.assertIn("模块 a 的编码分配给", out)
        self.assertIn("[c] 第 1 次判定", out)
        self.assertIn("集成判定", out)
        self.assertIn("dag.json", out)
        run_dir = next((self.tmp / "runs").iterdir())
        code, out2, _ = self._cli("replay", str(run_dir), "--speed", "0")
        self.assertEqual(code, 0)
        self.assertIn("调度 smart", out2)

    def test_human_review_in_terminal(self):
        with mock.patch("builtins.input", return_value=""):
            code, out, _ = self._cli("run", "--task", str(RB.root), "--config", "b4", "--human-review",
                                     "--runs-dir", str(self.tmp / "runs"), "--db", str(self.tmp / "db"),
                                     llm=FakeLLM(rb_team()))
        self.assertIn(code, (0, 2))
        self.assertIn("等待负责人确认规划", out)
        self.assertIn("负责公共函数 ringbuf_init", out)
        self.assertIn("负责人确认了规划", out)

    def test_heartbeat_continues_while_other_module_waits(self):
        buf = io.StringIO()
        printer = cli.LivePrinter(heartbeat_s=0.2, out=buf)
        try:
            printer({"ts": 100.0, "type": "agent_started", "node_id": "pdu", "data": {"agent": "tester", "attempt": 1}})
            printer({"ts": 100.0, "type": "agent_started", "node_id": "crc", "data": {"agent": "tester", "attempt": 1}})
            printer({"ts": 101.0, "type": "agent_finished", "node_id": "crc",
                     "data": {"agent": "tester", "ok": True, "files": []}})
            printer({"ts": 101.0, "type": "verify_started", "node_id": "crc", "data": {"attempt": 1, "scope": "module"}})
            time.sleep(0.7)
        finally:
            printer.close()
        self.assertIn("模型还在思考", buf.getvalue())         # pdu 的测试 Agent 还在等

    def test_usage_errors(self):
        base = ("run", "--task", str(RB.root), "--runs-dir", str(self.tmp / "runs"), "--db", str(self.tmp / "db"))
        self.assertEqual(self._cli(*base, "--config", "b5")[0], 3)                 # 缺 --token-budget
        self.assertEqual(self._cli(*base, "--config", "b2", "--human-review")[0], 3)
        self.assertEqual(self._cli(*base, "--config", "full", "--token-budget", "5")[0], 3)
        self.assertEqual(self._cli("eval", "--configs", "b5,full", "--out", str(self.tmp / "o"))[0], 3)

    def test_b5_cli(self):
        good = block("src/ringbuf.c", ref(RB, "ringbuf.c")) + block("tests/test_ringbuf.c", RB_TESTS["a"])
        code, out, _ = self._cli("run", "--task", str(RB.root), "--config", "b5", "--token-budget", "100000",
                                 "--runs-dir", str(self.tmp / "runs"), "--db", str(self.tmp / "db"),
                                 llm=FakeLLM(lambda m: good))
        self.assertIn(code, (0, 2))
        self.assertIn("同等花费对照", out)


if __name__ == "__main__":
    unittest.main()

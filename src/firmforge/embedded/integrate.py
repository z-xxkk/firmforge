"""集成：所有模块通过以后，写主程序 app_main.c 把模块串起来，编译、烧到板子上跑，看串口输出（DECISIONS 第 131 条）。

以前上板流程只交付一个个通过检查的模块，没有主程序，用户拿到的固件不会做需求里说的事。

- 编码 Agent 按需求和设计里的 app 说明写 app_main.c，同时交一份"串口上应该看到什么"（app_expect.json）；
- 平台编译整个工程（各模块代码 + app_main.c，不带测试），烧录，复位跑两轮：第一轮看正常运行，
  第二轮是复位以后（检查掉电保存这类要求）；
- 客观检查：不崩溃、不意外重启、程序一直在打印；期望的每一行都要出现够次数，带周期的还要检查间隔；
- 期望是写主程序的 Agent 自己交的（它最清楚打印的原话），所以只能证明"程序真的跑到了这些地方"，
  不能证明需求理解得对——这一点在报告里写明，人在"等你看结果"时可以看串口记录。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..agents.brief import Brief
from ..grounding.profile import HardwareProfile
from ..grounding.rules import RuleSet
from .design import Design, DesignModule, design_json
from .prompting import system_prompt

APP_FILE = "app_main.c"
EXPECT_FILE = "app_expect.json"
PHASES = ("first", "after_reset")
PHASE_NAMES = {"first": "第一轮（上电运行）", "after_reset": "第二轮（复位以后）"}
MIN_WINDOW_S, MAX_WINDOW_S = 12.0, 40.0
CRASH = ("Guru Meditation", "abort() was called", "Backtrace:", "ESP_ERROR_CHECK failed", "assert failed",
         "Stack canary", "stack overflow")
_LOG_TS = re.compile(r"^[IWE] \((\d+)\) ")
_RESET = re.compile(r"rst:0x[0-9a-fA-F]+")


def app_module() -> DesignModule:
    """集成在界面和事件里当作一个叫 app 的"模块"（等人看结果、显示检查进度都复用模块的那一套）。"""
    return DesignModule("app", "主程序（把各模块串起来上板运行）", "hardware", (APP_FILE,))


@dataclass(frozen=True)
class Expect:
    pattern: str
    phase: str = "first"
    min_count: int = 1
    every_s: float | None = None          # 周期打印：相邻两次的间隔不超过它的 1.5 倍
    what: str = ""                        # 这一行说明需求里的哪一条

    def to_dict(self) -> dict:
        return {"pattern": self.pattern, "phase": self.phase, "min_count": self.min_count, "every_s": self.every_s,
                "what": self.what}


def parse_expectations(text: str) -> tuple[list[Expect], list[str]]:
    """app_expect.json → 期望列表和格式问题（有问题就退回让 Agent 改）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], [f"{EXPECT_FILE} 不是合法的 JSON：{exc}"]
    items = data.get("expect") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return [], [f"{EXPECT_FILE} 要写成 {{\"expect\": [...]}}，至少一条"]
    out, problems = [], []
    for i, d in enumerate(items[:12], 1):
        if not isinstance(d, dict) or not isinstance(d.get("pattern"), str) or not d["pattern"].strip():
            problems.append(f"第 {i} 条要有 pattern（正则表达式）")
            continue
        try:
            re.compile(d["pattern"])
        except re.error as exc:
            problems.append(f"第 {i} 条的 pattern 不是合法的正则表达式：{exc}")
            continue
        phase = d.get("phase", "first")
        if phase not in PHASES:
            problems.append(f"第 {i} 条的 phase 只能是 first 或 after_reset")
            continue
        every = d.get("every_s")
        if every is not None and (isinstance(every, bool) or not isinstance(every, (int, float)) or not 0.1 <= every <= 10):
            problems.append(f"第 {i} 条的 every_s 要是 0.1–10 之间的秒数（不是周期打印就不写）")
            continue
        n = d.get("min_count", 1)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 100:
            problems.append(f"第 {i} 条的 min_count 要是 1–100 的整数")
            continue
        out.append(Expect(d["pattern"], phase, n, float(every) if every is not None else None, str(d.get("what", ""))[:120]))
    return out, problems


def window_seconds(expects: list[Expect]) -> float:
    """每一轮读多久串口：能看到每个周期打印至少 3 次，至少 12 秒，最多 40 秒。"""
    longest = max((e.every_s or 0.0) for e in expects) if expects else 0.0
    return max(MIN_WINDOW_S, min(MAX_WINDOW_S, 3 * longest + 6))


@dataclass
class AppCheck:
    ok: bool
    skipped: str = ""
    problems: list[str] = field(default_factory=list)
    matched: list[dict] = field(default_factory=list)

    def report(self) -> str:
        if self.skipped:
            return f"上板运行跳过：{self.skipped}"
        lines = ["上板运行：" + ("通过" if self.ok else "没通过")]
        lines += [f"- {p}" for p in self.problems]
        for m in self.matched:
            lines.append(f"  · {PHASE_NAMES[m['phase']]} /{m['pattern']}/：出现 {m['count']} 次（至少 {m['min_count']}）"
                         + (f"，最大间隔 {m['max_gap_s']:.1f} 秒（周期 {m['every_s']} 秒）" if m.get("max_gap_s") is not None else ""))
        return "\n".join(lines)


def _app_lines(out: str) -> list[str]:
    return [ln.rstrip("\r") for ln in out.splitlines()]


def check_app_output(outputs: dict[str, str], expects: list[Expect], seconds: float) -> AppCheck:
    """outputs：每一轮的串口原文。只看客观的东西：崩溃、意外重启、期望的行出现了几次、周期打印的间隔。"""
    res = AppCheck(ok=True)
    for phase in PHASES:
        out = outputs.get(phase, "")
        name = PHASE_NAMES[phase]
        hit = next((c for c in CRASH if c in out), None)
        if hit:
            res.problems.append(f"{name}：芯片崩溃（{hit}）")
        if len(_RESET.findall(out)) > 1:
            res.problems.append(f"{name}：运行中芯片自己重启了（串口上出现 {len(_RESET.findall(out))} 次启动信息）")
        if not any(_LOG_TS.match(ln) for ln in _app_lines(out)):
            res.problems.append(f"{name}：串口上没有看到程序的日志输出（要用 ESP_LOGI 打印）")
    for e in expects:
        lines = [ln for ln in _app_lines(outputs.get(e.phase, "")) if re.search(e.pattern, ln)]
        need = e.min_count
        if e.every_s:
            need = max(need, int(seconds // e.every_s) - 1)
        info = {"pattern": e.pattern, "phase": e.phase, "count": len(lines), "min_count": need, "every_s": e.every_s}
        if e.every_s and len(lines) >= 2:
            ts = [int(m.group(1)) / 1000 for m in (_LOG_TS.match(ln) for ln in lines) if m]
            if len(ts) >= 2:
                info["max_gap_s"] = max(b - a for a, b in zip(ts, ts[1:]))
                if info["max_gap_s"] > 1.5 * e.every_s:
                    res.problems.append(f"{PHASE_NAMES[e.phase]}：/{e.pattern}/ 应该每 {e.every_s} 秒一次，"
                                        f"最大间隔却有 {info['max_gap_s']:.1f} 秒")
        if len(lines) < need:
            res.problems.append(f"{PHASE_NAMES[e.phase]}：/{e.pattern}/（{e.what or '期望的输出'}）只出现 {len(lines)} 次，"
                                f"至少要 {need} 次")
        res.matched.append(info)
    res.ok = not res.problems
    return res


# ---------------------------------------------------------------- 提示词

def integrator_messages(requirement: str, profile: HardwareProfile, rules: RuleSet | None, design: Design,
                        headers: dict[str, str]) -> list[dict]:
    scopes = {s for m in design.modules for s in m.scopes} - {"all"}
    system = system_prompt("coder_idf.md", profile, rules, scopes)
    b = Brief(intent="写主程序 app_main.c，把已经通过检查的各个模块串起来，实现需求里说的完整功能",
              location="集成 › 主程序")
    b.constraints += [
        f"只输出两个文件：main/{APP_FILE}（定义 void app_main(void)）和 main/{EXPECT_FILE}",
        "各模块的代码已经通过检查、不能修改；只能通过下面列出的公共头文件调用它们",
        "所有打印都用 ESP_LOGI / ESP_LOGW / ESP_LOGE（TAG 用 \"app\"），平台靠日志里的时间戳检查周期",
        "初始化失败要打印错误并停在原地（例如每秒打印一次错误），不要 abort、不要让芯片反复重启",
        "不许进入深度睡眠；主循环里用 vTaskDelay 等待，不要忙等",
        f"main/{EXPECT_FILE} 写串口上应该看到的输出，平台会烧录后复位跑两轮（每轮十几到几十秒）逐条核对",
    ]
    b.attach("需求文档", requirement)
    b.attach("整体设计（app 一项是主程序要做的事）", design_json(design))
    b.attach("各模块的公共头文件（只读）", "\n\n".join(f"main/{p}\n```c\n{c.rstrip()}\n```" for p, c in sorted(headers.items())))
    b.attach(f"{EXPECT_FILE} 的格式", EXPECT_HELP)
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


EXPECT_HELP = """```json
{"expect": [
  {"pattern": "温度 [-0-9.]+ ℃", "every_s": 2, "what": "每 2 秒打印一次温度"},
  {"pattern": "上次保存的最高温度", "phase": "after_reset", "what": "复位后先打印上次保存的值"}
]}
```

- pattern：正则表达式，和你在 app_main.c 里打印的原话对应（只匹配日志正文就行）；
- phase：first（上电后第一轮，默认）或 after_reset（平台复位芯片后的第二轮，用来检查掉电保存一类的要求）；
- every_s：周期打印的周期（秒），平台会检查次数够不够、相邻两次的间隔是否超过它的 1.5 倍；不是周期打印就不写；
- min_count：至少出现几次（默认 1）；
- 需求里每一条能在串口上看到的要求都写一条；不要写打印内容里不会出现的东西。"""


def integrator_repair_message(attempt: int, max_attempts: int, report: str) -> str:
    return "\n".join([f"第 {attempt} 次没有通过（最多 {max_attempts} 次）。问题如下：", "", report.strip(), "",
                      f"请输出修改后的 main/{APP_FILE}。模块代码不能改；如果确实是某个模块的问题，在说明里写清楚。",
                      f"main/{EXPECT_FILE} 已经锁定（第一次交的期望就是验收标准）：应该改程序让它打印出期望的内容，"
                      "而不是删掉或放宽期望。只有原来的期望本身写错了（和需求不符）才能改，这时必须在 JSON 里加 "
                      "\"change_reason\" 写清为什么原来的不对——会原样告诉负责人；没写理由的改动不算数。"])

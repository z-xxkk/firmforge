"""上板测试的测试程序与结果解析（DECISIONS 第 78、81 条）。

测试程序 = 被测模块及其依赖 + 测试 Agent 写的 Unity 用例 + 平台提供的辅助文件：
- ff_test.h / ff_test.c：读寄存器、计时、打印调试信息；
- ff_regs.h：规则里提到的寄存器的地址（从 SVD 算出），测试用 FF_REG_外设_寄存器 读，不靠被测代码自己报告；
- ff_test_main.c：打印开始标记 → 跑全部用例 → 打印结束标记，然后停住（不重启，方便读完串口）。

结果解析看串口输出：
- 每个用例一行  <文件>:<行>:<用例名>:PASS / FAIL:<原因> / IGNORE
- 汇总行        N Tests M Failures K Ignored
- 芯片崩溃（Guru Meditation、abort、看门狗复位）记为 crash；没有结束标记记为 hang（卡死或超时）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..grounding.rules import RuleSet
from ..grounding.svd import SvdDevice

BEGIN, END = "FF_TESTS_BEGIN", "FF_TESTS_END"
TEST_REQUIRES = ("unity", "esp_timer")

FF_TEST_H = """/* FirmForge 平台提供：上板测试辅助函数。 */
#pragma once
#include <stdint.h>

/* 读外设寄存器（地址用 ff_regs.h 里的 FF_REG_* 宏）。 */
uint32_t ff_reg_read(uint32_t addr);
/* 开机后的微秒数。 */
int64_t ff_time_us(void);
/* 打印调试信息，会进测试报告。 */
void ff_log(const char *fmt, ...);
"""

FF_TEST_C = """/* FirmForge 平台提供：上板测试辅助函数。 */
#include <stdarg.h>
#include <stdio.h>
#include "esp_timer.h"
#include "ff_test.h"

uint32_t ff_reg_read(uint32_t addr) { return *(volatile uint32_t *)addr; }

int64_t ff_time_us(void) { return esp_timer_get_time(); }

void ff_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    printf("FF_LOG: ");
    vprintf(fmt, ap);
    printf("\\n");
    va_end(ap);
}
"""

FF_TEST_MAIN = f"""/* FirmForge 平台提供：跑全部 Unity 用例，打印开始、结束标记后停住。 */
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "unity.h"
#include "unity_test_runner.h"

void app_main(void)
{{
    vTaskDelay(pdMS_TO_TICKS(2000));     /* 等电脑那边打开串口（烧录完自动复位后才开始读） */
    printf("{BEGIN}\\n");
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
    printf("{END}\\n");
    while (1) {{
        vTaskDelay(pdMS_TO_TICKS(1000));
    }}
}}
"""


def regs_header(rules: RuleSet | None, svd: SvdDevice | None, scopes: set[str] | None = None) -> str:
    """规则里出现的寄存器 → FF_REG_外设_寄存器 地址宏。SVD 里没有的写成注释，说明无法读。"""
    lines = ["/* FirmForge 按规则和 SVD 生成：规则里提到的寄存器地址。 */", "#pragma once", ""]
    seen = set()
    for r in (rules.select(scopes, "approved") if rules is not None else []):
        c = r.check or {}
        if c.get("type") != "register_value":
            continue
        key = (c["peripheral"], c["register"])
        if key in seen:
            continue
        seen.add(key)
        name = f"FF_REG_{c['peripheral']}_{c['register']}".upper()
        addr = svd.addresses.get(key) if svd is not None else None
        lines.append(f"#define {name} 0x{addr:08X}u  /* {r.id} */" if addr is not None
                     else f"/* {name}：SVD 里没有这个寄存器，无法读取（{r.id}） */")
    return "\n".join(lines) + "\n"


def harness_files(rules: RuleSet | None, svd: SvdDevice | None, scopes: set[str] | None = None) -> dict[str, str]:
    """放进 main/ 的平台文件（相对 main/ 的路径）。"""
    return {"ff_test_main.c": FF_TEST_MAIN, "ff_test.c": FF_TEST_C, "include/ff_test.h": FF_TEST_H,
            "include/ff_regs.h": regs_header(rules, svd, scopes)}


# ---------------------------------------------------------------- 结果解析

_CASE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<name>.+?):(?P<status>PASS|FAIL|IGNORE)(?::\s*(?P<msg>.*))?\s*$",
                   re.M)
_SUMMARY = re.compile(r"^(\d+) Tests (\d+) Failures (\d+) Ignored", re.M)
_DONE = re.compile(r"^\d+ Tests \d+ Failures \d+ Ignored\s*\r?\n(?:OK|FAIL)\b", re.M)
_CRASH = re.compile(r"Guru Meditation|abort\(\) was called|Backtrace:|rst:0x[0-9a-f]+ \((?:TG\dWDT|RTCWDT|INT_WDT|"
                    r"TASK_WDT|PANIC)|Task watchdog got triggered|stack overflow", re.I)


@dataclass
class CaseResult:
    name: str
    status: str             # pass / fail / ignore
    message: str = ""
    where: str = ""


@dataclass
class BoardTestResult:
    cases: list[CaseResult] = field(default_factory=list)
    summary: tuple[int, int, int] | None = None
    crash: str = ""          # 崩溃的那一行
    hang: bool = False       # 没有结束标记
    started: bool = False
    logs: list[str] = field(default_factory=list)     # ff_log 打印的内容
    skipped: str = ""        # 没跑成（例如板子没连），原因写在这里

    @property
    def failed(self) -> list[CaseResult]:
        return [c for c in self.cases if c.status == "fail"]

    @property
    def ok(self) -> bool:
        return (not self.skipped and self.started and not self.crash and not self.hang and not self.failed
                and bool(self.cases))

    def verdict(self) -> str:
        if self.skipped:
            return "skipped"
        return "pass" if self.ok else "fail"

    def report(self) -> str:
        if self.skipped:
            return f"上板测试跳过：{self.skipped}"
        lines = []
        if not self.started:
            lines.append("没有看到测试开始标记：程序可能没烧进去、启动就崩了，或串口没连上")
        for c in self.failed:
            lines.append(f"失败：{c.name}（{c.where}）{c.message}")
        if self.crash:
            lines.append(f"芯片崩溃：{self.crash}")
        if self.hang:
            lines.append("没有看到测试结束标记：程序卡死或超时")
        if self.started and not self.cases and not self.crash:
            lines.append("一个用例都没有跑（测试文件里没有 TEST_CASE？）")
        passed = sum(1 for c in self.cases if c.status == "pass")
        lines.append(f"共 {len(self.cases)} 个用例，通过 {passed}，失败 {len(self.failed)}")
        lines += [f"调试信息：{x}" for x in self.logs[:20]]
        return "\n".join(lines)


_ESCAPED = re.compile(r"(?:\\x[0-9A-Fa-f]{2})+")


def unescape_unity(text: str) -> str:
    """ESP-IDF 的 Unity 把非 ASCII 字符（中文用例名）打印成 \\xE4\\xB8… 这样的转义，还原成 UTF-8 文字。"""
    def back(m: re.Match) -> str:
        raw = bytes(int(h, 16) for h in re.findall(r"\\x([0-9A-Fa-f]{2})", m.group(0)))
        return raw.decode("utf-8", "replace")
    return _ESCAPED.sub(back, text)


def parse_board_output(text: str) -> BoardTestResult:
    text = unescape_unity(text)
    res = BoardTestResult()
    i = text.find(BEGIN)
    res.started = i >= 0
    body = text[i + len(BEGIN):] if i >= 0 else text
    j = body.find(END)
    if j >= 0:
        body = body[:j]
    # USB 串口偶尔丢几个字节，结束标记可能不完整；Unity 的汇总行加 OK/FAIL 也算正常结束
    finished = j >= 0 or _DONE.search(body) is not None
    res.hang = res.started and not finished
    for m in _CASE.finditer(body):
        res.cases.append(CaseResult(m["name"].strip(), m["status"].lower(), (m["msg"] or "").strip(),
                                    f"{m['file'].strip()}:{m['line']}"))
    sums = _SUMMARY.findall(body)
    res.summary = tuple(int(x) for x in sums[-1]) if sums else None
    crash = _CRASH.search(text[i:] if i >= 0 else text)
    if crash:
        start = text.rfind("\n", 0, crash.start()) + 1
        end = text.find("\n", crash.end())
        res.crash = text[start:end if end >= 0 else None].strip()[:200]
    res.logs = [ln.split("FF_LOG:", 1)[1].strip() for ln in body.splitlines() if "FF_LOG:" in ln]
    return res

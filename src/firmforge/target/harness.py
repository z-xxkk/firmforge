"""上板测试的测试程序与结果解析（DECISIONS 第 78、81 条）。

测试程序 = 被测模块及其依赖 + 测试 Agent 写的 Unity 用例 + 平台提供的辅助文件：
- ff_test.h / ff_test.c：读寄存器、计时、打印调试信息；
- ff_regs.h：规则里提到的寄存器的地址（从 SVD 算出），测试用 FF_REG_外设_寄存器 读，不靠被测代码自己报告；
- ff_test_main.c：打印开始标记 → 每个用例单独开机跑（跑完一个软重启，失败不连累后面的用例）→ 打印汇总和结束标记，然后停住。
  芯片软重启不会复位外接器件，所以每个用例开始前按硬件档案把外接 I2C 器件软复位（DECISIONS 第 96 条），
  免得上一个用例留下的配置影响下一个用例。

结果解析看串口输出：
- 每个用例一行  <文件>:<行>:<用例名>:PASS / FAIL:<原因> / IGNORE
- 汇总行        N Tests M Failures K Ignored
- 芯片崩溃（Guru Meditation、abort、看门狗复位）记为 crash；没有结束标记记为 hang（卡死或超时）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..grounding.profile import HardwareProfile
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

_TEST_MAIN = """/* FirmForge 平台提供：每个 Unity 用例单独开机运行（DECISIONS 第 91 条），全部跑完打印汇总和结束标记后停住。
   跑到第几个、累计失败数记在软重启后不清零的内存里；上电或硬复位时从头开始。
   芯片软重启不会复位外接器件，硬件档案写了复位方法的器件在每个用例前软复位（DECISIONS 第 96 条）。 */
#include <stdio.h>
#include "esp_attr.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "unity.h"
#include "unity_test_runner.h"
@RESET_DECL@
#define FF_MAGIC 0x46465431u

static RTC_NOINIT_ATTR uint32_t s_magic, s_next, s_tests, s_failures, s_ignores;

void app_main(void)
{
    if (esp_reset_reason() != ESP_RST_SW || s_magic != FF_MAGIC) {
        vTaskDelay(pdMS_TO_TICKS(2000));     /* 等电脑那边打开串口（烧录完自动复位后才开始读） */
        printf("@BEGIN@\\n");
        s_magic = FF_MAGIC;
        s_next = s_tests = s_failures = s_ignores = 0;
    }
    int total = unity_get_test_count();
    if (s_next < (uint32_t)total) {
        @RESET_CALL@UNITY_BEGIN();
        unity_run_test_by_index((int)s_next);
        s_tests += Unity.NumberOfTests;
        s_failures += Unity.TestFailures;
        s_ignores += Unity.TestIgnores;
        s_next++;
        fflush(stdout);
        vTaskDelay(pdMS_TO_TICKS(50));       /* 让串口发完再重启 */
        esp_restart();
    }
    s_magic = 0;
    printf("\\n-----------------------\\n%u Tests %u Failures %u Ignored\\n%s\\n",
           (unsigned)s_tests, (unsigned)s_failures, (unsigned)s_ignores, s_failures ? "FAIL" : "OK");
    printf("@END@\\n");
    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
"""


def _reset_code(profile: HardwareProfile | None) -> tuple[str, str]:
    """外接 I2C 器件的软复位代码：（声明部分，调用语句）。档案里没有可复位的器件就都是空的。"""
    devs = [d for d in (profile.devices if profile is not None else ())
            if d.reset is not None and d.address is not None and d.bus.startswith("i2c")]
    if not devs:
        return "", ""
    lines = ['#include "driver/i2c_master.h"', '#include "hw_config.h"', "",
             "/* 写一个寄存器复位器件；器件没应答只打印一行，不让用例失败（缺器件时由用例自己报错）。 */",
             "static void ff_reset_one(i2c_master_bus_handle_t bus, uint16_t addr, uint8_t reg, uint8_t val,"
             " const char *name)",
             "{",
             "    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr,"
             " .scl_speed_hz = 100000};",
             "    i2c_master_dev_handle_t dev;",
             "    uint8_t buf[2] = {reg, val};",
             "    if (i2c_master_bus_add_device(bus, &dc, &dev) != ESP_OK) {",
             '        printf("FF_LOG: 用例前复位 %s 失败：加不进总线\\n", name);',
             "        return;",
             "    }",
             "    if (i2c_master_transmit(dev, buf, 2, 50) != ESP_OK) {",
             '        printf("FF_LOG: 用例前复位 %s 失败：器件没有应答\\n", name);',
             "    }",
             "    i2c_master_bus_rm_device(dev);",
             "}",
             "",
             "/* 每个用例开始前把外接器件恢复到上电状态，用完把总线删掉，用例自己重新建。 */",
             "static void ff_reset_devices(void)",
             "{"]
    for bus in sorted({d.bus for d in devs}):
        port, sig = int(bus[3:]), re.sub(r"[^A-Za-z0-9]", "_", bus).upper()
        lines += ["    {",
                  f"        i2c_master_bus_config_t bc = {{.i2c_port = {port}, .sda_io_num = FF_PIN_{sig}_SDA,"
                  f" .scl_io_num = FF_PIN_{sig}_SCL,",
                  "                                     .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,",
                  "                                     .flags.enable_internal_pullup = true};",
                  "        i2c_master_bus_handle_t bus;",
                  "        if (i2c_new_master_bus(&bc, &bus) == ESP_OK) {"]
        for d in devs:
            if d.bus == bus:
                r = d.reset.value
                lines.append(f"            ff_reset_one(bus, FF_{d.name.upper()}_ADDR, 0x{r['reg']:02X}, 0x{r['value']:02X},"
                             f' "{d.name}");  /* {d.part}：{d.reset.note[:80].replace("*/", "")} */')
        lines += ["            i2c_del_master_bus(bus);",
                  "        } else {",
                  f'            printf("FF_LOG: 用例前复位外接器件失败：建不了 {bus} 总线\\n");',
                  "        }",
                  "    }"]
    wait = max(d.reset.value["wait_ms"] for d in devs)
    lines += [f"    vTaskDelay(pdMS_TO_TICKS({wait}));     /* 等器件复位完成 */", "}", ""]
    return "\n".join(lines) + "\n", "ff_reset_devices();\n        "


def main_source(profile: HardwareProfile | None = None) -> str:
    decl, call = _reset_code(profile)
    return (_TEST_MAIN.replace("@BEGIN@", BEGIN).replace("@END@", END)
            .replace("@RESET_DECL@", decl).replace("@RESET_CALL@", call))


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


def harness_files(rules: RuleSet | None, svd: SvdDevice | None, scopes: set[str] | None = None,
                  profile: HardwareProfile | None = None) -> dict[str, str]:
    """放进 main/ 的平台文件（相对 main/ 的路径）。给了硬件档案时，每个用例前复位档案里可复位的外接器件。"""
    return {"ff_test_main.c": main_source(profile), "ff_test.c": FF_TEST_C, "include/ff_test.h": FF_TEST_H,
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
    crash = _CRASH.search(text, max(i, 0))          # 在全文里从开始标记处找，位置才和下面截取那一行对得上
    if crash:
        start = text.rfind("\n", 0, crash.start()) + 1
        end = text.find("\n", crash.end())
        res.crash = text[start:end if end >= 0 else None].strip()[:200]
    res.logs = [ln.split("FF_LOG:", 1)[1].strip() for ln in body.splitlines() if "FF_LOG:" in ln]
    return res

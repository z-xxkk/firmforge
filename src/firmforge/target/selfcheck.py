"""接线自检（DECISIONS 第 124 条）：诊断 Agent 说"硬件或接线有问题"时，平台烧一个自己写的、确定没问题的小程序去实测，
不靠模型的判断。

复测里没过的 19 次有 9 次是在第 1–2 次尝试就被诊断为硬件问题而停下，实际接线都是好的（标准答案在同一块板上通过）；
三个 Agent 用同一个模型，会一起看错（例如都把 MPU6050 的 INT_PIN_CFG=0xB0 当成高有效）。客观测量能打破这种一起看错。

自检项从硬件档案里来（程序生成，不经模型）：
- I2C 器件：在总线上探测它的地址有没有应答；
- 跳线（profile.loopbacks，GPIO 编号）：一端输出高/低，另一端带反向上下拉读，两个方向各测 3 次；
- INT 线（Device.int_probe）：清掉挂着的中断后 INT 脚应是无效电平，按档案写寄存器让器件产生中断，等一会儿应变成有效电平。

自检程序用上板测试同一套东西跑（测试主程序、每个用例单独开机、串口解析），每一项是一个 TEST_CASE。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..grounding.profile import Device, HardwareProfile

CASE_PREFIX = "接线自检："
TEST_FILE = "test/test_ff_wiring.c"


@dataclass(frozen=True)
class Item:
    kind: str                   # i2c / loop / int
    name: str                   # 用例名（也给人看）
    device: str = ""
    gpios: tuple[int, ...] = ()
    signals: tuple[str, ...] = ()   # 对应项目接线里的哪几根线（第 134 条：结果写回每根线的状态）
    params: tuple = ()              # adc：(min, max)；level：(value,)


@dataclass
class WiringCheck:
    items: list[dict] = field(default_factory=list)     # {"name", "kind", "ok": True/False/None, "detail"}
    skipped: str = ""                                    # 没法测的原因（没有板子、编译失败……）

    @property
    def all_ok(self) -> bool:
        return not self.skipped and bool(self.items) and all(i["ok"] is True for i in self.items)

    @property
    def failed(self) -> list[dict]:
        return [i for i in self.items if i["ok"] is False]

    def evidence(self) -> str:
        if self.skipped:
            return f"接线自检没能运行：{self.skipped}"
        mark = {True: "正常", False: "不通", None: "没测出结果"}
        return "；".join(f"{i['name'].removeprefix(CASE_PREFIX)}：{mark[i['ok']]}"
                        + (f"（{i['detail']}）" if i["detail"] else "") for i in self.items)


def _bus_pins(profile: HardwareProfile, bus: str) -> tuple[int, int] | None:
    sda, scl = profile.pins.get(f"{bus}.sda"), profile.pins.get(f"{bus}.scl")
    return (sda.value, scl.value) if sda is not None and scl is not None else None


def select_items(profile: HardwareProfile, devices_used: set[str] | None = None,
                 gpios_used: set[int] | None = None) -> list[Item]:
    """和这个模块有关的自检项。devices_used：用到的器件名（None 表示全部）；gpios_used：用到的 GPIO（None 表示全部）。"""
    out: list[Item] = []
    for d in profile.devices:
        if devices_used is not None and d.name not in devices_used:
            continue
        if d.address is None or not d.bus.startswith("i2c") or _bus_pins(profile, d.bus) is None:
            continue
        out.append(Item("i2c", f"{CASE_PREFIX}{d.name}（{d.part}）在 {d.bus} 的 0x{d.address:02X} 应答", d.name))
        if d.int_probe is not None and "int" in d.pins:
            g = d.pins["int"]
            sig = profile.pins.get(f"{d.name}.int")
            g = sig.value if sig is not None else g
            out.append(Item("int", f"{CASE_PREFIX}{d.name}（{d.part}）的 INT 线接到 GPIO{g}", d.name, (g,)))
    for lb in profile.loopbacks:
        a, b = lb["a"], lb["b"]
        if gpios_used is not None and not ({a, b} & gpios_used):
            continue
        out.append(Item("loop", f"{CASE_PREFIX}GPIO{a} 和 GPIO{b} 之间的跳线连通", gpios=(a, b)))
    return out


_HEAD = """/* 固件工坊平台生成：接线自检（DECISIONS 第 124 条）。不是 Agent 写的代码。 */
#include <string.h>
#include "unity.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ff_test.h"

static i2c_master_bus_handle_t ffw_bus(int port, int sda, int scl)
{
    i2c_master_bus_config_t c = {.i2c_port = port, .sda_io_num = sda, .scl_io_num = scl,
                                 .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
                                 .flags.enable_internal_pullup = true};
    i2c_master_bus_handle_t bus = NULL;
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, i2c_new_master_bus(&c, &bus), "建不了 I2C 总线");
    return bus;
}

static i2c_master_dev_handle_t ffw_dev(i2c_master_bus_handle_t bus, uint16_t addr)
{
    i2c_device_config_t dc = {.dev_addr_length = I2C_ADDR_BIT_LEN_7, .device_address = addr, .scl_speed_hz = 100000};
    i2c_master_dev_handle_t dev = NULL;
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, i2c_master_bus_add_device(bus, &dc, &dev), "加不进总线");
    return dev;
}

static esp_err_t ffw_wr(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t val)
{
    uint8_t b[2] = {reg, val};
    return i2c_master_transmit(dev, b, 2, 50);
}

static esp_err_t ffw_rd(i2c_master_dev_handle_t dev, uint8_t reg, uint8_t *val)
{
    return i2c_master_transmit_receive(dev, &reg, 1, val, 1, 50);
}

/* out 输出 level，in 带反向上下拉读：读到 level 才算真的连着（没接线时读到的是上下拉的电平）。 */
static int ffw_drive(int out, int in, int level)
{
    gpio_set_pull_mode(in, level ? GPIO_PULLDOWN_ONLY : GPIO_PULLUP_ONLY);
    gpio_set_level(out, level);
    esp_rom_delay_us(100);
    return gpio_get_level(in) == level;
}

static int ffw_loop(int out, int in)
{
    gpio_reset_pin(out);
    gpio_reset_pin(in);
    gpio_set_direction(out, GPIO_MODE_OUTPUT);
    gpio_set_direction(in, GPIO_MODE_INPUT);
    int ok = 1;
    for (int k = 0; k < 3; k++) {
        ok &= ffw_drive(out, in, 1);
        ok &= ffw_drive(out, in, 0);
    }
    gpio_reset_pin(out);
    gpio_reset_pin(in);
    return ok;
}
"""


def _i2c_case(profile: HardwareProfile, it: Item, d: Device) -> str:
    port = int(d.bus[3:])
    sda, scl = _bus_pins(profile, d.bus)
    return f"""
TEST_CASE("{it.name}", "[ffwire]")
{{
    i2c_master_bus_handle_t bus = ffw_bus({port}, {sda}, {scl});
    esp_err_t err = i2c_master_probe(bus, 0x{d.address:02X}, 50);
    ff_log("探测 {d.bus}（SDA=GPIO{sda}、SCL=GPIO{scl}）的 0x{d.address:02X}：%s", esp_err_to_name(err));
    i2c_del_master_bus(bus);
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, err, "器件没有应答：没接上、地址不对或总线引脚不对");
}}
"""


def _int_case(profile: HardwareProfile, it: Item, d: Device) -> str:
    v = d.int_probe.value
    port = int(d.bus[3:])
    sda, scl = _bus_pins(profile, d.bus)
    pin = it.gpios[0]
    active = 1 if v["active"] == "high" else 0
    pull = {"up": "GPIO_PULLUP_ONLY", "down": "GPIO_PULLDOWN_ONLY"}.get(v.get("pull", ""), "GPIO_FLOATING")
    pre = "".join(f"    ffw_rd(dev, 0x{r:02X}, &x);\n" for r in v.get("pre_clear", []))
    setup = "".join(f"    TEST_ASSERT_EQUAL(ESP_OK, ffw_wr(dev, 0x{r:02X}, 0x{val:02X}));\n" for r, val in v["setup"])
    off = "".join(f"    ffw_wr(dev, 0x{r:02X}, 0x{val:02X});\n" for r, val in v.get("off", []))
    note = d.int_probe.note.replace("*/", "")[:160]
    return f"""
/* {d.part}：{note} */
TEST_CASE("{it.name}", "[ffwire]")
{{
    i2c_master_bus_handle_t bus = ffw_bus({port}, {sda}, {scl});
    i2c_master_dev_handle_t dev = ffw_dev(bus, 0x{d.address:02X});
    uint8_t x = 0;
    gpio_reset_pin({pin});
    gpio_set_direction({pin}, GPIO_MODE_INPUT);
    gpio_set_pull_mode({pin}, {pull});
{pre}    ffw_rd(dev, 0x{v["clear"]:02X}, &x);
    esp_rom_delay_us(300);
    int before = gpio_get_level({pin});
{setup}    vTaskDelay(pdMS_TO_TICKS({v["wait_ms"]}));
    int during = gpio_get_level({pin});
    ffw_rd(dev, 0x{v["clear"]:02X}, &x);
    esp_rom_delay_us(300);
    int after = gpio_get_level({pin});
{off}    ff_log("INT 脚 GPIO{pin}：清除后 %d，产生中断后 %d，再清除后 %d（有效电平 {active}）", before, during, after);
    i2c_master_bus_rm_device(dev);
    i2c_del_master_bus(bus);
    gpio_reset_pin({pin});
    TEST_ASSERT_TRUE_MESSAGE(before != {active}, "还没产生中断 INT 脚就是有效电平：线可能短路或接错");
    TEST_ASSERT_TRUE_MESSAGE(during == {active}, "器件产生了中断，INT 脚却没变成有效电平：INT 线没接通");
}}
"""


def _loop_case(it: Item) -> str:
    a, b = it.gpios
    return f"""
TEST_CASE("{it.name}", "[ffwire]")
{{
    int ab = ffw_loop({a}, {b}), ba = ffw_loop({b}, {a});
    ff_log("GPIO{a}→GPIO{b}：%s；GPIO{b}→GPIO{a}：%s", ab ? "通" : "不通", ba ? "通" : "不通");
    TEST_ASSERT_TRUE_MESSAGE(ab && ba, "跳线不通：一端输出高低电平，另一端读不到");
}}
"""


def items_for_connections(profile: HardwareProfile, connections) -> list[Item]:
    """按项目的完整连线（embedded/wiring.py 的 Connection）生成自检项（第 134 条第 3 阶段）。
    profile 要用已经换成项目接线的档案（with_wiring），I2C 的总线引脚从里面取。look 类平台测不了，不生成。"""
    out: list[Item] = []
    devs = {d.name: d for d in profile.devices}
    by_sig = {c.signal: c for c in connections}
    done_loops: set[frozenset] = set()
    i2c_done: dict[str, Item] = {}
    for c in connections:
        e, k = c.expect or {}, (c.expect or {}).get("kind")
        if k == "i2c_ack" and e.get("device") in devs:
            d = devs[e["device"]]
            if d.name in i2c_done:                       # 同一个器件的 SDA、SCL 合成一项
                it = i2c_done[d.name]
                i2c_done[d.name] = Item(it.kind, it.name, it.device, it.gpios, it.signals + (c.signal,))
                out[out.index(it)] = i2c_done[d.name]
                continue
            if d.address is None or _bus_pins(profile, d.bus) is None:
                continue
            it = Item("i2c", f"{CASE_PREFIX}{d.name}（{d.part}）在 {d.bus} 的 0x{d.address:02X} 应答", d.name, (), (c.signal,))
            i2c_done[d.name] = it
            out.append(it)
        elif k == "loopback" and e.get("with") in by_sig:
            o = by_sig[e["with"]]
            pair = frozenset((c.gpio, o.gpio))
            if pair in done_loops:
                continue
            done_loops.add(pair)
            out.append(Item("loop", f"{CASE_PREFIX}GPIO{c.gpio}（{c.signal}）和 GPIO{o.gpio}（{o.signal}）之间连通",
                            gpios=(c.gpio, o.gpio), signals=(c.signal, o.signal)))
        elif k == "adc_mv":
            out.append(Item("adc", f"{CASE_PREFIX}{c.signal}（GPIO{c.gpio}）接 {c.to}，电压在 {e['min']}–{e['max']} mV",
                            gpios=(c.gpio,), signals=(c.signal,), params=(int(e["min"]), int(e["max"]))))
        elif k == "level":
            out.append(Item("level", f"{CASE_PREFIX}{c.signal}（GPIO{c.gpio}）接 {c.to}，电平是 {e['value']}",
                            gpios=(c.gpio,), signals=(c.signal,), params=(int(e["value"]),)))
    return out


_ADC_HEAD = """
#include "esp_adc/adc_oneshot.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"

/* 读 GPIO 上的电压（mV，8 次平均，用芯片支持的校准方案换算）；读不了返回 -1。 */
static int ffw_adc_mv(int gpio)
{
    adc_unit_t unit;
    adc_channel_t chan;
    if (adc_oneshot_io_to_channel(gpio, &unit, &chan) != ESP_OK) {
        ff_log("GPIO%d 不是 ADC 引脚", gpio);
        return -1;
    }
    adc_oneshot_unit_handle_t h = NULL;
    adc_oneshot_unit_init_cfg_t uc = {.unit_id = unit};
    if (adc_oneshot_new_unit(&uc, &h) != ESP_OK) {
        return -1;
    }
    adc_oneshot_chan_cfg_t cc = {.atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    adc_oneshot_config_channel(h, chan, &cc);
    adc_cali_handle_t cal = NULL;
    esp_err_t ce = ESP_FAIL;
#if ADC_CALI_SCHEME_CURVE_FITTING_SUPPORTED
    adc_cali_curve_fitting_config_t cf = {.unit_id = unit, .chan = chan, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ce = adc_cali_create_scheme_curve_fitting(&cf, &cal);
#elif ADC_CALI_SCHEME_LINE_FITTING_SUPPORTED
    adc_cali_line_fitting_config_t lf = {.unit_id = unit, .atten = ADC_ATTEN_DB_12, .bitwidth = ADC_BITWIDTH_DEFAULT};
    ce = adc_cali_create_scheme_line_fitting(&lf, &cal);
#endif
    long sum = 0;
    int n = 0;
    for (int i = 0; i < 8; i++) {
        int raw = 0, mv = 0;
        if (adc_oneshot_read(h, chan, &raw) != ESP_OK) {
            continue;
        }
        if (ce == ESP_OK && adc_cali_raw_to_voltage(cal, raw, &mv) == ESP_OK) {
            sum += mv;
        } else {
            sum += (long)raw * 3100 / 4095;          /* 没有校准时粗略换算 */
        }
        n++;
        esp_rom_delay_us(500);
    }
    adc_oneshot_del_unit(h);
    return n ? (int)(sum / n) : -1;
}
"""


def _adc_case(it: Item) -> str:
    g, (lo, hi) = it.gpios[0], it.params
    return f"""
TEST_CASE("{it.name}", "[ffwire]")
{{
    int mv = ffw_adc_mv({g});
    ff_log("ADC GPIO{g}：%d mV（应在 {lo}–{hi} mV）", mv);
    TEST_ASSERT_TRUE_MESSAGE(mv >= {lo} && mv <= {hi}, "电压不在设计说的范围：线没接、接错地方，或者脚上还连着别的线");
}}
"""


def _level_case(it: Item) -> str:
    g, (v,) = it.gpios[0], it.params
    pull = "GPIO_PULLDOWN_ONLY" if v else "GPIO_PULLUP_ONLY"           # 反着拉：读到 v 才说明外面真的接着
    return f"""
TEST_CASE("{it.name}", "[ffwire]")
{{
    gpio_reset_pin({g});
    gpio_set_direction({g}, GPIO_MODE_INPUT);
    gpio_set_pull_mode({g}, {pull});
    esp_rom_delay_us(200);
    int a = gpio_get_level({g});
    esp_rom_delay_us(2000);
    int b = gpio_get_level({g});
    gpio_reset_pin({g});
    ff_log("电平 GPIO{g}：%d、%d（应为 {v}）", a, b);
    TEST_ASSERT_TRUE_MESSAGE(a == {v} && b == {v}, "电平不对：线没接、接错地方，或者脚上还连着别的线");
}}
"""


def check_source(profile: HardwareProfile, items: list[Item]) -> str:
    devs = {d.name: d for d in profile.devices}
    parts = [_HEAD] + ([_ADC_HEAD] if any(it.kind == "adc" for it in items) else [])
    for it in items:
        if it.kind == "i2c":
            parts.append(_i2c_case(profile, it, devs[it.device]))
        elif it.kind == "int":
            parts.append(_int_case(profile, it, devs[it.device]))
        elif it.kind == "loop":
            parts.append(_loop_case(it))
        elif it.kind == "adc":
            parts.append(_adc_case(it))
        elif it.kind == "level":
            parts.append(_level_case(it))
    return "".join(parts)


def results_from(items: list[Item], board_result) -> WiringCheck:
    """把上板结果（target.harness.BoardTestResult）对应回自检项。没跑到的项记为没测出结果（ok=None）。"""
    if getattr(board_result, "skipped", ""):
        return WiringCheck(skipped=board_result.skipped)
    cases = {c.name.strip(): c for c in board_result.cases}
    logs = list(getattr(board_result, "logs", []))
    out = []
    for it in items:
        c = cases.get(it.name)
        detail = ""
        if c is not None and c.status == "fail":
            detail = re.sub(r"\s+", " ", c.message)[:120]
        hint = next((ln for ln in logs if (it.kind == "loop" and f"GPIO{it.gpios[0]}→" in ln)
                     or (it.kind == "int" and f"INT 脚 GPIO{it.gpios[0]}" in ln)
                     or (it.kind == "adc" and f"ADC GPIO{it.gpios[0]}：" in ln)
                     or (it.kind == "level" and f"电平 GPIO{it.gpios[0]}：" in ln)
                     or (it.kind == "i2c" and "探测" in ln and it.name.split("的 ")[-1].split(" ")[0] in ln)), "")
        out.append({"name": it.name, "kind": it.kind, "ok": None if c is None else c.status == "pass",
                    "detail": (hint[:120] if it.kind in ("adc", "level") and hint else detail or hint[:120]),
                    "signals": list(it.signals)})
    return WiringCheck(items=out)


def run_check(profile: HardwareProfile, items: list[Item], idf, board, build_dir: Path,
              log_path: Path | None = None) -> WiringCheck:
    """编译、烧录、跑接线自检。没有板子、编译失败都记为没法测（skipped），由调用方按"测不了"处理。"""
    from .esp_idf import DEFAULT_REQUIRES, IdfProject
    from .harness import TEST_REQUIRES, harness_files
    if not items:
        return WiringCheck(skipped="硬件档案里没有和这个模块有关的接线自检项")
    if idf is None or board is None:
        return WiringCheck(skipped="没有 ESP-IDF 或没有板子")
    extra = ("esp_adc",) if any(it.kind == "adc" for it in items) else ()
    proj = IdfProject(Path(build_dir), idf, target=str(profile.chip.value),
                      requires=DEFAULT_REQUIRES + TEST_REQUIRES + extra, whole_archive=True)
    proj.write(profile, {**harness_files(None, None, None, profile=profile), TEST_FILE: check_source(profile, items)})
    b = proj.build()
    if not b.ok:
        return WiringCheck(skipped="自检程序编译失败：" + ("\n".join(d.render() for d in b.errors) or b.log_tail[-300:])[:300])
    return results_from(items, board.run(proj, log_path))

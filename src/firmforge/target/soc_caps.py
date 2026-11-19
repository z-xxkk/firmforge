"""芯片能力从 ESP-IDF 自己的源码读（DECISIONS 第 114 条）：components/soc/<芯片>/include/soc/soc_caps.h。

读出：有哪些 GPIO（SOC_GPIO_VALID_GPIO_MASK）、哪些只能输入、有没有 USB 串口（SOC_USB_SERIAL_JTAG_SUPPORTED）、
有哪些外设（SOC_<外设>_SUPPORTED、SOC_I2C_NUM、SOC_UART_NUM……）。这样 ESP-IDF 支持的芯片平台都能用，
不用在代码里列芯片表。ESP-IDF 不在时退回内置的三种芯片（也是从 v6.1 的 soc_caps.h 抄下来的）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PERIPHERALS = ("gpio", "i2c", "uart", "spi", "ledc", "rmt", "pcnt", "gptimer", "adc", "dac", "twai", "i2s",
               "mcpwm", "usb_serial_jtag", "usb_otg", "sdmmc", "touch_sensor", "temp_sensor")


@dataclass(frozen=True)
class ChipCaps:
    target: str                                  # esp32c3、esp32p4……
    gpios: tuple[int, ...]                       # 有效的 GPIO
    input_only: tuple[int, ...] = ()             # 只能输入的 GPIO
    usb_serial_jtag: bool = False                # 芯片自带 USB 串口（控制台走它）
    peripherals: tuple[str, ...] = ()            # 有哪些外设（小写，和规则的 scope 写法一致）
    source: str = ""                             # 读自哪个文件；空表示内置表


# ESP-IDF 不在时用（照 v6.1 的 soc_caps.h）
BUILTIN = {
    "esp32c3": ChipCaps("esp32c3", tuple(range(22)), (), True,
                        ("gpio", "i2c", "uart", "spi", "ledc", "rmt", "gptimer", "adc", "twai", "i2s", "usb_serial_jtag",
                         "temp_sensor")),
    "esp32s3": ChipCaps("esp32s3", tuple(g for g in range(49) if g not in (22, 23, 24, 25)), (), True,
                        ("gpio", "i2c", "uart", "spi", "ledc", "rmt", "pcnt", "gptimer", "adc", "twai", "i2s", "mcpwm",
                         "usb_serial_jtag", "usb_otg", "sdmmc", "touch_sensor", "temp_sensor")),
    "esp32": ChipCaps("esp32", tuple(g for g in range(40) if g not in (24, 28, 29, 30, 31)), (34, 35, 36, 37, 38, 39),
                      False, ("gpio", "i2c", "uart", "spi", "ledc", "rmt", "pcnt", "gptimer", "adc", "dac", "twai", "i2s",
                              "mcpwm", "sdmmc", "touch_sensor")),
}

_DEFINE = re.compile(r"^\s*#define\s+(SOC_\w+)\s+(.+?)\s*(?://.*|/\*.*)?$", re.M)


def _eval_mask(expr: str, defs: dict[str, str]) -> int | None:
    """算 SOC_GPIO_VALID_GPIO_MASK 这类表达式：只认数字、BITn、已定义的宏和 & | ~ << >> + - ( )。
    ((1U<<SOC_GPIO_PIN_COUNT) - 1) 这种写法要有减号（C 系列、S31 都这么写）。"""
    for _ in range(5):                                   # 展开引用到的其他宏
        expr = re.sub(r"\bSOC_\w+\b", lambda m: f"({defs[m.group(0)]})" if m.group(0) in defs else m.group(0), expr)
    expr = re.sub(r"\bBIT(\d+)\b", r"(1<<\1)", expr)
    expr = re.sub(r"\b(0[xX][0-9a-fA-F]+|\d+)[uUlL]+\b", r"\1", expr)
    if not re.fullmatch(r"[0-9a-fA-FxX()\s&|~<>+\-]+", expr):
        return None
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))      # noqa: S307  只剩数字和位运算
    except Exception:                                          # noqa: BLE001
        return None


def caps_file(idf_root: Path, target: str) -> Path:
    return Path(idf_root) / "components" / "soc" / target / "include" / "soc" / "soc_caps.h"


def list_targets(idf_root: Path) -> list[str]:
    """这个 ESP-IDF 支持的芯片（有 soc_caps.h 的）。"""
    soc = Path(idf_root) / "components" / "soc"
    return sorted(p.name for p in soc.iterdir() if p.is_dir() and p.name != "linux" and caps_file(idf_root, p.name).is_file()) \
        if soc.is_dir() else []


def parse_caps(target: str, text: str, source: str = "") -> ChipCaps:
    defs = {k: v.strip() for k, v in _DEFINE.findall(text)}
    count = _eval_mask(defs.get("SOC_GPIO_PIN_COUNT", "0"), defs) or 0
    mask = _eval_mask(defs["SOC_GPIO_VALID_GPIO_MASK"], defs) if "SOC_GPIO_VALID_GPIO_MASK" in defs else (1 << count) - 1
    out_mask = _eval_mask(defs["SOC_GPIO_VALID_OUTPUT_GPIO_MASK"], defs) if "SOC_GPIO_VALID_OUTPUT_GPIO_MASK" in defs else mask
    mask = mask if mask is not None else (1 << count) - 1
    gpios = tuple(g for g in range(max(count, mask.bit_length())) if mask >> g & 1)
    input_only = tuple(g for g in gpios if out_mask is not None and not (out_mask >> g & 1))

    def on(name: str) -> bool:
        v = defs.get(name, "0").strip("() ")
        return v not in ("0", "") and not v.startswith("0U")
    found = ["gpio"] if gpios else []
    for p in PERIPHERALS[1:]:
        key = {"i2c": "SOC_I2C_NUM", "uart": "SOC_UART_NUM", "spi": "SOC_SPI_PERIPH_NUM",
               "sdmmc": "SOC_SDMMC_HOST_SUPPORTED"}.get(p)
        if (key and on(key)) or on(f"SOC_{p.upper()}_SUPPORTED"):
            found.append(p)
    return ChipCaps(target, gpios, input_only, on("SOC_USB_SERIAL_JTAG_SUPPORTED"), tuple(found), source)


def load_caps(target: str, idf_root: Path | None = None) -> ChipCaps | None:
    """先读 ESP-IDF 的 soc_caps.h；ESP-IDF 不在就用内置表；都没有返回 None。"""
    if idf_root is None:
        from .esp_idf import idf_path
        idf_root = idf_path()
    f = caps_file(idf_root, target)
    if f.is_file():
        return parse_caps(target, f.read_text(encoding="utf-8", errors="replace"), str(f))
    return BUILTIN.get(target)


def sdkconfig_defaults(caps: ChipCaps, flash_mb: int | None = None) -> str:
    """不在内置配置里的芯片：按读出的能力生成 sdkconfig.defaults（控制台走芯片自带 USB 串口或 UART0）。"""
    lines = [f"# 固件工坊按 ESP-IDF 的 soc_caps.h 生成：{caps.target}"]
    if flash_mb in (2, 4, 8, 16, 32):
        lines.append(f"CONFIG_ESPTOOLPY_FLASHSIZE_{flash_mb}MB=y")
    lines.append("CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y" if caps.usb_serial_jtag else "CONFIG_ESP_CONSOLE_UART_DEFAULT=y")
    lines.append("CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y")
    return "\n".join(lines) + "\n"


def chip_gpios(target: str) -> tuple[int, ...] | None:
    """芯片上有哪些 GPIO；不认识的芯片返回 None（不检查范围）。"""
    caps = load_caps(target)
    return caps.gpios if caps is not None else None

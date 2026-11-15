"""硬件档案：需求澄清的产出，之后每个 Agent 和判定器都读它（DECISIONS 第 66 条第 ① 步）。

每个字段都记录"这个值是怎么知道的"：用户说的、资料上写的、还是在板子上实测的。
仿制板的网页资料可信度低，关键项要靠实测确认（tasks_inbox/test_strategy_research.md 第 4 次调研）。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ORIGINS = ("user", "document", "measured", "default")      # 用户说的 / 资料 / 实测 / 默认值
_SIGNAL = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class ProfileError(ValueError):
    """硬件档案内容不合法。"""


@dataclass(frozen=True)
class Fact:
    value: object
    origin: str = "user"
    note: str = ""              # 例如资料出处、实测命令

    def __post_init__(self):
        if self.origin not in ORIGINS:
            raise ProfileError(f"未知的来源 {self.origin!r}，只能是 {', '.join(ORIGINS)}")


@dataclass(frozen=True)
class Device:
    """外接器件，例如 MPU6050。"""
    name: str                   # 代码和规则里用的名字，例如 imu
    part: str                   # 型号，例如 MPU6050
    bus: str                    # i2c0 / spi2 / uart1 / gpio
    address: int | None = None  # I2C 地址
    pins: dict[str, int] = field(default_factory=dict)      # 例如 {"int": 5}
    params: dict[str, Fact] = field(default_factory=dict)   # 器件参数，例如实测的 WHO_AM_I、温度换算系数（DECISIONS 第 89 条）
    # 软复位：{"reg": 寄存器, "value": 写入值, "wait_ms": 等待毫秒}。上板测试每个用例前用它复位器件（DECISIONS 第 96 条），
    # 不写进 hw_config.h
    reset: Fact | None = None
    category: str = ""          # 知识库目录里的类别，例如 运动传感器（DECISIONS 第 114 条）；不进提示词
    # 怎样让器件在 INT 脚上产生一次中断（接线自检用，第 124 条）：{"setup": [[寄存器, 值], ...], "active": "high"/"low",
    # "clear": 读哪个寄存器清除, "wait_ms": 等多久, "pull": "up"/"down"/""，"off": [[寄存器, 值], ...] 测完关掉}。不进提示词
    int_probe: Fact | None = None


@dataclass(frozen=True)
class HardwareProfile:
    chip: Fact                              # 例如 esp32s3
    chip_variant: Fact                      # 例如 ESP32-S3FH4R2
    board: Fact                             # 例如 esp32s3_supermini
    framework: Fact                         # 例如 esp-idf
    framework_version: Fact                 # 例如 v6.1
    pins: dict[str, Fact] = field(default_factory=dict)        # 信号 → GPIO，例如 "i2c0.sda" → 8
    reserved_pins: dict[int, str] = field(default_factory=dict)  # 不能用的 GPIO → 原因
    devices: tuple[Device, ...] = ()
    # 板子上用杜邦线连在一起的两个 GPIO（接线自检用，第 124 条）：({"a": 6, "b": 7, "note": ...}, ...)。
    # 用 GPIO 编号而不是信号名：线是焊在物理引脚上的，项目给信号起什么名字都不影响
    loopbacks: tuple[dict, ...] = ()

    def problems(self) -> list[str]:
        """自洽检查：同一个 GPIO 不能分给两个信号，也不能用保留引脚。"""
        out, used = [], {}
        for sig, fact in sorted(self.pins.items()):
            gpio = fact.value
            if not isinstance(gpio, int):
                out.append(f"{sig} 的引脚不是整数：{gpio!r}")
                continue
            if gpio in self.reserved_pins:
                out.append(f"{sig} 用了保留引脚 GPIO{gpio}（{self.reserved_pins[gpio]}）")
            if gpio in used:
                out.append(f"GPIO{gpio} 同时分给了 {used[gpio]} 和 {sig}")
            used.setdefault(gpio, sig)
        for d in self.devices:
            if d.reset is None:
                continue
            v = d.reset.value
            if not (isinstance(v, dict) and all(isinstance(v.get(k), int) and not isinstance(v.get(k), bool)
                                                for k in ("reg", "value", "wait_ms"))):
                out.append(f"外接器件 {d.name} 的 reset 要写成 {{\"reg\": 整数, \"value\": 整数, \"wait_ms\": 整数}}")
            elif d.address is None or not d.bus.startswith("i2c"):
                out.append(f"外接器件 {d.name} 有 reset，但不是有地址的 I2C 器件，测试程序没法复位它")
            elif not {f"{d.bus}.sda", f"{d.bus}.scl"} <= set(self.pins):
                out.append(f"外接器件 {d.name} 有 reset，但档案里没有 {d.bus}.sda / {d.bus}.scl 引脚")
        for lb in self.loopbacks:
            ends = [lb.get("a"), lb.get("b")] if isinstance(lb, dict) else []
            if len(ends) != 2 or not all(isinstance(g, int) and not isinstance(g, bool) for g in ends) or ends[0] == ends[1]:
                out.append(f"回环 {lb!r} 要写成 {{\"a\": GPIO, \"b\": GPIO}}（两个不同的 GPIO 编号）")
            elif any(g in self.reserved_pins for g in ends):
                out.append(f"回环 GPIO{ends[0]}–GPIO{ends[1]} 用了保留引脚")
        for d in self.devices:
            if d.int_probe is None:
                continue
            v = d.int_probe.value
            ok = (isinstance(v, dict) and v.get("active") in ("high", "low") and isinstance(v.get("clear"), int)
                  and isinstance(v.get("wait_ms"), int) and isinstance(v.get("setup"), list)
                  and all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, int) for y in x)
                          for x in v["setup"] + v.get("off", [])))
            if not ok:
                out.append(f"外接器件 {d.name} 的 int_probe 要写成 {{\"setup\": [[寄存器, 值]], \"active\": \"high\"/\"low\", "
                           f"\"clear\": 寄存器, \"wait_ms\": 毫秒}}")
            elif "int" not in d.pins or d.address is None or not d.bus.startswith("i2c"):
                out.append(f"外接器件 {d.name} 有 int_probe，但不是带 INT 脚的 I2C 器件")
        names = [d.name for d in self.devices]
        for n in sorted({n for n in names if names.count(n) > 1}):
            out.append(f"外接器件重名：{n}")
        return out

    def scopes(self) -> set[str]:
        """用到的外设和器件：i2c0.sda → i2c；pwm.out → ledc；imu.int、led 这类普通引脚 → gpio；MPU6050 → mpu6050。
        规则的适用范围（scope）用这些名字。"""
        out = set()
        for sig in self.pins:
            out.add(_peripheral(sig.split(".")[0]))
        for d in self.devices:
            out.add(d.part.lower())
            out.add(_peripheral(d.bus))
        return out

    def summary_lines(self) -> list[str]:
        """拼进 system prompt 的硬件档案（程序生成，不经模型改写，DECISIONS 第 67 条）。"""
        lines = [f"芯片：{self.chip.value}（{self.chip_variant.value}）",
                 f"板子：{self.board.value}",
                 f"开发框架：{self.framework.value} {self.framework_version.value}"]
        if self.pins:
            lines.append("引脚：" + "，".join(f"{s}=GPIO{f.value}" for s, f in sorted(self.pins.items())))
        if self.reserved_pins:
            by_reason: dict[str, list[int]] = {}
            for g, why in sorted(self.reserved_pins.items()):
                by_reason.setdefault(why, []).append(g)
            lines.append("不能用的引脚：" + "；".join(f"{_gpio_ranges(gs)}（{why}）" for why, gs in by_reason.items()))
        for d in self.devices:
            addr = f"，地址 0x{d.address:02X}" if d.address is not None else ""
            lines.append(f"外接器件 {d.name}：{d.part}，总线 {d.bus}{addr}")
            for k, f in sorted(d.params.items()):
                v = f"0x{f.value:02X}" if isinstance(f.value, int) and not isinstance(f.value, bool) else f.value
                note = f"；{f.note}" if f.note else ""
                lines.append(f"  {d.name}.{k} = {v}（{_ORIGIN_ZH[f.origin]}{note}，宏 {param_macro(d.name, k)}）")
        return lines

    def with_wiring(self, wiring: dict[str, int], note: str = "本项目的接线") -> "HardwareProfile":
        """换成某个项目的接线（DECISIONS 第 108 条）：引脚只留这个项目用到的信号；芯片、保留引脚、器件不变。"""
        from dataclasses import replace
        return replace(self, pins={s: Fact(int(g), "user", note) for s, g in sorted(wiring.items())})

    def wiring_problems(self, wiring, devices_used: set[str] | None = None) -> list[str]:
        """检查一套项目接线：信号名、GPIO 在芯片上存在、不用保留引脚、不重复；用到的外接器件的总线引脚要写上。
        devices_used：项目用到的外接器件名（Device.name）。"""
        if not isinstance(wiring, dict):
            return ["wiring 要写成 {\"信号\": GPIO 编号}，例如 {\"i2c0.sda\": 4}"]
        out, used = [], {}
        from ..target.soc_caps import chip_gpios          # 芯片有哪些 GPIO 从 ESP-IDF 源码读（DECISIONS 第 114 条）
        gpios = chip_gpios(str(self.chip.value))
        for sig, g in sorted(wiring.items()):
            if not isinstance(sig, str) or not _SIGNAL.match(sig):
                out.append(f"信号名 {sig!r} 要写成\"外设或器件.引脚\"，小写，例如 i2c0.sda、imu.int")
                continue
            if isinstance(g, bool) or not isinstance(g, int):
                out.append(f"{sig} 的引脚要写成 GPIO 编号（整数），现在是 {g!r}")
                continue
            if gpios is not None and g not in gpios:
                out.append(f"{sig}：{self.chip.value} 没有 GPIO{g}")
            if g in self.reserved_pins:
                out.append(f"{sig} 用了不能用的 GPIO{g}（{self.reserved_pins[g]}）")
            if g in used:
                out.append(f"GPIO{g} 同时分给了 {used[g]} 和 {sig}")
            used.setdefault(g, sig)
        for d in self.devices:
            if devices_used is not None and d.name not in devices_used:
                continue
            if d.bus.startswith("i2c") and not {f"{d.bus}.sda", f"{d.bus}.scl"} <= set(wiring):
                out.append(f"用到了外接器件 {d.name}（{d.part}），接线里要写上 {d.bus}.sda 和 {d.bus}.scl")
        return out

    def to_dict(self) -> dict:
        d = asdict(self)
        d["reserved_pins"] = {str(k): v for k, v in self.reserved_pins.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "HardwareProfile":
        try:
            fact = lambda x: Fact(**x) if isinstance(x, dict) else Fact(x)       # noqa: E731
            return cls(
                chip=fact(d["chip"]), chip_variant=fact(d["chip_variant"]), board=fact(d["board"]),
                framework=fact(d["framework"]), framework_version=fact(d["framework_version"]),
                pins={k: fact(v) for k, v in d.get("pins", {}).items()},
                reserved_pins={int(k): v for k, v in d.get("reserved_pins", {}).items()},
                devices=tuple(Device(**{**x, "params": {k: fact(v) for k, v in x.get("params", {}).items()},
                                        "reset": fact(x["reset"]) if x.get("reset") is not None else None,
                                        "int_probe": fact(x["int_probe"]) if x.get("int_probe") is not None else None})
                              for x in d.get("devices", [])),
                loopbacks=tuple(dict(x) for x in d.get("loopbacks", [])))
        except (KeyError, TypeError) as exc:
            raise ProfileError(f"硬件档案缺字段或字段不对：{exc}") from exc

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "HardwareProfile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


_ORIGIN_ZH = {"user": "用户提供", "document": "资料", "measured": "实测", "default": "默认值"}


def param_macro(device: str, key: str) -> str:
    """器件参数在 hw_config.h 里的宏名：imu + temp_offset_c → FF_IMU_TEMP_OFFSET_C。"""
    return "FF_" + re.sub(r"[^A-Za-z0-9]", "_", f"{device}_{key}").upper()


def _gpio_ranges(gpios: list[int]) -> str:
    """[26, 27, 28, 43] → GPIO26–28、GPIO43（连续的合并成一段，省 token）。"""
    out, start = [], None
    for i, g in enumerate(gpios):
        if start is None:
            start = g
        if i + 1 == len(gpios) or gpios[i + 1] != g + 1:
            out.append(f"GPIO{start}" if start == g else f"GPIO{start}–{g}")
            start = None
    return "、".join(out)


# ESP32 的外设名；pwm 在 ESP32 上由 LEDC 外设产生；其余名字（器件的中断脚、led 等）都是普通 GPIO
_PERIPHERALS = {"i2c", "uart", "spi", "ledc", "adc", "dac", "i2s", "twai", "rmt", "pcnt", "usb", "gpio"}
_ALIASES = {"pwm": "ledc", "can": "twai"}


def _peripheral(name: str) -> str:
    base = re.sub(r"\d+$", "", name.lower())
    base = _ALIASES.get(base, base)
    return base if base in _PERIPHERALS else "gpio"


"""硬件档案：需求澄清的产出，之后每个 Agent 和判定器都读它（DECISIONS 第 66 条第 ① 步）。

每个字段都记录"这个值是怎么知道的"：用户说的、资料上写的、还是在板子上实测的。
仿制板的网页资料可信度低，关键项要靠实测确认（tasks_inbox/test_strategy_research.md 第 4 次调研）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ORIGINS = ("user", "document", "measured", "default")      # 用户说的 / 资料 / 实测 / 默认值


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
        names = [d.name for d in self.devices]
        for n in sorted({n for n in names if names.count(n) > 1}):
            out.append(f"外接器件重名：{n}")
        return out

    def summary_lines(self) -> list[str]:
        """拼进 system prompt 的硬件档案（程序生成，不经模型改写，DECISIONS 第 67 条）。"""
        lines = [f"芯片：{self.chip.value}（{self.chip_variant.value}）",
                 f"板子：{self.board.value}",
                 f"开发框架：{self.framework.value} {self.framework_version.value}"]
        if self.pins:
            lines.append("引脚：" + "，".join(f"{s}=GPIO{f.value}" for s, f in sorted(self.pins.items())))
        if self.reserved_pins:
            lines.append("不能用的引脚：" + "，".join(f"GPIO{g}（{why}）" for g, why in sorted(self.reserved_pins.items())))
        for d in self.devices:
            addr = f"，地址 0x{d.address:02X}" if d.address is not None else ""
            lines.append(f"外接器件 {d.name}：{d.part}，总线 {d.bus}{addr}")
        return lines

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
                devices=tuple(Device(**x) for x in d.get("devices", [])))
        except (KeyError, TypeError) as exc:
            raise ProfileError(f"硬件档案缺字段或字段不对：{exc}") from exc

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "HardwareProfile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

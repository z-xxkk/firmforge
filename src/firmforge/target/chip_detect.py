"""插上板子时识别芯片（DECISIONS 第 114 条）：用 ESP-IDF 自带的 esptool 读芯片型号和 Flash 大小。

是实测，不是猜：和硬件档案写的芯片对不上时，不往错的芯片上烧，直接告诉用户。
识别不了（串口被占用、板子没进下载模式）不下结论，照旧烧录。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

DETECT_TIMEOUT_S = 30


@dataclass(frozen=True)
class ChipInfo:
    target: str = ""           # esp32c3（和 ESP-IDF 的 target 名一致）；空表示没识别出来
    description: str = ""      # ESP32-C3 (QFN32) (revision v0.4)
    features: str = ""
    flash_mb: int | None = None
    port: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.target)


def chip_target(name: str) -> str:
    """esptool 的芯片名（ESP32-C3）→ ESP-IDF 的 target 名（esp32c3）。"""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_esptool(text: str, port: str = "") -> ChipInfo:
    m = re.search(r"^Connected to (\S+) on", text, re.M) or re.search(r"^Chip is (ESP[\w-]+)", text, re.M)
    if not m:
        tail = "\n".join(line for line in text.strip().splitlines()[-3:])
        return ChipInfo(port=port, error=tail or "esptool 没有输出")
    desc = re.search(r"^Chip type:\s+(.+)$", text, re.M)
    feat = re.search(r"^Features:\s+(.+)$", text, re.M)
    size = re.search(r"^Detected flash size:\s*(\d+)\s*MB", text, re.M)
    return ChipInfo(chip_target(m.group(1)), desc.group(1).strip() if desc else m.group(1),
                    feat.group(1).strip() if feat else "", int(size.group(1)) if size else None, port)


def detect_chip(python: str, port: str, timeout: float = DETECT_TIMEOUT_S) -> ChipInfo:
    """python：ESP-IDF 环境里的 Python（带 esptool）。"""
    try:
        r = subprocess.run([python, "-m", "esptool", "--port", port, "flash-id"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return ChipInfo(port=port, error=f"识别芯片超过 {timeout:.0f} 秒")
    except OSError as exc:
        return ChipInfo(port=port, error=f"运行 esptool 失败：{exc}")
    return parse_esptool(r.stdout + "\n" + r.stderr, port)


def mismatch(info: ChipInfo, expected: str) -> str:
    """识别出的芯片和硬件档案对不上时返回说明；对得上或没识别出来返回空串。"""
    if not info.ok or not expected or info.target == chip_target(expected):
        return ""
    return (f"板子上的芯片是 {info.description}（{info.target}），硬件档案写的是 {expected}："
            f"不往这块板子上烧。换成 {expected} 的板子，或者换成这块板子的硬件档案")

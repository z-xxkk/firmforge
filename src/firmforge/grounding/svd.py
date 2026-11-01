"""读 CMSIS-SVD 寄存器描述文件（标准库 xml.etree），供规则判定器第 3 步核对寄存器类规则。

乐鑫的 SVD 官方注明"还在完善中，可能缺外设或寄存器"（DECISIONS 第 76 条），
所以 SVD 里找不到时结论是"无法核对"，不是"规则错了"；只有找到了却对不上才判错。
"""

from __future__ import annotations

import gzip
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FieldInfo:
    peripheral: str
    register: str
    name: str
    lsb: int
    width: int
    access: str = ""

    def fits(self, value: int) -> bool:
        return 0 <= value < (1 << self.width)


def _int(text: str | None, default: int = 0) -> int:
    if text is None:
        return default
    t = text.strip().lower()
    if t.startswith("#"):
        return int(t[1:].replace("x", "0"), 2)
    return int(t, 0)


class SvdDevice:
    def __init__(self, name: str, fields: dict[tuple[str, str, str], FieldInfo], registers: set[tuple[str, str]],
                 peripherals: set[str], addresses: dict[tuple[str, str], int] | None = None):
        self.name = name
        self.fields = fields
        self.registers = registers
        self.peripherals = peripherals
        self.addresses = addresses or {}        # (外设, 寄存器) → 绝对地址，供上板测试读寄存器

    @classmethod
    def parse(cls, xml_text: str) -> "SvdDevice":
        root = ET.fromstring(xml_text)
        periph_nodes = {p.findtext("name"): p for p in root.iter("peripheral")}
        fields: dict[tuple[str, str, str], FieldInfo] = {}
        registers: set[tuple[str, str]] = set()
        addresses: dict[tuple[str, str], int] = {}
        for pname, p in periph_nodes.items():
            base_addr = _int(p.findtext("baseAddress"))
            src = p
            base = p.get("derivedFrom")
            if base and base in periph_nodes and p.find("registers") is None:
                src = periph_nodes[base]                # 派生外设沿用基础外设的寄存器
            for r in src.iter("register"):
                rname = r.findtext("name") or ""
                registers.add((pname, rname))
                addresses[(pname, rname)] = base_addr + _int(r.findtext("addressOffset"))
                for f in r.iter("field"):
                    fname = f.findtext("name") or ""
                    if f.find("bitOffset") is not None:
                        lsb, width = _int(f.findtext("bitOffset")), _int(f.findtext("bitWidth"), 1)
                    elif f.find("lsb") is not None:
                        lsb = _int(f.findtext("lsb"))
                        width = _int(f.findtext("msb")) - lsb + 1
                    else:                               # bitRange 形如 [7:4]
                        rng = (f.findtext("bitRange") or "[0:0]").strip("[]").split(":")
                        lsb = int(rng[1])
                        width = int(rng[0]) - lsb + 1
                    access = f.findtext("access") or r.findtext("access") or ""
                    fields[(pname, rname, fname)] = FieldInfo(pname, rname, fname, lsb, width, access)
        return cls(root.findtext("name") or "", fields, registers, set(periph_nodes), addresses)

    @classmethod
    def load(cls, path: Path) -> "SvdDevice":
        path = Path(path)
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        return cls.parse(data.decode("utf-8", errors="replace"))

    def check_field(self, peripheral: str, register: str, field: str, value: int) -> tuple[str, str]:
        """返回 (结论, 说明)。结论：ok / wrong / unknown（SVD 里没有，无法核对）。"""
        if peripheral not in self.peripherals:
            return "unknown", f"SVD 里没有外设 {peripheral}"
        if (peripheral, register) not in self.registers:
            return "unknown", f"SVD 里 {peripheral} 没有寄存器 {register}"
        info = self.fields.get((peripheral, register, field))
        if info is None:
            return "unknown", f"SVD 里 {peripheral}.{register} 没有字段 {field}"
        if not isinstance(value, int) or not info.fits(value):
            return "wrong", f"{peripheral}.{register}.{field} 只有 {info.width} 位，放不下 {value!r}"
        if info.access == "read-only":
            return "wrong", f"{peripheral}.{register}.{field} 是只读字段，不能要求写入某个值"
        return "ok", f"{peripheral}.{register}.{field} 位 {info.lsb}，宽 {info.width}"

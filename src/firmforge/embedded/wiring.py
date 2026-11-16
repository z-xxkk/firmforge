"""项目的完整接线（DECISIONS 第 134 条，ARCHITECTURE §15.2）：设计 Agent 交的每根线都写明另一端接什么、为什么、
自检应该测到什么。以前只有"信号 → GPIO 编号"，另一端没地方写，平台还把这个计划当成接好的事实传给后面的 Agent。

平台检查（不过就退回设计 Agent）：
- 每条都有 signal、gpio、to、why、expect；GPIO 存在、不是保留引脚、不重复（沿用 HardwareProfile.wiring_problems）；
- to 指向存在的东西：电源脚（3V3、GND、5V）、器件的脚（imu.SDA，器件要在档案里）、本项目的另一个信号（回环）、
  或者写成"元件：……"（手头的元件，器件清单在第 2 阶段补上后再核对）；
- expect 是平台自检能测的几种之一，参数写全；
- 不能占用板上现有的接线（评测题用的跳线、器件的线）去接别的东西：GPIO1 现在和 GPIO0 之间有一根跳线，
  拿它读 ADC 读到的是 GPIO0 的状态（对话测试里 adc_reader 就是这样一直读到乱跳的电压）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..grounding.profile import HardwareProfile

POWER = ("3V3", "GND", "5V")
EXPECT_KINDS = {
    "i2c_ack": "I2C 器件应答（device：器件名）",
    "loopback": "和本项目另一个信号的 GPIO 通断（with：那个信号名）",
    "adc_mv": "ADC 读到的电压范围（min、max，单位 mV，0–3600）",
    "level": "静态电平（value：0 或 1，内部上下拉反着拉也读到它）",
    "look": "平台测不了（例如只接一个 LED），由你接好后确认",
}


@dataclass(frozen=True)
class Connection:
    signal: str
    gpio: int
    to: str
    why: str = ""
    expect: dict = field(default_factory=dict)
    unplug: str = ""                 # 这个 GPIO 板上原来接着别的线，要先拔掉（平台检查时填上原来那根线的说明）

    def to_dict(self) -> dict:
        d = {"signal": self.signal, "gpio": self.gpio, "to": self.to, "why": self.why, "expect": dict(self.expect)}
        if self.unplug:
            d["unplug"] = self.unplug
        return d

    def line(self) -> str:
        """给人和 Agent 看的一行：adc.vin：GPIO3 → 3V3（读一个已知电压）。"""
        return (f"{self.signal}：GPIO{self.gpio} → {self.to}" + (f"（{self.why}）" if self.why else "")
                + (f"；先拔掉原来的线：{self.unplug}" if self.unplug else ""))


def board_wiring(profile: HardwareProfile) -> dict[int, str]:
    """板上现在已经接着的线（评测题用的那套）：GPIO → 说明。档案里写的器件引脚和跳线。"""
    out: dict[int, str] = {}
    for sig, f in profile.pins.items():
        out[int(f.value)] = f"{sig}" + (f"（{f.note}）" if f.note else "")
    for lb in profile.loopbacks:
        if isinstance(lb, dict) and isinstance(lb.get("a"), int) and isinstance(lb.get("b"), int):
            note = lb.get("note") or "杜邦线"
            out[lb["a"]] = f"跳线连着 GPIO{lb['b']}（{note}）"
            out[lb["b"]] = f"跳线连着 GPIO{lb['a']}（{note}）"
    return out


def board_wiring_lines(profile: HardwareProfile) -> list[str]:
    return [f"GPIO{g}：{d}" for g, d in sorted(board_wiring(profile).items())]


def _loop_pairs(profile: HardwareProfile) -> set[frozenset]:
    return {frozenset((lb["a"], lb["b"])) for lb in profile.loopbacks
            if isinstance(lb, dict) and isinstance(lb.get("a"), int) and isinstance(lb.get("b"), int)}


def parse_connections(raw, profile: HardwareProfile, devices_used: set[str] | None = None
                      ) -> tuple[list[Connection], list[str]]:
    """设计 Agent 交的 wiring → 连线列表和问题。旧格式 {"信号": GPIO} 退回让它补全。"""
    if isinstance(raw, dict):
        return [], ['wiring 要写成连线列表，每根线写明另一端接什么、为什么、自检应测到什么，例如 '
                    '[{"signal": "adc.vin", "gpio": 3, "to": "3V3", "why": "读已知电压", '
                    '"expect": {"kind": "adc_mv", "min": 3000, "max": 3400}}]']
    if not isinstance(raw, list):
        return [], ["wiring 要写成连线列表"]
    conns, problems = [], []
    raw_unplug = {d.get("signal"): d.get("unplug") for d in raw if isinstance(d, dict)}
    for i, d in enumerate(raw, 1):
        tag = f"第 {i} 根线"
        if not isinstance(d, dict):
            problems.append(f"{tag} 要写成对象")
            continue
        sig, g, to = d.get("signal"), d.get("gpio"), d.get("to")
        if isinstance(sig, str):
            tag = f"{tag}（{sig}）"
        if not isinstance(to, str) or not to.strip():
            problems.append(f"{tag}：要写 to——另一端接到哪里（3V3、GND、器件的脚如 imu.SDA、另一个信号、或\"元件：电位器中间脚\"）")
        if not isinstance(d.get("why"), str) or not d["why"].strip():
            problems.append(f"{tag}：要写 why——为什么这么接")
        exp = d.get("expect")
        if not isinstance(exp, dict) or exp.get("kind") not in EXPECT_KINDS:
            problems.append(f"{tag}：要写 expect——自检应该测到什么，kind 只能是 "
                            + "；".join(f"{k}：{v}" for k, v in EXPECT_KINDS.items()))
            exp = {}
        conns.append(Connection(sig if isinstance(sig, str) else "", g if isinstance(g, int) and not isinstance(g, bool) else -1,
                                to.strip() if isinstance(to, str) else "", str(d.get("why") or "").strip(), dict(exp)))
    problems += profile.wiring_problems({c.signal: (c.gpio if c.gpio >= 0 else None) for c in conns if c.signal},
                                        devices_used)
    signals = {c.signal for c in conns}
    dev_names = {dv.name for dv in profile.devices}
    occupied = board_wiring(profile)
    loops = _loop_pairs(profile)
    by_sig = {c.signal: c for c in conns}
    for c in list(conns):
        tag = f"{c.signal or '某根线'}"
        if c.to:
            head = c.to.split(".", 1)[0]
            ok = (c.to in POWER or c.to in signals or c.to.startswith(("元件：", "元件:"))
                  or (head in dev_names and "." in c.to))
            if not ok:
                problems.append(f"{tag}：to = {c.to!r} 不知道是什么——写 3V3、GND、5V，器件的脚（"
                                + "、".join(f"{n}.引脚名" for n in sorted(dev_names)) + "），本项目的另一个信号，或\"元件：……\"")
        e = c.expect
        k = e.get("kind")
        if k == "i2c_ack" and e.get("device") not in dev_names:
            problems.append(f"{tag}：i2c_ack 要写 device，是档案里的器件之一：{'、'.join(sorted(dev_names))}")
        if k == "loopback" and e.get("with") not in signals - {c.signal}:
            problems.append(f"{tag}：loopback 要写 with，是本项目另一个信号的名字")
        if k == "adc_mv":
            lo, hi = e.get("min"), e.get("max")
            if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)) or not 0 <= lo < hi <= 3600:
                problems.append(f"{tag}：adc_mv 要写整数 min、max（mV，0 ≤ min < max ≤ 3600）")
        if k == "level" and e.get("value") not in (0, 1):
            problems.append(f"{tag}：level 要写 value，0 或 1")
        if c.gpio in occupied and c.gpio >= 0:
            same_signal = profile.pins.get(c.signal) is not None and int(profile.pins[c.signal].value) == c.gpio
            partner = by_sig.get(c.to) if c.to in signals else None
            same_loop = partner is not None and frozenset((c.gpio, partner.gpio)) in loops
            if not (same_signal or same_loop) and raw_unplug.get(c.signal) is not True:
                problems.append(f"{tag}：GPIO{c.gpio} 板上现在接着 {occupied[c.gpio]}，不能直接拿来接 {c.to or '别的东西'}；"
                                "换一个空着的 GPIO（沿用同一个信号可以）；实在没有合适的脚，就在这根线里写 \"unplug\": true，"
                                "接线清单会让负责人先拔掉原来那根线")
            elif not (same_signal or same_loop):
                conns[conns.index(c)] = replace(c, unplug=occupied[c.gpio])
    return conns, problems


def connections_from_any(raw) -> list[Connection]:
    """从 wiring.json、上一次的设计里读回连线（不检查）；旧格式 {"信号": GPIO} 读成另一端未知的连线。"""
    if isinstance(raw, dict):
        return [Connection(s, int(g), "") for s, g in raw.items() if isinstance(g, int)]
    out = []
    for d in raw or []:
        if isinstance(d, dict) and isinstance(d.get("signal"), str) and isinstance(d.get("gpio"), int):
            out.append(Connection(d["signal"], d["gpio"], str(d.get("to") or ""), str(d.get("why") or ""),
                                  dict(d.get("expect") or {})))
    return out

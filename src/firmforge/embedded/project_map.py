"""工程图和接线图的数据（DECISIONS 第 109、113 条）：从一次上板流程的运行记录算出来，只用能保证准确的来源。

| 内容 | 来源 | 为什么能保证 |
|---|---|---|
| 模块、层级、接口原型、依赖 | design.json | 设计检查强制（层级、依赖、函数原型），头文件锁定 |
| 层间连线上的函数名 | 上层模块最后一次交的代码 | 去掉注释和字符串后字面出现的下层接口名（"用到"，不是"调用"） |
| 模块用到的外设、引脚、器件 | 代码里的 FF_PIN_*、FF_<器件>_ADDR、器件参数宏 | 合规检查禁止直接写引脚数字，只能用宏 |
| 接线、器件是否应答 | wiring.json | 设计给出并经检查的接线；上板时用例前复位器件有没有应答 |
| 测试结果、没过的用例用到的接口 | 串口日志 / 电脑测试报告，测试代码 | 结果来自真板或真跑；"用到"同样是字面扫描 |

不推断数据流向、不解释代码在做什么、不从源码推断寄存器写入（第 109 条舍弃）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..grounding.compliance import strip_c
from ..grounding.profile import CHIP_GPIOS, HardwareProfile, param_macro
from ..orchestrator.dag import declared_functions
from ..target.harness import parse_board_output

_PIN_MACRO = re.compile(r"\bFF_PIN_([A-Z0-9_]+)\b")
_RUN_TEST = re.compile(r"\bRUN_TEST\s*\(\s*(\w+)\s*\)")


@dataclass
class MapModule:
    id: str
    title: str
    layer: str                                   # hardware / logic
    interfaces: list[str]                        # 设计给出的函数原型
    names: list[str]                             # 接口函数名
    status: str = "pending"                      # pending / pass / fail / skipped
    result: str = "还没检查"                      # "上板 3/5"、"电脑上测 4/4"、"编译没过"……
    attempt: int = 0
    failing_uses: list[str] = field(default_factory=list)     # 没过的用例里用到的本模块接口
    failed_cases: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)          # 代码里用到的引脚信号（i2c0.sda……）
    devices: list[str] = field(default_factory=list)          # 代码里用到的外接器件（imu……）
    used_by: list[str] = field(default_factory=list)          # 哪些模块用到了它


@dataclass
class MapEdge:
    src: str                                     # 上层模块
    dst: str                                     # 被依赖的下层模块
    uses: list[str] = field(default_factory=list)            # src 代码里字面用到的 dst 接口


@dataclass
class MapPeripheral:
    id: str                                      # 信号前缀：i2c0、uart1、pwm……
    pins: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class MapDevice:
    name: str
    part: str
    bus: str
    address: int | None
    pins: list[tuple[str, int]] = field(default_factory=list)  # 器件自己的引脚（imu.int……）
    checked: bool | None = None                  # None：还没实测；True：应答了；False：没应答
    why: str = ""


@dataclass
class ProjectMap:
    modules: list[MapModule]
    edges: list[MapEdge]
    peripherals: list[MapPeripheral]
    devices: list[MapDevice]
    wiring_source: str = "default"               # design：设计定的；default：硬件档案的默认接线
    pins: dict[str, int] = field(default_factory=dict)
    unused_gpios: list[int] = field(default_factory=list)
    reserved: dict[int, str] = field(default_factory=dict)
    board: str = ""
    chip: str = ""

    @property
    def by_id(self) -> dict[str, MapModule]:
        return {m.id: m for m in self.modules}

    def module_links(self, mid: str) -> tuple[list[str], list[str]]:
        """模块用到的（外设前缀，器件名）。"""
        m = self.by_id.get(mid)
        if m is None:
            return [], []
        dev_names = {d.name for d in self.devices}
        periph = sorted({s.split(".", 1)[0] for s in m.signals} - dev_names)
        devs = sorted(set(m.devices) | ({s.split(".", 1)[0] for s in m.signals} & dev_names))
        return periph, devs

    def summary(self) -> dict:
        tested = [m for m in self.modules if m.status in ("pass", "fail")]
        return {"modules": len(self.modules), "passed": sum(1 for m in tested if m.status == "pass"),
                "tested": len(tested), "pins": len(self.pins), "devices": len(self.devices)}


# ---------------------------------------------------------------- 读运行记录

def _attempts(run_dir: Path, mid: str) -> list[Path]:
    d = Path(run_dir) / "nodes" / mid
    if not d.is_dir():
        return []
    return sorted((p for p in d.glob("attempt_*") if p.is_dir()), key=lambda p: int(p.name.split("_")[-1]))


def latest_files(run_dir: Path, mid: str) -> dict[str, str]:
    """模块最后一次交上来的文件（main/ 下的相对路径 → 内容）；还没交就是空的。"""
    for a in reversed(_attempts(run_dir, mid)):
        main = a / "main"
        if main.is_dir() and any(main.rglob("*")):
            return {f.relative_to(main).as_posix(): f.read_text(encoding="utf-8", errors="replace")
                    for f in sorted(main.rglob("*")) if f.is_file()}
    return {}


def _prototype(header: str, name: str) -> str:
    """头文件里这个函数的声明（压成一行，去掉预处理行和 extern "C" 包装）；找不到就是空字符串。"""
    body = "\n".join(ln for ln in strip_c(header).splitlines() if not ln.lstrip().startswith("#"))
    body = re.sub(r'extern\s*""\s*\{|\}', " ", body)      # strip_c 把 "C" 变成了 ""
    for stmt in body.split(";"):
        one = " ".join(stmt.split())
        if re.search(rf"\b{re.escape(name)}\s*\(", one) and not one.startswith("typedef"):
            return one + ";"
    return ""


def _uses(text: str, names: list[str]) -> list[str]:
    code = strip_c(text)
    return [n for n in names if re.search(rf"\b{re.escape(n)}\b", code)]


def _case_bodies(test: str) -> dict[str, str]:
    """测试文件里每个用例的代码：Unity 上板用例 TEST_CASE("名字", ...) { ... }；电脑上的用例是 RUN_TEST 登记的函数。"""
    out: dict[str, str] = {}
    code = test
    for m in re.finditer(r'TEST_CASE\s*\(\s*"((?:[^"\\]|\\.)*)"[^)]*\)\s*\{', code):
        out[m.group(1)] = _block(code, m.end() - 1)
    for name in _RUN_TEST.findall(code):
        m = re.search(rf"\b(?:static\s+)?void\s+{re.escape(name)}\s*\(\s*(?:void)?\s*\)\s*\{{", code)
        if m:
            out[name] = _block(code, m.end() - 1)
    return out


def _block(code: str, start: int) -> str:
    depth = 0
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[start:i + 1]
    return code[start:]


def _result(run_dir: Path, m: MapModule) -> None:
    """最后一次检查的结果：硬件层看串口日志，逻辑层看电脑测试报告。"""
    tries = _attempts(run_dir, m.id)
    if not tries:
        return
    last = tries[-1]
    m.attempt = int(last.name.split("_")[-1])
    report = (last / "report.txt").read_text(encoding="utf-8", errors="replace") if (last / "report.txt").is_file() else ""
    if (last / "serial.log").is_file():
        r = parse_board_output((last / "serial.log").read_text(encoding="utf-8", errors="replace"))
        if r.skipped:
            m.status, m.result = "skipped", "上板跳过"
            return
        ok = sum(1 for c in r.cases if c.status == "pass")
        m.result = f"上板 {ok}/{len(r.cases)}" + ("，芯片崩溃" if r.crash else "")
        m.status = "pass" if r.ok else "fail"
        m.failed_cases = [c.name for c in r.failed]
        return
    head = report.strip().splitlines()[0] if report.strip() else ""
    if head.startswith(("ESP-IDF 不可用", "没有配置板子")):
        m.status, m.result = "skipped", "没上板（没有板子或 ESP-IDF）"
        return
    if head.startswith("电脑测试跳过"):
        m.status, m.result = "skipped", "电脑测试跳过"
        return
    if head.startswith(("编译失败", "测试编译失败")):
        m.status, m.result = "fail", "编译没过"
        return
    if head.startswith("违规"):
        m.status, m.result = "fail", "合规没过"
        return
    mm = re.search(r"通过 (\d+) 个，失败 (\d+) 个", report)
    if mm:
        p, f = int(mm.group(1)), int(mm.group(2))
        m.result = f"电脑上测 {p}/{p + f}"
        m.status = "pass" if f == 0 and p > 0 else "fail"
        m.failed_cases = [ln.split("　", 1)[0].removeprefix("失败：") for ln in report.splitlines() if ln.startswith("失败：")]


def build_project_map(run_dir: Path, profile: HardwareProfile | None = None) -> ProjectMap | None:
    """从运行目录算工程图的数据；还没有设计（design.json）就返回 None。profile：硬件档案（芯片、保留引脚、器件）。"""
    run_dir = Path(run_dir)
    try:
        design = json.loads((run_dir / "design.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        wiring = json.loads((run_dir / "wiring.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        wiring = {}
    pins: dict[str, int] = {s: int(g) for s, g in (wiring.get("pins") or {}).items()} if wiring else (
        {s: int(f.value) for s, f in profile.pins.items()} if profile is not None else {})
    macro_to_signal = {re.sub(r"[^A-Za-z0-9]", "_", s).upper(): s for s in pins}
    prof_devices = list(profile.devices) if profile is not None else []
    dev_names = {d.name for d in prof_devices}

    mods: list[MapModule] = []
    code: dict[str, str] = {}
    tests: dict[str, str] = {}
    for d in design.get("modules", []):
        provides = list(d.get("provides", []))
        names = []
        for p in provides:                            # 评测题的设计只写了函数名；写需求时是完整原型
            got = declared_functions(p if p.rstrip().endswith(";") else p + ";") if "(" in p else [p.strip()]
            names += [n for n in got if n and n not in names]
        files = latest_files(run_dir, d["id"])
        header = files.get(f"include/{d['id']}.h", "")
        protos = [_prototype(header, n) or next((p for p in provides if n in p and "(" in p), n + "(…)") for n in names]
        m = MapModule(d["id"], d.get("title", d["id"]), d.get("layer", "hardware"), protos, names)
        code[m.id] = "\n".join(c for p, c in files.items() if not p.startswith(("test/", "test_host/")))
        tests[m.id] = "\n".join(c for p, c in files.items() if p.startswith(("test/", "test_host/")))
        stripped = strip_c(code[m.id])
        m.signals = sorted({macro_to_signal[x] for x in _PIN_MACRO.findall(stripped) if x in macro_to_signal})
        m.devices = sorted(dv.name for dv in prof_devices
                           if re.search(rf"\bFF_{re.escape(dv.name.upper())}_ADDR\b", stripped)
                           or any(re.search(rf"\b{param_macro(dv.name, k)}\b", stripped) for k in dv.params))
        _result(run_dir, m)
        if m.failed_cases and tests[m.id]:
            bodies = _case_bodies(tests[m.id])
            hit = set()
            for case in m.failed_cases:
                body = bodies.get(case) or next((b for n, b in bodies.items() if n.strip() == case.strip()), "")
                hit |= set(_uses(body, m.names))
            m.failing_uses = [n for n in m.names if n in hit]
        mods.append(m)
    by = {m.id: m for m in mods}
    edges = []
    for d in design.get("modules", []):
        for dep in d.get("depends_on", []):
            if dep in by:
                e = MapEdge(d["id"], dep, _uses(code.get(d["id"], ""), by[dep].names))
                edges.append(e)
                by[dep].used_by.append(d["id"])

    devices = []
    for dv in prof_devices:
        uses_it = any(dv.name in m.devices or any(s.split(".", 1)[0] == dv.name for s in m.signals) for m in mods)
        listed = {x.get("name") for x in wiring.get("devices", [])} if wiring else set()
        if not (uses_it or dv.name in listed):
            continue
        chk = next((x for x in wiring.get("checked", []) if x.get("device") == dv.name), None) if wiring else None
        devices.append(MapDevice(dv.name, dv.part, dv.bus, dv.address,
                                 sorted((s, g) for s, g in pins.items() if s.split(".", 1)[0] == dv.name),
                                 None if chk is None else bool(chk.get("ok")), (chk or {}).get("why", "")))
    groups: dict[str, list[tuple[str, int]]] = {}
    for s, g in sorted(pins.items(), key=lambda x: x[1]):
        pre = s.split(".", 1)[0]
        if pre in dev_names:
            continue                                  # 器件自己的引脚画在器件上
        groups.setdefault(pre, []).append((s, g))
    used_prefixes = {s.split(".", 1)[0] for m in mods for s in m.signals} | {d.bus for d in devices}
    peripherals = [MapPeripheral(p, v) for p, v in groups.items() if p in used_prefixes or wiring.get("source") == "design"]
    chip = str(profile.chip.value) if profile is not None else ""
    reserved = dict(profile.reserved_pins) if profile is not None else {}
    used = set(pins.values())
    unused = [g for g in CHIP_GPIOS.get(chip, ()) if g not in used and g not in reserved]
    return ProjectMap(mods, edges, peripherals, devices, wiring.get("source", "default") if wiring else "default",
                      pins, unused, reserved, str(profile.board.value) if profile is not None else "", chip)

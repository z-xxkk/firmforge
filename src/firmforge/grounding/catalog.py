"""知识库目录（DECISIONS 第 114 条，方案 A"只做目录"）：把各块板子目录里的规则、资料、实测冲突排成固定层级。

    芯片 → 某芯片 → 某外设            芯片手册、勘误、SVD，引用它们的规则
    开发框架 → ESP-IDF v6.1 → 某外设    ESP-IDF 头文件，引用它们的规则
    外接器件 → 某类 → 某型号            器件手册，适用范围写了这个型号的规则
    开发板 → 某块板子                  硬件档案、实测记录、实测规则、手册和实测对不上的地方
    其他资料                          归不进上面任何一类的资料（例如 I2C 总线规范）；没有就不出现

归类只看登记信息，不让模型判断：资料按编号（芯片名开头、idf- 开头、器件型号开头）和等级（user 是实测或用户提供），
规则先看适用范围里有没有器件型号，再看它引用的资料归在哪。外设按 ESP-IDF soc_caps 的外设顺序排。
同一份资料被几块板子登记，只出现一次，记下用到它的板子。
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..target.soc_caps import PERIPHERALS

TOP = ("chip", "framework", "device", "board", "other")
TOP_NAMES = {"chip": "芯片", "framework": "开发框架", "device": "外接器件", "board": "开发板", "other": "其他资料"}
PERIPHERAL_NAMES = {"gptimer": "GPTimer", "usb_serial_jtag": "USB 串口", "usb_otg": "USB OTG", "temp_sensor": "温度传感器",
                    "touch_sensor": "触摸", "sdmmc": "SD 卡"}
OTHER_PERIPHERAL = "其他"


@dataclass
class Entry:
    kind: str                       # rule / doc / conflict
    id: str
    title: str
    data: dict
    boards: list[str] = field(default_factory=list)     # 来自哪些板子目录（hardware/<名字>）
    path: tuple[str, ...] = ()      # 在目录里的位置（标题），搜索结果里显示

    def text(self) -> str:
        """搜索用的全部文字。"""
        d = self.data
        cite = d.get("cite") or {}
        return " ".join(str(x) for x in (self.id, self.title, d.get("text", ""), cite.get("doc", ""), cite.get("quote", ""),
                                         " ".join(d.get("scope", [])), d.get("url", ""), d.get("resolution", ""),
                                         " ".join(d.get("notes", [])))).lower()


@dataclass
class Node:
    key: str
    title: str
    children: list["Node"] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    info: dict = field(default_factory=dict)            # 节点本身的说明：芯片能力、硬件档案、器件参数

    def child(self, key: str, title: str) -> "Node":
        for c in self.children:
            if c.key == key:
                return c
        n = Node(key, title)
        self.children.append(n)
        return n

    def count(self) -> int:
        return len(self.entries) + sum(c.count() for c in self.children)

    def walk(self, path: tuple[str, ...] = ()):
        yield self, path
        for c in self.children:
            yield from c.walk(path + (c.title,))

    def find(self, key_path: tuple[str, ...]) -> "Node | None":
        node = self
        for k in key_path:
            node = next((c for c in node.children if c.key == k), None)
            if node is None:
                return None
        return node


@dataclass
class Catalog:
    root: Node
    errors: list[str] = field(default_factory=list)     # 读不了的文件：要明说，不能显示成"没有规则"

    def all_entries(self) -> list[Entry]:
        out: list[Entry] = []
        for n, _ in self.root.walk():
            out += n.entries
        return out

    def search(self, query: str) -> list[Entry]:
        words = [w for w in query.lower().split() if w]
        if not words:
            return []
        return [e for e in self.all_entries() if all(w in e.text() for w in words)]

    def rules_citing(self, doc_id: str) -> list[Entry]:
        return [e for e in self.all_entries() if e.kind == "rule" and (e.data.get("cite") or {}).get("doc") == doc_id]


def peripheral_title(p: str) -> str:
    return PERIPHERAL_NAMES.get(p, p.upper())


def peripheral_of(scopes) -> str:
    """适用范围里排在最前的外设；没有外设（例如只写了 all）归"其他"。"""
    hits = [s for s in scopes if s in PERIPHERALS]
    return min(hits, key=PERIPHERALS.index) if hits else OTHER_PERIPHERAL


def chip_title(chip: str) -> str:
    """esp32c3 → ESP32-C3。"""
    return chip.upper().replace("ESP32", "ESP32-").rstrip("-") if chip.lower().startswith("esp32") else chip


def _read_json(p: Path, errors: list[str], what: str):
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{what} {p} 读不了：{exc}")
        return None


def build_catalog(project: Path, hardware_dir: str = "hardware") -> Catalog:
    project = Path(project)
    errors: list[str] = []
    boards: list[tuple[str, Path, dict]] = []
    hroot = project / hardware_dir
    for hw in sorted(p for p in hroot.iterdir() if p.is_dir()) if hroot.is_dir() else []:
        prof = _read_json(hw / "profile.json", errors, "硬件档案")
        if isinstance(prof, dict):
            boards.append((hw.name, hw, prof))
    chips = sorted({str((p.get("chip") or {}).get("value", "")) for _, _, p in boards} - {""})
    parts: dict[str, tuple[str, str]] = {}                  # 型号小写 → (类别, 型号)
    for _, _, p in boards:
        for d in p.get("devices", []):
            parts.setdefault(str(d.get("part", "")).lower(), (d.get("category") or "未分类", d.get("part", "")))
    framework = next((f"{p.get('framework', {}).get('value', '')} {p.get('framework_version', {}).get('value', '')}".strip()
                      for _, _, p in boards), "") or "ESP-IDF"
    framework = framework.replace("esp-idf", "ESP-IDF")

    root = Node("", "知识库")
    tops = {k: root.child(k, TOP_NAMES[k]) for k in TOP}

    # ---- 资料（按板子目录里的 sources.toml）
    docs: dict[str, Entry] = {}
    doc_place: dict[str, tuple[str, ...]] = {}
    for name, hw, _ in boards:
        f = hw / "sources.toml"
        if not f.is_file():
            continue
        try:
            items = tomllib.loads(f.read_text(encoding="utf-8")).get("doc", [])
        except (OSError, ValueError) as exc:
            errors.append(f"资料清单 {f} 读不了：{exc}")
            continue
        for d in items:
            did = str(d.get("id", ""))
            if not did:
                continue
            if did in docs:
                docs[did].boards.append(name)
                continue
            docs[did] = Entry("doc", did, str(d.get("title", did)), dict(d), [name])
            doc_place[did] = _doc_place(did, d, chips, parts, name)

    # ---- 规则
    rules: list[tuple[Entry, tuple[str, ...]]] = []
    seen_rules: dict[tuple, Entry] = {}
    for name, hw, prof in boards:
        data = _read_json(hw / "rules.json", errors, "规则库")
        if data is None:
            continue
        if not isinstance(data, list) or not all(isinstance(r, dict) and "id" in r for r in data):
            errors.append(f"规则库 {hw / 'rules.json'} 读不了：内容不是规则列表")
            continue
        chip = str((prof.get("chip") or {}).get("value", ""))
        for r in data:
            key = (r["id"], r.get("text"))
            if key in seen_rules:                       # 几块板子共用同一条规则：只列一次
                seen_rules[key].boards.append(name)
                continue
            e = Entry("rule", r["id"], str(r.get("text", "")), r, [name])
            seen_rules[key] = e
            rules.append((e, _rule_place(r, docs, doc_place, parts, chip, name)))

    # ---- 先建好固定的骨架：芯片、器件、板子（没有条目也列出来，节点本身有说明）
    from ..target.soc_caps import load_caps
    for chip in chips:
        n = tops["chip"].child(chip, chip_title(chip))
        try:
            caps = load_caps(chip)
        except Exception:                                   # noqa: BLE001  ESP-IDF 路径不对也不影响目录
            caps = None
        if caps is not None:
            n.info = {"type": "chip", "chip": chip, "gpios": list(caps.gpios), "input_only": list(caps.input_only),
                      "usb_serial_jtag": caps.usb_serial_jtag, "peripherals": list(caps.peripherals),
                      "source": caps.source}
    fw = tops["framework"].child("idf", framework)
    fw.info = {"type": "framework", "title": framework}
    for low, (cat, part) in sorted(parts.items(), key=lambda kv: (kv[1][0], kv[1][1])):
        dev = tops["device"].child(cat, cat).child(low, part)
        dev.info = {"type": "device", "part": part,
                    "boards": [{"board": name, **d} for name, _, p in boards for d in p.get("devices", [])
                               if str(d.get("part", "")).lower() == low]}
    for name, hw, prof in boards:
        meta = {}
        if (hw / "board.toml").is_file():
            try:
                meta = tomllib.loads((hw / "board.toml").read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                errors.append(f"{hw / 'board.toml'} 读不了：{exc}")
        b = tops["board"].child(name, str(meta.get("name") or (prof.get("board") or {}).get("value", name)))
        b.info = {"type": "board", "dir": f"{hardware_dir}/{name}", "profile": prof, "board": meta}

    def place(entry: Entry, where: tuple[str, ...]):
        node = root
        titles = []
        for k in where:
            node = node.child(k, _title_for(k, node))
            titles.append(node.title)
        entry.path = tuple(titles)
        node.entries.append(entry)

    for did, e in docs.items():
        place(e, doc_place[did])
    for e, where in rules:
        place(e, where)

    # ---- 手册和实测对不上的地方
    from .conflicts import load_conflicts
    for name, hw, _ in boards:
        try:
            items = load_conflicts(hw, None)
        except (OSError, ValueError) as exc:
            errors.append(f"{hw / 'conflicts.json'} 读不了：{exc}")
            continue
        for c in items:
            place(Entry("conflict", c.id, c.title, {"id": c.id, "title": c.title, "doc": c.doc, "page": c.page,
                                                    "quote": c.quote, "doc_claim": c.doc_claim, "record": c.record,
                                                    "measured_claim": c.measured_claim, "resolution": c.resolution},
                        [name]), ("board", name))

    # 排序：外设按 soc_caps 顺序，"其他"放最后；条目规则在前、资料在后
    for n, _ in root.walk():
        if n.children and all(c.key in PERIPHERALS or c.key == OTHER_PERIPHERAL for c in n.children):
            n.children.sort(key=lambda c: PERIPHERALS.index(c.key) if c.key in PERIPHERALS else len(PERIPHERALS))
        n.entries.sort(key=lambda e: ({"conflict": 0, "rule": 1, "doc": 2}[e.kind], e.id))
    if not tops["other"].count():
        root.children.remove(tops["other"])
    return Catalog(root, errors)


def _title_for(key: str, parent: Node) -> str:
    if parent.key == "chip" and parent is not None:
        return chip_title(key)
    if key in PERIPHERALS or key == OTHER_PERIPHERAL:
        return peripheral_title(key) if key in PERIPHERALS else key
    return TOP_NAMES.get(key, key)


def _doc_place(did: str, d: dict, chips: list[str], parts: dict, board: str) -> tuple[str, ...]:
    low = did.lower()
    title = str(d.get("title", "")).lower()
    if d.get("tier") == "user":
        return ("board", board)
    for p, (cat, _part) in parts.items():
        if p and (low.startswith(p) or p in title):
            return ("device", cat, p)
    if low.startswith("idf-") or d.get("kind") == "header" and "espressif" in str(d.get("url", "")):
        tokens = low.replace("_", "-").split("-")
        hit = [t for t in tokens if t in PERIPHERALS]
        return ("framework", "idf", min(hit, key=PERIPHERALS.index) if hit else OTHER_PERIPHERAL)
    for c in sorted(chips, key=len, reverse=True):          # esp32c3 先于 esp32
        if low.startswith(c + "-"):
            return ("chip", c)
    return ("other",)


def _rule_place(r: dict, docs: dict, doc_place: dict, parts: dict, chip: str, board: str) -> tuple[str, ...]:
    scopes = [str(s).lower() for s in r.get("scope", [])]
    for s in scopes:
        if s in parts:
            return ("device", parts[s][0], s)
    doc = (r.get("cite") or {}).get("doc", "")
    where = doc_place.get(doc)
    per = peripheral_of(scopes)
    if where and where[0] == "board":
        return ("board", board)
    if where and where[0] == "framework":
        return ("framework", "idf", per)
    if where and where[0] == "chip":
        return ("chip", where[1], per)
    return ("chip", chip, per) if chip else ("other",)


def reject_rule(rules_path: Path, rule_id: str, reason: str) -> dict:
    """在知识库里驳回一条错的规则：退回"待核对"并记下理由，以后不再给 Agent 用，重新提炼时会再核对。"""
    from datetime import date
    from .rules import RuleSet
    reason = reason.strip()
    if not reason:
        raise ValueError("驳回要写一句理由")
    rs = RuleSet.load(rules_path)
    if rule_id not in rs.rules:
        raise ValueError(f"规则库 {rules_path} 里没有 {rule_id}")
    r = rs.rules[rule_id].reject(f"驳回（知识库，{date.today().isoformat()}）：{reason}")
    rs.put(r)
    rs.save(rules_path)
    return r.to_dict()

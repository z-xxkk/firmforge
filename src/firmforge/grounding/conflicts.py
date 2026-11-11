"""资料与实测不符的记录（hardware/<板子>/conflicts.json）：手册怎么说（资料、页码、原句）、实测怎么说（实测记录编号）、怎么处理。

界面上做成对照卡（DECISIONS 第 105 条）。加载时机械核对：原句必须真的在手册那一页（和规则的原文核对同一个函数），
实测记录编号必须在 measured.md 里，核对不过的在界面上标出来，不当成已证实的冲突显示。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .sources import SourceError, SourceLibrary, quote_found

_RECORD = re.compile(r"^##\s+(M-\d+)\b", re.M)


@dataclass(frozen=True)
class Conflict:
    id: str
    title: str
    doc: str
    page: int | None
    quote: str
    doc_claim: str
    record: str
    measured_claim: str
    resolution: str
    problems: tuple[str, ...] = ()           # 核对没通过的原因；空表示原句和实测记录都核对过


def measured_ids(hwdir: Path) -> set[str]:
    p = Path(hwdir) / "measured.md"
    return set(_RECORD.findall(p.read_text(encoding="utf-8"))) if p.is_file() else set()


def load_conflicts(hwdir: Path, library: SourceLibrary | None) -> list[Conflict]:
    """读 conflicts.json 并逐条核对。文件不存在返回空列表；格式不对抛 ValueError。"""
    p = Path(hwdir) / "conflicts.json"
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{p}：应该是一个列表")
    records = measured_ids(hwdir)
    out = []
    for d in data:
        try:
            doc, meas = d["document"], d["measured"]
            c = Conflict(d["id"], d["title"], doc["doc"], doc.get("page"), doc["quote"], doc["claim"],
                         meas["record"], meas["claim"], d.get("resolution", ""))
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{p}：第 {len(out) + 1} 条缺字段 {exc}") from exc
        problems = []
        if c.record not in records:
            problems.append(f"measured.md 里没有实测记录 {c.record}")
        if library is None:
            problems.append("资料库没有加载，没有核对原句")
        else:
            try:
                if not quote_found(library.get(c.doc), c.page, c.quote):
                    problems.append(f"原句不在 {c.doc} 第 {c.page} 页")
            except (SourceError, KeyError) as exc:
                problems.append(f"找不到资料：{exc}")
        out.append(Conflict(**{**c.__dict__, "problems": tuple(problems)}))
    return out

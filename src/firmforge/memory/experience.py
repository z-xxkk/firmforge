"""经验库（ARCHITECTURE §9）。

- 写入：某次修正让失败层前进了或者直接通过时，记一条 {category, layer, pattern, fix}；
  pattern 是判定器给出的失败 key（不由 LLM 编写），fix 由诊断 Agent 总结（不超过 3 行）。
  写入前先检索：同类别、同层、pattern 的归一化相似度达到阈值就合并（uses 加 1），不新增。
- 使用：编码 Agent 首次编码时用"需求关键词 + 任务类别"检索 top-3，修正轮用"失败的 key"检索 top-3。
- 模块最终通过时，给用到的经验记一次 helped。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from ..store import Store
from .bm25 import BM25

MERGE_THRESHOLD = 0.8
MAX_FIX_LINES = 3


@dataclass(frozen=True)
class Experience:
    id: int
    category: str
    layer: str
    pattern: str
    fix: str
    uses: int = 0
    helped: int = 0

    def render(self) -> str:
        return f"- [{self.layer}] 出现 `{self.pattern}` 时：{self.fix}"


def clip_fix(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[:MAX_FIX_LINES])


class ExperienceBase:
    def __init__(self, store: Store):
        self.store = store

    def all(self, category: str | None = None) -> list[Experience]:
        sql = "SELECT id, category, layer, pattern, fix, uses, helped FROM experiences"
        rows = self.store.query(sql + (" WHERE category = ?" if category else "") + " ORDER BY id",
                                (category,) if category else ())
        return [Experience(**r) for r in rows]

    def search(self, query: str, k: int = 3, category: str | None = None) -> list[Experience]:
        pool = self.all(category) if category else []
        if not pool:
            pool = self.all()
        if not pool:
            return []
        index = BM25([f"{e.category} {e.layer} {e.pattern} {e.fix}" for e in pool])
        return [pool[i] for i, _ in index.search(query, k)]

    def save(self, category: str, layer: str, pattern: str, fix: str, source_run: str) -> tuple[int, bool]:
        """返回 (经验 id, 是否合并进了已有的一条)。"""
        fix = clip_fix(fix)
        same = [e for e in self.all(category) if e.layer == layer]
        if same:
            index = BM25([e.pattern for e in same])
            best = max(range(len(same)), key=lambda i: index.similarity(pattern, i))
            if index.similarity(pattern, best) >= MERGE_THRESHOLD:
                self.store.execute("UPDATE experiences SET uses = uses + 1 WHERE id = ?", (same[best].id,))
                return same[best].id, True
        new_id = self.store.execute(
            "INSERT INTO experiences (category, layer, pattern, fix, source_run, created_at, uses, helped) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0)", (category, layer, pattern, fix, source_run, time.time()))
        return new_id, False

    def mark_used(self, ids: list[int]) -> None:
        for i in ids:
            self.store.execute("UPDATE experiences SET uses = uses + 1 WHERE id = ?", (i,))

    def mark_helped(self, ids: list[int]) -> None:
        for i in set(ids):
            self.store.execute("UPDATE experiences SET helped = helped + 1 WHERE id = ?", (i,))

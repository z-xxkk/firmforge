"""BM25 关键词检索（ARCHITECTURE §9）。

中文按单字和双字切分，英文按单词切分（标识符同时拆出下划线两边的部分）。
DeepSeek 不提供 embedding 接口，而且数据量小，关键词检索就够用；以后要换向量检索，只替换这个模块。
"""

from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[一-鿿]+")


def tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens: list[str] = []
    for w in _WORD.findall(text):
        tokens.append(w)
        parts = [p for p in w.split("_") if p]
        if len(parts) > 1:
            tokens.extend(parts)
    for run in _CJK.findall(text):
        tokens.extend(run)                                    # 单字
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))   # 双字
    return tokens


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in docs]
        self.lens = [sum(c.values()) for c in self.docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 0.0
        df: Counter = Counter()
        for c in self.docs:
            df.update(c.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str | list[str], i: int) -> float:
        q = tokenize(query) if isinstance(query, str) else query
        doc, dl = self.docs[i], self.lens[i]
        s = 0.0
        for t in set(q):
            tf = doc.get(t, 0)
            if not tf:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1))
            s += self.idf.get(t, 0.0) * tf * (self.k1 + 1) / denom
        return s

    def search(self, query: str, k: int = 3, min_score: float = 0.0) -> list[tuple[int, float]]:
        q = tokenize(query)
        scored = [(i, self.score(q, i)) for i in range(len(self.docs))]
        scored = [x for x in scored if x[1] > min_score]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]

    def similarity(self, query: str, i: int) -> float:
        """归一化相似度：查询对文档的得分 ÷ 文档对自己的得分（0–1 左右），用于判断"是不是同一条"。"""
        self_score = self.score(list(self.docs[i]), i)
        return self.score(query, i) / self_score if self_score > 0 else 0.0

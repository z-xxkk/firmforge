"""知识库（ARCHITECTURE §9）：kb/*.md 按二级标题切块，用 BM25 检索。

内容是自己写的摘要（编码规则、协议要点、芯片注意事项等），不整段复制规范原文。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config
from .bm25 import BM25


@dataclass(frozen=True)
class Chunk:
    source: str          # 文件名
    title: str           # "文档标题 / 小节标题"
    text: str

    def render(self) -> str:
        return f"### {self.title}（kb/{self.source}）\n\n{self.text.strip()}"


def split_markdown(name: str, text: str) -> list[Chunk]:
    doc_title, chunks, head, body = name, [], None, []
    for line in text.splitlines():
        if line.startswith("# ") and doc_title == name:
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            if head is not None:
                chunks.append(Chunk(name, f"{doc_title} / {head}", "\n".join(body)))
            head, body = line[3:].strip(), []
        elif head is not None:
            body.append(line)
    if head is not None:
        chunks.append(Chunk(name, f"{doc_title} / {head}", "\n".join(body)))
    return chunks


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self._index = BM25([f"{c.title}\n{c.text}" for c in chunks]) if chunks else None

    @classmethod
    def load(cls, path: Path | str | None = None) -> "KnowledgeBase":
        path = Path(path) if path else config.kb_dir()
        chunks: list[Chunk] = []
        for f in sorted(path.glob("*.md")) if path.is_dir() else []:
            chunks += split_markdown(f.name, f.read_text(encoding="utf-8"))
        return cls(chunks)

    def search(self, query: str, k: int = 3) -> list[Chunk]:
        if self._index is None:
            return []
        return [self.chunks[i] for i, _ in self._index.search(query, k)]

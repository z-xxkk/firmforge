"""资料登记与原文核对（规则判定器第 0、2 步，DECISIONS 第 66 条）。

- 每份资料登记编号、来源网址、版本、文件指纹；官方与否按域名白名单判定，不让模型说了算。
- 原文按"页"保存：PDF 一页一项（第 1 页下标 0）；网页、头文件、SVD 只有一项。
- 引用核对：规则里引用的原文必须在指定页里找得到（空白、断行连字符、全角半角差异忽略），找不到这条规则就作废。

读 PDF 用 pypdf（DECISIONS 第 73 条，核心库只用标准库的唯一例外）；没装时明确报不可用。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

KINDS = ("pdf", "html", "text", "header", "svd")
TIERS = ("official_manual", "official_errata", "official_code", "official_example", "community", "user")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}     # 越小越可信

# 官方域名白名单：只按域名判定（DECISIONS 第 66 条第 0 步）
OFFICIAL_DOMAINS = {
    "espressif": ("espressif.com", "documentation.espressif.com", "docs.espressif.com", "github.com/espressif"),
    "invensense": ("invensense.tdk.com", "tdk.com"),
    "analog": ("analog.com",),            # MAX30102 现属 ADI
    "st": ("st.com",),
}


class SourceError(ValueError):
    """资料不可用或登记信息不对。"""


def is_official(url: str) -> bool:
    if not url:
        return False
    p = urlparse(url)
    host = (p.netloc or "").lower()
    path = (p.path or "").lower()
    for domains in OFFICIAL_DOMAINS.values():
        for d in domains:
            if "/" in d:                      # 例如 github.com/espressif：主机加路径前缀
                h, prefix = d.split("/", 1)
                if host == h and path.lstrip("/").startswith(prefix + "/"):
                    return True
            elif host == d or host.endswith("." + d):
                return True
    return False


@dataclass(frozen=True)
class Document:
    id: str                     # 例如 esp32s3-trm
    title: str
    kind: str
    tier: str
    url: str = ""
    version: str = ""
    sha256: str = ""
    pages: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise SourceError(f"{self.id}：未知的资料类型 {self.kind!r}")
        if self.tier not in TIERS:
            raise SourceError(f"{self.id}：未知的来源等级 {self.tier!r}")
        if self.tier.startswith("official") and self.url and not is_official(self.url):
            raise SourceError(f"{self.id}：标成官方资料，但网址 {self.url} 不在官方域名白名单里")

    def page(self, n: int | None) -> str:
        """n 从 1 开始；None 表示整份资料。"""
        if n is None:
            return "\n".join(self.pages)
        if not 1 <= n <= len(self.pages):
            raise SourceError(f"{self.id} 没有第 {n} 页（共 {len(self.pages)} 页）")
        return self.pages[n - 1]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf_pages(path: Path) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise SourceError("读 PDF 需要 pypdf（pip install pypdf），当前不可用") from exc
    reader = PdfReader(str(path))
    return tuple((p.extract_text() or "") for p in reader.pages)


def load_document(path: Path, *, id: str, title: str, kind: str, tier: str, url: str = "",
                  version: str = "") -> Document:
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"{id}：找不到文件 {path}")
    if kind == "pdf":
        pages = extract_pdf_pages(path)
    else:
        pages = (path.read_text(encoding="utf-8", errors="replace"),)
    return Document(id=id, title=title, kind=kind, tier=tier, url=url, version=version,
                    sha256=file_sha256(path), pages=pages)


# ---------------------------------------------------------------- 引用核对

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def normalize(text: str) -> str:
    """全角转半角、统一连字符、去掉断行连字符、空白压成一个空格。"""
    t = unicodedata.normalize("NFKC", text).translate(_DASHES)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)          # 断行连字符：regis-\nter → register
    return re.sub(r"\s+", " ", t).strip()


def _squash(text: str) -> str:
    """再去掉所有空白：PDF 抽出来的中文、表格常有多余或缺失的空格。"""
    return re.sub(r"\s+", "", text)


def quote_found(doc: Document, page: int | None, quote: str) -> bool:
    """引用原文是否出现在指定页。引用里的"…"或"..."表示省略，各段须按顺序出现。"""
    segments = [s for s in re.split(r"…|\.\.\.", normalize(quote)) if s.strip()]
    if not segments:
        return False
    hay = _squash(normalize(doc.page(page)))
    pos = 0
    for seg in segments:
        i = hay.find(_squash(seg), pos)
        if i < 0:
            return False
        pos = i + len(_squash(seg))
    return True


# ---------------------------------------------------------------- 资料库

class SourceLibrary:
    """一个项目用到的全部资料；元数据存 sources.json，原文缓存在 cache/<id>.json。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.docs: dict[str, Document] = {}

    def add(self, doc: Document) -> None:
        if doc.id in self.docs and self.docs[doc.id].sha256 != doc.sha256:
            raise SourceError(f"资料编号 {doc.id} 已登记，但文件内容不同")
        self.docs[doc.id] = doc

    def get(self, doc_id: str) -> Document:
        if doc_id not in self.docs:
            raise SourceError(f"资料 {doc_id} 没有登记")
        return self.docs[doc_id]

    def save(self) -> None:
        (self.root / "cache").mkdir(parents=True, exist_ok=True)
        meta = []
        for d in self.docs.values():
            m = asdict(d)
            pages = m.pop("pages")
            meta.append(m)
            (self.root / "cache" / f"{d.id}.json").write_text(json.dumps(pages, ensure_ascii=False),
                                                              encoding="utf-8")
        (self.root / "sources.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")

    @classmethod
    def load(cls, root: Path) -> "SourceLibrary":
        lib = cls(root)
        meta_path = Path(root) / "sources.json"
        if meta_path.is_file():
            for m in json.loads(meta_path.read_text(encoding="utf-8")):
                cache = Path(root) / "cache" / f"{m['id']}.json"
                pages = tuple(json.loads(cache.read_text(encoding="utf-8"))) if cache.is_file() else ()
                lib.add(Document(**m, pages=pages))
        return lib

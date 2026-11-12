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
# official_mirror：官方原版文档放在非官方网站上（例如停产器件 MPU6050 的手册，DECISIONS 第 79 条）
TIERS = ("official_manual", "official_errata", "official_code", "official_example", "official_mirror", "community",
         "user")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}     # 越小越可信

# 官方域名白名单：只按域名判定（DECISIONS 第 66 条第 0 步）
OFFICIAL_DOMAINS = {
    "espressif": ("espressif.com", "documentation.espressif.com", "docs.espressif.com", "github.com/espressif",
                  "raw.githubusercontent.com/espressif"),
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
    # PDF 按版面提取的文字：保留表格的列对齐，寄存器表里某个位在哪一列（B7…B0）只有这样才看得出（DECISIONS 第 98 条）
    layout: tuple[str, ...] = field(default=(), repr=False)
    # 每页的寄存器位标注："寄存器（地址）：B1 = DIE_TEMP_RDY"，平台按 PDF 里每格文字的坐标算出，不是原文，不能当引用
    bits: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise SourceError(f"{self.id}：未知的资料类型 {self.kind!r}")
        if self.tier not in TIERS:
            raise SourceError(f"{self.id}：未知的来源等级 {self.tier!r}")
        if self.tier.startswith("official") and self.tier != "official_mirror" and self.url and not is_official(self.url):
            raise SourceError(f"{self.id}：标成官方资料，但网址 {self.url} 不在官方域名白名单里")

    def page(self, n: int | None) -> str:
        """n 从 1 开始；None 表示整份资料。"""
        if n is None:
            return "\n".join(self.pages)
        if not 1 <= n <= len(self.pages):
            raise SourceError(f"{self.id} 没有第 {n} 页（共 {len(self.pages)} 页）")
        return self.pages[n - 1]

    def page_layout(self, n: int | None) -> str:
        """按版面提取的那一页；没有（不是 PDF，或旧缓存）就是空字符串。"""
        if not self.layout or n is None or not 1 <= n <= len(self.layout):
            return ""
        return self.layout[n - 1]

    def page_bits(self, n: int | None) -> str:
        if not self.bits or n is None or not 1 <= n <= len(self.bits):
            return ""
        return self.bits[n - 1]


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
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)       # 字体编码提示太多，不影响取文字
    reader = PdfReader(str(path))
    return tuple((p.extract_text() or "") for p in reader.pages)


def extract_pdf_layout(path: Path) -> tuple[str, ...]:
    """按版面提取：用空格把文字摆回原来的列。旋转的文字会丢，所以只作补充，逐字文本照样保留。"""
    from pypdf import PdfReader
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(str(path))
    out = []
    for p in reader.pages:
        try:
            text = p.extract_text(extraction_mode="layout") or ""
        except Exception:                                   # noqa: BLE001  个别页排版取不出来就留空，不影响逐字文本
            text = ""
        out.append("\n".join(line.rstrip() for line in text.splitlines()))
    return tuple(out)


# ---------------------------------------------------------------- 寄存器位标注

_BIT_HEAD = re.compile(r"^(?:B|Bit ?|BIT ?)([0-7])$")
_IDENT = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")
_HEXADDR = re.compile(r"0[xX][0-9A-Fa-f]{1,4}")


def _width(text: str, size: float) -> float:
    """估算一段文字的宽度（点）：PDF 不直接给宽度，按常见无衬线字体的平均字宽估。"""
    return sum(0.28 if ch == " " else 0.6 for ch in text) * size


def bit_notes(runs: list[tuple[float, float, float, str]]) -> list[str]:
    """runs：一页里每段文字的（x, y, 字号, 原样文字）。找出表头是 B7…B0 的寄存器表，
    按每格文字的中心落在哪一列，标出单个位的字段在第几位（DECISIONS 第 99 条）。
    只标有把握的：一整行连成一段文字（例如 MPU6050 的寄存器表）、同一寄存器两个字段落到同一位，都不标。"""
    items = [(x + _width(s, f) / 2, x, x + _width(s, f), y, f, s.strip()) for x, y, f, s in runs if s.strip()]
    out = []
    by_y: dict[float, list] = {}
    for it in items:
        by_y.setdefault(round(it[3], 1), []).append(it)
    for y0, row in sorted(by_y.items(), reverse=True):
        heads = sorted((it for it in row if _BIT_HEAD.match(it[5])), key=lambda it: it[0])
        if [int(_BIT_HEAD.match(h[5]).group(1)) for h in heads] != list(range(7, -1, -1)):
            continue
        cols = [(7 - i, h[0]) for i, h in enumerate(heads)]
        step = (cols[-1][1] - cols[0][1]) / 7
        left, right = cols[0][1] - step / 2, cols[-1][1] + step / 2
        size = heads[0][4]
        nxt = max((y for y, r in by_y.items() if y < y0 and any(_BIT_HEAD.match(i[5]) for i in r) and
                   len([i for i in r if _BIT_HEAD.match(i[5])]) == 8), default=-1e9)
        body = [it for it in items if nxt < it[3] < y0 - size * 0.5]
        addrs = []                       # 每一行最左边的十六进制数是寄存器地址（右边那个是上电值）
        for y in sorted({it[3] for it in body}, reverse=True):
            hexes = sorted((it for it in body if it[3] == y and it[1] > right - step * 0.2 and _HEXADDR.fullmatch(it[5])),
                           key=lambda it: it[1])
            if hexes:
                addrs.append((y, hexes[0][5].replace("X", "x")))
        names = [it for it in body if it[0] < left]
        # 从名字列一直伸进位列（或横跨到地址列）的整段文字：这一行的格子没有分开，对不准
        spans = [it for it in body if (it[0] < left and it[2] > left + step * 0.5) or
                 (it[1] < right - step and it[2] > right + step and not _HEXADDR.fullmatch(it[5]))]
        cells = sorted((it for it in body if left <= it[0] <= right and it[2] <= right + step * 0.3),
                       key=lambda it: -it[3])
        fields: list[list] = []          # [中心, 左, 右, 最高 y, 最低 y, 文字]
        for c, l, r, y, f, s in cells:
            for fd in fields:
                # 同一格里上下两行拼成一个字段名（DIE_ + TEMP_RDY、FIFO_ + ROLL + OVER_EN）
                if abs(fd[0] - c) < step / 2 and 0 < fd[4] - y <= f * 1.6 and \
                        (fd[5].endswith("_") or s.startswith(("_", "[")) or (_IDENT.fullmatch(fd[5]) and _IDENT.fullmatch(s))):
                    fd[1], fd[2], fd[4], fd[5] = min(fd[1], l), max(fd[2], r), y, fd[5] + s
                    fd[0] = (fd[1] + fd[2]) / 2
                    break
            else:
                fields.append([c, l, r, y, y, s])
        groups: dict[str, dict] = {}
        for c, l, r, ytop, ybot, s in fields:
            near = [(abs((ytop + ybot) / 2 - ay), a) for ay, a in addrs if ybot - size * 1.2 <= ay <= ytop + size * 1.2]
            if not near:
                continue
            a = min(near)[1]
            g = groups.setdefault(a, {"top": ytop, "bot": ybot, "single": [], "multi": []})
            g["top"], g["bot"] = max(g["top"], ytop), min(g["bot"], ybot)
            if "[" in s:
                g["multi"].append(s)
            elif _IDENT.fullmatch(s) and len(s) >= 3 and r - l < step * 1.2:     # 单个字母（如 R/W 表里的 W）不标
                g["single"].append((min(cols, key=lambda col: abs(col[1] - c))[0], s))
        for a, g in groups.items():
            ay = next(y for y, x in addrs if x == a)
            top, bot = max(g["top"], ay) + size * 0.6, min(g["bot"], ay) - size * 0.6
            name = " ".join(it[5] for it in sorted(names, key=lambda it: -it[3]) if bot <= it[3] <= top)
            if any(bot <= it[3] <= top for it in spans):
                continue                                   # 这一行有跨列的整段文字，对不准
            single = [(b, s) for b, s in g["single"] if s not in name.split()]
            if len({b for b, _ in single}) < len(single):
                single = []                                # 两个字段落到同一位：没对准，整行不标
            parts = [f"B{b} = {s}" for b, s in sorted(single, reverse=True)]
            if g["multi"]:
                parts.append("多位字段 " + "、".join(g["multi"]) + "（跨几列，占哪几位以手册正文为准）")
            if parts:
                out.append(f"{name or '寄存器'}（{a}）：" + "；".join(parts))
    return out


def extract_pdf_bits(path: Path) -> tuple[str, ...]:
    """每页的寄存器位标注（没有寄存器表的页是空字符串）。"""
    from pypdf import PdfReader
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    out = []
    for p in PdfReader(str(path)).pages:
        runs = []

        def visit(text, cm, tm, font, size, runs=runs):
            if text.strip():
                runs.append((tm[4] * cm[0] + cm[4], tm[5] * cm[3] + cm[5], abs(size * tm[0] * cm[0]) or 1.0, text))
        try:
            p.extract_text(visitor_text=visit)
            out.append("\n".join(bit_notes(runs)))
        except Exception:                                   # noqa: BLE001  取不出来就不标，不影响原文
            out.append("")
    return tuple(out)


def load_document(path: Path, *, id: str, title: str, kind: str, tier: str, url: str = "",
                  version: str = "") -> Document:
    path = Path(path)
    if not path.is_file():
        raise SourceError(f"{id}：找不到文件 {path}")
    layout: tuple[str, ...] = ()
    bits: tuple[str, ...] = ()
    if kind == "pdf":
        pages = extract_pdf_pages(path)
        layout = extract_pdf_layout(path)
        bits = extract_pdf_bits(path)
    else:
        pages = (path.read_text(encoding="utf-8", errors="replace"),)
    return Document(id=id, title=title, kind=kind, tier=tier, url=url, version=version,
                    sha256=file_sha256(path), pages=pages, layout=layout, bits=bits)


# ---------------------------------------------------------------- 引用核对

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")


def normalize(text: str, join_hyphens: bool = True) -> str:
    """全角转半角、统一连字符、去掉断行连字符（join_hyphens）、空白压成一个空格。"""
    t = unicodedata.normalize("NFKC", text).translate(_DASHES)
    if join_hyphens:
        t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)      # 断行连字符：regis-\nter → register
    return re.sub(r"\s+", " ", t).strip()


def _squash(text: str) -> str:
    """再去掉所有空白：PDF 抽出来的中文、表格常有多余或缺失的空格。"""
    return re.sub(r"\s+", "", text)


def quote_found(doc: Document, page: int | None, quote: str) -> bool:
    """引用原文是否出现在指定页。引用里的"…"或"..."表示省略，各段须按顺序出现。"""
    segments = [s for s in re.split(r"…|\.\.\.", normalize(quote)) if s.strip()]
    if not segments:
        return False
    texts = [doc.page(page)] + ([doc.page_layout(page)] if doc.page_layout(page) else [])
    # 两种都试：去掉断行连字符（regis-\nter）和保留原样（表格里行尾本来就有的连字符，例如 USB_D-）；
    # 逐字文本和按版面的文字也都试（表格里的一行在两种提取里顺序不同）
    return any(_in_order(_squash(normalize(text, j)), segments) for text in texts for j in (True, False))


def _in_order(hay: str, segments: list[str]) -> bool:
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
            layout = m.pop("layout")
            bits = m.pop("bits")
            meta.append(m)
            (self.root / "cache" / f"{d.id}.json").write_text(json.dumps(pages, ensure_ascii=False),
                                                              encoding="utf-8")
            if layout:
                (self.root / "cache" / f"{d.id}.layout.json").write_text(json.dumps(layout, ensure_ascii=False),
                                                                         encoding="utf-8")
            if bits:
                (self.root / "cache" / f"{d.id}.bits.json").write_text(json.dumps(bits, ensure_ascii=False),
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
                lay = Path(root) / "cache" / f"{m['id']}.layout.json"
                layout = tuple(json.loads(lay.read_text(encoding="utf-8"))) if lay.is_file() else ()
                bf = Path(root) / "cache" / f"{m['id']}.bits.json"
                bits = tuple(json.loads(bf.read_text(encoding="utf-8"))) if bf.is_file() else ()
                lib.add(Document(**m, pages=pages, layout=layout, bits=bits))
        return lib


# ---------------------------------------------------------------- 资料清单

def load_manifest(manifest: Path, raw_dir: Path, cache_root: Path) -> SourceLibrary:
    """按资料清单（sources.toml，进仓库）登记资料：原件在 raw_dir（不进仓库），先核对 sha256 再取文字；
    取过的文字缓存在 cache_root，指纹没变就直接用缓存（1500 页的手册取一次要几十秒）。"""
    import tomllib
    data = tomllib.loads(Path(manifest).read_text(encoding="utf-8"))
    cached = SourceLibrary.load(cache_root)
    lib = SourceLibrary(cache_root)
    for d in data.get("doc", []):
        meta = {k: d.get(k, "") for k in ("id", "title", "kind", "tier", "url", "version")}
        want = d.get("sha256", "")
        old = cached.docs.get(meta["id"])
        if old is not None and old.sha256 == want and old.pages and (meta["kind"] != "pdf" or (old.layout and old.bits)):
            lib.add(old)
            continue
        path = Path(raw_dir) / d["file"]
        if not path.is_file():
            raise SourceError(f"{meta['id']}：缺原件 {path}，请按清单里的网址下载")
        got = file_sha256(path)
        if want and got != want:
            raise SourceError(f"{meta['id']}：{path.name} 的指纹和清单不符，可能不是同一份文件")
        lib.add(load_document(path, **meta))
    lib.save()
    return lib



# ---------------------------------------------------------------- 拖进来的资料（DECISIONS 第 114 条）

SUFFIX_KIND = {".pdf": "pdf", ".html": "html", ".htm": "html", ".txt": "text", ".md": "text", ".h": "header",
               ".svd": "svd"}


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)          # JSON 字符串也是合法的 TOML 基本字符串


def register_source(manifest: Path, raw_dir: Path, cache_root: Path, file: Path, *, title: str = "",
                    url: str = "", version: str = "") -> tuple[Document, bool]:
    """把用户给的一份资料登记进资料清单：算指纹、复制原件到 raw_dir、追加到 sources.toml、提取文字。
    同一份文件（指纹相同）已经登记过就直接返回它，第二个值是 False。用户给的资料等级是 user（不算官方）。"""
    file = Path(file)
    kind = SUFFIX_KIND.get(file.suffix.lower())
    if kind is None:
        raise SourceError(f"{file.name}：只能登记 {'、'.join(sorted(SUFFIX_KIND))} 文件")
    if not file.is_file():
        raise SourceError(f"找不到文件 {file}")
    digest = file_sha256(file)
    import tomllib
    manifest = Path(manifest)
    text = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
    entries = tomllib.loads(text).get("doc", []) if text else []
    lib = SourceLibrary.load(cache_root)
    for d in entries:
        if d.get("sha256") == digest:
            if d["id"] in lib.docs:
                return lib.docs[d["id"]], False
            return load_manifest(manifest, raw_dir, cache_root).get(d["id"]), False
    base = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", file.stem).lower()).strip("-")[:40] or "doc"
    taken = {d.get("id") for d in entries} | set(lib.docs)
    doc_id, n = base, 2
    while doc_id in taken:
        doc_id, n = f"{base}-{n}", n + 1
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    name = file.name
    target = raw_dir / name
    if target.exists() and file_sha256(target) != digest:
        name = f"{doc_id}{file.suffix.lower()}"
        target = raw_dir / name
    if target.resolve() != file.resolve():
        import shutil
        shutil.copyfile(file, target)
    doc = load_document(target, id=doc_id, title=title or file.stem, kind=kind, tier="user", url=url, version=version)
    block = (f"\n[[doc]]\n# 用户在对话里拖进来的资料\nid = {_toml_str(doc_id)}\ntitle = {_toml_str(doc.title)}\n"
             f"kind = {_toml_str(kind)}\ntier = \"user\"\nversion = {_toml_str(version)}\nurl = {_toml_str(url)}\n"
             f"file = {_toml_str(name)}\nsha256 = {_toml_str(digest)}\n")
    manifest.write_text(text.rstrip("\n") + ("\n" if text.strip() else "") + block, encoding="utf-8")
    lib.add(doc)
    lib.save()
    return doc, True

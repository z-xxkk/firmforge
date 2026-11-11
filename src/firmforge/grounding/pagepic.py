"""手册原页：把规则引用的那一页渲染成图片，并找出引用原文在页面上的位置（界面上框出来）。

做法参考法律 AI 的"点引用、在旁边打开原文档并高亮那一段"（CoCounsel、Noxtua 的引用侧栏）。
- 图片：poppler 的 pdftoppm；位置：poppler 的 pdftotext -bbox 给出每个单词的坐标，把引用原文按顺序对到单词上。
  两个命令经 config.Toolchain 取（可用 FF_PDFTOPPM、FF_PDFTOTEXT 覆盖）。
- 没装 poppler 时返回原因，界面改为只显示按版面排列的文字，不假装有图（CLAUDE.md 硬性规则 4）。
- 框只用来指出位置；规则核对仍是逐字比对原文（sources.quote_found），不依赖这里。
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .sources import _squash, normalize

_PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">')
_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)


@dataclass(frozen=True)
class Word:
    x0: float                                  # 页面坐标（点），原点在左上角
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class PageWords:
    width: float
    height: float
    words: tuple[Word, ...]


def raw_pdf(hwdir: Path, raw_dir: Path, doc_id: str) -> Path | None:
    """资料清单（sources.toml）里这份资料的原件；不是 PDF、清单里没有或原件不在，都返回 None。"""
    manifest = Path(hwdir) / "sources.toml"
    if not manifest.is_file():
        return None
    for d in tomllib.loads(manifest.read_text(encoding="utf-8")).get("doc", []):
        if d.get("id") == doc_id and d.get("kind") == "pdf" and d.get("file"):
            p = Path(raw_dir) / d["file"]
            return p if p.is_file() else None
    return None


def _tool(name: str) -> str | None:
    from ..config import Toolchain
    return shutil.which(getattr(Toolchain.from_env(), name))


def parse_bbox(text: str) -> PageWords:
    """解析 pdftotext -bbox 的输出（只取第一页）。"""
    m = _PAGE.search(text)
    if m is None:
        raise ValueError("pdftotext 的输出里没有页面")
    words = tuple(Word(float(a), float(b), float(c), float(d), html.unescape(t))
                  for a, b, c, d, t in _WORD.findall(text[m.end():].split("</page>", 1)[0]))
    return PageWords(float(m.group(1)), float(m.group(2)), words)


def page_words(pdf: Path, page: int) -> PageWords | str:
    """第 page 页（从 1 开始）每个单词的位置；失败返回原因。"""
    exe = _tool("pdftotext")
    if exe is None:
        return "没装 pdftotext（Ubuntu 上是 poppler-utils 包），不能定位原文在页面上的位置"
    try:
        r = subprocess.run([exe, "-f", str(page), "-l", str(page), "-bbox", str(pdf), "-"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"读页面文字位置失败：{exc}"
    if r.returncode != 0:
        return f"读页面文字位置失败：{(r.stderr or r.stdout).strip()[-200:]}"
    try:
        return parse_bbox(r.stdout)
    except ValueError as exc:
        return f"读页面文字位置失败：{exc}"


def quote_boxes(words: tuple[Word, ...] | list[Word], quote: str, min_len: int = 12) -> list[Box]:
    """引用原文在页面上的位置：每一行一个框。引用里的"…"表示省略，各段分别找；整段找不到时退而找开头的一截
    （至少 min_len 个字符，引用可能跨了表格或页脚），都找不到返回空列表。"""
    chars: list[str] = []
    owner: list[int] = []
    for i, w in enumerate(words):
        t = _squash(normalize(w.text, join_hyphens=False))
        nxt = words[i + 1] if i + 1 < len(words) else None
        if t.endswith("-") and nxt is not None and nxt.y0 > w.y1 - 1:
            t = t[:-1]                                   # 行尾断行连字符：regis-（换行）ter → register
        chars.extend(t)
        owner.extend([i] * len(t))
    hay = "".join(chars)
    hit: set[int] = set()
    pos = 0
    for seg in (s for s in re.split(r"…|\.\.\.", normalize(quote)) if s.strip()):
        needle = _squash(seg)
        i = hay.find(needle, pos)
        n = len(needle)
        while i < 0 and n > min_len:
            n = max(min_len, n * 2 // 3)
            i = hay.find(needle[:n], pos)
        if i < 0:
            continue
        hit.update(owner[i:i + n])
        pos = i + n
    lines: list[list[float]] = []
    for i in sorted(hit):
        w = words[i]
        for ln in lines:
            if abs((ln[1] + ln[3]) / 2 - (w.y0 + w.y1) / 2) < (w.y1 - w.y0) * 0.5:
                ln[0], ln[1], ln[2], ln[3] = min(ln[0], w.x0), min(ln[1], w.y0), max(ln[2], w.x1), max(ln[3], w.y1)
                break
        else:
            lines.append([w.x0, w.y0, w.x1, w.y1])
    return [Box(*ln) for ln in sorted(lines, key=lambda b: b[1])]


def render_page(pdf: Path, page: int, out_png: Path, *, dpi: int = 110) -> str:
    """把第 page 页渲染成 PNG。成功返回空字符串，失败返回原因。已渲染过（且比原件新）就直接用。"""
    out_png = Path(out_png)
    if out_png.is_file() and out_png.stat().st_mtime >= Path(pdf).stat().st_mtime:
        return ""
    exe = _tool("pdftoppm")
    if exe is None:
        return "没装 pdftoppm（Ubuntu 上是 poppler-utils 包），不能显示原页图片"
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run([exe, "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile",
                            str(pdf), str(out_png.with_suffix(""))], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"渲染原页失败：{exc}"
    if r.returncode != 0 or not out_png.is_file():
        return f"渲染原页失败：{(r.stderr or r.stdout).strip()[-200:]}"
    return ""

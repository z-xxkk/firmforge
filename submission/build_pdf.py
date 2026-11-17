"""把 submission/方案书.md 转成 PDF：自带的极简 Markdown → HTML（标题、段落、列表、表格、代码块、图片、加粗），
再用本机的 Google Chrome 无头打印成 PDF。只处理方案书里用到的写法。

用法：python3 submission/build_pdf.py
"""

import html
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC, HTML, PDF = HERE / "方案书.md", HERE / "方案书.html", HERE / "方案书.pdf"

CSS = """
@page { size: A4; margin: 20mm 18mm 20mm 18mm; }
body { font-family: "Noto Sans CJK SC", "Noto Sans CJK JP", "Source Han Sans SC", sans-serif; font-size: 10.5pt;
       line-height: 1.75; color: #1c2026; }
h1 { font-size: 22pt; text-align: center; margin: 40mm 0 12mm; line-height: 1.35; }
h2 { font-size: 15pt; border-bottom: 2px solid #2f6f68; padding-bottom: 3px; margin-top: 22px; page-break-after: avoid; }
h3 { font-size: 12pt; color: #2f6f68; margin-top: 16px; page-break-after: avoid; }
p { margin: 6px 0; text-align: justify; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 1px solid #c9ced4; padding: 4px 6px; vertical-align: top; }
th { background: #e6f0ee; }
pre { background: #f3f4f6; padding: 8px 10px; font-size: 8pt; line-height: 1.45; white-space: pre-wrap;
      font-family: "Noto Sans Mono CJK SC", "DejaVu Sans Mono", monospace; page-break-inside: avoid; }
code { font-family: "Noto Sans Mono CJK SC", "DejaVu Sans Mono", monospace; font-size: 9pt; background: #f3f4f6; padding: 0 2px; }
pre code { background: none; padding: 0; }
img { max-width: 100%; border: 1px solid #d3d7dc; }
figure { margin: 10px 0 16px; page-break-inside: avoid; text-align: center; }
figcaption { font-size: 9pt; color: #6e7580; }
hr { border: none; border-top: 1px solid #c9ced4; margin: 14px 0; }
.cover p { text-align: center; font-size: 12pt; margin: 4px 0; }
ul { margin: 4px 0 4px 20px; padding: 0; }
li { margin: 2px 0; }
"""


def inline(t: str) -> str:
    t = html.escape(t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return t


def convert(md: str) -> str:
    lines, out, i = md.splitlines(), [], 0
    cover = True
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            body = [r for r in rows if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            h = "".join(f"<th>{inline(c)}</th>" for c in body[0])
            rest = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body[1:])
            out.append(f"<table><thead><tr>{h}</tr></thead><tbody>{rest}</tbody></table>")
            continue
        m = re.match(r"!\[(.*?)\]\((.*?)\)", ln)
        if m:
            out.append(f'<figure><img src="{html.escape(m.group(2))}"><figcaption>{inline(m.group(1))}</figcaption></figure>')
            i += 1
            continue
        if ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        h = re.match(r"(#{1,3}) (.*)", ln)
        if h:
            if cover and len(h.group(1)) == 2:          # 第一个二级标题之前是封面
                out.append('</div><div style="page-break-before: always"></div>')
                cover = False
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            i += 1
            continue
        if ln.strip() == "---":
            out.append("" if cover else "<hr>")
            i += 1
            continue
        if ln.strip():
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return '<div class="cover">' + "\n".join(out)


def main() -> None:
    body = convert(SRC.read_text(encoding="utf-8"))
    HTML.write_text(f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>FirmForge 方案书</title>'
                    f"<style>{CSS}</style></head><body>{body}</body></html>", encoding="utf-8")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", HTML.as_uri()], check=True, capture_output=True, timeout=180)
    print(f"已生成 {PDF}（{PDF.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()

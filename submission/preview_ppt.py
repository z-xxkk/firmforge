"""粗略预览 PPT（本机没有 LibreOffice 时自查版面用）：背景图、卡片、箭头、图片、文字（按 Noto Sans CJK 实测宽度换行），
图表画灰框；同时报告文字超出文本框高度、超出页面的地方。只为自查，不追求和 PowerPoint 一模一样。

用法：.venv/bin/python submission/preview_ppt.py submission/方案讲解.pptx 输出目录
"""
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

DPI = 96
FB = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FN = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_fonts = {}


def font(size_pt, bold):
    px = max(6, int(size_pt * DPI / 72))
    k = (px, bold)
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(FB if bold else FN, px, index=2)
    return _fonts[k]


def px(emu):
    return int(emu / 914400 * DPI)


def wrap(t, f, maxw):
    lines = []
    for raw in t.split("\n"):
        cur = ""
        for ch in raw:
            if f.getlength(cur + ch) > maxw and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def arrow(d, x, y, w, h, kind, col):
    if kind == "LEFT":
        pts = [(x + w, y + h * .3), (x + w * .4, y + h * .3), (x + w * .4, y), (x, y + h / 2), (x + w * .4, y + h),
               (x + w * .4, y + h * .7), (x + w, y + h * .7)]
    elif kind == "DOWN":
        pts = [(x + w * .3, y), (x + w * .7, y), (x + w * .7, y + h * .6), (x + w, y + h * .6), (x + w / 2, y + h),
               (x, y + h * .6), (x + w * .3, y + h * .6)]
    elif kind == "UP":
        pts = [(x + w * .3, y + h), (x + w * .7, y + h), (x + w * .7, y + h * .4), (x + w, y + h * .4), (x + w / 2, y),
               (x, y + h * .4), (x + w * .3, y + h * .4)]
    else:
        pts = [(x, y + h * .3), (x + w * .6, y + h * .3), (x + w * .6, y), (x + w, y + h / 2), (x + w * .6, y + h),
               (x + w * .6, y + h * .7), (x, y + h * .7)]
    d.polygon(pts, fill=col)


def main(src: Path, outdir: Path) -> list[str]:
    outdir.mkdir(parents=True, exist_ok=True)
    prs = Presentation(str(src))
    W, H = int(prs.slide_width / 914400 * DPI), int(prs.slide_height / 914400 * DPI)
    issues = []
    for n, slide in enumerate(prs.slides, 1):
        im = Image.new("RGB", (W, H), "white")
        for sh in slide.slide_layout.shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                p = Image.open(io.BytesIO(sh.image.blob)).convert("RGBA").resize((max(1, px(sh.width)), max(1, px(sh.height))))
                im.paste(p, (px(sh.left), px(sh.top)), p)
        d = ImageDraw.Draw(im)
        for sh in slide.shapes:
            x, y, w, h = px(sh.left), px(sh.top), px(sh.width), px(sh.height)
            if x + w > W + 2 or y + h > H + 2 or x < -2 or y < -2:
                issues.append(f"第 {n} 页 {sh.name!r} 超出页面")
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                pic = Image.open(io.BytesIO(sh.image.blob)).convert("RGB")
                iw, ih = pic.size
                box = (int(iw * sh.crop_left), int(ih * sh.crop_top), int(iw * (1 - sh.crop_right)), int(ih * (1 - sh.crop_bottom)))
                im.paste(pic.crop(box).resize((max(1, w), max(1, h))), (x, y))
                d.rectangle([x, y, x + w, y + h], outline="#c9ced4")
                continue
            if sh.has_chart:
                d.rectangle([x, y, x + w, y + h], outline="#888", fill="#f4f6fa")
                d.text((x + 8, y + 8), "[图表] " + (sh.chart.chart_title.text_frame.text if sh.chart.has_title else ""),
                       fill="#333", font=font(11, False))
                continue
            if sh.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                try:
                    col = "#" + str(sh.fill.fore_color.rgb)
                except Exception:
                    col = None
                name = str(sh.auto_shape_type)
                if "OVAL" in name:
                    d.ellipse([x, y, x + w, y + h], fill=col)
                elif "ARROW" in name:
                    kind = next((k for k in ("LEFT", "DOWN", "UP") if k in name), "RIGHT")
                    arrow(d, x, y, w, h, kind, col)
                else:
                    d.rounded_rectangle([x, y, x + w, y + h], radius=int(min(w, h) * .08), fill=col)
            if sh.has_text_frame and sh.text_frame.text.strip():
                tf = sh.text_frame
                ml, mt = px(tf.margin_left), px(tf.margin_top)
                inner = w - ml - px(tf.margin_right)
                blocks, total = [], 0
                for p in tf.paragraphs:
                    if not p.runs:
                        continue
                    r = p.runs[0]
                    size = r.font.size.pt if r.font.size else 18
                    f = font(size, bool(r.font.bold))
                    try:
                        col = "#" + str(r.font.color.rgb)
                    except Exception:
                        col = "#000"
                    lines = wrap("".join(x.text for x in p.runs), f, inner)
                    lh = int(size * DPI / 72 * 1.25)
                    sa = int((p.space_after.pt if p.space_after is not None else 0) * DPI / 72)
                    blocks.append((lines, f, col, lh, sa, p.alignment))
                    total += lh * len(lines) + sa
                avail = h - mt - px(tf.margin_bottom)
                if total > avail + 2:
                    issues.append(f"第 {n} 页 {sh.name!r}：文字高 {total}px，框只有 {avail}px：{tf.text[:30]!r}")
                cy = y + mt + (max(0, (avail - total) // 2) if "MIDDLE" in str(tf.vertical_anchor) else 0)
                for lines, f, col, lh, sa, al in blocks:
                    for ln in lines:
                        lw = f.getlength(ln)
                        cx = x + ml + ((inner - lw) / 2 if al is not None and "CENTER" in str(al) else 0)
                        d.text((cx, cy), ln, fill=col, font=f)
                        cy += lh
                    cy += sa
        im.save(outdir / f"p{n:02d}.png")
    fs = sorted(outdir.glob("p*.png"))
    for k in range(0, len(fs), 4):                      # 每 4 页拼一张总览
        sheet = Image.new("RGB", (1280, 720), "white")
        for i, f in enumerate(fs[k:k + 4]):
            sheet.paste(Image.open(f).resize((640, 360)), ((i % 2) * 640, (i // 2) * 360))
        sheet.save(outdir / f"sheet{k // 4 + 1}.png")
    return issues


if __name__ == "__main__":
    found = main(Path(sys.argv[1]), Path(sys.argv[2]))
    print("\n".join(found) or "没有发现文字溢出或超出页面")

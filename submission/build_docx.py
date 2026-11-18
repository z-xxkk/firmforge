"""把演示视频逐字稿（Markdown）转成 Word 文档，只用标准库（docx 就是 zip 里的几份 XML）。

念的话：黑色正文；（括号里的操作）：蓝灰色、小一号、左侧缩进，录的时候一眼分清。
用法：python3 submission/build_docx.py
"""
import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).parent
SRC = HERE / "演示视频逐字稿.md"
OUT = HERE / "演示视频逐字稿.docx"
FONT = "微软雅黑"


def runs(text: str, *, color=None, size=None, bold=False) -> str:
    """把 **粗体** 和 `代码` 拆成多个 run。size 单位是半磅。"""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        b, t, mono = bold, part, False
        if part.startswith("**"):
            b, t = True, part[2:-2]
        elif part.startswith("`"):
            t, mono = part[1:-1], True
        pr = [f'<w:rFonts w:ascii="{"Consolas" if mono else FONT}" w:hAnsi="{"Consolas" if mono else FONT}" '
              f'w:eastAsia="{FONT}"/>']
        if b:
            pr.append("<w:b/>")
        if color:
            pr.append(f'<w:color w:val="{color}"/>')
        if size:
            pr.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
        out.append(f'<w:r><w:rPr>{"".join(pr)}</w:rPr><w:t xml:space="preserve">{escape(t)}</w:t></w:r>')
    return "".join(out)


def para(body: str, *, style=None, ind=0, before=0, after=120, shade=None, keep=False) -> str:
    pr = []
    if style:
        pr.append(f'<w:pStyle w:val="{style}"/>')
    if keep:
        pr.append("<w:keepNext/>")
    if shade:
        pr.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>')
    pr.append(f'<w:spacing w:before="{before}" w:after="{after}" w:line="360" w:lineRule="auto"/>')
    if ind:
        pr.append(f'<w:ind w:left="{ind}"/>')
    return f'<w:p><w:pPr>{"".join(pr)}</w:pPr>{body}</w:p>'


def convert(md: str) -> str:
    ps = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line == "---":
            continue
        if line.startswith("### "):
            ps.append(para(runs(line[4:], color="0A3AB8", size=28, bold=True), style="Heading3", before=240, keep=True))
        elif line.startswith("## "):
            ps.append(para(runs(line[3:], color="0A3AB8", size=32, bold=True), style="Heading2", before=360, keep=True))
        elif line.startswith("# "):
            ps.append(para(runs(line[2:], size=40, bold=True), style="Title", after=240))
        elif line.startswith("（"):                      # 操作
            ps.append(para(runs(line, color="5B6B82", size=21), ind=420, shade="F1F4F9", after=100))
        elif line.startswith("- "):
            ps.append(para(runs("• " + line[2:], size=22), ind=360))
        elif re.match(r"\d+\. ", line):
            ps.append(para(runs(line, size=22), ind=360))
        else:                                            # 念白或说明
            ps.append(para(runs(line, size=24)))
    return "".join(ps)


DOC = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{}'
       '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
       '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="851" w:footer="992" w:gutter="0"/>'
       '</w:sectPr></w:body></w:document>')
STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
          f'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:eastAsia="{FONT}"/>'
          '<w:sz w:val="24"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault></w:docDefaults>'
          '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
          '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
          '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/>'
          '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style></w:styles>')
TYPES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         '<Override PartName="/word/document.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
         '<Override PartName="/word/styles.xml" '
         'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')
DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>')


def main():
    body = convert(SRC.read_text(encoding="utf-8"))
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", TYPES)
        z.writestr("_rels/.rels", RELS)
        z.writestr("word/_rels/document.xml.rels", DOC_RELS)
        z.writestr("word/styles.xml", STYLES)
        z.writestr("word/document.xml", DOC.format(body))
    print(f"已生成 {OUT}")


if __name__ == "__main__":
    main()

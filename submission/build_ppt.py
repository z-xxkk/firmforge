"""按大赛模板生成方案讲解 PPT：submission/方案讲解.pptx。

模板：~/Downloads/2026未来杯PPT模版.pptx（16:9，13.33 × 7.5 英寸）。用模板自带的版式（背景图、大赛标识都在版式上）：
  标题幻灯片（蓝色封面）、标题和内容（蓝色目录）、两栏内容（白底、顶部蓝色横幅，正文页）、节标题（深蓝页）。
模板原有的 6 页示例删掉，每页的文字、卡片、图表、截图由这里画；字体沿用模板的思源黑体。
数字全部来自 docs/TEST_PLAN.md 第 11 节、submission/data/ 和运行记录（与方案书一致）。

用法：.venv/bin/python submission/build_ppt.py
"""

from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
TEMPLATE = Path.home() / "Downloads" / "2026未来杯PPT模版.pptx"
OUT = HERE / "方案讲解.pptx"
SHOTS = HERE / "screenshots"

BOLD, NORMAL = "思源黑体 CN Bold", "思源黑体 CN Normal"
BLUE = RGBColor(0x0A, 0x3A, 0xB8)        # 和模板横幅一致的深蓝
INK = RGBColor(0x1C, 0x20, 0x26)
MUTED = RGBColor(0x5B, 0x63, 0x6E)
TINT = RGBColor(0xEA, 0xF1, 0xFE)        # 卡片底色（浅蓝）
GREEN = RGBColor(0x1E, 0x8E, 0x5A)
RED = RGBColor(0xC8, 0x48, 0x3D)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CYAN = RGBColor(0x7F, 0xE3, 0xFF)


# ---------------------------------------------------------------- 基础

def set_font(run, name: str) -> None:
    run.font.name = name
    rpr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        el = rpr.find(qn(tag))
        if el is None:
            el = etree.SubElement(rpr, qn(tag))
        el.set("typeface", name)


def cn_quotes(t: str) -> str:
    """英文双引号成对换成中文引号“”。"""
    out, open_ = [], True
    for ch in t:
        if ch == '"':
            out.append("“" if open_ else "”")
            open_ = not open_
        else:
            out.append(ch)
    return "".join(out)


def text(slide, x, y, w, h, paras, *, anchor=MSO_ANCHOR.TOP, name="文本", margin=0.05):
    """paras：[(文字, 字号, 粗体, 颜色, 对齐)]；文字里可以有多段（列表里每项一段）。"""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for side in ("margin_left", "margin_right", "margin_top", "margin_bottom"):
        setattr(tf, side, Inches(margin))
    first = True
    for item in paras:
        t, size, bold, color = item[:4]
        align = item[4] if len(item) > 4 else PP_ALIGN.LEFT
        space = item[5] if len(item) > 5 else 4
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.space_after = Pt(space)
        r = p.add_run()
        r.text = cn_quotes(t)
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color
        set_font(r, BOLD if bold else NORMAL)
    return tb


def soft_shadow(shape, blur=14, dist=4, alpha=18, color="0A1A4A"):
    """柔和的投影（向下偏一点、很淡），让卡片和截图浮在页面上。"""
    spPr = shape._element.spPr
    for e in spPr.findall(qn("a:effectLst")):
        spPr.remove(e)
    eff = etree.SubElement(spPr, qn("a:effectLst"))
    sh = etree.SubElement(eff, qn("a:outerShdw"), blurRad=str(Pt(blur)), dist=str(Pt(dist)), dir="5400000",
                          algn="t", rotWithShape="0")
    clr = etree.SubElement(sh, qn("a:srgbClr"), val=color)
    etree.SubElement(clr, qn("a:alpha"), val=str(alpha * 1000))


_ROUNDED = {}


def rounded_png(path: Path, region=None) -> Path:
    """截图（或其中一块）做成圆角、带一圈细边的 PNG，放进 PPT 像产品图一样。"""
    import tempfile
    from PIL import Image, ImageDraw
    key = (str(path), region)
    if key in _ROUNDED:
        return _ROUNDED[key]
    im = Image.open(path).convert("RGBA")
    if region:
        im = im.crop(region)
    k = 2                                           # 放大一倍再画圆角，边缘更平滑
    im = im.resize((im.width * k, im.height * k), Image.LANCZOS)
    r = 14 * k
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r, fill=255)
    im.putalpha(mask)
    ImageDraw.Draw(im).rounded_rectangle((0, 0, im.width - 1, im.height - 1), r, outline=(205, 212, 224, 255), width=2 * k)
    out = Path(tempfile.mkdtemp(prefix="ffppt_")) / "shot.png"
    im.save(out)
    _ROUNDED[key] = out
    return out


SECTION = {"tag": ""}


def footer(slide, dark=False):
    """右下角：章节和页码，统一的小字。"""
    n = len(slide.part.package.presentation_part.presentation.slides)
    tag = SECTION["tag"]
    col = RGBColor(0xC8, 0xD4, 0xF5) if dark else MUTED
    text(slide, 8.4, 7.02, 4.4, 0.3, [(f"{tag}　　{n:02d}", 10, False, col, PP_ALIGN.RIGHT, 0)], name="页脚", margin=0)


def accent(slide, dark=False):
    """标题下面一段短的渐变强调线。"""
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.57), Inches(1.5), Inches(0.9), Inches(0.05))
    s.name = "强调线"
    s.adjustments[0] = 0.5
    s.line.fill.background()
    s.fill.gradient()
    s.fill.gradient_angle = 0
    st = s.fill.gradient_stops
    st[0].color.rgb = CYAN if dark else BLUE
    st[1].color.rgb = RGBColor(0x5C, 0xE1, 0xFF)
    s.shadow.inherit = False


def card(slide, x, y, w, h, fill=TINT, name="卡片"):
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.name = name
    s.adjustments[0] = 0.08
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.fill.background()
    soft_shadow(s, blur=10, dist=2, alpha=10)
    return s


def circle_num(slide, x, y, d, label, fill=BLUE):
    s = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.fill.background()
    s.shadow.inherit = False
    tf = s.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    r.font.size = Pt(14)
    r.font.bold = True
    r.font.color.rgb = WHITE
    set_font(r, BOLD)
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    return s


def arrow(slide, x, y, w=0.35, h=0.3, color=BLUE):
    s = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    return s


def picture(slide, path: Path, x, y, w=None, h=None):
    pic = slide.shapes.add_picture(str(rounded_png(path)), Inches(x), Inches(y), Inches(w) if w else None,
                                   Inches(h) if h else None)
    soft_shadow(pic)
    return pic


def shot_fit(slide, name: str, x, y, w, h):
    """整张截图按框的比例居中裁切，再做成圆角带阴影的图放进去。"""
    from PIL import Image
    iw, ih = Image.open(SHOTS / name).size
    want, have = w / h, iw / ih
    if have > want:
        cw = int(ih * want)
        region = ((iw - cw) // 2, 0, (iw - cw) // 2 + cw, ih)
    else:
        ch = int(iw / want)
        region = (0, (ih - ch) // 2, iw, (ih - ch) // 2 + ch)
    return shot(slide, name, region, x, y, w=w, h=h)


def crop_to(pic, box_w, box_h):
    """按框的比例裁掉图片多余的部分（居中），图片放进框里不变形。"""
    iw, ih = pic.image.size
    want = box_w / box_h
    have = iw / ih
    if have > want:
        cut = (1 - want / have) / 2
        pic.crop_left = pic.crop_right = cut
    else:
        cut = (1 - have / want) / 2
        pic.crop_top = pic.crop_bottom = cut
    pic.width, pic.height = Inches(box_w), Inches(box_h)


def title(slide, t, *, dark=False):
    text(slide, 0.55, 0.92, 12.2, 0.62, [(t, 26, True, WHITE if dark else BLUE)], name="标题", margin=0)


def notes(slide, t):
    slide.notes_slide.notes_text_frame.text = t


# ---------------------------------------------------------------- 各页

def cover(prs):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("标题幻灯片"))
    text(s, 0.97, 1.6, 11.4, 1.5, [("固件工坊 FirmForge", 44, True, WHITE, PP_ALIGN.CENTER, 4),
                                     ("面向嵌入式固件开发的多 Agent 编排与验证平台", 26, True, WHITE, PP_ALIGN.CENTER, 0)], name="题目")
    text(s, 1.17, 3.4, 11.0, 2.3, [
        ("赛题一　Agent 编排创新应用", 18, False, CYAN, PP_ALIGN.CENTER, 10),
        ("团队名称：kami之队", 20, True, WHITE, PP_ALIGN.CENTER),
        ("演讲人：詹学坤", 20, True, WHITE, PP_ALIGN.CENTER),
        ("指导教师：马贵侯", 20, True, WHITE, PP_ALIGN.CENTER),
        ("学校：集美大学诚毅学院", 20, True, WHITE, PP_ALIGN.CENTER)], name="团队信息")
    notes(s, "大家好，我们是集美大学诚毅学院 kami之队，作品是固件工坊，英文名 FirmForge——面向嵌入式固件开发的多 Agent 编排与验证平台。")


def agenda(prs):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("标题和内容"))
    text(s, 1.6, 2.6, 1.6, 2.4, [("目", 54, True, WHITE, PP_ALIGN.CENTER, 0), ("录", 54, True, WHITE, PP_ALIGN.CENTER, 0)],
         name="目录二字")
    items = [("1　选题背景与平台概览", "让 AI 写控制硬件的代码，难在哪里"),
             ("2　架构与协作流程", "六层架构 · 谁做什么、交给谁"),
             ("3　软件亮点", "工程图 · 接线图与实测 · 知识库 · AI 团队与流程设计器"),
             ("4　调度、返工与验证", "任务交给谁 · 错了怎么改 · 怎么防 AI 出错"),
             ("5　核心创新与效果", "把对的信息传给对的人 · 真实演示 · 评测数据")]
    paras = []
    for a, b in items:
        paras.append((a, 20, True, WHITE, PP_ALIGN.LEFT, 0))
        paras.append(("　　" + b, 15, False, CYAN, PP_ALIGN.LEFT, 10))
    text(s, 4.9, 1.9, 7.6, 4.2, paras, anchor=MSO_ANCHOR.MIDDLE, name="目录")


def content(prs, t, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("节标题" if dark else "两栏内容"))
    title(s, t, dark=dark)
    accent(s, dark=dark)
    footer(s, dark=dark)
    return s


ORANGE = RGBColor(0xC2, 0x6A, 0x12)
ORANGE_T = RGBColor(0xFD, 0xF0, 0xE2)
GREEN_T = RGBColor(0xE6, 0xF4, 0xEC)


def shape_arrow(slide, x, y, w, h, kind="right", color=BLUE):
    kinds = {"right": MSO_SHAPE.RIGHT_ARROW, "left": MSO_SHAPE.LEFT_ARROW, "down": MSO_SHAPE.DOWN_ARROW,
             "up": MSO_SHAPE.UP_ARROW}
    s = slide.shapes.add_shape(kinds[kind], Inches(x), Inches(y), Inches(w), Inches(h))
    s.fill.solid()
    s.fill.fore_color.rgb = color
    s.line.fill.background()
    s.shadow.inherit = False
    return s


def slide_background(prs):
    s = content(prs, "选题背景：让 AI 写\"控制硬件的代码\"，难在哪里")
    text(s, 0.6, 1.6, 12.1, 0.7, [("嵌入式代码就是装进芯片、直接控制传感器和电路的程序（手环、空调、工业设备里都有）。"
                                   "让 AI 来写，有三个普通软件没有的难点：", 15, False, INK)])
    pains = [("资料在厂家说明书里", "芯片和传感器怎么用，写在几百页的厂家说明书里。AI 凭记忆写，常把型号相近的芯片记混——"
                                   "就像凭印象背一台没见过的机器的操作步骤。"),
             ("对不对，只有装到板子上才知道", "代码能编译、在电脑上测试通过，都不代表真能用；必须烧进开发板运行，"
                                       "看它实际输出什么，才知道传感器有没有被正确驱动。"),
             ("线怎么接，代码里看不出来", "开发板上每根线接在哪、另一头连着什么，代码里看不出来；线接错了，"
                                     "读到的数据就是乱的，代码写得再好也没用。")]
    for i, (h, b) in enumerate(pains):
        x = 0.6 + i * 4.1
        card(s, x, 2.3, 3.8, 2.75)
        circle_num(s, x + 0.25, 2.5, 0.5, str(i + 1))
        text(s, x + 0.85, 2.42, 2.9, 0.72, [(h, 15, True, BLUE)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 0.25, 3.2, 3.35, 1.8, [(b, 13, False, INK)])
    card(s, 0.6, 5.3, 12.1, 0.95, fill=ORANGE_T)
    text(s, 0.8, 5.33, 11.8, 0.9, [
        ("一个真实例子", 14, True, ORANGE, PP_ALIGN.LEFT, 2),
        ("我们买到的姿态传感器，实际是另一个兼容型号。按说明书写的代码会把它认错、温度也算错——只有装到板子上实测才发现。"
         "所以平台里的硬件事实都要有出处、能被实测验证，不能只听 AI 的。", 13, False, INK)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "嵌入式代码是控制硬件的程序。让 AI 写有三个难点：资料在说明书里、对不对只有装到板子上才知道、线怎么接代码里看不出来。"
             "举个例子：我们的传感器实际是另一个兼容型号，按说明书写的代码会认错，只有实测才发现。")


def slide_overview(prs):
    s = content(prs, "固件工坊：一句话需求 → 在真实开发板上验证过的程序", dark=True)
    stats = [("67%", "难题通过率", "改进前 39%，单个 AI 只有 33%"), ("12/12", "没见过的新题全部通过", "4 道平台从没做过的新题"),
             ("0.83 元", "一个真实需求的花费", "从一句话到装板验证，19 分钟"), ("632", "自动化测试用例全部通过", "保证平台本身不出错")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.6 + i * 3.1
        text(s, x, 2.0, 2.9, 1.0, [(big, 44, True, CYAN, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.05, 2.9, 0.5, [(lab, 16, True, WHITE, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.55, 2.9, 0.5, [(sub, 12, False, RGBColor(0xC9, 0xD8, 0xFF), PP_ALIGN.CENTER, 0)])
    text(s, 0.9, 4.55, 11.5, 1.5, [
        ("你用一句话说要在开发板上实现什么功能；平台把任务拆开，安排多个 AI 角色分工写代码、写测试，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("每一步都用编译器、开发板实测这些\"不会说谎\"的方式检查；没通过就找出错在哪、交给对应的 AI 改，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("最后装到板子上实际运行，交付一个能直接用的程序和一份验证报告。", 16, False, WHITE, PP_ALIGN.CENTER, 2)])
    notes(s, "四个数字：难题通过率从 39% 提升到 67%；没见过的新题全部通过；一个真实需求不到一块钱、二十多分钟；632 个自动化测试保证平台本身可靠。")


def slide_arch(prs):
    s = content(prs, "总体架构：AI 负责干活，对错由不依赖 AI 的检查来判")
    layers = [("操作界面", "桌面软件：对话、知识库、工作流设计器、AI 团队、设置；也可以用命令行，或让别的 AI 通过 MCP 调用"),
              ("流程编排", "按工作流文件一步步执行；决定先做哪个模块、交给哪个 AI；没通过时组织返工"),
              ("AI 团队", "10 个分工不同的 AI 角色：查资料、核对、设计、写代码（快/强两档）、写测试、诊断、审查……"),
              ("资料与事实", "厂家说明书、开发框架源码、我们在板子上的实测记录；人确认过的硬件规则；每个项目的接线记录"),
              ("客观检查", "规范检查、编译、装到开发板上跑测试、接线实测、\"故意改坏\"检验测试、主程序上板运行"),
              ("底层工具", "大模型接口（DeepSeek）、开发板编译烧录工具、串口；全部只用 Python 标准库实现")]
    for i, (n, d) in enumerate(layers):
        y = 1.8 + i * 0.73
        hl = n in ("客观检查", "资料与事实")
        card(s, 0.6, y, 2.3, 0.6, fill=BLUE if hl else TINT)
        text(s, 0.6, y, 2.3, 0.6, [(n, 16, True, WHITE if hl else BLUE, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 3.1, y, 9.7, 0.6, [(d, 14, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 6.25, 12.1, 0.5, [("深色两层是我们的重点：硬件事实要有出处，对错由客观检查判定；工具缺失或没接板子时如实标\"跳过\"，绝不假装通过", 13, False, MUTED)])
    notes(s, "从上到下六层。深色两层是重点：AI 只负责干活，硬件事实要有出处，对错由不依赖 AI 的客观检查来判。")


def slide_agents(prs):
    s = content(prs, "AI 团队：每个角色各管一件事，可以用图形界面编排流程")
    rows = [("设计师 designer", "把需求拆成小模块，定好验收标准和接线"), ("程序员 coder（快档 / 强档）", "写代码；简单的交便宜的快档，难的交强档"),
            ("测试员 tester", "写测试；看不到程序员的代码，防止\"自己考自己\""), ("诊断员 diagnoser", "没通过时判断错在代码、测试、设计还是硬件"),
            ("资料员 researcher 等", "去说明书里查硬件资料，另一个 AI 逐条核对原文"), ("审查员 reviewer", "通过后再把代码审一遍")]
    for i, (a, d) in enumerate(rows):
        y = 1.8 + i * 0.72
        card(s, 0.6, y, 5.3, 0.62)
        text(s, 0.75, y, 2.15, 0.62, [(a, 12, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 2.95, y, 2.9, 0.62, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 6.17, 5.3, 0.45, [("每个角色都有版本、用哪档模型、能用哪些工具、做过多少次、成功率多少", 11, False, MUTED)])
    shot_fit(s, "08_工作流设计器.png", 6.2, 1.8, 6.55, 3.95)
    text(s, 6.2, 5.85, 6.55, 0.8, [("工作流设计器：像画流程图一样拖拽步骤，支持先后、同时、条件、循环、停下来等人确认；"
                                   "保存前平台会把流程空跑几遍，确认画出来的就是实际执行的", 12, False, MUTED)])
    notes(s, "平台里有 10 个 AI 角色，各管一件事。比如测试员看不到程序员的代码，避免自己考自己。流程可以在图形界面里拖拽编排。")


def slide_flow(prs):
    s = content(prs, "协作流程：从一句话需求到交付，谁做什么、交给谁")
    # 图例
    for i, (lab, fill, col) in enumerate((("AI 角色", TINT, BLUE), ("平台自动检查", GREEN_T, GREEN), ("需要你确认", ORANGE_T, ORANGE))):
        x = 7.3 + i * 1.85
        card(s, x, 1.58, 0.3, 0.26, fill=fill)
        text(s, x + 0.38, 1.52, 1.45, 0.38, [(lab, 11, False, col, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    steps = [  # (编号, 谁, 做什么, 交出什么, 类型)
        ("1", "你", "用一句话说要实现什么", "需求", "user"),
        ("2", "资料员 + 核对员", "去说明书里查要用的硬件资料，逐条核对原文", "硬件规则（你确认）", "ai"),
        ("3", "设计师", "拆成小模块，定验收标准，写清每根线怎么接", "设计和接线清单（你确认）", "ai"),
        ("4", "你 + 平台", "你按清单接线，平台烧小程序实测每根线", "接线确认无误", "user"),
        ("5", "程序员 和 测试员", "同时写代码和测试；测试员看不到代码", "代码 + 测试", "ai"),
        ("6", "平台", "编译、装到开发板上跑测试", "检查报告", "check"),
        ("7", "诊断员", "没通过时判断错在哪，交回对应的角色修改", "修改指令", "ai"),
        ("8", "程序员 + 平台", "所有模块通过后写主程序，装板实际运行两轮", "可直接用的程序 + 验证报告", "check")]
    fills = {"ai": (TINT, BLUE), "check": (GREEN_T, GREEN), "user": (ORANGE_T, ORANGE)}
    bw, bh, gap = 2.75, 1.45, 0.37
    colx = [0.6 + c * (bw + gap) for c in range(4)]
    place = {"1": (colx[0], 1.95), "2": (colx[1], 1.95), "3": (colx[2], 1.95), "4": (colx[3], 1.95),
             "5": (colx[3], 3.9), "6": (colx[2], 3.9), "8": (colx[0], 3.9)}
    by_num = {st[0]: st for st in steps}

    def box(num, x, y, w, h):
        _, who, what, out, kind = by_num[num]
        fill, accent = fills[kind]
        card(s, x, y, w, h, fill=fill)
        circle_num(s, x + 0.12, y + 0.1, 0.4, num, fill=accent)
        text(s, x + 0.6, y + 0.06, w - 0.7, 0.48, [(who, 14, True, accent, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 0.15, y + 0.55, w - 0.3, 0.55, [(what, 12, False, INK, PP_ALIGN.LEFT, 0)])
        text(s, x + 0.15, y + h - 0.4, w - 0.3, 0.36, [("→ " + out, 11, True, accent, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    for num, (x, y) in place.items():
        box(num, x, y, bw, bh)
    for k in range(3):                                           # 1→2→3→4
        shape_arrow(s, colx[k] + bw + 0.04, 1.95 + bh / 2 - 0.14, gap - 0.08, 0.28, "right")
    shape_arrow(s, colx[3] + bw / 2 - 0.15, 1.95 + bh + 0.05, 0.3, 0.45, "down")      # 4↓5
    shape_arrow(s, colx[3] - gap + 0.04, 3.9 + bh / 2 - 0.14, gap - 0.08, 0.28, "left")  # 5→6
    # 6 → 8：全部通过（中间一格是箭头和说明）
    ax0, ax1 = colx[0] + bw + 0.08, colx[2] - 0.08
    shape_arrow(s, ax0, 3.9 + bh / 2 - 0.16, ax1 - ax0, 0.32, "left", color=GREEN)
    text(s, ax0, 3.9 + bh / 2 - 0.62, ax1 - ax0, 0.4, [("全部模块通过", 12, True, GREEN, PP_ALIGN.CENTER, 0)])
    # 7：没通过时的分支，在 6 和 5 的下面
    x7, y7, w7, h7 = colx[2], 5.75, bw * 2 + gap, 0.82
    _, who, what, out, kind = by_num["7"]
    card(s, x7, y7, w7, h7, fill=TINT)
    circle_num(s, x7 + 0.12, y7 + 0.2, 0.4, "7", fill=BLUE)
    text(s, x7 + 0.6, y7 + 0.04, w7 - 0.7, 0.74,
         [("诊断员：判断错在哪，交回对应的角色修改", 14, True, BLUE, PP_ALIGN.LEFT, 2),
          ("错在代码 → 程序员；错在测试 → 测试员；验收标准定错 → 回到 3 设计师；次数用完由你决定接着修还是放行",
           11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    shape_arrow(s, colx[2] + bw / 2 - 0.15, 3.9 + bh + 0.03, 0.3, 0.42, "down", color=RED)   # 6↓7 没通过
    text(s, colx[2] + bw / 2 + 0.2, 3.9 + bh + 0.02, 1.2, 0.4, [("没通过", 11, True, RED, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    shape_arrow(s, colx[3] + bw / 2 - 0.15, 3.9 + bh + 0.03, 0.3, 0.42, "up", color=ORANGE)  # 7↑5 修改后重测
    text(s, colx[3] + bw / 2 + 0.2, 3.9 + bh + 0.02, 1.6, 0.4, [("改完再检查", 11, True, ORANGE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "这一页是完整的协作流程。第一行是准备：你提需求，资料员查说明书、设计师拆模块定接线，你按清单接线、平台实测。"
             "第二行是干活：程序员和测试员同时写，平台装到板子上检查，没通过诊断员判断错在哪、交回对应的人改，"
             "全部通过后写主程序装板运行，交付。橙色是需要你确认的地方。")


def slide_sched(prs):
    s = content(prs, "智能调度：每个任务交给最合适的 AI")
    card(s, 0.6, 1.8, 6.6, 1.35)
    text(s, 0.8, 1.85, 6.3, 1.25, [("得分 = 成功率 − 花费 − 忙碌程度 + 给少用者的机会", 17, True, BLUE, PP_ALIGN.CENTER, 4),
                                   ("每次分配任务时，给每个候选 AI 按这四项打分，取最高的", 12, False, MUTED, PP_ALIGN.CENTER, 0)],
         anchor=MSO_ANCHOR.MIDDLE)
    pts = [("看历史成绩", "同一类任务（比如\"第一次写硬件代码\"）谁做得好就优先交给谁，学到\"首次用便宜的快档、返工换强档\"这类规律"),
           ("看花费和忙闲", "贵的扣分，手上活多的扣分"), ("给新手机会", "做得少的 AI 适当给机会，避免一直只用一个"),
           ("连续失败就换人", "同一模块上一直修不好的 AI，下一轮换掉")]
    for i, (h, d) in enumerate(pts):
        y = 3.4 + i * 0.78
        circle_num(s, 0.6, y + 0.05, 0.42, str(i + 1))
        text(s, 1.15, y, 6.0, 0.75, [(h, 15, True, INK, PP_ALIGN.LEFT, 0), (d, 12, False, MUTED, PP_ALIGN.LEFT, 0)])
    cd = CategoryChartData()
    cd.categories = ["一个接一个", "同时 2 个", "同时 4 个", "同时 8 个"]
    cd.add_series("4 个模块总用时（秒）", (20.7, 11.5, 7.4, 5.5))
    _, hs = shot(s, "15_调度明细.png", (1000, 700, 1592, 848), 7.5, 1.8, w=5.3)
    text(s, 7.5, 1.8 + hs + 0.05, 5.3, 0.35, [("软件里每个模块的\"调度\"标签：为什么交给这个 AI，每个候选的得分", 11, False, MUTED)])
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.5), Inches(1.8 + hs + 0.45), Inches(5.3),
                            Inches(5.7 - (1.8 + hs + 0.45)), cd)
    ch = gf.chart
    ch.has_title = True
    ch.chart_title.text_frame.text = "并行调度：4 个模块总用时（秒，越低越好）"
    ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
    ch.has_legend = False
    pl = ch.plots[0]
    pl.has_data_labels = True
    pl.data_labels.font.size = Pt(12)
    pl.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    pl.series[0].format.fill.solid()
    pl.series[0].format.fill.fore_color.rgb = BLUE
    ch.value_axis.has_major_gridlines = False
    ch.value_axis.visible = False
    ch.category_axis.tick_labels.font.size = Pt(11)
    text(s, 7.5, 5.8, 5.3, 0.7, [("模拟测试（每次调用 AI 固定等 2 秒，只比调度）：几个 AI 同时干活，4 个模块时快 3.7 倍", 12, False, MUTED)])
    notes(s, "调度就是决定每个任务交给哪个 AI。按历史成绩、花费、忙闲和给新手机会打分。右边是模拟测试：多个 AI 同时干活，4 个模块时快 3.7 倍。")


def slide_loop(prs):
    s = content(prs, "自动返工：找出错在哪，交给对的人改，并检查改了没有")
    branches = [("代码错了", "交给程序员改；一直改不好就换一个 AI"), ("测试错了", "交给测试员改；说要删的测试，平台核对真删了"),
                ("资料错了", "退回去重新核对这条硬件资料"), ("标准定错了", "交回设计师改验收标准，测试重写"),
                ("怀疑硬件", "先由平台烧小程序实测接线；线都正常就继续从代码里找原因")]
    card(s, 0.6, 1.9, 2.4, 3.9, fill=BLUE)
    text(s, 0.6, 1.9, 2.4, 3.9, [("检查没通过", 18, True, WHITE, PP_ALIGN.CENTER, 6), ("↓", 18, True, WHITE, PP_ALIGN.CENTER, 6),
                                 ("诊断员", 18, True, WHITE, PP_ALIGN.CENTER, 6), ("判断错在哪", 14, False, WHITE, PP_ALIGN.CENTER)],
         anchor=MSO_ANCHOR.MIDDLE)
    for i, (h, d) in enumerate(branches):
        y = 1.9 + i * 0.8
        arrow(s, 3.1, y + 0.2, 0.4, 0.28)
        card(s, 3.6, y, 4.4, 0.68)
        text(s, 3.7, y, 1.15, 0.68, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 4.85, y, 3.1, 0.68, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    extra = [("故意改坏，检查测试是否偷懒", "通过后平台故意把代码改错几处，测试发现不了就让测试员补测试"),
             ("误判硬件：9 次 → 0 次", "几个 AI 用同一个模型，会一起看错；用实测纠正"),
             ("错题本", "修好的问题记下来，以后遇到类似的拿出来参考"),
             ("实在修不好，交给你决定", "先讲清从第一次失败到现在发生了什么、放行有什么风险，再由你选接着修或放行")]
    for i, (h, d) in enumerate(extra):
        y = 1.9 + i * 0.98
        text(s, 8.3, y, 4.5, 0.95, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 2), (d, 12, False, INK, PP_ALIGN.LEFT, 0)])
    notes(s, "没通过时，诊断员判断错在哪，平台交给对应的角色修，还会检查交代的事有没有做。怀疑硬件时先实测，不靠 AI 猜。")


def slide_innovation(prs):
    s = content(prs, "核心创新：多个 AI 协作，关键是把对的信息传给对的人", dark=True)
    text(s, 0.6, 1.75, 5.9, 4.7, [
        ("一次失败带来的发现", 18, True, CYAN, PP_ALIGN.LEFT, 8),
        ("有个读电压的模块试了 4 次都没通过。我们把记录一条条查完发现：代码从头到尾没改过、一直是对的，失败全在测试上。原因是：", 14, False, WHITE, PP_ALIGN.LEFT, 8),
        ("· 设计师选的引脚上，原来插着另一根线，它不知道", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("· 接线只写了\"用哪个引脚\"，没写另一头接什么", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("· 平台把\"打算这么接\"当成\"已经接好\"告诉了测试员", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("· 验收标准要求\"把线接到地上时……\"，自动测试根本做不到", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("AI 们不缺讨论，缺的是正确的事实。", 15, True, CYAN, PP_ALIGN.LEFT, 0)])
    fixes = [("每个对话单独记录", "每个项目的接线单独存放，互不干扰"), ("设计必须写清接线", "每根线两头接什么、为什么、接好应该测到什么"),
             ("事实分等级", "\"计划\"→\"你说接好了\"→\"实测确认\"，只有平台能升级；没实测的不许拿来当判断依据"),
             ("交代的事要核对", "说要删的测试平台查有没有删；标准定错了可以退回设计师")]
    for i, (h, d) in enumerate(fixes):
        y = 1.8 + i * 1.12
        card(s, 6.9, y, 5.9, 0.98, fill=RGBColor(0x14, 0x4C, 0xD6))
        text(s, 7.1, y + 0.05, 5.6, 0.9, [(h, 16, True, WHITE, PP_ALIGN.LEFT, 2), (d, 12, False, RGBColor(0xD9, 0xE6, 0xFF), PP_ALIGN.LEFT, 0)],
             anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "这是我们最重要的发现：多个 AI 协作，问题往往不是讨论不够，而是拿到的事实是错的。所以我们让每条事实带等级，由平台根据实测来升级。")


def slide_demo(prs):
    s = content(prs, "运行效果：一句话需求，19 分钟、0.83 元交付装板验证过的程序")
    shot_fit(s, "01_对话_一次完整运行.png", 0.6, 1.8, 7.2, 4.5)
    rows = [("需求", "每 2 秒读 MPU6050 加速度和芯片温度，记录最大加速度并断电保存，重启后先打印上次的最大值"),
            ("接线", "故意拔掉传感器数据线：自检发现没应答、马上停下；插回后应答了才写代码"),
            ("拆分", "传感器驱动、片内温度、存储、换算、最大值 5 个模块，分别 3、2、3、1、1 次通过"),
            ("返工", "测试写错交回测试员改；故意改坏代码时测试没发现，就让测试员补测试"),
            ("交付", "主程序装板跑两轮核对输出；交付可以直接烧录的完整工程 + 验证报告")]
    for i, (h, d) in enumerate(rows):
        y = 1.8 + i * 0.9
        card(s, 8.05, y, 4.75, 0.8)
        text(s, 8.15, y, 0.75, 0.8, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 8.9, y, 3.85, 0.8, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "这是演示视频里那次真实运行：快档模型 19 分钟、0.83 元，5 个模块和主程序全部通过，并在板子上实际运行验证。")


def shot(slide, name: str, region, x, y, *, w=None, h=None):
    """截图的一块（像素区域 left, top, right, bottom），按宽或高放进页面，不变形。返回 (宽, 高)。"""
    from PIL import Image
    path = SHOTS / name
    iw, ih = Image.open(path).size
    l, t, r, b = region
    aspect = (r - l) / (b - t)
    if w is None:
        w = h * aspect
    if h is None:
        h = w / aspect
    pic = slide.shapes.add_picture(str(rounded_png(path, tuple(region))), Inches(x), Inches(y), Inches(w), Inches(h))
    soft_shadow(pic)
    return w, h


def bullets(slide, x, y, w, h, items, *, head=None, size=13, gap=9):
    paras = [(head, 18, True, BLUE, PP_ALIGN.LEFT, 8)] if head else []
    for it in items:
        if isinstance(it, tuple):
            paras.append((it[0], size + 1, True, INK, PP_ALIGN.LEFT, 1))
            paras.append((it[1], size - 1, False, MUTED, PP_ALIGN.LEFT, gap))
        else:
            paras.append((it, size, False, INK, PP_ALIGN.LEFT, gap))
    return text(slide, x, y, w, h, paras)


def slide_map(prs):
    s = content(prs, "亮点：工程图——整个项目一眼看清")
    shot(s, "14_工程图展开_带传感器.png", (0, 0, 1600, 960), 0.6, 1.75, w=7.9)
    bullets(s, 8.8, 1.75, 4.0, 4.9, [
        ("自动画出整个工程", "设计好以后，平台自动画出每个模块在哪一层、提供哪些接口、连到芯片的哪个外设、外面接了什么器件"),
        ("状态实时变色", "每个模块正在写、在测、通过、没通过，图上随时看得到；测试通过了几个也写在上面"),
        ("\"已实测确认\"", "器件旁边的这个标记，表示上板时平台确认它真的回应了，不是 AI 说接好了就算"),
        ("点一下看细节", "点任何一个模块，下面直接看它每一次的尝试、诊断、上板输出和代码")], size=13)
    notes(s, "这是工程图。设计好以后平台自动画出整个工程：模块、接口、芯片外设、外接器件。模块状态实时变色，器件旁标着已实测确认。点一个模块，下面就是它的修正过程。")


def slide_wiring(prs):
    s = content(prs, "亮点：接线图 + 接线实测——线接得对不对，平台自己测")
    w1, _ = shot(s, "13_接线图_带传感器.png", (1000, 95, 1590, 590), 0.6, 1.75, h=4.0)
    text(s, 0.6, 5.85, w1, 0.7, [("接线图：芯片每个引脚接到哪个器件的哪个脚，一目了然；绿字表示这根线已在板子上实测确认", 12, False, MUTED)])
    x2 = 0.6 + w1 + 0.35
    w2 = 12.75 - x2
    _, h2 = shot(s, "05_接线清单与自检没对上.png", (335, 188, 985, 415), x2, 1.75, w=w2)
    bullets(s, x2, 1.75 + h2 + 0.2, w2, 6.6 - (1.75 + h2 + 0.2), [
        "写代码之前，平台先列出接线清单：每根线两头接哪里、为什么",
        "你接好后，平台烧一个自己写的小程序去实测：演示时故意拔掉传感器数据线，自检发现 MPU6050 没应答，马上停下",
        "插回去再测、器件应答了才开始写代码——没有这一步时，AI 曾对着一根没接好的线白试了 4 次"], size=13, gap=7)
    notes(s, "左边是接线图，每个引脚接到哪个器件的哪个脚都画出来了。右边是接线清单和自检结果：演示时故意拔掉数据线，平台实测发现传感器没应答，就停下来告诉你，接好了才写代码。")


def slide_kb(prs):
    s = content(prs, "亮点：知识库——每条硬件事实都有出处，说明书和实测对不上会标出来")
    _, h = shot(s, "16_知识库_规则出处.png", (336, 55, 1592, 400), 0.6, 1.75, w=12.15)
    y = 1.75 + h + 0.3
    items = [("按目录管理", "芯片、开发框架、外接器件、开发板分门别类；规则、资料、实测记录都能搜"),
             ("每条规则带原文", "引用说明书哪一页的哪句话；平台核对原文真的存在，人确认后才给 AI 用"),
             ("说明书和实测对不上", "比如传感器编号说明书写 0x68、实测是 0x70：两种都列出，以实测为准")]
    for i, (hd, d) in enumerate(items):
        x = 0.6 + i * 4.1
        card(s, x, y, 3.85, 6.7 - y)
        text(s, x + 0.15, y + 0.08, 3.55, 6.7 - y - 0.16, [(hd, 15, True, BLUE, PP_ALIGN.LEFT, 3), (d, 12, False, INK, PP_ALIGN.LEFT, 0)])
    notes(s, "知识库里每条硬件规则都带说明书原文出处，平台会核对原文确实存在，人确认后才交给 AI。说明书和实测对不上的地方会单独标出来，以实测为准。")


def slide_failed(prs):
    s = content(prs, "亮点：实在修不好时，先讲清楚，再交给你决定")
    w1, _ = shot(s, "06_没通过的讲解与人工放行.png", (335, 60, 985, 690), 0.6, 1.72, h=4.95)
    x2 = 0.6 + w1 + 0.4
    bullets(s, x2, 1.75, 12.75 - x2, 4.9, [
        ("从第一次失败到现在发生了什么", "平台按运行记录自动整理（不经过 AI，讲解本身不会出错）：每次卡在哪、诊断怎么说、交给谁改了"),
        ("一句话点出关键", "比如\"代码 4 次都没改过，问题都出在测试或验收标准上\""),
        ("放行有什么风险", "哪些要求没被证明、交付的是哪一版、会影响哪些后续模块"),
        ("三个选择", "接着修（再试 3 次、换更强的 AI、可以带一句提示）／人工放行（必须写理由并再确认一次，结论如实记\"人工放行\"）／放弃")],
        size=13, gap=8)
    notes(s, "AI 实在修不好时，平台不是简单报错，而是先讲清楚经过和风险，再让你选接着修、人工放行或者放弃。放行必须写理由，结论如实记为人工放行，不算通过。")


def slide_ai_verify(prs):
    s = content(prs, "AI 用在哪里，又怎么防它出错")
    rows = [("查资料", "在说明书里检索、提炼硬件规则", "核对原文 + 你确认"),
            ("设计", "拆模块、定标准、定接线", "做不到的标准、占用已有的线 → 退回"),
            ("写代码 / 测试", "快档 / 强档模型 + 调用工具", "互相看不到 + 故意改坏 + 隐藏考题"),
            ("诊断", "判断错在哪", "怀疑硬件先实测；交代的事平台核对"),
            ("集成", "写主程序和应有的输出", "要输出什么第一次说了就锁定"),
            ("工具与记忆", "MCP 对接外部工具；错题本", "外部工具默认先问你；评测时只读")]
    hdr = [("环节", 1.7), ("AI 做什么", 3.0), ("怎么防它出错", 3.6)]
    x = 0.6
    for h, w in hdr:
        card(s, x, 1.8, w - 0.08, 0.45, fill=BLUE)
        text(s, x, 1.8, w - 0.08, 0.45, [(h, 13, True, WHITE, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
        x += w
    for i, r in enumerate(rows):
        y = 2.33 + i * 0.62
        x = 0.6
        for (c, (_, w)) in zip(r, hdr):
            card(s, x, y, w - 0.08, 0.54)
            text(s, x + 0.08, y, w - 0.24, 0.54, [(c, 12, c == r[0], BLUE if c == r[0] else INK, PP_ALIGN.LEFT, 0)],
                 anchor=MSO_ANCHOR.MIDDLE)
            x += w
    checks = ["规范检查：引脚、规则写得对不对", "编译：能不能变成芯片能运行的程序", "装板测试：烧进开发板逐个运行",
              "接线实测：每根线真的接好了没有", "故意改坏：测试能不能发现错误", "主程序运行：开机、重启各跑一次",
              "隐藏考题：只在评测用，AI 看不到"]
    card(s, 9.2, 1.8, 3.6, 4.85, fill=TINT)
    text(s, 9.35, 1.88, 3.35, 4.7, [("七道客观检查（不经过 AI）", 14, True, BLUE, PP_ALIGN.LEFT, 6)]
         + [(c, 12, False, INK, PP_ALIGN.LEFT, 5) for c in checks]
         + [("判断对错、讲解失败、确认硬件事实、决定放行，这几件事不交给 AI", 12, True, ORANGE, PP_ALIGN.LEFT, 0)])
    notes(s, "AI 在每个环节做什么、可能出什么错、我们怎么防；右边是七道不经过 AI 的客观检查。判断对错这类事不交给 AI。")


def slide_eval_perf(prs):
    s = content(prs, "量化评测：难题通过率 39% → 67%")
    cd = CategoryChartData()
    cd.categories = ["单个 AI", "多个 AI·不给资料", "完整系统"]
    cd.add_series("改进前", (33, 33, 39))
    cd.add_series("改进后", (33, 61, 67))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.75), Inches(6.3), Inches(3.55), cd)
    ch = gf.chart
    ch.has_title = True
    ch.chart_title.text_frame.text = "6 道上板难题的通过率（%，每种方案 18 次）"
    ch.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(13)
    ch.has_legend = True
    ch.legend.position = XL_LEGEND_POSITION.BOTTOM
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(11)
    pl = ch.plots[0]
    pl.has_data_labels = True
    pl.data_labels.font.size = Pt(12)
    pl.data_labels.position = XL_LABEL_POSITION.OUTSIDE_END
    pl.data_labels.number_format = '0"%"'
    pl.data_labels.number_format_is_linked = False
    for ser, col in zip(pl.series, (RGBColor(0x9F, 0xB6, 0xE8), BLUE)):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = col
    ch.value_axis.has_major_gridlines = False
    ch.value_axis.visible = False
    ch.value_axis.maximum_scale = 80
    ch.category_axis.tick_labels.font.size = Pt(12)
    bullets(s, 7.2, 1.75, 5.6, 3.6, [
        "15 道上板题，每题有标准答案和 AI 看不到的考题；每种方案每题跑 3 次",
        "完整系统提升 28 个百分点，经统计检验不是运气；\"不给资料\"的提升刚过线，结论还不稳",
        "把硬件问题误判、提前放弃：9 次 → 0 次",
        "4 道没见过的新题全部通过；但这几题较简单，单个 AI 也能过，不能据此说多个 AI 更好"], size=13, gap=7)
    stats = [("4.5 秒", "AI 回一次话（中位数）"), ("8.2 秒", "一次编译 + 装板测试"), ("3.7 倍", "4 个 AI 同时干活提速"),
             ("632 个", "平台自动化测试全过")]
    for i, (big, lab) in enumerate(stats):
        x = 0.6 + i * 3.08
        card(s, x, 5.5, 2.9, 1.1)
        text(s, x, 5.52, 2.9, 0.6, [(big, 24, True, BLUE, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, x, 6.1, 2.9, 0.42, [(lab, 12, False, INK, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "难题上完整系统从 39% 提升到 67%，经统计检验不是运气；下面是性能：AI 回一次话 4.5 秒，一次检查 8 秒左右，多个 AI 同时干活快 3.7 倍。")


def closing(prs):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("标题幻灯片"))
    text(s, 3.2, 2.2, 6.9, 2.2, [("谢谢观看", 48, True, WHITE, PP_ALIGN.CENTER, 4), ("Thanks", 28, False, WHITE, PP_ALIGN.CENTER, 12),
                                 ("github.com/z-xxkk/firmforge", 16, False, CYAN, PP_ALIGN.CENTER, 0)], name="结尾")


# ---------------------------------------------------------------- 组装

def main() -> None:
    prs = Presentation(str(TEMPLATE))
    ids = prs.slides._sldIdLst
    for sid in list(ids):                        # 删掉模板自带的 6 页示例（版式和背景留着）
        prs.part.drop_rel(sid.get(qn("r:id")))
        ids.remove(sid)
    cover(prs)
    agenda(prs)
    groups = [("01  选题背景与平台概览", (slide_background, slide_overview)),
              ("02  架构与协作流程", (slide_arch, slide_flow)),
              ("03  软件亮点", (slide_map, slide_wiring, slide_kb, slide_agents)),
              ("04  调度、返工与验证", (slide_sched, slide_loop, slide_failed, slide_ai_verify)),
              ("05  核心创新与效果", (slide_innovation, slide_demo, slide_eval_perf))]
    for tag, builds in groups:
        SECTION["tag"] = tag
        for build in builds:
            build(prs)
    closing(prs)
    prs.save(str(OUT))
    print(f"已生成 {OUT}，共 {len(prs.slides)} 页（正文 {len(prs.slides) - 3} 页）")


if __name__ == "__main__":
    main()

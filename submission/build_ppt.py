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


def card(slide, x, y, w, h, fill=TINT, name="卡片"):
    s = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    s.name = name
    s.adjustments[0] = 0.08
    s.fill.solid()
    s.fill.fore_color.rgb = fill
    s.line.fill.background()
    s.shadow.inherit = False
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
    pic = slide.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w) if w else None, Inches(h) if h else None)
    pic.line.color.rgb = RGBColor(0xC9, 0xCE, 0xD4)
    pic.line.width = Pt(0.75)
    return pic


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
    text(s, 0.97, 1.75, 11.4, 1.3, [("FirmForge：面向嵌入式固件开发的", 34, True, WHITE, PP_ALIGN.CENTER, 0),
                                     ("多 Agent 编排与验证平台", 34, True, WHITE, PP_ALIGN.CENTER, 0)], name="题目")
    text(s, 1.17, 3.4, 11.0, 2.3, [
        ("赛题一　Agent 编排创新应用", 18, False, CYAN, PP_ALIGN.CENTER, 10),
        ("团队名称：kami之队", 20, True, WHITE, PP_ALIGN.CENTER),
        ("演讲人：詹学坤", 20, True, WHITE, PP_ALIGN.CENTER),
        ("指导教师：马贵侯", 20, True, WHITE, PP_ALIGN.CENTER),
        ("学校：集美大学诚毅学院", 20, True, WHITE, PP_ALIGN.CENTER)], name="团队信息")
    notes(s, "大家好，我们是集美大学诚毅学院 kami之队，作品是 FirmForge——面向嵌入式固件开发的多 Agent 编排与验证平台。")


def agenda(prs):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("标题和内容"))
    text(s, 1.6, 2.6, 1.6, 2.4, [("目", 54, True, WHITE, PP_ALIGN.CENTER, 0), ("录", 54, True, WHITE, PP_ALIGN.CENTER, 0)],
         name="目录二字")
    items = [("1　选题背景", "让 AI 写控制硬件的代码，难在哪里"),
             ("2　平台架构与协作流程", "六层架构 · AI 团队 · 谁做什么、交给谁"),
             ("3　调度、返工与验证", "任务交给谁 · 错了怎么改 · 对错怎么判"),
             ("4　核心创新：把对的信息传给对的人", "事实分等级 · 接线实测 · 交代的事要核对"),
             ("5　运行效果与评测", "真实演示 · 与单个 AI 对比 · 性能")]
    paras = []
    for a, b in items:
        paras.append((a, 20, True, WHITE, PP_ALIGN.LEFT, 0))
        paras.append(("　　" + b, 15, False, CYAN, PP_ALIGN.LEFT, 10))
    text(s, 4.9, 1.9, 7.6, 4.2, paras, anchor=MSO_ANCHOR.MIDDLE, name="目录")


def content(prs, t, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("节标题" if dark else "两栏内容"))
    title(s, t, dark=dark)
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
    s = content(prs, "FirmForge：一句话需求 → 在真实开发板上验证过的程序", dark=True)
    stats = [("67%", "难题通过率", "改进前 39%，单个 AI 只有 33%"), ("12/12", "没见过的新题全部通过", "4 道平台从没做过的新题"),
             ("0.86 元", "一个真实需求的花费", "从一句话到装板验证，22 分钟"), ("625", "自动化测试用例全部通过", "保证平台本身不出错")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.6 + i * 3.1
        text(s, x, 2.0, 2.9, 1.0, [(big, 44, True, CYAN, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.05, 2.9, 0.5, [(lab, 16, True, WHITE, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.55, 2.9, 0.5, [(sub, 12, False, RGBColor(0xC9, 0xD8, 0xFF), PP_ALIGN.CENTER, 0)])
    text(s, 0.9, 4.55, 11.5, 1.5, [
        ("你用一句话说要在开发板上实现什么功能；平台把任务拆开，安排多个 AI 角色分工写代码、写测试，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("每一步都用编译器、开发板实测这些\"不会说谎\"的方式检查；没通过就找出错在哪、交给对应的 AI 改，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("最后装到板子上实际运行，交付一个能直接用的程序和一份验证报告。", 16, False, WHITE, PP_ALIGN.CENTER, 2)])
    notes(s, "四个数字：难题通过率从 39% 提升到 67%；没见过的新题全部通过；一个真实需求不到一块钱、二十多分钟；625 个自动化测试保证平台本身可靠。")


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
    pic = picture(s, SHOTS / "08_工作流设计器.png", 6.2, 1.8)
    crop_to(pic, 6.55, 3.95)
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
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.5), Inches(1.8), Inches(5.3), Inches(3.9), cd)
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


def slide_verify(prs):
    s = content(prs, "结果验证：对错由客观检查判定，不让 AI 自己给自己打分")
    layers = [("规范检查", "引脚只能用平台给的名字，必须遵守确认过的硬件规则"), ("编译", "代码能不能变成芯片能运行的程序"),
              ("装板测试", "烧进开发板，逐个测试单独运行，读回结果"), ("接线实测", "每根线是否真的接好、读数是否正常"),
              ("故意改坏", "测试能不能发现被故意改错的代码"), ("主程序运行", "装板跑两轮：第一次开机、断电重启后各看一次"),
              ("隐藏考题", "只在评测时用，AI 完全看不到，像考官手里的试卷")]
    for i, (h, d) in enumerate(layers):
        y = 1.8 + i * 0.64
        card(s, 0.6, y, 6.3, 0.56)
        text(s, 0.75, y, 1.6, 0.56, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 2.35, y, 4.5, 0.56, [(d, 12, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 7.3, 1.8, 5.5, 4.6, [
        ("为什么不能让 AI 自己判", 18, True, BLUE, PP_ALIGN.LEFT, 8),
        ("AI 写的测试可能和代码一起错（比如把标准放宽），自己判自己就会\"自我感觉良好\"。", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("所以：测试员看不到代码；评测时用隐藏考题当裁判——平台自己的结论和考官一致的比例从 56% 提升到 72%。", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("主程序要输出什么，第一次说了就锁定，不许事后改小（真跑中发现过 AI 删掉自己做不到的要求）。", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("没工具、没接板子时如实标\"跳过\"，绝不算通过。", 14, False, INK, PP_ALIGN.LEFT, 8)])
    notes(s, "七道客观检查。关键是不让 AI 自己给自己打分：测试员看不到代码，评测时用 AI 看不到的隐藏考题当裁判。")


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


def slide_wiring(prs):
    s = content(prs, "接线实测：线没接对，就不往下写代码")
    pic = picture(s, SHOTS / "05_接线清单与自检没对上.png", 0.6, 1.8)
    crop_to(pic, 6.9, 4.3)
    text(s, 7.8, 1.8, 5.0, 4.6, [
        ("真实运行记录", 18, True, BLUE, PP_ALIGN.LEFT, 8),
        ("设计师给出：把 0 号引脚接到 3.3 伏电源，用来测电压，接好后应读到约 3 伏；并提醒先拔掉原来的线", 14, False, INK, PP_ALIGN.LEFT, 10),
        ("第 1 次实测：只有 0.45 伏 → 没接好，平台停下，告诉你实测值", 14, False, RED, PP_ALIGN.LEFT, 10),
        ("重新接线后：3.0 伏 → 对上了，才开始写代码", 14, False, GREEN, PP_ALIGN.LEFT, 10),
        ("没有这一步之前，AI 对着一根没接好的线白白试了 4 次", 14, False, INK, PP_ALIGN.LEFT, 10),
        ("实测程序是平台自己写的，结果不经过 AI：能测传感器有没有回应、两个引脚通不通、电压多少、电平高低", 12, False, MUTED, PP_ALIGN.LEFT, 0)])
    notes(s, "真跑时的截图：第一次线没接好，只测到 0.45 伏，平台拦下；重新接好后 3 伏，才开始写代码。")


def slide_demo(prs):
    s = content(prs, "运行效果：一句话需求，22 分钟、0.86 元交付装板验证过的程序")
    pic = picture(s, SHOTS / "01_对话_一次完整运行.png", 0.6, 1.8)
    crop_to(pic, 7.2, 4.5)
    rows = [("需求", "每 2 秒读一次芯片温度和电压，记住最高温度；断电重启后还能说出上次的最高温度"),
            ("拆分", "温度、电压、存储、统计 4 个模块，分别 2、1、4、1 次通过"),
            ("返工", "存储模块诊断说某个测试写错要删，平台核对确实删了"),
            ("运行", "主程序装板跑两轮：第一次开机说\"没有记录\"，重启后说出上次的最高温度"),
            ("交付", "可以直接装进开发板的完整程序 + 验证报告")]
    for i, (h, d) in enumerate(rows):
        y = 1.8 + i * 0.9
        card(s, 8.05, y, 4.75, 0.8)
        text(s, 8.15, y, 0.75, 0.8, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 8.9, y, 3.85, 0.8, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "一个真实需求从头到尾：快档模型 22 分钟、不到一块钱，4 个模块和主程序全部通过，并在板子上实际运行验证。")


def slide_eval(prs):
    s = content(prs, "量化评测：难题通过率 39% → 67%")
    cd = CategoryChartData()
    cd.categories = ["单个 AI", "多个 AI·不给资料", "完整系统"]
    cd.add_series("改进前", (33, 33, 39))
    cd.add_series("改进后", (33, 61, 67))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.8), Inches(6.6), Inches(4.2), cd)
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
    text(s, 0.6, 6.05, 6.6, 0.5, [("完整系统 = 多个 AI + 人确认过的硬件资料 + 智能调度 + 错题本；单个 AI 只测了一次作为对照", 11, False, MUTED)])
    text(s, 7.6, 1.8, 5.2, 4.7, [
        ("怎么测的", 16, True, BLUE, PP_ALIGN.LEFT, 4),
        ("15 道上板题，每题有标准答案和 AI 看不到的考题；每种方案每题跑 3 次，看能不能通过考题", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("完整系统提升 28 个百分点，经统计检验不是运气（超过两倍误差范围）；\"多个 AI·不给资料\"的提升刚好过线，结论还不稳", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("把硬件问题误判成别的、提前放弃：9 次 → 0 次", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("4 道没见过的新题：完整系统全部通过；但这几题较简单，单个 AI 也能过，不能据此说多个 AI 更好", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("全部测量共花 22.58 元，所有运行记录都保留、可复查", 12, False, MUTED, PP_ALIGN.LEFT, 0)])
    notes(s, "在难题上，完整系统从 39% 提升到 67%，经统计检验不是运气。我们也如实说明了结论不稳的地方。")


def slide_perf(prs):
    s = content(prs, "性能与工程质量")
    stats = [("4.5 秒", "AI 回一次话的典型时间", "快档模型，3074 次的中位数"), ("8.2 秒", "一次检查（编译 + 装板测试）", "657 次的中位数，最长 30 秒"),
             ("3.7 倍", "多个 AI 同时干活的提速", "4 个模块时，相对一个接一个"), ("99%", "翻说明书工具的成功率", "1172 次")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.6 + i * 3.1
        card(s, x, 1.85, 2.9, 2.0)
        text(s, x, 1.95, 2.9, 0.85, [(big, 34, True, BLUE, PP_ALIGN.CENTER, 0)])
        text(s, x + 0.1, 2.85, 2.7, 0.45, [(lab, 13, True, INK, PP_ALIGN.CENTER, 0)])
        text(s, x + 0.1, 3.3, 2.7, 0.45, [(sub, 11, False, MUTED, PP_ALIGN.CENTER, 0)])
    text(s, 0.6, 4.2, 12.1, 2.3, [
        ("平台本身怎么保证不出错", 16, True, BLUE, PP_ALIGN.LEFT, 6),
        ("625 个自动化测试用例（用假 AI，不联网、不花钱），包括随机乱点界面的长时间测试；还会故意改坏平台代码，检验这些测试管不管用", 14, False, INK, PP_ALIGN.LEFT, 6),
        ("15 道上板题全部经过真板自检：标准答案能通过，故意写错的版本会被抓住", 14, False, INK, PP_ALIGN.LEFT, 6),
        ("140 条设计决定全部记录在案；每次运行的过程、AI 的每次回复、每一版代码都留档，可以回放复查", 14, False, INK, PP_ALIGN.LEFT, 6)])
    notes(s, "AI 回一次话典型 4.5 秒，一次检查 8 秒左右；平台本身有 625 个自动化测试保证可靠，所有过程都留档可复查。")


def slide_ai(prs):
    s = content(prs, "AI 用在哪里，又在哪里不能信它")
    rows = [("查资料", "在说明书里检索、提炼硬件规则", "可能引错原文 → 核对原文 + 你确认"),
            ("设计", "拆模块、定标准、定接线", "可能提出做不到的标准 → 平台检查退回"),
            ("写代码 / 测试", "快档 / 强档模型 + 调用工具", "可能一起错 → 互相看不到 + 故意改坏 + 隐藏考题"),
            ("诊断", "判断错在哪", "会误判硬件 → 先实测；交代的事平台核对"),
            ("集成", "写主程序和应有的输出", "可能把要求改小 → 第一次说了就锁定"),
            ("工具与记忆", "MCP 对接外部工具；错题本", "外部工具默认先问你；评测时错题本只读")]
    hdr = [("环节", 1.75), ("AI 做什么", 3.2), ("可能出什么错 → 怎么防", 4.15)]
    x = 0.6
    for h, w in hdr:
        card(s, x, 1.8, w - 0.08, 0.45, fill=BLUE)
        text(s, x, 1.8, w - 0.08, 0.45, [(h, 13, True, WHITE, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
        x += w
    for i, r in enumerate(rows):
        y = 2.33 + i * 0.6
        x = 0.6
        for (c, (_, w)) in zip(r, hdr):
            card(s, x, y, w - 0.08, 0.52)
            text(s, x + 0.08, y, w - 0.24, 0.52, [(c, 12, c == r[0], BLUE if c == r[0] else INK, PP_ALIGN.LEFT, 0)],
                 anchor=MSO_ANCHOR.MIDDLE)
            x += w
    text(s, 9.9, 1.8, 2.9, 4.6, [
        ("不交给 AI 的事", 15, True, BLUE, PP_ALIGN.LEFT, 6),
        ("判断对错", 13, False, INK, PP_ALIGN.LEFT, 4), ("讲解失败经过", 13, False, INK, PP_ALIGN.LEFT, 4),
        ("最终确认硬件事实", 13, False, INK, PP_ALIGN.LEFT, 4), ("决定是否放行", 13, False, INK, PP_ALIGN.LEFT, 12),
        ("目前的不足", 15, True, BLUE, PP_ALIGN.LEFT, 6),
        ("只在一款开发板上完整验证", 12, False, INK, PP_ALIGN.LEFT, 3), ("测试悄悄放宽标准还发现不了", 12, False, INK, PP_ALIGN.LEFT, 3),
        ("评测题数量还不多", 12, False, INK, PP_ALIGN.LEFT, 3)])
    notes(s, "每个环节 AI 做什么、可能出什么错、我们怎么防。判断对错、讲解失败、确认硬件事实、是否放行，这几件事不交给 AI。")


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
    for build in (slide_background, slide_overview, slide_arch, slide_agents, slide_flow, slide_sched, slide_loop,
                  slide_verify, slide_innovation, slide_wiring, slide_demo, slide_eval, slide_perf, slide_ai):
        build(prs)
    closing(prs)
    prs.save(str(OUT))
    print(f"已生成 {OUT}，共 {len(prs.slides)} 页（正文 {len(prs.slides) - 3} 页）")


if __name__ == "__main__":
    main()

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
    items = [("1　选题背景与需求", "嵌入式开发为什么难交给大模型"),
             ("2　平台架构与编排", "六层架构 · Agent 团队 · 工作流"),
             ("3　调度、自我循环与验证", "智能调度 · 诊断分派 · 客观判定"),
             ("4　核心创新：信息在 Agent 之间正确传递", "工程事实 · 接线自检 · 交接核对"),
             ("5　运行效果与量化评测", "真实演示 · 基线对比 · 性能")]
    paras = []
    for a, b in items:
        paras.append((a, 20, True, WHITE, PP_ALIGN.LEFT, 0))
        paras.append(("　　" + b, 15, False, CYAN, PP_ALIGN.LEFT, 10))
    text(s, 4.9, 1.9, 7.6, 4.2, paras, anchor=MSO_ANCHOR.MIDDLE, name="目录")


def content(prs, t, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts.get_by_name("节标题" if dark else "两栏内容"))
    title(s, t, dark=dark)
    return s


def slide_background(prs):
    s = content(prs, "选题背景：嵌入式代码\"能编译\"远不等于\"能用\"")
    pains = [("事实藏在手册里", "一个驱动要用几十个寄存器、位定义、时序；模型凭记忆写会混淆型号。我们的 MPU6050 模块实为 MPU-6500 兼容芯片，芯片编号读出 0x70 而不是手册写的 0x68"),
             ("对错只能上板判定", "编译通过、电脑上测试通过都不够；必须烧到真实开发板、读串口，才知道外设是否真的被驱动"),
             ("接线是隐藏的前提", "引脚另一端接了什么，不在代码里也不在需求里；线接错，读数就没有意义")]
    for i, (h, b) in enumerate(pains):
        x = 0.6 + i * 4.1
        card(s, x, 1.85, 3.8, 2.75)
        circle_num(s, x + 0.25, 2.07, 0.5, str(i + 1))
        text(s, x + 0.9, 2.08, 2.8, 0.5, [(h, 18, True, BLUE)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, x + 0.25, 2.75, 3.35, 2.1, [(b, 14, False, INK)])
    text(s, 0.6, 4.9, 12.1, 1.2, [
        ("现有方案的不足（对应赛题三大挑战）", 16, True, BLUE, PP_ALIGN.LEFT, 4),
        ("编排：多为一次生成或固定流水线，没有\"错在哪、交给谁改\"；调度：所有任务同一个模型；验证：用 Agent 自己写的测试判自己的代码", 14, False, INK)])
    notes(s, "嵌入式开发有三个特点让大模型直接写代码效果很差：事实在手册里、对错只能上板判、接线是隐藏前提。这正对应赛题说的编排、调度、验证三大挑战。")


def slide_overview(prs):
    s = content(prs, "FirmForge：一句话需求 → 在真实开发板上验证过的完整工程", dark=True)
    stats = [("67%", "上板测试题通过率", "改进前 39%，单 Agent 33%"), ("12/12", "留出题全部通过", "平台从未见过的 4 道新外设题"),
             ("0.86 元", "真实需求端到端", "4 个模块 + 主程序，22 分钟"), ("625", "单元测试全部通过", "假模型，不联网不花钱")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.6 + i * 3.1
        text(s, x, 2.0, 2.9, 1.0, [(big, 44, True, CYAN, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.05, 2.9, 0.5, [(lab, 16, True, WHITE, PP_ALIGN.CENTER, 0)])
        text(s, x, 3.55, 2.9, 0.5, [(sub, 12, False, RGBColor(0xC9, 0xD8, 0xFF), PP_ALIGN.CENTER, 0)])
    text(s, 0.9, 4.55, 11.5, 1.4, [
        ("用户用一句话描述要在开发板上实现的功能；平台拆模块、调度多个 Agent 分工写代码和测试，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("用编译器、上板测试、接线实测等客观判定器逐层检查，没通过就诊断、交给对应 Agent 修正，", 16, False, WHITE, PP_ALIGN.CENTER, 2),
        ("最后烧到板子上运行、核对串口，交付可直接烧录的工程和验证报告。", 16, False, WHITE, PP_ALIGN.CENTER, 2)])
    notes(s, "四个数字：难题通过率 39% 提升到 67%，提升超过两倍标准误；留出题全过；真实需求不到一块钱；625 个单元测试。")


def slide_arch(prs):
    s = content(prs, "总体架构：六层，客观判定独立于大模型")
    layers = [("界面层", "桌面软件（对话 · 知识库 · 工作流设计器 · 团队 · 设置）· 命令行 · MCP 服务端"),
              ("编排层", "工作流解释器（TOML）· 调度器（smart / serial / round_robin）· 自我循环 · 接着修 / 人工放行"),
              ("Agent 层", "analyst · researcher · rule_checker · designer · coder-fast / strong · tester · diagnoser · reviewer"),
              ("知识与事实层", "资料库（手册、ESP-IDF 头文件、板上实测，sha256 校验）· 规则库 · 项目接线记录 · 经验库"),
              ("判定层", "合规 · ESP-IDF 编译 · 上板 Unity 测试 · 接线自检 · 变异体 · 主程序上板核对 · 隐藏测试"),
              ("工具层", "DeepSeek（标准库实现 OpenAI 兼容接口）· 工具流水线 · MCP 客户端 · ESP-IDF · 串口")]
    for i, (n, d) in enumerate(layers):
        y = 1.8 + i * 0.73
        hl = n in ("判定层", "知识与事实层")
        card(s, 0.6, y, 2.3, 0.6, fill=BLUE if hl else TINT)
        text(s, 0.6, y, 2.3, 0.6, [(n, 16, True, WHITE if hl else BLUE, PP_ALIGN.CENTER, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 3.1, y, 9.7, 0.6, [(d, 14, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 0.6, 6.25, 12.1, 0.5, [("核心库只用 Python 标准库；工具缺失、没有板子时对应层记\"跳过\"并写明原因，绝不假装通过", 13, False, MUTED)])
    notes(s, "六层架构。深色两层是我们的重点：所有对错由判定层客观裁决，硬件事实由知识与事实层提供并带状态。")


def slide_agents(prs):
    s = content(prs, "Agent 团队与可视化工作流编排")
    rows = [("designer", "拆模块、接口、验收条件、完整接线"), ("coder-fast / strong", "快档 / 强档编码，由调度器挑"),
            ("tester", "只看接口写上板测试"), ("diagnoser", "判失败责任：代码 / 测试 / 规则 / 设计 / 硬件"),
            ("researcher 等", "查手册提炼规则；rule_checker 核对原文"), ("reviewer", "通过后再审一遍代码")]
    text(s, 0.6, 1.75, 4.9, 0.45, [("每个 Agent：角色、版本、档位、思考强度、工具、记忆", 14, True, BLUE)])
    for i, (a, d) in enumerate(rows):
        y = 2.3 + i * 0.66
        card(s, 0.6, y, 4.9, 0.56)
        text(s, 0.75, y, 2.35, 0.56, [(a, 13, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 3.05, y, 2.4, 0.56, [(d, 12, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    pic = picture(s, SHOTS / "08_工作流设计器.png", 5.8, 1.8)
    crop_to(pic, 6.95, 4.2)
    text(s, 5.8, 6.06, 6.95, 0.62, [("工作流设计器：拖拽编排顺序 / 并行 / 条件 / 循环 / 子流程 / 等人确认，与 TOML 代码实时同步；"
                                   "保存前用真正的解释器空跑 6 种情况", 12, False, MUTED)])
    notes(s, "Agent 有版本管理和历史成绩。工作流可视化编排、存成 TOML 由解释器执行，空跑预演保证图上画的就是实际执行的。")


def slide_flow(prs):
    s = content(prs, "编排流程：每一步都有客观关口和人工确认点")
    steps = [("需求", "一句话描述"), ("规则", "按需求查规则\n核对原文、人工确认"), ("设计", "模块 + 验收条件\n+ 完整接线"),
             ("接线", "按清单接线\n自检对上才往下"), ("模块", "编码 ∥ 写测试\n合规→编译→上板"), ("集成", "主程序上板跑两轮\n交付完整工程")]
    w, gap = 1.75, 0.38
    for i, (h, d) in enumerate(steps):
        x = 0.6 + i * (w + gap)
        hl = h in ("接线", "集成")
        card(s, x, 2.0, w, 2.1, fill=BLUE if hl else TINT)
        text(s, x, 2.15, w, 0.5, [(h, 20, True, WHITE if hl else BLUE, PP_ALIGN.CENTER, 0)])
        text(s, x + 0.1, 2.75, w - 0.2, 1.3, [(d, 13, False, WHITE if hl else INK, PP_ALIGN.CENTER, 0)])
        if i < len(steps) - 1:
            arrow(s, x + w + 0.03, 2.9, 0.32, 0.3)
    text(s, 0.6, 4.5, 12.1, 1.9, [
        ("人工确认点（开了\"设计后等我确认\"才停）：确认新规则 · 确认设计 · 接线 · 每个模块看结果 · 看主程序结果", 15, False, INK, PP_ALIGN.LEFT, 8),
        ("隔离：测试 Agent 看不到实现；评测用的隐藏测试只有评测流程能读，任何 Agent 的上下文都不含它", 15, False, INK, PP_ALIGN.LEFT, 8),
        ("每一步都发事件：界面实时显示、可回放；每次大模型调用、每次尝试的代码和报告都留档，结论可追溯", 15, False, INK, PP_ALIGN.LEFT, 8)])
    notes(s, "深色两步是这次新加的关口：接线必须实测对上才写代码；集成要烧到板子上跑两轮核对串口才交付。")


def slide_sched(prs):
    s = content(prs, "智能调度：按成功率、成本、负载和探索打分选 Agent")
    card(s, 0.6, 1.85, 6.6, 1.25)
    text(s, 0.8, 1.95, 6.3, 1.1, [("score = p − α·成本 − β·负载 + γ·√(ln(N+1) / (n+1))", 17, True, BLUE, PP_ALIGN.CENTER, 4),
                                   ("p：该 Agent 在此类任务上的平滑成功率；n：它的尝试次数；最后一项为 UCB 探索", 12, False, MUTED, PP_ALIGN.CENTER, 0)],
         anchor=MSO_ANCHOR.MIDDLE)
    pts = [("按任务分桶学习", "硬件层 / 逻辑层 × 第一次写 / 修改，分别统计，学到\"首次用快档、修改换强档\"这类规律"),
           ("关键路径优先", "就绪模块按到终点的最长依赖路径排序"),
           ("连续失败换人", "同一模块上没修好的编码 Agent，修改轮会被换掉"),
           ("可解释", "每次分配的得分明细随事件发出，界面\"调度\"标签可查")]
    for i, (h, d) in enumerate(pts):
        y = 3.35 + i * 0.78
        circle_num(s, 0.6, y + 0.05, 0.42, str(i + 1))
        text(s, 1.15, y, 6.0, 0.75, [(h, 15, True, INK, PP_ALIGN.LEFT, 0), (d, 12, False, MUTED, PP_ALIGN.LEFT, 0)])
    # 压测图：4 个模块时串行 vs 智能并行
    cd = CategoryChartData()
    cd.categories = ["串行", "并行·上限2", "并行·上限4", "并行·上限8"]
    cd.add_series("4 个模块总用时（秒）", (20.7, 11.5, 7.4, 5.5))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.5), Inches(1.85), Inches(5.3), Inches(3.9), cd)
    ch = gf.chart
    ch.has_title = True
    ch.chart_title.text_frame.text = "调度压测：4 个模块总用时（秒，越低越好）"
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
    text(s, 7.5, 5.85, 5.3, 0.6, [("假模型每次调用固定 2 秒，只比调度；4 个模块时并行比串行快 3.7 倍", 12, False, MUTED)])
    notes(s, "调度公式：成功率减成本减负载加探索。按任务类型分桶学习。右边是压测：4 个模块时并行快 3.7 倍。")


def slide_loop(prs):
    s = content(prs, "自我循环：诊断判责任，交给对的人修，并核对是否照办")
    branches = [("代码", "编码 Agent 按报告和诊断改，可换 Agent"), ("测试", "测试 Agent 改；要删的用例由平台核对真删了"),
                ("规则", "规则退回核对后再改"), ("设计", "设计 Agent 改验收条件，测试从头写"),
                ("硬件", "先烧平台自写的自检程序实测；接线都正常就带着实测结果重新诊断")]
    card(s, 0.6, 1.9, 2.4, 3.9, fill=BLUE)
    text(s, 0.6, 1.9, 2.4, 3.9, [("检查没通过", 18, True, WHITE, PP_ALIGN.CENTER, 6), ("↓", 18, True, WHITE, PP_ALIGN.CENTER, 6),
                                 ("诊断 Agent", 18, True, WHITE, PP_ALIGN.CENTER, 6), ("判定责任方", 14, False, WHITE, PP_ALIGN.CENTER)],
         anchor=MSO_ANCHOR.MIDDLE)
    for i, (h, d) in enumerate(branches):
        y = 1.9 + i * 0.8
        arrow(s, 3.1, y + 0.2, 0.4, 0.28)
        card(s, 3.6, y, 4.3, 0.68)
        text(s, 3.7, y, 0.8, 0.68, [(h, 15, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 4.45, y, 3.4, 0.68, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    extra = [("变异体检验测试", "通过后把代码故意改坏几处，测试没发现就让测试 Agent 补（不给实现代码）"),
             ("硬件误诊 9 次 → 0 次", "三个 Agent 用同一个模型会一起看错，用实测打破"),
             ("经验库", "修好的问题存进 SQLite，类似模块编码时检索参考"),
             ("接着修 / 人工放行", "次数用完先讲清经过和放行风险，再由人决定")]
    for i, (h, d) in enumerate(extra):
        y = 1.9 + i * 0.98
        text(s, 8.25, y, 4.5, 0.95, [(h, 15, True, BLUE, PP_ALIGN.LEFT, 2), (d, 12, False, INK, PP_ALIGN.LEFT, 0)])
    notes(s, "诊断判责任方，平台按责任分派，还核对交代的事有没有照办。硬件问题不信模型，先实测。")


def slide_verify(prs):
    s = content(prs, "结果验证：对错由客观判定器裁决，不让 Agent 自己判自己")
    layers = [("合规检查", "引脚只能用档案生成的宏、遵守已确认规则"), ("ESP-IDF 编译", "真实编译，-Werror"),
              ("上板测试", "每个用例单独开机运行，解析串口"), ("接线自检", "I2C 应答、跳线通断、ADC 电压、引脚电平"),
              ("变异体", "测试能否发现故意改坏的代码"), ("主程序上板", "擦 NVS 后跑两轮，核对串口输出和周期"),
              ("隐藏测试", "只在评测，Agent 看不到")]
    for i, (h, d) in enumerate(layers):
        y = 1.8 + i * 0.64
        card(s, 0.6, y, 6.3, 0.56)
        text(s, 0.75, y, 1.7, 0.56, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 2.45, y, 4.4, 0.56, [(d, 12, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    text(s, 7.3, 1.8, 5.5, 4.6, [
        ("防止\"自己判自己\"", 18, True, BLUE, PP_ALIGN.LEFT, 8),
        ("测试 Agent 看不到实现代码，报告里的实现片段也先删掉", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("评测时用隐藏测试当裁判，统计\"自检准确率\"：完整系统 56% → 72%", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("主程序的期望输出第一次交上来就锁定；要改必须写理由并展示给人（真跑中发现 Agent 删掉自己做不到的期望后加上）", 14, False, INK, PP_ALIGN.LEFT, 8),
        ("工具缺失、没有板子时记\"跳过\"，整次结论最多\"不完整\"，绝不算通过", 14, False, INK, PP_ALIGN.LEFT, 8)])
    notes(s, "七层客观判定。重点是防止 Agent 自己判自己：测试看不到实现、隐藏测试当裁判、期望锁定。")


def slide_innovation(prs):
    s = content(prs, "核心创新：协同的关键是信息在 Agent 之间正确传递", dark=True)
    text(s, 0.6, 1.75, 5.9, 4.7, [
        ("一次失败的复盘", 18, True, CYAN, PP_ALIGN.LEFT, 8),
        ("ADC 模块试了 4 次都没通过，逐条查运行记录：代码一次都没改、一直是对的，失败全在测试上。根源：", 14, False, WHITE, PP_ALIGN.LEFT, 8),
        ("设计选的引脚上原来有一根评测跳线，设计看不到", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("接线只写\"信号→引脚号\"，没写另一端接什么", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("平台把\"计划\"当成\"已接好\"传给测试 Agent", 14, False, WHITE, PP_ALIGN.LEFT, 4),
        ("验收条件要求\"接 GND 时……\"，自动测试做不到；诊断两次判对了，却没有退回设计的路", 14, False, WHITE, PP_ALIGN.LEFT, 4)])
    fixes = [("每个对话一个项目", "接线按对话分开存，互不相通"), ("设计交完整连线", "另一端接什么、为什么、自检应测到什么；不许占用已有线"),
             ("事实带状态", "计划 → 已接 → 实测，只有平台能升级；没实测的线不许写读数断言"),
             ("交接要核对", "诊断要删的用例平台核对；诊断可退回设计")]
    for i, (h, d) in enumerate(fixes):
        y = 1.8 + i * 1.12
        card(s, 6.9, y, 5.9, 0.98, fill=RGBColor(0x14, 0x4C, 0xD6))
        text(s, 7.1, y + 0.05, 5.6, 0.9, [(h, 16, True, WHITE, PP_ALIGN.LEFT, 2), (d, 12, False, RGBColor(0xD9, 0xE6, 0xFF), PP_ALIGN.LEFT, 0)],
             anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "这是我们最重要的发现：多 Agent 协同不是让它们多讨论，它们缺的是事实。所以我们建立了带状态的工程事实，由平台而不是 Agent 来升级。")


def slide_wiring(prs):
    s = content(prs, "接线自检：线没接对，就不往下写代码")
    pic = picture(s, SHOTS / "05_接线清单与自检没对上.png", 0.6, 1.8)
    crop_to(pic, 6.9, 4.3)
    text(s, 7.8, 1.8, 5.0, 4.6, [
        ("真跑实录", 18, True, BLUE, PP_ALIGN.LEFT, 8),
        ("设计给出：GPIO0 → 3V3，读 ADC，自检应在 2800–3100 mV；写明先拔掉原来的跳线", 14, False, INK, PP_ALIGN.LEFT, 10),
        ("第 1 次自检：449 mV → 没对上，平台停下，告诉人实测值", 14, False, RED, PP_ALIGN.LEFT, 10),
        ("重接后：3005 mV → 对上，开始写代码", 14, False, GREEN, PP_ALIGN.LEFT, 10),
        ("没有这一步之前，Agent 对着一根没接好的线白试了 4 次", 14, False, INK, PP_ALIGN.LEFT, 10),
        ("自检程序由平台自己写、结果不经模型：I2C 应答、跳线通断、ADC 电压、引脚电平", 13, False, MUTED, PP_ALIGN.LEFT, 0)])
    notes(s, "这是真跑时的截图：第一次线没接好，读到 449 毫伏，平台拦下；重接后 3005 毫伏才开始写代码。")


def slide_demo(prs):
    s = content(prs, "运行效果：一句话需求，22 分钟、0.86 元交付上板验证过的工程")
    pic = picture(s, SHOTS / "01_对话_一次完整运行.png", 0.6, 1.8)
    crop_to(pic, 7.2, 4.5)
    rows = [("需求", "每 2 秒读芯片温度和 ADC 电压，记录最高温度存进 NVS，重启后打印上次的值"),
            ("模块", "温度 2 次、ADC 1 次、NVS 4 次、统计 1 次通过"),
            ("交接", "NVS 诊断要求删的用例，平台核对已删"),
            ("集成", "主程序第 3 次通过：首次上电打印\"无历史记录\"，复位后打印上次保存的最高温度"),
            ("交付", "firmware/ 完整工程 + 验证报告")]
    for i, (h, d) in enumerate(rows):
        y = 1.8 + i * 0.9
        card(s, 8.05, y, 4.75, 0.8)
        text(s, 8.15, y, 0.75, 0.8, [(h, 14, True, BLUE, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
        text(s, 8.9, y, 3.85, 0.8, [(d, 11, False, INK, PP_ALIGN.LEFT, 0)], anchor=MSO_ANCHOR.MIDDLE)
    notes(s, "真实需求端到端，快档模型 22 分钟、0.86 元，4 个模块和主程序全部通过并上板实测。")


def slide_eval(prs):
    s = content(prs, "量化评测：难题通过率 39% → 67%，提升超过两倍标准误")
    cd = CategoryChartData()
    cd.categories = ["单 Agent", "多 Agent·无规则", "完整系统"]
    cd.add_series("改进前", (33, 33, 39))
    cd.add_series("改进后", (33, 61, 67))
    gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(0.6), Inches(1.8), Inches(6.6), Inches(4.2), cd)
    ch = gf.chart
    ch.has_title = True
    ch.chart_title.text_frame.text = "6 道上板测试题通过率（%，每配置 18 次）"
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
    text(s, 0.6, 6.05, 6.6, 0.5, [("单 Agent 只测了基线；完整系统 = 多 Agent + 已确认规则 + 智能调度 + 经验库（冻结）", 11, False, MUTED)])
    text(s, 7.6, 1.8, 5.2, 4.7, [
        ("统计口径", 16, True, BLUE, PP_ALIGN.LEFT, 4),
        ("按题聚类的标准误；逐题配对差值超过两倍标准误才说\"有提升\"", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("完整系统 +28% ± 10%：有提升；无规则 +28% ± 13%：勉强，结论不稳", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("硬件误诊停下 9 次 → 0 次；自检准确率 56% → 72%", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("留出题（4 道新外设）：完整系统 12/12（修正 1 道题目缺陷后重判）；题目偏简单，单 Agent 也能过，不能据此说多 Agent 更好", 13, False, INK, PP_ALIGN.LEFT, 10),
        ("最终测量合计花费 22.58 元，全部运行记录可复核", 12, False, MUTED, PP_ALIGN.LEFT, 0)])
    notes(s, "难题上完整系统 39% 到 67%，提升超过两倍标准误。我们也如实说明了结论不稳的地方。")


def slide_perf(prs):
    s = content(prs, "性能与工程质量")
    stats = [("4.5 s", "快档模型调用延迟中位数", "3074 次调用；90% 分位 56 s"), ("8.2 s", "判定（编译 + 上板）中位数", "657 次；最长 29.7 s"),
             ("3.7×", "4 个模块并行加速", "相对串行调度（压测）"), ("99%", "手册翻页工具成功率", "1172 次；搜索 93%")]
    for i, (big, lab, sub) in enumerate(stats):
        x = 0.6 + i * 3.1
        card(s, x, 1.85, 2.9, 2.0)
        text(s, x, 1.95, 2.9, 0.85, [(big, 36, True, BLUE, PP_ALIGN.CENTER, 0)])
        text(s, x + 0.1, 2.85, 2.7, 0.45, [(lab, 13, True, INK, PP_ALIGN.CENTER, 0)])
        text(s, x + 0.1, 3.3, 2.7, 0.45, [(sub, 11, False, MUTED, PP_ALIGN.CENTER, 0)])
    text(s, 0.6, 4.2, 12.1, 2.3, [
        ("工程质量", 16, True, BLUE, PP_ALIGN.LEFT, 6),
        ("625 个单元测试（假模型，不联网不花钱），含界面固定种子随机操作长测；变异测试检验测试本身", 14, False, INK, PP_ALIGN.LEFT, 6),
        ("15 道上板评测题：标准答案在真板上通过、变异体在预期层失败，全部自检合格才用", 14, False, INK, PP_ALIGN.LEFT, 6),
        ("140 条设计决定全部记录在 DECISIONS.md；每次运行的事件、调用、代码快照全部留档，可回放可复核", 14, False, INK, PP_ALIGN.LEFT, 6)])
    notes(s, "延迟来自全部真实运行记录的统计；并行加速来自压测。工程质量靠大量单元测试和自检合格的评测题。")


def slide_ai(prs):
    s = content(prs, "人工智能使用说明与局限")
    rows = [("查资料", "LLM + 手册检索（RAG）", "可能引错原文 → 原文核对 + 人工确认"),
            ("设计", "LLM 拆模块、写接线", "可能写出做不到的条件 → 平台检查退回"),
            ("编码 / 测试", "快档 / 强档 + 工具调用", "可能一起错 → 隔离 + 变异体 + 隐藏测试"),
            ("诊断", "LLM 判责任方", "会误判硬件 → 先实测；指令是否照办平台核对"),
            ("集成", "LLM 写主程序和期望", "可能放宽期望 → 锁定，改要写理由"),
            ("MCP / 记忆", "服务端 + 客户端；SQLite 经验库", "外部工具默认问人；评测时经验库冻结")]
    hdr = [("环节", 1.6), ("AI 的作用", 3.3), ("局限与应对", 4.2)]
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
        ("所有对错判定", 13, False, INK, PP_ALIGN.LEFT, 4), ("失败经过的讲解", 13, False, INK, PP_ALIGN.LEFT, 4),
        ("硬件事实的最终确认", 13, False, INK, PP_ALIGN.LEFT, 4), ("人工放行", 13, False, INK, PP_ALIGN.LEFT, 12),
        ("已知局限", 15, True, BLUE, PP_ALIGN.LEFT, 6),
        ("只在 ESP32-C3 上完整验证", 12, False, INK, PP_ALIGN.LEFT, 3), ("测试悄悄放宽要求还发现不了", 12, False, INK, PP_ALIGN.LEFT, 3),
        ("测试题样本量小", 12, False, INK, PP_ALIGN.LEFT, 3)])
    notes(s, "每个环节 AI 的作用和我们的应对。对错判定、失败讲解、硬件事实确认和放行不交给 AI。")


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

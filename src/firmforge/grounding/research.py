"""查资料并提炼规则（DECISIONS 第 66 条第 ② 步）与语义核对 Agent（第 ③ 步第 4 步）。

查资料 Agent 读不完上千页的手册，所以给它两个工具（工具集 sources）：
- doc_search：在已登记的资料里按关键词找页，返回资料编号、页码和片段；
- doc_page：读某份资料的某一页全文。
它最后输出一个 JSON 数组，每项是一条规则（字段见 rules.Rule）；引用必须从 doc_page 读到的原文里逐字摘抄。

语义核对 Agent 是独立的：只看规则和原文那一页，看不到查资料 Agent 的思考过程。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..agents.brief import Brief
from ..agents.context import load_prompt
from ..memory.bm25 import BM25
from ..tools.registry import ToolContext, ToolDef
from .profile import HardwareProfile
from .rules import KIND_NAMES, Rule, RuleError, RuleSet
from .sources import SourceLibrary
from .verifier import RuleResult

SEARCH_TOP_K = 5
SNIPPET_CHARS = 240
PAGE_CHARS = 6000


class PageIndex:
    """所有已登记资料的逐页 BM25 索引。"""

    def __init__(self, library: SourceLibrary):
        self.library = library
        self.keys: list[tuple[str, int | None]] = []
        texts = []
        for doc in library.docs.values():
            if doc.kind == "svd":              # SVD 给程序核对寄存器用，不给 Agent 逐页读
                continue
            if len(doc.pages) == 1:
                self.keys.append((doc.id, None))
                texts.append(doc.pages[0])
            else:
                for i, page in enumerate(doc.pages, 1):
                    self.keys.append((doc.id, i))
                    texts.append(page)
        self._bm25 = BM25(texts) if texts else None
        self._texts = texts

    def search(self, query: str, k: int = SEARCH_TOP_K, doc: str = "") -> list[tuple[str, int | None, str]]:
        if self._bm25 is None:
            return []
        out = []
        for i, _ in self._bm25.search(query, k * 4 if doc else k):
            d, p = self.keys[i]
            if doc and d != doc:
                continue
            out.append((d, p, _snippet(self._texts[i], query)))
            if len(out) >= k:
                break
        return out


def _snippet(text: str, query: str) -> str:
    flat = re.sub(r"\s+", " ", text)
    pos = -1
    for w in sorted(re.findall(r"\w+", query), key=len, reverse=True):
        pos = flat.lower().find(w.lower())
        if pos >= 0:
            break
    start = max(0, pos - SNIPPET_CHARS // 3) if pos >= 0 else 0
    return flat[start:start + SNIPPET_CHARS]


def _index(ctx: ToolContext) -> PageIndex:
    if ctx.library is None:
        raise ValueError("当前场景没有资料库")
    if ctx.page_index is None:
        ctx.page_index = PageIndex(ctx.library)
    return ctx.page_index


def _doc_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空")
    hits = _index(ctx).search(query, doc=str(args.get("doc", "")).strip())
    if not hits:
        return "没有找到相关页。换个关键词（英文手册用英文词），或用 doc 参数限定资料。"
    return "\n".join(f"- {d}" + (f" 第 {p} 页" if p else "") + f"：{s}" for d, p, s in hits)


def _doc_page(args: dict, ctx: ToolContext) -> str:
    doc = ctx.library.get(str(args.get("doc", ""))) if ctx.library is not None else None
    if doc is None:
        raise ValueError("当前场景没有资料库")
    page = args.get("page")
    text = doc.page(int(page) if page not in (None, "") else None)
    head = f"{doc.id}（{doc.title}，{doc.tier}{'，' + doc.version if doc.version else ''}）" \
        + (f" 第 {page} 页" if page not in (None, "") else "")
    if len(text) > PAGE_CHARS:
        text = text[:PAGE_CHARS] + f"\n……（本页过长，只显示前 {PAGE_CHARS} 字）"
    layout = doc.page_layout(int(page)) if page not in (None, "") else ""
    bits = doc.page_bits(int(page)) if page not in (None, "") else ""
    # 寄存器位标注是平台按 PDF 坐标算的，不是原文（DECISIONS 第 99 条）
    tail = (f"\n\n【寄存器位标注】（平台按 PDF 里每格文字的坐标算出，只标能对准的单个位；不是手册原文，"
            f"不能当引用；与正文冲突时以正文为准）\n{bits}") if bits else ""
    if not layout:
        return f"{head}\n\n{text}{tail}"
    if len(layout) > PAGE_CHARS:
        layout = layout[:PAGE_CHARS] + f"\n……（本页过长，只显示前 {PAGE_CHARS} 字）"
    # 逐字文本丢了表格的列位置；按版面的文字保留列对齐，寄存器的位在哪一列（B7…B0）看这一段（DECISIONS 第 98 条）
    return (f"{head}\n\n【逐字文本】\n{text}\n\n【按版面排列】（保留表格的列对齐：寄存器某一位在哪一列看这一段；"
            f"旋转的文字可能缺失。引用原文可以从任一段摘抄）\n{layout}{tail}")


SOURCE_TOOLS = (
    ToolDef(
        name="doc_search", toolset="sources",
        description="在已登记的芯片手册、器件数据手册、ESP-IDF 头文件里按关键词找页，返回资料编号、页码和片段。"
                    "英文手册请用英文关键词；可以用 doc 限定在一份资料里。",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string"}, "doc": {"type": "string", "description": "资料编号，可省略"}},
                    "required": ["query"], "additionalProperties": False},
        execute=_doc_search, timeout_s=20, summarize=lambda a, r: f"查「{a.get('query', '')}」"),
    ToolDef(
        name="doc_page", toolset="sources",
        description="读某份资料某一页的全文（头文件、网页没有页码，省略 page）。规则里的原文引用必须从这里逐字摘抄。",
        parameters={"type": "object",
                    "properties": {"doc": {"type": "string"}, "page": {"type": "integer"}},
                    "required": ["doc"], "additionalProperties": False},
        execute=_doc_page, timeout_s=10, summarize=lambda a, r: f"读 {a.get('doc', '')} 第 {a.get('page', '-')} 页"),
)


# ---------------------------------------------------------------- 查资料 Agent 的消息

def library_listing(library: SourceLibrary) -> str:
    rows = []
    for d in sorted(library.docs.values(), key=lambda d: d.id):
        pages = f"{len(d.pages)} 页" if len(d.pages) > 1 else "单页"
        rows.append(f"- {d.id}：{d.title}（{d.kind}，{d.tier}{'，' + d.version if d.version else ''}，{pages}）")
    return "\n".join(rows) or "（没有登记任何资料）"


def research_messages(profile: HardwareProfile, scopes: list[str], library: SourceLibrary, *,
                      trusted: RuleSet | None = None, feedback: list[RuleResult] | None = None,
                      missing: list[str] | None = None, accepted: list[Rule] | None = None) -> list[dict]:
    """scopes：这次要覆盖的外设和器件，如 ["i2c", "mpu6050"]。feedback：上一轮被退回的规则及原因。"""
    system = load_prompt("researcher.md").safe_substitute(kinds="、".join(f"{k}（{v}）" for k, v in KIND_NAMES.items()))
    intent = "为下面这些外设和器件提炼规则：" + "、".join(scopes)
    if feedback or accepted:
        intent = "修正上一轮被退回的规则（改正或删除），并补齐缺少的类别"
    b = Brief(intent=intent, location="规则 › 查资料并提炼规则")
    b.constraints += ["只能引用已登记的资料；引用原文必须从 doc_page 读到的文字里逐字摘抄",
                      "官方资料优先：官方手册 > 官方勘误 > 官方代码 > 官方例程 > 官方文档的非官方镜像 > 社区资料",
                      "等级为 user 的上板实测记录是在这块板子上实测的结果：与其他资料冲突时以实测为准，并引用实测记录的原句"
                      "（DECISIONS 第 95 条）",
                      f"开发框架版本以硬件档案为准：{profile.framework.value} {profile.framework_version.value}",
                      "不确定的事实不要写成规则"]
    b.attach("硬件档案", "\n".join(profile.summary_lines()))
    b.attach("已登记的资料", library_listing(library))
    if trusted is not None and len(trusted):
        b.attach("已有的可信规则（不要重复）", "\n".join(r.line() for r in trusted))
    if accepted:
        b.constraints.append("下面「本轮已通过核对的规则」已经通过，不要再输出它们（也不要换个说法再交一遍）；"
                             "只输出被退回规则的改正版（沿用原编号）和补齐缺项的新规则（用没出现过的新编号）")
        b.attach("本轮已通过核对的规则（不要再输出）", "\n".join(r.line() for r in accepted))
    if feedback:
        lines = []
        for res in feedback:
            lines.append(f"- {res.rule.id}「{res.rule.text}」：" + "；".join(res.reasons()))
        b.attach("被退回的规则", "\n".join(lines))
    if missing:
        b.attach("缺少的类别", "\n".join(f"- {m}" for m in missing))
    return [{"role": "system", "content": system}, {"role": "user", "content": b.render()}]


_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.S)


def parse_rules(text: str) -> tuple[list[Rule], list[str]]:
    """从回复里取最后一个 JSON 代码块（或整段回复），解析成规则；解析不了的逐条记下原因。"""
    blocks = _JSON_BLOCK.findall(text)
    raw = blocks[-1] if blocks else text.strip()
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [], [f"回复里没有合法的 JSON 规则数组：{exc}"]
    if isinstance(data, dict) and "rules" in data:
        data = data["rules"]
    if not isinstance(data, list):
        return [], ["JSON 顶层必须是规则数组"]
    rules, errors = [], []
    for i, item in enumerate(data, 1):
        try:
            if not isinstance(item, dict):
                raise RuleError("不是对象")
            item = {**item, "status": "draft"}          # 状态只能由判定器推进，模型说了不算
            rules.append(Rule.from_dict(item))
        except RuleError as exc:
            errors.append(f"第 {i} 条：{exc}")
    return rules, errors


# ---------------------------------------------------------------- 语义核对 Agent

def semantic_messages(rule: Rule, page_text: str) -> list[dict]:
    system = load_prompt("rule_checker.md").template
    body = (f"## 规则\n\n{rule.id}：{rule.text}\n\n引用：{rule.cite.quote}\n\n"
            f"## 原文所在页（{rule.cite.doc}" + (f" 第 {rule.cite.page} 页" if rule.cite.page else "")
            + f"）\n\n{page_text[:PAGE_CHARS]}")
    return [{"role": "system", "content": system}, {"role": "user", "content": body}]


def parse_verdict(text: str) -> tuple[bool, str]:
    blocks = _JSON_BLOCK.findall(text)
    raw = blocks[-1] if blocks else text.strip()
    try:
        d = json.loads(raw)
        return bool(d["faithful"]), str(d.get("reason", "")).strip() or "（没有给理由）"
    except (json.JSONDecodeError, KeyError, TypeError):
        return False, "核对 Agent 的回复格式不对，按不通过处理"


def llm_semantic_checker(llm, model: str, **kw):
    """把一个 LLM 包成规则判定器第 4 步用的核对函数。"""
    def check(rule: Rule, page_text: str) -> tuple[bool, str]:
        resp = llm.complete(semantic_messages(rule, page_text), model=model, temperature=0.0, **kw)
        return parse_verdict(resp.text)
    return check

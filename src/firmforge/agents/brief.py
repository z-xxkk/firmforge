"""任务单（DECISIONS 第 62 条）：发给任何 Agent 的用户消息都按同一张单子组织，固定 5 节、固定顺序。

    ## 意图        一句话说要做什么（来源：输入框原文 / 画布操作生成的说明 / 引擎安排的下一步）
    ## 当前位置    工作流 › 子流程 › 步骤（模块、第几次）
    ## 引用        每条一个带标签的块，原样给出它的"代码"：[workflow.step repair]、[module pdu]、[file src/pdu.c]、[diagnosis pdu#1]
    ## 约束        芯片、资源预算、禁用符号、只读文件……
    ## 附件        需求文档、公共头文件、检查报告、知识库条目……

画布上的步骤本来就是 TOML 代码，引用它就是贴它的代码，和贴一个 .c 文件同等待遇；
所以"可视化操作"和"在输入框里打字"在 AI 那边只有"意图"一句的来源不同。
"""

from __future__ import annotations

from dataclasses import dataclass, field

SECTIONS = ("意图", "当前位置", "引用", "约束", "附件")


def fence(body: str, lang: str = "") -> str:
    """用足够长的反引号包住内容，内容里本身有 ``` 也不会提前结束。"""
    ticks = "```"
    while ticks in body:
        ticks += "`"
    return f"{ticks}{lang}\n{body.rstrip()}\n{ticks}"


@dataclass
class Ref:
    tag: str                      # 如 "workflow.step repair"、"module pdu"、"file src/pdu.c"
    body: str
    lang: str = ""                # 代码块语言：toml、json、c；空表示普通文字

    def render(self) -> str:
        return f"[{self.tag}]\n" + (fence(self.body, self.lang) if self.lang else self.body.rstrip())


@dataclass
class Attachment:
    title: str
    body: str

    def render(self) -> str:
        body = self.body.rstrip()
        if any(ln.startswith("#") for ln in body.splitlines()):      # 自带标题的文档（如 spec.md）包进代码块，免得和任务单的节混在一起
            body = fence(body, "markdown")
        return f"### {self.title}\n\n{body}"


@dataclass
class Brief:
    intent: str
    location: str = ""
    refs: list[Ref] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)

    def ref(self, tag: str, body: str, lang: str = "") -> "Brief":
        self.refs.append(Ref(tag, body, lang))
        return self

    def attach(self, title: str, body: str) -> "Brief":
        if body and body.strip():
            self.attachments.append(Attachment(title, body))
        return self

    def render(self) -> str:
        parts = [f"## 意图\n\n{self.intent.strip()}"]
        if self.location:
            parts.append(f"## 当前位置\n\n{self.location}")
        if self.refs:
            parts.append("## 引用\n\n" + "\n\n".join(r.render() for r in self.refs))
        if self.constraints:
            parts.append("## 约束\n\n" + "\n".join(f"- {c}" for c in self.constraints))
        if self.attachments:
            parts.append("## 附件\n\n" + "\n\n".join(a.render() for a in self.attachments))
        return "\n\n".join(parts)


def parse_sections(text: str) -> dict[str, str]:
    """把任务单拆回 5 节（界面"发给 AI 的内容"和测试用）。"""
    out: dict[str, str] = {}
    current = None
    lines: list[str] = []
    in_fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            ticks = stripped[:len(stripped) - len(stripped.lstrip("`"))]
            if not in_fence:
                in_fence = ticks
            elif ticks == in_fence and stripped == ticks:
                in_fence = ""
        title = line[3:].strip() if (line.startswith("## ") and not in_fence) else None
        if title in SECTIONS:
            if current:
                out[current] = "\n".join(lines).strip()
            current, lines = title, []
        elif current:
            lines.append(line)
    if current:
        out[current] = "\n".join(lines).strip()
    return out

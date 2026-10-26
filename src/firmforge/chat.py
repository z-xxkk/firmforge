"""对话（DECISIONS 第 62 条，界面方案 B）：需求对话、运行结束后追问、让 AI 改工作流。核心库实现，界面只调用这里。

- 需求对话：负责人用一句话提需求，analyst Agent 追问不清楚的地方，整理出 spec.md 和公共头文件；
  头文件先用主机 gcc 做语法检查，通过了才能"开始运行"。自写需求没有隐藏测试，报告里会注明。
- 追问：把一次运行的结论、规划、诊断、交付物摘要作为附件，analyst 用平实的中文回答；
  负责人提出修改需求时，analyst 输出新的 spec.md 和头文件，界面据此开一次新运行。
- 改工作流：选中的步骤以 TOML 代码出现在任务单的「引用」里（和画布上一样），AI 回一份完整的新 TOML，
  过一遍"引擎能不能执行"的检查后交给负责人确认。
- 每次发给 AI 的都是任务单（agents/brief.py）；输入框里的 @pdu、@repair、@src/x.c 会变成任务单里的引用。
- 对话存成 .ff_chats/<id>.json，界面的会话列表从这里读。
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .agents.brief import Brief, Ref, fence
from .agents.context import load_prompt
from .agents.protocol import check_path
from .agents.registry import AgentDef, Registry, default_registry
from .config import Settings, Toolchain
from .llm import LLMClient
from .tasks import Task, adhoc_task
from .tools import ToolContext, ToolPipeline, default_registry as default_tools
from .tools.loop import call_with_tools

DEFAULT_CHATS_DIR = ".ff_chats"
_DOC_BLOCK = re.compile(r"^<<<FILE[ \t]+(?P<path>[^\r\n]+?)[ \t]*\r?\n(?P<body>.*?)^>>>FILE[ \t]*$", re.M | re.S)
_AT = re.compile(r"@([A-Za-z0-9_./-]+)")
_TOML_BLOCK = re.compile(r"```toml\s*\n(.*?)```", re.S)


@dataclass
class Draft:
    """analyst 整理出的需求：spec.md + include/*.h。"""
    spec: str
    headers: dict[str, str]                     # include/xxx.h -> 内容
    problems: list[str] = field(default_factory=list)   # 头文件语法检查发现的问题

    @property
    def ready(self) -> bool:
        return bool(self.spec.strip()) and bool(self.headers) and not self.problems


def parse_draft(text: str) -> Draft | None:
    spec, headers = "", {}
    for m in _DOC_BLOCK.finditer(text):
        raw = m["path"].strip().strip("`'\"")
        body = m["body"] if m["body"].endswith("\n") else m["body"] + "\n"
        if raw == "spec.md":
            spec = body
        else:
            path, why = check_path(raw, ("include",))
            if path is not None and path.endswith(".h"):
                headers[path] = body
    return Draft(spec, headers) if (spec or headers) else None


def check_headers(draft: Draft, toolchain: Toolchain | None = None, workdir: Path | None = None) -> list[str]:
    """用主机 gcc 对每个头文件做语法检查（-fsyntax-only）；工具链缺失时如实说明，不当作通过。"""
    import tempfile
    tc = toolchain or Toolchain.from_env()
    if not tc.available("cc"):
        return ["本机没有 gcc，头文件没法做语法检查（在 docker 后端里会检查）"]
    problems = []
    with tempfile.TemporaryDirectory(dir=workdir) as d:
        inc = Path(d) / "include"
        inc.mkdir()
        for path, body in draft.headers.items():
            (Path(d) / path).write_text(body, encoding="utf-8")
        for path in draft.headers:
            probe = Path(d) / "probe.c"
            probe.write_text(f'#include "{Path(path).name}"\n', encoding="utf-8")
            r = subprocess.run([tc.resolve("cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
                                "-fsyntax-only", f"-I{inc}", str(probe)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            if r.returncode != 0:
                problems.append(f"{path}：" + (r.stderr.strip().splitlines() or ["语法检查没通过"])[-1][:300])
    return problems


def materialize(draft: Draft, root: Path, name: str = "module") -> Task:
    """把需求写成一个临时任务目录（spec.md + include/），返回 Task。这种任务没有隐藏测试和参考实现。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "include").mkdir(exist_ok=True)
    spec = root / f"{name}.md"
    spec.write_text(draft.spec, encoding="utf-8")
    for path, body in draft.headers.items():
        (root / path).write_text(body, encoding="utf-8")
    return adhoc_task(spec, root / "include")


# ---------------------------------------------------------------- @ 引用

def resolve_refs(text: str, *, run_dir: Path | None = None, workflow=None) -> list[Ref]:
    """把输入框里的 @xxx 变成任务单的引用：模块（规划里的）、工作流步骤（TOML）、交付物里的文件。"""
    from .orchestrator.workflow import step_toml
    refs: list[Ref] = []
    dag = {}
    if run_dir and (run_dir / "dag.json").is_file():
        dag = {m["id"]: m for m in json.loads((run_dir / "dag.json").read_text(encoding="utf-8")).get("modules", [])}
    steps = {}
    if workflow is not None:
        steps = {s.id: (s, "main") for s in workflow.main}
        for name, ss in workflow.subflows.items():
            steps.update({s.id: (s, f"subflow.{name}.steps") for s in ss})
    for name in dict.fromkeys(_AT.findall(text)):
        if name in dag:
            refs.append(Ref(f"module {name}", json.dumps(dag[name], ensure_ascii=False, indent=2), "json"))
        elif name in steps:
            s, table = steps[name]
            refs.append(Ref(f"workflow.step {name}", step_toml(s, table), "toml"))
        elif run_dir and "/" in name:
            f = run_dir / "final" / name
            if f.is_file() and f.resolve().is_relative_to((run_dir / "final").resolve()):
                refs.append(Ref(f"file {name}", f.read_text(encoding="utf-8"), "c"))
    return refs


def run_summary(run_dir: Path) -> str:
    """一次运行的摘要：结论、规划、各模块情况、诊断、最终报告。给追问用。"""
    parts = []
    info = json.loads((run_dir / "run.json").read_text(encoding="utf-8")) if (run_dir / "run.json").is_file() else {}
    if info:
        parts.append(f"结论：{info.get('verdict')}；配置 {info.get('config')}；尝试 {info.get('attempts')} 次；"
                     f"token {info.get('total_tokens')}；费用 {round(info.get('cost') or 0, 4)} 元")
        for mid, m in (info.get("modules") or {}).items():
            parts.append(f"- 模块 {mid}：{m.get('status')}，{m.get('attempts')} 次尝试，交付第 {m.get('delivered')} 次"
                         + (f"；备注：{'；'.join(m['notes'])}" if m.get("notes") else ""))
    events = run_dir / "events.jsonl"
    if events.is_file():
        diags = [json.loads(x) for x in events.read_text(encoding="utf-8").splitlines() if '"diagnosis"' in x]
        for e in diags[-6:]:
            d = e.get("data", {})
            parts.append(f"- 诊断 {e.get('node_id')} 第 {d.get('attempt')} 次：{d.get('blame')}，{d.get('summary')}")
    report = run_dir / "final" / "report.md"
    if report.is_file():
        parts.append(fence(report.read_text(encoding="utf-8")[:6000], "markdown"))
    return "\n".join(parts) or "（这次运行没有留下记录）"


# ---------------------------------------------------------------- 对话

@dataclass
class ChatTurn:
    role: str                 # user | assistant
    text: str                 # 显示给人看的文字（用户原话 / 回复正文）
    sent: str = ""            # 实际发给 AI 的任务单（用户回合）
    ts: float = 0.0


class Conversation:
    def __init__(self, llm: LLMClient, settings: Settings, *, registry: Registry | None = None,
                 chat_id: str | None = None, chats_dir: Path | str = DEFAULT_CHATS_DIR, title: str = "",
                 toolchain: Toolchain | None = None, thinking: str | None = None):
        self.llm, self.settings = llm, settings
        self.registry = registry or default_registry()
        self.agent: AgentDef = self.registry.find("analyst")[0]
        self.id = chat_id or time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(chats_dir)
        self.title = title
        self.turns: list[ChatTurn] = []
        self.draft: Draft | None = None
        self.runs: list[str] = []                 # 这个对话开过的运行目录
        self.toolchain = toolchain
        self.thinking = thinking
        self._history: list[dict] = []            # 发给模型的完整对话（含工具往返）

    # ------------------------------------------------------------ 保存

    @property
    def path(self) -> Path:
        return self.dir / f"{self.id}.json"

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, "title": self.title, "runs": self.runs,
                "turns": [t.__dict__ for t in self.turns], "history": self._history,
                "draft": None if self.draft is None else {"spec": self.draft.spec, "headers": self.draft.headers,
                                                          "problems": self.draft.problems}}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path, llm: LLMClient, settings: Settings, **kw) -> "Conversation":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        c = cls(llm, settings, chat_id=data["id"], chats_dir=Path(path).parent, title=data.get("title", ""), **kw)
        c.turns = [ChatTurn(**t) for t in data.get("turns", [])]
        c.runs = list(data.get("runs", []))
        c._history = list(data.get("history", []))
        if data.get("draft"):
            c.draft = Draft(**data["draft"])
        return c

    # ------------------------------------------------------------ 发送

    def _complete(self, messages: list[dict]):
        tier = self.settings.tier(self.agent.model_tier)
        params = dict(model=tier.name, temperature=self.agent.temperature,
                      max_tokens=self.agent.max_tokens or tier.max_tokens or 8192,
                      extra_body=tier.extra_body(self.thinking or self.agent.thinking))
        box = default_tools()
        allowed = box.for_agent(self.agent.toolsets)
        if not allowed:
            return self.llm.complete(messages, **params)
        pipe = ToolPipeline(box, allowed=allowed, ctx=ToolContext(agent=self.agent.name),
                            max_chars=int(self.settings.tools.get("max_chars", 8000)))
        return call_with_tools(lambda m, tools: self.llm.complete(m, tools=tools, **params), messages, allowed, pipe,
                               max_rounds=int(self.settings.tools.get("max_rounds", 5)))

    def send(self, text: str, *, run_dir: Path | None = None, workflow=None) -> ChatTurn:
        """负责人说一句话。run_dir 不为空时附上那次运行的摘要（追问）。返回 analyst 的回复。"""
        if not self.title:
            self.title = text.strip().splitlines()[0][:24] if text.strip() else "新对话"
        location = "对话 › 运行结束后追问" if run_dir else "对话 › 需求分析"
        b = Brief(text.strip(), location, refs=resolve_refs(text, run_dir=run_dir, workflow=workflow))
        if self.draft is not None:
            b.attach("当前的需求草稿 spec.md", self.draft.spec)
            for p, body in self.draft.headers.items():
                b.attach(f"当前的头文件 {p}", fence(body, "c"))
        if run_dir is not None:
            b.attach(f"运行 {Path(run_dir).name} 的结果", run_summary(Path(run_dir)))
        sent = b.render()
        if not self._history:
            self._history = [{"role": "system", "content": load_prompt(self.agent.prompt_name).template}]
        msgs = self._history + [{"role": "user", "content": sent}]
        resp = self._complete(msgs)
        conv = (resp.raw or {}).get("conversation") if isinstance(resp.raw, dict) else None
        self._history = list(conv) if conv else msgs + [{"role": "assistant", "content": resp.text}]
        self.turns.append(ChatTurn("user", text, sent, time.time()))
        reply = ChatTurn("assistant", resp.text, "", time.time())
        self.turns.append(reply)
        draft = parse_draft(resp.text)
        if draft is not None:
            if not draft.spec and self.draft is not None:
                draft.spec = self.draft.spec
            if not draft.headers and self.draft is not None:
                draft.headers = dict(self.draft.headers)
            draft.problems = check_headers(draft, self.toolchain) if draft.headers else ["还没有公共头文件"]
            self.draft = draft
        self.save()
        return reply

    def start_task(self, root: Path) -> Task:
        """把当前草稿写成临时任务，供运行使用。"""
        if self.draft is None or not self.draft.ready:
            why = "；".join(self.draft.problems) if self.draft else "还没有整理好的需求"
            raise ValueError(f"需求还不能开始运行：{why}")
        name = re.sub(r"[^A-Za-z0-9_]+", "_", Path(next(iter(self.draft.headers))).stem) or "module"
        return materialize(self.draft, root / self.id, name)


def list_conversations(chats_dir: Path | str = DEFAULT_CHATS_DIR) -> list[dict]:
    """会话列表：最近的在前。每项 {id, title, path, runs, mtime}。"""
    out = []
    for p in sorted(Path(chats_dir).glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"id": d.get("id", p.stem), "title": d.get("title") or p.stem, "path": p,
                    "runs": d.get("runs", []), "mtime": p.stat().st_mtime})
    return out


# ---------------------------------------------------------------- 让 AI 改工作流

def propose_workflow_edit(llm: LLMClient, settings: Settings, workflow, instruction: str,
                          selected: list[str] = (), registry: Registry | None = None):
    """返回 (新工作流或 None, 问题列表, 发出的任务单, AI 的原始回复)。不会自动保存。"""
    from .orchestrator.workflow import dump_workflow, engine_problems, parse_workflow, step_toml
    registry = registry or default_registry()
    agent = registry.find("analyst")[0]
    b = Brief(instruction.strip() + "\n\n请输出修改后的完整工作流 TOML（一个 ```toml 代码块），版本号加一，based_on 写原版本号。"
              "只能用这些步骤类型：agent、verify、parallel、condition、loop、subflow、foreach、human；"
              "条件只能用 verify_passed、signature_repeated、attempts_exhausted、review_needs_fix、human_review。",
              f"工作流设计器 › {workflow.name} {workflow.version}")
    by_id = {s.id: (s, "main") for s in workflow.main}
    for name, ss in workflow.subflows.items():
        by_id.update({s.id: (s, f"subflow.{name}.steps") for s in ss})
    for sid in selected:
        if sid in by_id:
            s, table = by_id[sid]
            b.ref(f"workflow.step {sid} 选中的步骤", step_toml(s, table), "toml")
    b.ref(f"workflow {workflow.name} {workflow.version} 完整工作流", dump_workflow(workflow), "toml")
    sent = b.render()
    tier = settings.tier(agent.model_tier)
    resp = llm.complete([{"role": "system", "content": load_prompt(agent.prompt_name).template},
                         {"role": "user", "content": sent}],
                        model=tier.name, temperature=agent.temperature,
                        max_tokens=agent.max_tokens or tier.max_tokens or 8192, extra_body=tier.extra_body())
    m = _TOML_BLOCK.search(resp.text)
    if not m:
        return None, ["AI 的回复里没有 ```toml 代码块"], sent, resp.text
    try:
        new = parse_workflow(tomllib.loads(m.group(1)), "AI 的修改")
    except Exception as exc:
        return None, [f"AI 给的 TOML 读不出来：{exc}"], sent, resp.text
    return new, engine_problems(new, registry), sent, resp.text

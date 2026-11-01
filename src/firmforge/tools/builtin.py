"""3 个内置工具（tools_mcp_framework.md 第 4 节）。

隔离规则：任何内置工具都不读 hidden_tests/、reference/、mutants/；compile_check 只跑第 1、2 层，
工作目录是运行目录下的一份临时副本，里面只有 Agent 传进来的源文件和依赖模块的实现。
"""

from __future__ import annotations

import itertools
import shutil
import threading

from ..agents.protocol import check_path, write_files
from .registry import ToolContext, ToolDef

KB_TOP_K = 3
_counter = itertools.count(1)
_counter_lock = threading.Lock()


def _kb_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        raise ValueError("query 不能为空")
    kb = ctx.kb
    if kb is None:
        from ..memory.knowledge import KnowledgeBase
        kb = ctx.kb = KnowledgeBase.load()
    chunks = kb.search(query, k=KB_TOP_K)
    if not chunks:
        return "知识库里没有找到相关内容。"
    return "\n\n".join(c.render() for c in chunks)


def _kb_summary(args: dict, result: str) -> str:
    n = result.count("### ")
    return f"找到 {n} 段" if n else "没有找到"


def _compile_check(args: dict, ctx: ToolContext) -> str:
    from ..verifier import verify
    files = args.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("files 必须是非空对象：文件路径 → 文件内容")
    if ctx.task is None or ctx.scratch_dir is None:
        raise ValueError("当前场景不能试编译")
    accepted, problems = {}, []
    for raw, content in files.items():
        path, why = check_path(str(raw), ctx.allowed_src_dirs)
        if path is None or not path.startswith("src/"):
            problems.append(f"{raw}：{why or '只编译 src/ 下的文件'}")
            continue
        if not isinstance(content, str):
            problems.append(f"{raw}：内容必须是字符串")
            continue
        accepted[path] = content if content.endswith("\n") else content + "\n"
    if not accepted:
        raise ValueError("没有可以编译的文件：" + "；".join(problems))
    with _counter_lock:
        n = next(_counter)
    work = ctx.scratch_dir / f"compile_{n:04d}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        write_files(work, {**{p: c for p, c in ctx.dep_files.items() if p.startswith("src/")}, **accepted})
        report = verify(work, ctx.task, layers=["host_compile", "cross_compile"], stop_on_fail=False,
                        toolchain=ctx.toolchain)
        from ..agents.context import report_details
        lines = [f"试编译了 {len(accepted)} 个文件（{', '.join(sorted(accepted))}）。"]
        if problems:
            lines.append("这些文件没有参与：" + "；".join(problems))
        lines.append(report_details(report, work))
        return "\n".join(lines)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _compile_summary(args: dict, result: str) -> str:
    if "失败" in result:
        return "有编译错误"
    if "跳过" in result:
        return "部分检查被跳过"
    return "主机、交叉编译都通过"


def _list_interface(args: dict, ctx: ToolContext) -> str:
    from ..agents.context import public_headers
    from ..orchestrator.dag import public_functions
    if ctx.task is None:
        raise ValueError("当前场景没有任务")
    parts = []
    if ctx.module is not None:
        m = ctx.module
        parts.append(f"## 本模块 {m.id}：{m.title}\n\n要写的文件：{', '.join(m.files)}")
        if m.provides:
            parts.append("要实现的公共函数：\n" + "\n".join(f"- {f}" for f in m.provides))
        for hdr, protos in m.interface.items():
            parts.append(f"本模块提供的内部接口 `{hdr}`：\n" + "\n".join(f"- `{p}`" for p in protos))
        if ctx.plan is not None:
            for d in m.depends_on:
                dm = ctx.plan.by_id.get(d)
                if dm is None:
                    continue
                for hdr, protos in dm.interface.items():
                    parts.append(f"依赖模块 {d} 提供的 `{hdr}`：\n" + "\n".join(f"- `{p}`" for p in protos))
    else:
        funcs = public_functions(ctx.task)
        parts.append("要实现的公共函数：\n" + ("\n".join(f"- {f}" for f in funcs) or "（无）"))
    for h in public_headers(ctx.task):
        parts.append(f"公共头文件 `include/{h.name}`：\n\n```c\n{h.read_text(encoding='utf-8').rstrip()}\n```")
    return "\n\n".join(parts)


BUILTIN_TOOLS = (
    ToolDef(
        name="kb_search", toolset="knowledge",
        description="在嵌入式知识库（编码规则、协议要点、芯片注意事项、CRC 参数等）里检索，返回最相关的 3 段。",
        parameters={"type": "object", "properties": {"query": {"type": "string", "description": "要查的内容，一句话"}},
                    "required": ["query"], "additionalProperties": False},
        execute=_kb_search, timeout_s=10, summarize=_kb_summary),
    ToolDef(
        name="compile_check", toolset="build",
        description="把草稿交给主机 gcc 和 arm-none-eabi-gcc 试编译（-Wall -Wextra -Werror），返回结果和报错原文。"
                    "只编译 src/ 下的 .c/.h，已完成的依赖模块会自动带上。交卷前用它发现语法错误和警告。",
        parameters={"type": "object",
                    "properties": {"files": {"type": "object", "description": "文件路径（如 src/foo.c）→ 完整文件内容",
                                             "additionalProperties": {"type": "string"}}},
                    "required": ["files"], "additionalProperties": False},
        execute=_compile_check, timeout_s=60, summarize=_compile_summary),
    ToolDef(
        name="list_interface", toolset="interface",
        description="列出本模块要实现的函数、依赖模块提供的函数原型和公共头文件全文，用来核对接口。",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        execute=_list_interface, timeout_s=10, summarize=lambda a, r: "已列出接口"),
)

# 查资料工具（DECISIONS 第 66 条第 ② 步），定义在 grounding.research
from ..grounding.research import SOURCE_TOOLS  # noqa: E402

BUILTIN_TOOLS = BUILTIN_TOOLS + SOURCE_TOOLS

"""Agent 输出协议（ARCHITECTURE §5.1）：文件块解析与路径白名单。

    <<<FILE src/ringbuf.c
    …文件内容…
    >>>FILE
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

FILE_BLOCK = re.compile(r"^<<<FILE[ \t]+(?P<path>[^\r\n]+?)[ \t]*\r?\n(?P<body>.*?)^>>>FILE[ \t]*$",
                        re.M | re.S)
OPEN_TAG = re.compile(r"^<<<FILE\b", re.M)
ALLOWED_SUFFIXES = (".c", ".h")


@dataclass
class ParseResult:
    files: dict[str, str] = field(default_factory=dict)        # 相对路径 -> 内容
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (路径, 原因)
    unterminated: int = 0                                       # 没有 >>>FILE 结尾的块数


def check_path(raw: str, allowed_dirs: tuple[str, ...]) -> tuple[str | None, str | None]:
    """校验 Agent 想写的路径。返回 (规范化路径, None) 或 (None, 拒绝原因)。"""
    text = raw.strip().strip("`'\"").replace("\\", "/")
    if not text:
        return None, "路径为空"
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None, "不允许绝对路径"
    p = PurePosixPath(text)
    if any(part in ("..", "") for part in p.parts) or ".." in text.split("/"):
        return None, "不允许包含 .."
    norm = p.as_posix()
    if len(p.parts) != 2 or p.parts[0] not in allowed_dirs:
        allowed = "、".join(f"{d}/" for d in allowed_dirs) or "（无）"
        return None, f"只允许写在 {allowed} 下（不能有子目录）"
    if p.suffix not in ALLOWED_SUFFIXES:
        return None, "只允许 .c 或 .h 文件"
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", p.name):
        return None, "文件名只能包含字母、数字、下划线、点和连字符"
    return norm, None


def parse_files(text: str, allowed_dirs: tuple[str, ...]) -> ParseResult:
    result = ParseResult()
    matched_starts = set()
    for m in FILE_BLOCK.finditer(text):
        matched_starts.add(m.start())
        path, reason = check_path(m["path"], allowed_dirs)
        if path is None:
            result.rejected.append((m["path"].strip(), reason))
            continue
        body = m["body"]
        if not body.endswith("\n"):
            body += "\n"
        result.files[path] = body
    result.unterminated = sum(1 for m in OPEN_TAG.finditer(text) if m.start() not in matched_starts)
    return result


def write_files(workdir: Path, files: dict[str, str]) -> list[Path]:
    """把解析出的文件写进工作目录（路径已经过 check_path 校验）。"""
    written = []
    for rel, content in files.items():
        dst = Path(workdir) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8", newline="\n")
        written.append(dst)
    return written

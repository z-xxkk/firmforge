"""错误签名（ARCHITECTURE §2.4）：判断修正是不是在原地打转。

保留错误类型、标识符名、测试函数名；去掉路径、行列号、内存地址、进程号。
"""

from __future__ import annotations

import hashlib
import re

_DIRS = re.compile(r"(?:[A-Za-z]:)?[/\\]?(?:[\w.\-]+[/\\])+(?=[\w.\-]+)")
_LINE_COL = re.compile(r"(\.[A-Za-z]\w*):\d+(?::\d+)?")
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_PID = re.compile(r"==\d+==")
_SPACES = re.compile(r"\s+")


def normalize_message(message: str) -> str:
    s = _DIRS.sub("", message)
    s = _LINE_COL.sub(r"\1", s)
    s = _HEX.sub("0x?", s)
    s = _PID.sub("==?==", s)
    return _SPACES.sub(" ", s).strip()


def basename(path: str) -> str:
    return re.split(r"[/\\]", path)[-1]


def compile_key(file: str | None, severity: str, message: str) -> str:
    sev = "error" if severity == "fatal error" else severity
    prefix = f"{basename(file)}: " if file else ""
    return f"{prefix}{sev}: {normalize_message(message)}"


def layer_signature(layer: str, keys: list[str]) -> str:
    joined = "\n".join(sorted(set(keys)))
    return f"{layer}:{hashlib.sha1(joined.encode('utf-8')).hexdigest()[:12]}"

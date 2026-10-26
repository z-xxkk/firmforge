"""设置页写回配置文件（DECISIONS 第 62 条）：设置页改的就是 firmforge.toml 和 .env（借鉴 DeepSeek Harness）。

- 按行修改：只替换要改的那一行的值，保留注释和其他内容；没有这一项就插到所在表的末尾；没有这个表就加在文件末尾；
- 密钥只写不读：set_secret() 写 .env，界面永远拿不到原文，has_secret() 只回答"有没有"；
- 写之前先用 tomllib 确认改完的文本能读回。
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

_TABLE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(#.*)?$")


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {toml_value(x)}" for k, x in v.items()) + " }"
    return json.dumps(str(v), ensure_ascii=False)


def _split_comment(rest: str) -> tuple[str, str]:
    """把 'value   # 注释' 拆开；字符串里的 # 不算注释。"""
    in_str, quote = False, ""
    for i, ch in enumerate(rest):
        if in_str:
            if ch == "\\" and quote == '"':
                continue
            if ch == quote:
                in_str = False
        elif ch in "\"'":
            in_str, quote = True, ch
        elif ch == "#":
            return rest[:i].rstrip(), rest[i:]
    return rest.rstrip(), ""


def set_value(text: str, table: str, key: str, value) -> str:
    """把 [table] 里的 key 改成 value，返回新文本。"""
    lines = text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        m = _TABLE.match(ln)
        if m:
            if start is not None and end is None:
                end = i
            if m.group(1) == table and not ln.lstrip().startswith("[["):
                start, end = i, None
    new_line = f"{key} = {toml_value(value)}"
    if start is None:
        out = text.rstrip("\n") + f"\n\n[{table}]\n{new_line}\n"
    else:
        end = len(lines) if end is None else end
        key_re = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*(.*)$")
        for i in range(start + 1, end):
            m = key_re.match(lines[i])
            if m:
                _, comment = _split_comment(m.group(2))
                pad = " " * max(1, 34 - len(new_line)) if comment else ""
                lines[i] = f"{m.group(1)}{new_line}{pad}{comment}"
                break
        else:
            insert = end
            while insert > start + 1 and not lines[insert - 1].strip():
                insert -= 1
            lines.insert(insert, new_line)
        out = "\n".join(lines) + "\n"
    tomllib.loads(out)                     # 改完必须还能读
    return out


def update_file(path: Path, changes: list[tuple[str, str, object]]) -> None:
    """changes：[(表, 键, 值)]。"""
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    for table, key, value in changes:
        text = set_value(text, table, key, value)
    path.write_text(text, encoding="utf-8")


def remove_table(text: str, table: str) -> str:
    lines = text.splitlines()
    out, skipping = [], False
    for ln in lines:
        m = _TABLE.match(ln)
        if m:
            skipping = m.group(1) == table
        if not skipping:
            out.append(ln)
    result = "\n".join(out).rstrip("\n") + "\n"
    tomllib.loads(result)
    return result


# ---------------------------------------------------------------- .env 里的密钥

def set_secret(env_path: Path, name: str, value: str) -> None:
    """写入或替换 .env 里的 NAME=value；value 为空时什么都不做（界面上"留空则不修改"）。"""
    if not value:
        return
    if "\n" in value or "\r" in value:
        raise ValueError("密钥里不能有换行")
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=")
    for i, ln in enumerate(lines):
        if pat.match(ln):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass


def has_secret(env_path: Path, name: str, environ: dict | None = None) -> bool:
    import os
    environ = os.environ if environ is None else environ
    if environ.get(name, "").strip():
        return True
    env_path = Path(env_path)
    if not env_path.is_file():
        return False
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(name)}\s*=\s*(\S+)")
    return any(pat.match(ln) for ln in env_path.read_text(encoding="utf-8").splitlines())

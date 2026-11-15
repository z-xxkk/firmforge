"""给 Agent 写的驱动代码造"变异体"（DECISIONS 第 125 条）：故意改坏一处，看 Agent 自己写的测试能不能发现。

做法参照 Meta 的 ACH（Mutation-Guided LLM-based Test Generation at Meta，FSE 2025）和 MuTAP（Dakhel 等，2023）：
测试没发现的改动（存活的变异体）交给测试 Agent，让它补测试。

只做文本替换，不解析 C 语法；改坏的地方挑嵌入式驱动里最常见、最容易测漏的几类：
- 去掉等待：vTaskDelay(...) / esp_rom_delay_us(...) 的参数改成 0；
- 改寄存器值：调用参数或 #define 里的两位十六进制常数，翻转最高的一位；
- 改换算系数：带小数点的数、或 ≥ 100 的整数常数放大一倍（#define、static const、赋值）；
- 改比较：if 条件里的 <= 换成 <、>= 换成 >、== 换成 !=；
- 参数检查失效：return ESP_ERR_INVALID_ARG 换成 return ESP_OK。

给测试 Agent 的说明只写"在哪个函数里改了什么"，不给代码（测试 Agent 看不到实现代码，第 122 条之前就是这么定的）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KINDS = ("delay", "regval", "scale", "compare", "argcheck")
KIND_NAMES = {"delay": "去掉等待", "regval": "改寄存器值", "scale": "改换算系数", "compare": "改比较条件",
              "argcheck": "参数检查失效"}


@dataclass(frozen=True)
class Mutant:
    file: str
    line: int                   # 从 1 开始
    kind: str
    func: str                   # 改动所在的函数（找不到是空字符串）
    describe: str               # 给测试 Agent 看的说明（不含代码）
    content: str                # 改完以后整个文件的内容


_FUNC = re.compile(r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;]*\)\s*\{?\s*$")
_DELAY = re.compile(r"\b(vTaskDelay|esp_rom_delay_us)\s*\((.+)\)\s*;")
_HEX = re.compile(r"\b0[xX]([0-9A-Fa-f]{2})\b(?![0-9A-Fa-f])")
_SCALE = re.compile(r"(?<![\w.])(\d+\.\d+f?|\d{3,}(?:\.\d*)?f?)(?![\w.])")
_CMP = (("<=", "<"), (">=", ">"), ("==", "!="))


def _is_code(line: str) -> bool:
    t = line.strip()
    return bool(t) and not t.startswith(("//", "/*", "*", "#include", "#pragma", "#if", "#endif", "#else"))


def _strip_comment(line: str) -> tuple[str, str]:
    i = line.find("//")
    j = line.find("/*")
    cut = min(x for x in (i, j, len(line)) if x >= 0)
    return line[:cut], line[cut:]


def _func_at(lines: list[str], idx: int) -> str:
    """往上找最近的函数头（行首是类型、以 ( ) 结尾）。"""
    for k in range(idx, -1, -1):
        s = lines[k]
        if s and not s[0].isspace() and "(" in s and not s.startswith(("#", "/", "*", "}")):
            m = _FUNC.match(s.split("//")[0].rstrip())
            if m and m.group(1) not in ("if", "for", "while", "switch", "return"):
                return m.group(1)
    return ""


def candidates(path: str, content: str) -> list[Mutant]:
    """这个文件里所有可以改的地方，按类别和出现顺序排。"""
    lines = content.split("\n")
    out: list[Mutant] = []

    def add(i: int, kind: str, new_line: str, desc: str):
        func = _func_at(lines, i)
        where = f"在 {func} 里" if func else f"在 {path} 第 {i + 1} 行"
        out.append(Mutant(path, i + 1, kind, func, f"{where}{desc}",
                          "\n".join(lines[:i] + [new_line] + lines[i + 1:])))

    for i, raw in enumerate(lines):
        if not _is_code(raw):
            continue
        code, rest = _strip_comment(raw)
        m = _DELAY.search(code)
        if m and m.group(2).strip() not in ("0", "pdMS_TO_TICKS(0)"):
            new = code[:m.start(2)] + "0" + code[m.end(2):]
            add(i, "delay", new + rest, f"去掉了一处等待（{m.group(1)}，原来等 {m.group(2).strip()}）")
            continue
        is_define = code.lstrip().startswith("#define")
        if "return ESP_ERR_INVALID_ARG" in code:
            add(i, "argcheck", code.replace("return ESP_ERR_INVALID_ARG", "return ESP_OK", 1) + rest,
                "让一处参数检查失效：本该返回 ESP_ERR_INVALID_ARG 的地方改成返回 ESP_OK")
            continue
        h = _HEX.search(code)
        if h and (is_define or "(" in code):
            v = int(h.group(1), 16)
            if v:
                nv = v ^ (1 << (v.bit_length() - 1))
                new = code[:h.start()] + f"0x{nv:02X}" + code[h.end():]
                add(i, "regval", new + rest, f"把一个十六进制常数（寄存器地址或取值）从 0x{v:02X} 改成了 0x{nv:02X}")
                continue
        s = _SCALE.search(code)
        if s and re.search(r"(?i)hz|speed|freq|clk|baud|stack|size|len|timeout|_ms\b|_us\b|delay|prio", code):
            s = None                                   # 时钟、波特率、栈大小、超时这类改了多半测不出也不该测
        if s and (is_define or "=" in code or "{" in code) and not re.search(r"\[\s*$", code[:s.start()]):
            txt = s.group(1)
            num = float(txt.rstrip("f"))
            if num:
                suffix = "f" if txt.endswith("f") else ""
                nv = num * 2
                new_txt = (f"{nv:.6g}" if "." in txt else str(int(nv))) + suffix
                if "." in txt and "." not in new_txt and "e" not in new_txt:
                    new_txt = new_txt.rstrip("f") + ".0" + suffix
                new = code[:s.start()] + new_txt + code[s.end():]
                add(i, "scale", new + rest, f"把一个常数从 {txt} 改成了 {new_txt}（放大一倍）")
                continue
        if re.match(r"\s*(?:\}\s*else\s+)?if\s*\(", code):
            for a, b in _CMP:
                if a in code:
                    add(i, "compare", code.replace(a, b, 1) + rest, f"把一处判断条件里的 {a} 改成了 {b}")
                    break
    return out


def pick(files: dict[str, str], n: int) -> list[Mutant]:
    """从驱动的 .c 文件里挑最多 n 个变异体：先每类挑一个（类别轮流，同类挑最靠前的），不够再按顺序补。"""
    cands = [c for path in sorted(files) if path.endswith(".c") for c in candidates(path, files[path])]
    chosen: list[Mutant] = []
    for kind in KINDS:
        same = [x for x in cands if x.kind == kind]
        if kind == "scale":                            # 带小数点的（换算系数）优先
            same.sort(key=lambda x: "." not in x.describe.split("从 ")[-1].split(" ")[0])
        c = same[0] if same else None
        if c is not None and len(chosen) < n:
            chosen.append(c)
    for c in cands:
        if len(chosen) >= n:
            break
        if c not in chosen and (c.kind, c.func or c.line) not in {(x.kind, x.func or x.line) for x in chosen}:
            chosen.append(c)
    return chosen

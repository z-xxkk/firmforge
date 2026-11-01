"""逻辑层模块的电脑测试（DECISIONS 第 81 条）：主机 gcc 编译模块 + 测试 Agent 写的 Unity 测试，运行并解析结果。

逻辑层与硬件无关，所以不需要板子；编译开 -Wall -Wextra -Werror 和 ASan/UBSan，越界、未定义行为直接算失败。
主机编译器经过 config.Toolchain（环境变量 FF_CC 可覆盖），找不到时结果记为 skipped（CLAUDE.md 硬性规则 4）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..verifier.layers import parse_unity, sanitizer_lines

RUN_TIMEOUT_S = 20
CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]


@dataclass
class HostTestResult:
    compiled: bool = False
    compile_log: str = ""
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)       # (用例, 原因)
    sanitizer: list[str] = field(default_factory=list)
    timeout: bool = False
    skipped: str = ""

    @property
    def ok(self) -> bool:
        return (not self.skipped and self.compiled and not self.failed and not self.sanitizer and not self.timeout
                and bool(self.passed))

    def verdict(self) -> str:
        return "skipped" if self.skipped else ("pass" if self.ok else "fail")

    def report(self) -> str:
        if self.skipped:
            return f"电脑测试跳过：{self.skipped}"
        if not self.compiled:
            return "测试编译失败：\n" + self.compile_log[-2000:]
        lines = [f"失败：{n}　{msg}" for n, msg in self.failed]
        lines += [f"内存或未定义行为错误：{s}" for s in self.sanitizer[:5]]
        if self.timeout:
            lines.append(f"运行超过 {RUN_TIMEOUT_S} 秒（死循环？）")
        if not self.passed and not self.failed:
            lines.append("一个用例都没有跑")
        lines.append(f"通过 {len(self.passed)} 个，失败 {len(self.failed)} 个")
        return "\n".join(lines)


def run_host_tests(files: dict[str, str], test_path: str, workdir: Path,
                   toolchain: config.Toolchain | None = None) -> HostTestResult:
    """files：main/ 下的相对路径 → 内容（本模块和依赖的逻辑层模块，不含测试）；test_path：测试文件的相对路径。"""
    tc = toolchain or config.Toolchain.from_env()
    cc = tc.resolve("cc")
    if cc is None:
        return HostTestResult(skipped=f"找不到主机编译器 {tc.cc}")
    unity = config.unity_dir()
    if not (unity / "unity.c").is_file():
        return HostTestResult(skipped=f"找不到 Unity 源码：{unity}")
    work = Path(workdir)
    shutil.rmtree(work, ignore_errors=True)
    for rel, content in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    srcs = [str(work / r) for r in sorted(files) if r.endswith(".c") and r != test_path]
    exe = work / "host_test"
    cmd = [cc, *CFLAGS, "-I", str(work / "include"), "-I", str(work), "-I", str(unity), *srcs,
           str(work / test_path), str(unity / "unity.c"), "-lm", "-o", str(exe)]
    comp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    res = HostTestResult(compiled=comp.returncode == 0, compile_log=(comp.stdout + comp.stderr).replace(str(work) + "/", ""))
    if not res.compiled:
        return res
    try:
        # 外面设置的 LD_PRELOAD（例如沙箱注入的库）会让 ASan 拒绝启动，测试程序里去掉它
        env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
        run = subprocess.run([str(exe)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=RUN_TIMEOUT_S, cwd=str(work), env=env)
        out = run.stdout + run.stderr
    except subprocess.TimeoutExpired as exc:
        res.timeout = True
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    u = parse_unity(out)
    res.passed = u.passed
    res.failed = [(name, msg) for name, _f, _l, msg in u.failed]
    res.sanitizer = sanitizer_lines(out)
    return res

"""判定器各层的实现（ARCHITECTURE §3）以及工具输出解析。

每层函数签名为 fn(ctx) -> LayerResult。工具缺失时该层或子检查标记为 skipped /
complete=False 并写明原因，绝不判为通过。
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..config import Toolchain
from ..tasks import Task
from .report import FAIL, PASS, SKIPPED, Diagnostic, LayerResult
from .signature import basename, compile_key, normalize_message

TITLES = {
    "host_compile": "主机编译",
    "cross_compile": "交叉编译",
    "resources": "资源预算",
    "static_check": "静态检查",
    "unit_tests": "单元测试",
    "hidden_tests": "隐藏测试",
}

HOST_FLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic"]
CROSS_FLAGS = ["-mcpu=cortex-m3", "-mthumb", "-Os", "-ffunction-sections", "-fdata-sections",
               "-std=c11", "-Wall", "-Wextra", "-Werror", "-fstack-usage"]
TEST_FLAGS = ["-std=c11", "-g", "-O0", "-fno-omit-frame-pointer"]
SANITIZE_FLAGS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
COVERAGE_FLAGS = ["--coverage"]
STACK_NOTE = "栈检查只看单个函数的栈帧，没有分析调用链的最坏情况"
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


# ---------------------------------------------------------------- 上下文

@dataclass
class BuildContext:
    workdir: Path
    task: Task
    toolchain: Toolchain
    build_dir: Path
    host_objs: list[Path] = field(default_factory=list)
    cross_objs: list[Path] = field(default_factory=list)
    cross_ok: bool = False

    @property
    def src_dir(self) -> Path:
        return self.workdir / "src"

    @property
    def tests_dir(self) -> Path:
        return self.workdir / "tests"

    def sources(self) -> list[Path]:
        return sorted(self.src_dir.glob("*.c")) if self.src_dir.is_dir() else []

    def include_flags(self) -> list[str]:
        return ["-I", str(self.task.include_dir), "-I", str(self.src_dir)]

    def rel(self, path: str | Path | None) -> str | None:
        """把绝对路径转成相对工作目录或任务目录的路径，便于阅读。"""
        if path is None:
            return None
        p = Path(path)
        for base in (self.workdir, self.task.root, self.build_dir):
            try:
                return p.resolve().relative_to(base.resolve()).as_posix()
            except (ValueError, OSError):
                continue
        return str(path)


@dataclass
class ProcResult:
    returncode: int | None       # None 表示超时
    output: str
    timed_out: bool = False


def run_proc(cmd: list[str], *, cwd: Path | None = None, timeout: float | None = 120,
             env: dict[str, str] | None = None) -> ProcResult:
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = exc.output or b""
        return ProcResult(None, out.decode("utf-8", errors="replace"), True)
    except OSError as exc:
        return ProcResult(-1, f"无法执行 {cmd[0]}：{exc}")
    return ProcResult(proc.returncode, proc.stdout.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------- 解析器

GCC_DIAG = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$", re.M)
UNDEF_REF = re.compile(r"undefined reference to [`'\"]?([\w$.]+)['\"]?")
MULTI_DEF = re.compile(r"multiple definition of [`'\"]?([\w$.]+)['\"]?")


def parse_gcc(output: str, rel=None) -> list[Diagnostic]:
    """解析 GCC 诊断和链接错误；note 级别忽略。"""
    rel = rel or (lambda p: p)
    diags: list[Diagnostic] = []
    for m in GCC_DIAG.finditer(output):
        sev = m["sev"]
        if sev == "note":
            continue
        severity = "error" if sev in ("error", "fatal error") else "warning"
        diags.append(Diagnostic(severity, m["msg"].strip(), compile_key(m["file"], sev, m["msg"]),
                                rel(m["file"]), int(m["line"])))
    for line in output.splitlines():
        for regex, kind in ((UNDEF_REF, "undefined"), (MULTI_DEF, "multiple")):
            mm = regex.search(line)
            if mm:
                diags.append(Diagnostic("error", normalize_message(line), f"link:{kind}:{mm[1]}"))
    return diags


UNITY_PASS = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<name>\w+):PASS", re.M)
UNITY_FAIL = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<name>\w+):FAIL(?::\s*(?P<msg>.*))?$", re.M)
UNITY_IGNORE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<name>\w+):IGNORE", re.M)
UNITY_SUMMARY = re.compile(r"^(\d+) Tests (\d+) Failures (\d+) Ignored", re.M)


@dataclass
class UnityResult:
    passed: list[str]
    failed: list[tuple[str, str, int, str]]     # (测试名, 文件, 行号, 消息)
    ignored: list[str]
    summary: tuple[int, int, int] | None        # (总数, 失败, 忽略)


def parse_unity(output: str) -> UnityResult:
    passed = [m["name"] for m in UNITY_PASS.finditer(output)]
    failed = [(m["name"], m["file"], int(m["line"]), (m["msg"] or "").strip())
              for m in UNITY_FAIL.finditer(output)]
    ignored = [m["name"] for m in UNITY_IGNORE.finditer(output)]
    sums = UNITY_SUMMARY.findall(output)
    summary = tuple(int(x) for x in sums[-1]) if sums else None
    return UnityResult(passed, failed, ignored, summary)


def sanitizer_lines(output: str) -> list[str]:
    """摘出 ASan / UBSan 报错所在行。"""
    return [ln.strip() for ln in output.splitlines()
            if "ERROR: AddressSanitizer" in ln or "runtime error:" in ln]


def sanitizer_kind(line: str) -> str:
    m = re.search(r"AddressSanitizer:\s*([\w\-]+)", line)
    if m:
        return f"asan:{m[1]}"
    m = re.search(r"runtime error:\s*(.*)$", line)
    if m:
        # 去掉具体数值，保留错误类型
        return "ubsan:" + re.sub(r"-?\d+", "N", normalize_message(m[1]))
    return normalize_message(line)


SIZE_LINE = re.compile(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([0-9a-fA-F]+)\s+(.+?)\s*$", re.M)


def parse_size(output: str) -> list[tuple[str, int, int, int]]:
    """解析 Berkeley 格式 size 输出：[(文件, text, data, bss)]。"""
    return [(m[6], int(m[1]), int(m[2]), int(m[3])) for m in SIZE_LINE.finditer(output)]


def parse_su(text: str) -> list[tuple[str, int, str]]:
    """解析 .su 文件：[(函数名, 字节数, 限定词)]。兼容 GCC 和 clang 格式。"""
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        func = parts[0].rsplit(":", 1)[-1]
        try:
            size = int(parts[1])
        except ValueError:
            continue
        rows.append((func, size, parts[2].strip()))
    return rows


def parse_nm_undefined(output: str) -> list[str]:
    syms = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-2] == "U":
            syms.append(parts[-1])
    return syms


CPPCHECK_LINE = re.compile(r"^(?P<file>(?:[A-Za-z]:)?[^:\n]*):(?P<line>\d+):(?P<sev>\w+):(?P<id>[\w\-]+):(?P<msg>.*)$", re.M)


def parse_cppcheck(output: str) -> list[tuple[str, int, str, str, str]]:
    """[(文件, 行号, 级别, 规则 id, 消息)]"""
    return [(m["file"], int(m["line"]), m["sev"], m["id"], m["msg"].strip())
            for m in CPPCHECK_LINE.finditer(output)]


GCOV_FILE = re.compile(r"^File '(?P<file>.+)'\s*$", re.M)
GCOV_LINES = re.compile(r"Lines executed:\s*(?P<pct>\d+(?:\.\d+)?)% of (?P<n>\d+)")


def parse_gcov(output: str, source_name: str) -> tuple[float, int] | None:
    """在 gcov 输出里找到指定源文件那一节，返回 (百分比, 行数)。"""
    sections = list(GCOV_FILE.finditer(output))
    for i, m in enumerate(sections):
        if basename(m["file"]) != source_name:
            continue
        end = sections[i + 1].start() if i + 1 < len(sections) else len(output)
        lm = GCOV_LINES.search(output, m.end(), end)
        if lm:
            return float(lm["pct"]), int(lm["n"])
    return None


# ---------------------------------------------------------------- 工具函数

def _new(layer: str) -> LayerResult:
    return LayerResult(layer, TITLES[layer])


def _skip(lr: LayerResult, reason: str) -> LayerResult:
    lr.status = SKIPPED
    lr.complete = False
    lr.notes.append(reason)
    return lr


def _missing(tool_name: str, tc: Toolchain) -> str:
    var = config.TOOL_SPECS[tool_name][0]
    return f"未找到 {getattr(tc, tool_name)}（可用环境变量 {var} 指定），本项未运行"


def _compile_failure(lr: LayerResult, src: Path, proc: ProcResult, ctx: BuildContext, kind: str) -> None:
    diags = [d for d in parse_gcc(proc.output, ctx.rel) if d.severity == "error"]
    if not diags:
        tail = proc.output.strip().splitlines()[-1:] or ["无输出"]
        diags = [Diagnostic("error", f"{kind}失败：{tail[0]}", f"{kind}_failed:{src.name}", ctx.rel(src))]
    lr.diagnostics.extend(diags)


def _add_log(lr: LayerResult, title: str, output: str) -> None:
    if output.strip():
        lr.log += f"$ {title}\n{output.rstrip()}\n"


# ---------------------------------------------------------------- L1 主机编译

def host_compile(ctx: BuildContext) -> LayerResult:
    lr = _new("host_compile")
    cc = ctx.toolchain.resolve("cc")
    if cc is None:
        return _skip(lr, _missing("cc", ctx.toolchain))
    srcs = ctx.sources()
    if not srcs:
        lr.status = FAIL
        lr.diagnostics.append(Diagnostic("error", "src/ 下没有 .c 源文件", "no_sources"))
        return lr
    out_dir = ctx.build_dir / "host"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for src in srcs:
        obj = out_dir / f"{src.stem}.o"
        proc = run_proc([cc, *HOST_FLAGS, *ctx.include_flags(), "-c", str(src), "-o", str(obj)])
        _add_log(lr, f"gcc -c {src.name}", proc.output)
        if proc.returncode != 0:
            ok = False
            _compile_failure(lr, src, proc, ctx, "编译")
        else:
            ctx.host_objs.append(obj)
    lr.metrics["files"] = len(srcs)
    lr.status = PASS if ok else FAIL
    return lr


# ---------------------------------------------------------------- L2 交叉编译

def cross_compile(ctx: BuildContext) -> LayerResult:
    lr = _new("cross_compile")
    cc = ctx.toolchain.resolve("cross_cc")
    if cc is None:
        return _skip(lr, _missing("cross_cc", ctx.toolchain))
    srcs = ctx.sources()
    if not srcs:
        lr.status = FAIL
        lr.diagnostics.append(Diagnostic("error", "src/ 下没有 .c 源文件", "no_sources"))
        return lr
    out_dir = ctx.build_dir / "cross"
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for src in srcs:
        # 工作目录设为 .ff_build/cross，让 .su 文件落在这里
        proc = run_proc([cc, *CROSS_FLAGS, *ctx.include_flags(), "-c", str(src.resolve()),
                         "-o", f"{src.stem}.o"], cwd=out_dir)
        _add_log(lr, f"arm-none-eabi-gcc -c {src.name}", proc.output)
        if proc.returncode != 0:
            ok = False
            _compile_failure(lr, src, proc, ctx, "交叉编译")
        else:
            ctx.cross_objs.append(out_dir / f"{src.stem}.o")
    lr.metrics["files"] = len(srcs)
    lr.status = PASS if ok else FAIL
    ctx.cross_ok = ok
    return lr


# ---------------------------------------------------------------- L3 资源预算

def resources(ctx: BuildContext) -> LayerResult:
    lr = _new("resources")
    if not ctx.cross_ok or not ctx.cross_objs:
        return _skip(lr, "交叉编译未运行或未成功，无法统计资源")
    size = ctx.toolchain.resolve("cross_size")
    if size is None:
        return _skip(lr, _missing("cross_size", ctx.toolchain))
    proc = run_proc([size, *[str(o) for o in ctx.cross_objs]])
    _add_log(lr, "arm-none-eabi-size", proc.output)
    rows = parse_size(proc.output)
    if proc.returncode != 0 or not rows:
        lr.status = FAIL
        lr.diagnostics.append(Diagnostic("error", "size 输出无法解析", "budget:size_failed"))
        return lr
    flash = sum(t + d for _f, t, d, _b in rows)
    ram = sum(d + b for _f, _t, d, b in rows)

    stack_rows = []
    for su in sorted((ctx.build_dir / "cross").glob("*.su")):
        stack_rows += parse_su(su.read_text(encoding="utf-8", errors="replace"))
    max_func, max_stack = None, 0
    for func, nbytes, _q in stack_rows:
        if nbytes > max_stack:
            max_func, max_stack = func, nbytes
    lr.metrics.update({"flash": flash, "ram": ram, "max_stack": max_stack, "max_stack_func": max_func})
    lr.notes.append(STACK_NOTE)
    if not stack_rows:
        lr.notes.append("没有找到 .su 栈使用文件")

    b = ctx.task.budget
    lr.metrics["budget"] = {"flash": b.flash_bytes, "ram": b.ram_bytes, "stack": b.stack_bytes}
    if b.flash_bytes is not None and flash > b.flash_bytes:
        lr.diagnostics.append(Diagnostic("error", f"Flash 用量 {flash} 字节，超出预算 {b.flash_bytes}", "budget:flash"))
    if b.ram_bytes is not None and ram > b.ram_bytes:
        lr.diagnostics.append(Diagnostic("error", f"静态 RAM 用量 {ram} 字节，超出预算 {b.ram_bytes}", "budget:ram"))
    if b.stack_bytes is not None and max_stack > b.stack_bytes:
        lr.diagnostics.append(Diagnostic("error", f"函数 {max_func} 栈帧 {max_stack} 字节，超出预算 {b.stack_bytes}",
                                         "budget:stack"))
    for func, _n, qual in stack_rows:
        if "dynamic" in qual:
            lr.diagnostics.append(Diagnostic("error", f"函数 {func} 使用了动态栈（VLA 或 alloca）",
                                             f"budget:dynamic_stack:{func}"))
    lr.status = FAIL if lr.diagnostics else PASS
    return lr


# ---------------------------------------------------------------- L4 静态检查

def _forbidden_check(ctx: BuildContext, lr: LayerResult) -> bool:
    """禁用符号检查；返回是否实际运行了。"""
    tc = ctx.toolchain
    if ctx.cross_ok and ctx.cross_objs and tc.available("cross_nm"):
        nm, objs, where = tc.resolve("cross_nm"), ctx.cross_objs, "交叉"
    elif ctx.host_objs and tc.available("nm"):
        nm, objs, where = tc.resolve("nm"), ctx.host_objs, "主机"
        lr.notes.append("交叉编译目标不可用，禁用符号检查改用主机目标文件")
    else:
        lr.complete = False
        lr.notes.append("没有可用的目标文件或 nm 工具，禁用符号检查未运行")
        return False
    forbidden = set(ctx.task.forbidden_symbols)
    for obj in objs:
        proc = run_proc([nm, "-u", str(obj)])
        _add_log(lr, f"nm -u {obj.name}（{where}）", proc.output)
        if proc.returncode != 0:
            lr.diagnostics.append(Diagnostic("error", f"nm 运行失败：{obj.name}", f"nm_failed:{obj.name}"))
            continue
        for sym in parse_nm_undefined(proc.output):
            name = sym if sym in forbidden else sym.lstrip("_")
            if name in forbidden:
                lr.diagnostics.append(Diagnostic(
                    "error", f"使用了禁用符号 {name}（{obj.stem}.c）", f"forbidden:{name}", f"src/{obj.stem}.c"))
    return True


def _cppcheck(ctx: BuildContext, lr: LayerResult) -> bool:
    exe = ctx.toolchain.resolve("cppcheck")
    if exe is None:
        lr.complete = False
        lr.notes.append(_missing("cppcheck", ctx.toolchain))
        return False
    cmd = [exe, "--std=c11", "--enable=warning,performance,portability", "--inline-suppr", "--quiet",
           "--suppress=missingIncludeSystem", "--template={file}:{line}:{severity}:{id}:{message}",
           f"-I{ctx.task.include_dir}", str(ctx.src_dir)]
    proc = run_proc(cmd)
    _add_log(lr, "cppcheck", proc.output)
    for file, line, sev, rule, msg in parse_cppcheck(proc.output):
        severity = sev if sev in ("error", "warning") else "info"
        lr.diagnostics.append(Diagnostic(severity, f"{rule}: {msg}", f"cppcheck:{rule}", ctx.rel(file) or None, line))
    return True


def static_check(ctx: BuildContext) -> LayerResult:
    lr = _new("static_check")
    ran_nm = _forbidden_check(ctx, lr)
    ran_cpp = _cppcheck(ctx, lr)
    if not ran_nm and not ran_cpp:
        lr.status = SKIPPED
        return lr
    lr.status = FAIL if lr.failing_keys() else PASS
    lr.metrics["forbidden"] = sum(1 for d in lr.diagnostics if d.key.startswith("forbidden:"))
    lr.metrics["cppcheck_issues"] = sum(1 for d in lr.diagnostics if d.key.startswith("cppcheck:"))
    return lr


# ---------------------------------------------------------------- L5 / 隐藏测试

def _test_env() -> dict[str, str]:
    env = dict(os.environ)
    # verify_asan_link_order=0：宿主机设置了 LD_PRELOAD 时 ASan 仍能运行
    env.setdefault("ASAN_OPTIONS", "detect_leaks=0:verify_asan_link_order=0")
    env.setdefault("UBSAN_OPTIONS", "print_stacktrace=1")
    return env


def _coverage(ctx: BuildContext, lr: LayerResult, objdir: Path, srcs: list[Path]) -> float | None:
    gcov = ctx.toolchain.resolve("gcov")
    if gcov is None:
        lr.notes.append(_missing("gcov", ctx.toolchain).replace("本项未运行", "未统计覆盖率"))
        return None
    total_lines, covered = 0, 0.0
    for src in srcs:
        proc = run_proc([gcov, "-n", "-o", str(objdir), str(src.resolve())], cwd=objdir)
        parsed = parse_gcov(proc.output, src.name)
        if parsed is None:
            lr.notes.append(f"覆盖率解析失败：{src.name}")
            return None
        pct, n = parsed
        total_lines += n
        covered += pct * n
    return round(covered / total_lines, 2) if total_lines else None


def run_test_suite(ctx: BuildContext, layer: str, tests_dir: Path, *, coverage: bool) -> LayerResult:
    lr = _new(layer)
    cc = ctx.toolchain.resolve("cc")
    if cc is None:
        return _skip(lr, _missing("cc", ctx.toolchain))
    unity = config.unity_dir()
    if not (unity / "unity.c").is_file():
        return _skip(lr, f"找不到 Unity 源码：{unity}")

    programs = sorted(tests_dir.glob("test_*.c")) if tests_dir.is_dir() else []
    support = sorted(p for p in tests_dir.glob("*.c") if not p.name.startswith("test_")) if tests_dir.is_dir() else []
    srcs = ctx.sources()
    if not programs:
        lr.status = FAIL
        lr.diagnostics.append(Diagnostic("error", "没有测试程序（tests/test_*.c）", "no_tests"))
        return lr

    sanitize = config.sanitize_enabled()
    base = TEST_FLAGS + (SANITIZE_FLAGS if sanitize else [])
    if not sanitize:
        lr.notes.append("FF_SANITIZE=0：未启用 ASan/UBSan")
    incs = ctx.include_flags() + ["-I", str(unity), "-I", str(tests_dir.resolve())]
    root = ctx.build_dir / layer
    dirs = {k: root / k for k in ("src", "unity", "support", "tests", "bin")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    def compile_one(src: Path, out_dir: Path, flags: list[str], kind: str) -> Path | None:
        obj = out_dir / f"{src.stem}.o"
        proc = run_proc([cc, *flags, *incs, "-c", str(src.resolve()), "-o", str(obj)], cwd=out_dir)
        _add_log(lr, f"gcc -c {src.name}", proc.output)
        if proc.returncode != 0:
            _compile_failure(lr, src, proc, ctx, kind)
            return None
        return obj

    common_ok = True
    src_flags = base + (COVERAGE_FLAGS if coverage else [])
    src_objs = [compile_one(s, dirs["src"], src_flags, "编译") for s in srcs]
    lib_objs = [compile_one(s, dirs["unity"], base, "编译 Unity")
                for s in (unity / "unity.c", config.unity_support_dir() / "unity_weak_hooks.c")]
    sup_objs = [compile_one(s, dirs["support"], base, "测试支撑文件编译") for s in support]
    if None in src_objs + lib_objs + sup_objs:
        common_ok = False

    totals = {"total": 0, "failed": 0, "ignored": 0, "programs": len(programs)}
    problems = not common_ok
    coverage_valid = True        # 有程序超时或崩溃时 .gcda 不完整
    link_flags = (SANITIZE_FLAGS if sanitize else []) + (COVERAGE_FLAGS if coverage else [])
    libs = [] if os.name == "nt" else ["-lm"]
    timeout = config.test_timeout_s()

    for prog in programs if common_ok else []:
        tobj = compile_one(prog, dirs["tests"], base, "测试编译")
        if tobj is None:
            problems = True
            continue
        exe = dirs["bin"] / f"{prog.stem}{EXE_SUFFIX}"
        objs = [str(tobj), *map(str, sup_objs), *map(str, src_objs), *map(str, lib_objs)]
        proc = run_proc([cc, *link_flags, *objs, "-o", str(exe), *libs], cwd=root)
        _add_log(lr, f"链接 {prog.stem}", proc.output)
        if proc.returncode != 0:
            problems = True
            _compile_failure(lr, prog, proc, ctx, "链接")
            continue

        run = run_proc([str(exe)], cwd=root, timeout=timeout, env=_test_env())
        _add_log(lr, f"运行 {prog.stem}", run.output)
        res = parse_unity(run.output)
        if run.timed_out:
            problems = True
            lr.diagnostics.append(Diagnostic("error", f"{prog.stem}：超时（可能死循环），超过 {timeout:g} 秒",
                                             f"timeout:{prog.stem}", ctx.rel(prog)))
            coverage_valid = False
            totals["total"] += len(res.passed) + len(res.failed)
            totals["failed"] += len(res.failed)
            continue
        for name, file, line, msg in res.failed:
            lr.diagnostics.append(Diagnostic("error", f"{name}:FAIL: {msg}" if msg else f"{name}:FAIL",
                                             f"test:{name}:FAIL", ctx.rel(file), line))
        if res.summary is not None:
            total, failed, ignored = res.summary
        else:
            total, failed, ignored = len(res.passed) + len(res.failed), len(res.failed), len(res.ignored)
        totals["total"] += total
        totals["failed"] += failed
        totals["ignored"] += ignored
        if failed:
            problems = True
        abnormal = (res.summary is None and run.returncode != 0) or \
                   (res.summary is not None and failed == 0 and run.returncode != 0)
        if abnormal:
            problems = True
            coverage_valid = False
            lines = sanitizer_lines(run.output)
            if lines:
                for ln in lines[:3]:
                    lr.diagnostics.append(Diagnostic("error", f"{prog.stem}：{ln}",
                                                     f"crash:{prog.stem}:{sanitizer_kind(ln)}", ctx.rel(prog)))
            else:
                lr.diagnostics.append(Diagnostic("error", f"{prog.stem}：异常退出（返回码 {run.returncode}）",
                                                 f"crash:{prog.stem}:rc", ctx.rel(prog)))
        elif res.summary is None:
            problems = True
            lr.diagnostics.append(Diagnostic("error", f"{prog.stem}：没有 Unity 汇总行（缺少 UNITY_END？）",
                                             f"no_summary:{prog.stem}", ctx.rel(prog)))

    if common_ok and totals["total"] == 0 and not problems:
        problems = True
        lr.diagnostics.append(Diagnostic("error", "测试总数为 0", "no_tests"))

    lr.metrics.update(totals)
    if coverage:
        lr.metrics["coverage_pct"] = None
        if not coverage_valid:
            lr.notes.append("有测试程序超时或异常退出，覆盖率不统计")
        elif common_ok and srcs:
            lr.metrics["coverage_pct"] = _coverage(ctx, lr, dirs["src"], srcs)
    lr.status = FAIL if problems else PASS
    return lr


def unit_tests(ctx: BuildContext) -> LayerResult:
    return run_test_suite(ctx, "unit_tests", ctx.tests_dir, coverage=True)


def hidden_tests(ctx: BuildContext) -> LayerResult:
    return run_test_suite(ctx, "hidden_tests", ctx.task.hidden_tests_dir, coverage=False)


LAYER_FUNCS = {
    "host_compile": host_compile,
    "cross_compile": cross_compile,
    "resources": resources,
    "static_check": static_check,
    "unit_tests": unit_tests,
    "hidden_tests": hidden_tests,
}


def timed(fn, ctx: BuildContext) -> LayerResult:
    t0 = time.perf_counter()
    lr = fn(ctx)
    lr.duration_s = time.perf_counter() - t0
    return lr

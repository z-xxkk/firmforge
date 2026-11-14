"""变异测试（只用标准库）：把判定相关的代码故意改错一处，看测试能不能发现。

每个"变异体"只改一处：比较符号（== 和 !=、< 和 <=……）、and 和 or、去掉 not、True 和 False、
整数加一、+ 和 -、in 和 not in、break 和 continue、return 值换成 None。
改完在临时目录里跑相关的测试：测试失败 = 变异体被"杀死"（好）；测试全过 = "存活"，说明这里缺测试，
或者这处改动其实不影响结果（等价变异体，要人看）。

用法：
    python tests/mutation.py                         # 全部目标
    python tests/mutation.py --only harness          # 只跑某个目标
    python tests/mutation.py --list                  # 只列出变异体，不跑
结果写到 results/mutation/<目标>.txt（存活的变异体逐个列出）。
"""

from __future__ import annotations

import argparse
import ast
import copy
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# 目标：文件、要变异的函数（类名.方法名 或 函数名）、用来判定的测试模块
TARGETS = {
    "harness": ("firmforge/target/harness.py",
                ["parse_board_output", "unescape_unity", "BoardTestResult.ok", "BoardTestResult.verdict",
                 "BoardTestResult.failed"],
                ["test_board_harness", "test_fault_injection", "test_board_tasks", "test_judge", "test_mutation_gaps"]),
    "board": ("firmforge/target/board.py", ["find_port", "BoardRunner.run"],
              ["test_board_harness", "test_fault_injection", "test_judge", "test_mutation_gaps"]),
    "judge": ("firmforge/embedded/board_task.py", ["judge_on_board", "hidden_case_count"],
              ["test_board_tasks", "test_board_eval", "test_fault_injection", "test_judge", "test_mutation_gaps"]),
    "compliance": ("firmforge/grounding/compliance.py", ["*"],
                   ["test_compliance", "test_board_tasks", "test_embedded_flow", "test_mutation_gaps"]),
    "eval_summary": ("firmforge/embedded/board_eval.py",
                     ["summarize", "plan_todo", "missing_scopes", "load_rows", "run_once", "evaluate", "_run_todo",
                      "measured_rules", "_read_cache", "unverified_rules"],
                     ["test_board_eval", "test_fault_injection", "test_mutation_gaps"]),
    # 修正循环的分派（第 122、123 条）：交给谁改、诊断补要、编译报错落在哪
    "repair_route": ("firmforge/embedded/flow.py", ["build_error_files", "EmbeddedFlow._diagnose", "EmbeddedFlow._module"],
                     ["test_repair_sim", "test_build_blame", "test_embedded_flow"]),
    "repair_interp": ("firmforge/embedded/fw_interpreter.py", ["FirmwareInterpreter._repair_loop"],
                      ["test_repair_sim", "test_build_blame", "test_firmware_workflow"]),
    "tool_args": ("firmforge/tools/pipeline.py", ["parse_arguments", "check_arguments", "param_usage"],
                  ["test_tools", "test_mcp"]),
}

CMP_SWAP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
            ast.GtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is}


@dataclass
class Mutant:
    target: str
    n: int
    line: int
    what: str
    source: str = ""


def _functions(tree: ast.Module, names: list[str]) -> list[ast.AST]:
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and ("*" in names or node.name in names):
            out.append(node)
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        "*" in names or f"{node.name}.{sub.name}" in names):
                    out.append(sub)
    return out


def _points(fn: ast.AST) -> list[tuple[ast.AST, str, int]]:
    """函数里所有可变异的位置：(节点, 种类, 序号)。docstring 和日志文字不变异。"""
    pts = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in CMP_SWAP:
                    pts.append((node, "cmp", i))
        elif isinstance(node, ast.BoolOp):
            pts.append((node, "boolop", 0))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            pts.append((node, "not", 0))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            pts.append((node, "bool", 0))
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            pts.append((node, "int", 0))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            if not (isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
                pts.append((node, "arith", 0))
        elif isinstance(node, (ast.Break, ast.Continue)):
            pts.append((node, "loopctl", 0))
        elif isinstance(node, ast.Return) and node.value is not None and not (
                isinstance(node.value, ast.Constant) and node.value.value is None):
            pts.append((node, "return", 0))
    return pts


def _apply(node: ast.AST, kind: str, i: int) -> str:
    """原地改节点，返回说明。"""
    if kind == "cmp":
        old = type(node.ops[i])
        node.ops[i] = CMP_SWAP[old]()
        return f"比较 {old.__name__} → {CMP_SWAP[old].__name__}"
    if kind == "boolop":
        old = type(node.op)
        node.op = ast.Or() if old is ast.And else ast.And()
        return f"{old.__name__} → {type(node.op).__name__}"
    if kind == "not":
        return "去掉 not"
    if kind == "bool":
        node.value = not node.value
        return f"{not node.value} → {node.value}"
    if kind == "int":
        node.value = node.value + 1
        return f"整数 {node.value - 1} → {node.value}"
    if kind == "arith":
        old = type(node.op)
        node.op = ast.Sub() if old is ast.Add else ast.Add()
        return f"{old.__name__} → {type(node.op).__name__}"
    if kind == "loopctl":
        return f"{type(node).__name__} → {'Continue' if isinstance(node, ast.Break) else 'Break'}"
    if kind == "return":
        node.value = ast.Constant(None)
        return "return 值 → None"
    raise ValueError(kind)


class _Replace(ast.NodeTransformer):
    """"去掉 not" 和 break/continue 互换要替换节点本身。"""

    def __init__(self, target: ast.AST, kind: str):
        self.target, self.kind = target, kind

    def visit(self, node):
        if node is self.target:
            if self.kind == "not":
                return node.operand
            if self.kind == "loopctl":
                return ast.Continue() if isinstance(node, ast.Break) else ast.Break()
        return super().visit(node)


def make_mutants(name: str) -> tuple[list[Mutant], str]:
    rel, funcs, _tests = TARGETS[name]
    text = (SRC / rel).read_text(encoding="utf-8")
    base = ast.parse(text)
    count = sum(len(_points(f)) for f in _functions(base, funcs))
    out = []
    for n in range(count):
        tree = copy.deepcopy(base)
        pts = [p for f in _functions(tree, funcs) for p in _points(f)]
        node, kind, i = pts[n]
        line = getattr(node, "lineno", 0)
        what = _apply(node, kind, i)
        if kind in ("not", "loopctl"):
            tree = _Replace(node, kind).visit(tree)
        ast.fix_missing_locations(tree)
        out.append(Mutant(name, n, line, what, ast.unparse(tree)))
    return out, ast.unparse(base)


MEM_LIMIT_MB = 1536     # 每个测试进程最多 1.5 GB：有的变异体会让列表无限增长，不能把整台电脑的内存吃光

# 测试进程里的看门狗线程：只看 Python 进程自己的内存，超了就退出（算变异体被杀死）。
# 不用 setrlimit：它会被子进程继承，带内存检查（AddressSanitizer）编译的测试程序一启动就要预留大量虚拟内存，会被误伤
_WATCHDOG = (
    "import os, threading, time\n"
    "def _watch():\n"
    "    page = os.sysconf('SC_PAGE_SIZE')\n"
    "    while True:\n"
    "        with open('/proc/self/statm') as f:\n"
    "            rss = int(f.read().split()[1]) * page\n"
    "        if rss > {limit} * 1024 * 1024:\n"
    "            os._exit(3)\n"
    "        time.sleep(0.2)\n"
    "threading.Thread(target=_watch, daemon=True).start()\n"
)


def run_tests(src_dir: Path, tests: list[str], timeout: float) -> tuple[bool, str]:
    """在 src_dir（src/ 的副本）上跑测试。返回 (测试全过, 输出尾巴)。"""
    code = (_WATCHDOG.format(limit=MEM_LIMIT_MB) + "import sys, unittest; sys.path.insert(0, sys.argv[1]); sys.path.append(sys.argv[2]); "
            "sys.path.insert(1, sys.argv[3]); "
            "s = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[4:]); "
            "r = unittest.TextTestRunner(verbosity=0, stream=open('/dev/null', 'w')).run(s); "
            "sys.exit(0 if r.wasSuccessful() else 1)")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1",
           "FF_HOME": str(ROOT)}                       # 副本里的包要找项目目录（third_party/ 等）
    try:
        r = subprocess.run([sys.executable, "-c", code, str(src_dir), str(SRC), str(ROOT / "tests"), *tests],
                           cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env)
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]
    except subprocess.TimeoutExpired:
        return False, "超时（算被杀死：变异体让测试卡住了）"


def run_target(name: str, jobs: int, timeout: float, out_dir: Path) -> tuple[int, list[Mutant]]:
    rel, _funcs, tests = TARGETS[name]
    mutants, base_src = make_mutants(name)
    work = Path(tempfile.mkdtemp(prefix=f"ff-mut-{name}-"))
    try:
        base_dir = work / "base"
        shutil.copytree(SRC, base_dir, ignore=shutil.ignore_patterns("__pycache__"))
        (base_dir / rel).write_text(base_src, encoding="utf-8")       # 先确认没改动时测试是全过的
        ok, tail = run_tests(base_dir, tests, timeout)
        if not ok:
            raise SystemExit(f"{name}：没改动时测试就不通过，先修好：\n{tail}")

        def one(m: Mutant) -> tuple[Mutant, bool]:
            d = work / f"m{m.n}"
            shutil.copytree(base_dir, d)
            (d / rel).write_text(m.source, encoding="utf-8")
            survived, _ = run_tests(d, tests, timeout)
            shutil.rmtree(d, ignore_errors=True)
            return m, survived

        survivors = []
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for m, survived in ex.map(one, mutants):
                if survived:
                    survivors.append(m)
        survivors.sort(key=lambda m: m.line)
        lines = [f"# {name}（{rel}）：{len(mutants)} 个变异体，存活 {len(survivors)} 个", ""]
        src_lines = (SRC / rel).read_text(encoding="utf-8").splitlines()
        for m in survivors:
            orig = src_lines[m.line - 1].strip() if 0 < m.line <= len(src_lines) else ""
            lines.append(f"第 {m.line} 行  {m.what}\n    {orig}")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{name}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return len(mutants), survivors
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="变异测试：故意把判定代码改错一处，看测试能不能发现")
    ap.add_argument("--only", default="", help=f"只跑这些目标（逗号分隔）：{', '.join(TARGETS)}")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--out", default=str(ROOT / "results" / "mutation"))
    ap.add_argument("--list", action="store_true", help="只列出变异体，不跑")
    args = ap.parse_args(argv)
    names = [n.strip() for n in args.only.split(",") if n.strip()] or list(TARGETS)
    total_m = total_s = 0
    for name in names:
        if args.list:
            ms, _ = make_mutants(name)
            print(f"{name}：{len(ms)} 个变异体")
            for m in ms:
                print(f"  第 {m.line} 行 {m.what}")
            continue
        t0 = time.time()
        n, surv = run_target(name, args.jobs, args.timeout, Path(args.out))
        total_m, total_s = total_m + n, total_s + len(surv)
        score = (n - len(surv)) / n if n else 1.0
        print(f"{name}：{n} 个变异体，杀死 {n - len(surv)}，存活 {len(surv)}（杀死率 {score:.0%}），"
              f"{time.time() - t0:.0f} 秒")
        for m in surv:
            print(f"    存活：第 {m.line} 行 {m.what}")
    if not args.list and total_m:
        print(f"合计：{total_m} 个变异体，存活 {total_s}，杀死率 {(total_m - total_s) / total_m:.0%}；"
              f"明细在 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

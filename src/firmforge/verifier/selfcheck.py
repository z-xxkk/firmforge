"""任务自检 check_task()：检验隐藏测试本身（"谁来验证验证者"）。

1. 参考实现跑 L1–L4 和隐藏测试，期望没有任何 fail；
2. 每个变异体替换参考实现中的对应文件后再跑一遍，期望第一个失败层正好是 expect_fail。
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Toolchain
from ..tasks import Mutant, Task
from .report import FAIL, SKIPPED, Report
from .runner import LAYER_ORDER, verify

SELFCHECK_LAYERS = ["host_compile", "cross_compile", "resources", "static_check"]

# 变异体检查结果
MUTANT_OK = "ok"                  # 在预期层失败
MUTANT_WRONG = "wrong"            # 在别的层失败，或者没有失败
MUTANT_UNVERIFIABLE = "unverifiable"  # 预期层因工具缺失被跳过，无法确认


@dataclass
class MutantResult:
    mutant: Mutant
    report: Report
    status: str

    @property
    def actual(self) -> str | None:
        return self.report.failed_layer


@dataclass
class SelfCheckResult:
    task: Task
    reference: Report
    mutants: list[MutantResult] = field(default_factory=list)

    @property
    def reference_ok(self) -> bool:
        return self.reference.verdict != FAIL

    @property
    def measured(self) -> dict:
        lr = self.reference.layer("resources")
        if lr is None or lr.status == SKIPPED:
            return {"flash": None, "ram": None, "stack": None}
        m = lr.metrics
        return {"flash": m.get("flash"), "ram": m.get("ram"), "stack": m.get("max_stack")}

    @property
    def verdict(self) -> str:
        """pass / fail / incomplete。"""
        if not self.reference_ok or any(m.status == MUTANT_WRONG for m in self.mutants):
            return "fail"
        if self.reference.verdict != "pass" or any(m.status == MUTANT_UNVERIFIABLE for m in self.mutants):
            return "incomplete"
        return "pass"


def _prepare(dst: Path, task: Task, mutant: Mutant | None = None) -> None:
    src = dst / "src"
    src.mkdir(parents=True)
    for f in sorted(task.reference_dir.iterdir()):
        if f.is_file() and f.suffix in (".c", ".h"):
            shutil.copy2(f, src / f.name)
    if mutant is not None:
        target = src / mutant.replaces
        if not target.exists():
            raise FileNotFoundError(f"变异体 {mutant.file} 要替换的 {mutant.replaces} 不在 reference/ 中")
        shutil.copy2(task.root / mutant.file, target)


def _classify(report: Report, expect: str) -> str:
    actual = report.failed_layer
    if actual == expect:
        return MUTANT_OK
    expected_lr = report.layer(expect)
    order = LAYER_ORDER.index
    skipped_by_tool = (expected_lr is not None and expected_lr.status == SKIPPED
                       and "前一层失败，未运行" not in expected_lr.notes)
    if skipped_by_tool and (actual is None or order(actual) > order(expect)):
        return MUTANT_UNVERIFIABLE
    return MUTANT_WRONG


def check_task(task: Task, toolchain: Toolchain | None = None) -> SelfCheckResult:
    with tempfile.TemporaryDirectory(prefix=f"ff_selfcheck_{task.id}_") as tmp:
        tmp_path = Path(tmp)
        ref_dir = tmp_path / "reference"
        _prepare(ref_dir, task)
        ref_report = verify(ref_dir, task, layers=SELFCHECK_LAYERS, include_hidden=True,
                            stop_on_fail=False, toolchain=toolchain)
        result = SelfCheckResult(task, ref_report)
        for i, mutant in enumerate(task.mutants):
            mdir = tmp_path / f"mutant_{i}"
            _prepare(mdir, task, mutant)
            rep = verify(mdir, task, layers=SELFCHECK_LAYERS, include_hidden=True,
                         stop_on_fail=True, toolchain=toolchain)
            result.mutants.append(MutantResult(mutant, rep, _classify(rep, mutant.expect_fail)))
    return result

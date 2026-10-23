"""判定器入口：verify()。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from ..config import Toolchain
from ..tasks import Task
from .layers import LAYER_FUNCS, TITLES, BuildContext, timed
from .report import FAIL, SKIPPED, Diagnostic, LayerResult, Report
from .signature import layer_signature

LAYER_ORDER = ["host_compile", "cross_compile", "resources", "static_check", "unit_tests", "hidden_tests"]
OBJECTIVE_LAYERS = LAYER_ORDER[:5]
BUILD_DIR_NAME = ".ff_build"


def verify(workdir: Path, task: Task, *, layers: list[str] | None = None,
           include_hidden: bool = False, stop_on_fail: bool = True,
           toolchain: Toolchain | None = None) -> Report:
    """对工作目录逐层判定。

    layers 为 None 时运行 L1–L5；include_hidden=True 时再追加隐藏测试层。
    """
    workdir = Path(workdir).resolve()
    toolchain = toolchain or Toolchain.from_env()
    selected = list(layers) if layers is not None else list(OBJECTIVE_LAYERS)
    unknown = [x for x in selected if x not in LAYER_FUNCS]
    if unknown:
        raise ValueError(f"未知的层：{', '.join(unknown)}")
    if "hidden_tests" in selected and not include_hidden:
        raise ValueError("运行隐藏测试层必须显式传 include_hidden=True")
    if include_hidden and "hidden_tests" not in selected:
        selected.append("hidden_tests")

    build_dir = workdir / BUILD_DIR_NAME
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)
    ctx = BuildContext(workdir, task, toolchain, build_dir)

    t0 = time.perf_counter()
    report = Report()
    failed = False
    for name in LAYER_ORDER:
        if name not in selected:
            continue
        if failed and stop_on_fail:
            report.layers.append(LayerResult(name, TITLES[name], SKIPPED, complete=False,
                                             notes=["前一层失败，未运行"]))
            continue
        try:
            lr = timed(LAYER_FUNCS[name], ctx)
        except Exception as exc:  # 判定器自身的错误，不能当作通过
            lr = LayerResult(name, TITLES[name], FAIL, diagnostics=[
                Diagnostic("error", f"判定器内部错误：{type(exc).__name__}: {exc}", "internal_error")])
        if lr.status == FAIL:
            failed = True
            lr.signature = layer_signature(name, lr.failing_keys())
        report.layers.append(lr)
    report.duration_s = time.perf_counter() - t0
    return report

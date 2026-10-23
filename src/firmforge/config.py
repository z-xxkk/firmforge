"""外部工具与运行环境配置。

所有外部命令都从 Toolchain 取得，每个命令都可以用环境变量覆盖，
例如 FF_CC=gcc-13、FF_CPPCHECK=/opt/cppcheck/bin/cppcheck。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path

# 字段名 -> (环境变量, 默认命令, 中文说明)
TOOL_SPECS: dict[str, tuple[str, str, str]] = {
    "cc": ("FF_CC", "gcc", "主机 C 编译器（L1、L5、隐藏测试）"),
    "nm": ("FF_NM", "nm", "主机符号表工具（L4 备用）"),
    "gcov": ("FF_GCOV", "gcov", "覆盖率统计（L5）"),
    "cross_cc": ("FF_CROSS_CC", "arm-none-eabi-gcc", "交叉编译器（L2）"),
    "cross_size": ("FF_CROSS_SIZE", "arm-none-eabi-size", "目标文件尺寸统计（L3）"),
    "cross_nm": ("FF_CROSS_NM", "arm-none-eabi-nm", "交叉符号表工具（L4）"),
    "cppcheck": ("FF_CPPCHECK", "cppcheck", "静态分析（L4）"),
}


@dataclass(frozen=True)
class Toolchain:
    cc: str = "gcc"
    nm: str = "nm"
    gcov: str = "gcov"
    cross_cc: str = "arm-none-eabi-gcc"
    cross_size: str = "arm-none-eabi-size"
    cross_nm: str = "arm-none-eabi-nm"
    cppcheck: str = "cppcheck"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Toolchain":
        env = os.environ if env is None else env
        values = {}
        for name, (var, default, _desc) in TOOL_SPECS.items():
            values[name] = env.get(var) or default
        return cls(**values)

    def resolve(self, name: str) -> str | None:
        """返回工具的完整路径；找不到时返回 None。"""
        return shutil.which(getattr(self, name))

    def available(self, name: str) -> bool:
        return self.resolve(name) is not None

    def names(self) -> list[str]:
        return [f.name for f in fields(self)]


def tool_version(path: str) -> str:
    """取工具 --version 输出的第一行，失败时返回空字符串。"""
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (proc.stdout or proc.stderr).strip().splitlines()
    return out[0] if out else ""


def project_root() -> Path:
    """FirmForge 安装目录（含 third_party/）。可用 FF_HOME 覆盖。"""
    env = os.environ.get("FF_HOME")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


def unity_dir() -> Path:
    return project_root() / "third_party" / "unity"


def unity_support_dir() -> Path:
    return project_root() / "third_party" / "unity_support"


def test_timeout_s() -> float:
    """单个测试程序的超时时间，FF_TEST_TIMEOUT 覆盖，默认 10 秒。"""
    try:
        return float(os.environ.get("FF_TEST_TIMEOUT", "10"))
    except ValueError:
        return 10.0


def sanitize_enabled() -> bool:
    """FF_SANITIZE=0 时关闭 ASan/UBSan（Windows 本机临时使用）。"""
    return os.environ.get("FF_SANITIZE", "1") != "0"

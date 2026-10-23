"""测试公用：把 src/ 加入 sys.path，并提供常用路径和跳过条件。"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from firmforge.config import Toolchain  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
TASKS = ROOT / "tasks"
TC = Toolchain.from_env()
HAS_CC = TC.available("cc")
HAS_CROSS = all(TC.available(n) for n in ("cross_cc", "cross_size", "cross_nm"))
HAS_CPPCHECK = TC.available("cppcheck")
HAS_HOST_SIZE = shutil.which("size") is not None


def copy_fixture(name: str, dst: Path) -> Path:
    """把样例复制到临时目录，避免 .ff_build 写进仓库。"""
    target = dst / name
    shutil.copytree(FIXTURES / name, target)
    return target

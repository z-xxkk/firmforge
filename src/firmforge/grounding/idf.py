"""在指定版本的 ESP-IDF 源码树里核对接口（规则判定器第 3 步；合规检查也用它）。

不靠模型回忆接口：直接在该版本的头文件里找函数声明（DECISIONS 第 76 条）。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


class IdfTree:
    def __init__(self, root: Path):
        self.root = Path(root)
        if not (self.root / "components").is_dir():
            raise FileNotFoundError(f"{self.root} 不是 ESP-IDF 源码树（缺 components/）")

    def version(self) -> str:
        """读 tools/cmake/version.cmake，形如 v6.1。"""
        f = self.root / "tools" / "cmake" / "version.cmake"
        if not f.is_file():
            return ""
        nums = dict(re.findall(r"set\(IDF_VERSION_(MAJOR|MINOR|PATCH)\s+(\d+)\)", f.read_text(encoding="utf-8")))
        if not nums:
            return ""
        v = f"v{nums.get('MAJOR', '0')}.{nums.get('MINOR', '0')}"
        return v if nums.get("PATCH", "0") == "0" else f"{v}.{nums['PATCH']}"

    @lru_cache(maxsize=256)
    def find_header(self, include: str) -> Path | None:
        """按 #include 写法（如 driver/i2c_master.h）找到头文件；找不到返回 None。"""
        for p in self.root.glob(f"components/**/include/{include}"):
            return p
        return None

    def declares(self, include: str, symbol: str) -> tuple[bool, str]:
        """头文件里是否声明了这个函数、类型或宏。返回 (是否找到, 说明)。"""
        h = self.find_header(include)
        if h is None:
            return False, f"ESP-IDF {self.version()} 里没有头文件 {include}"
        text = h.read_text(encoding="utf-8", errors="replace")
        sym = re.escape(symbol)
        patterns = (rf"\b{sym}\s*\(", rf"#define\s+{sym}\b", rf"\}}\s*{sym}\s*;", rf"\btypedef\b[^;]*\b{sym}\s*;",
                    rf"\b{sym}\s*[,=]")
        if any(re.search(p, text) for p in patterns):
            return True, f"{h.relative_to(self.root).as_posix()} 声明了 {symbol}"
        return False, f"{include} 里找不到 {symbol}"

    def symbol_anywhere(self, symbol: str) -> list[Path]:
        """整个 components/ 的公开头文件里出现该符号的位置（用于判断旧接口是否已删除）。"""
        sym = re.compile(rf"\b{re.escape(symbol)}\s*\(")
        return [p for p in self.root.glob("components/**/include/**/*.h")
                if sym.search(p.read_text(encoding="utf-8", errors="replace"))]

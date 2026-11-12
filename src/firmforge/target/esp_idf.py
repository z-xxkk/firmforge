"""从 Python 调用 ESP-IDF：取环境、生成工程、编译（DECISIONS 第 66 条第 ⑥ 步"编译"）。

不经过 shell：用 `idf_tools.py export --format key-value` 取得 ESP-IDF 需要的环境变量，
再用 ESP-IDF 自己的 Python 环境运行 `idf.py`，Linux 和 Windows 一样（CLAUDE.md 硬性规则 3）。

ESP-IDF 位置：环境变量 FF_IDF_PATH，其次 IDF_PATH，最后默认 ~/esp/esp-idf-v6.1。
找不到或环境取不到时抛 IdfUnavailable，调用方把编译层记为 skipped 并写明原因，绝不判通过（硬性规则 4）。

同一个工程目录反复使用：第一次编译要把整个 ESP-IDF 编一遍（几分钟），之后只编改动的文件。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..grounding.profile import HardwareProfile, param_macro

DEFAULT_IDF = Path.home() / "esp" / "esp-idf-v6.1"
BUILD_TIMEOUT_S = 900
# 硬件层代码常用的驱动组件（ESP-IDF 5.3 起驱动拆成独立组件）
DEFAULT_REQUIRES = ("esp_driver_gpio", "esp_driver_i2c", "esp_driver_uart", "esp_driver_ledc", "esp_timer")
# 每种芯片的默认配置：S3 的控制台走芯片自带的 USB 串口；经典 ESP32 走 UART0（接板上的 USB 转串口芯片）
SDKCONFIG_DEFAULTS = {
    "esp32s3": "# FirmForge 生成：ESP32-S3（4 MB Flash，控制台走芯片自带的 USB 串口）\n"
               "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\nCONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y\nCONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y\n",
    "esp32c3": "# FirmForge 生成：ESP32-C3（4 MB Flash，控制台走芯片自带的 USB 串口）\n"
               "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\nCONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y\nCONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y\n",
    "esp32": "# FirmForge 生成：经典 ESP32（4 MB Flash，控制台走 UART0）\n"
             "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\nCONFIG_ESP_CONSOLE_UART_DEFAULT=y\nCONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT=y\n",
}


def sdkconfig_for(target: str, profile: HardwareProfile | None = None) -> str:
    """原来支持的三种芯片用固定配置（评测编译结果不变）；其他芯片按 ESP-IDF 的 soc_caps.h 生成（DECISIONS 第 114 条）。"""
    if target in SDKCONFIG_DEFAULTS:
        return SDKCONFIG_DEFAULTS[target]
    from .soc_caps import load_caps, sdkconfig_defaults
    caps = load_caps(target)
    if caps is None:
        raise ValueError(f"不支持的芯片 {target}：ESP-IDF 里没有它的 soc_caps.h（{idf_path()}）")
    flash = getattr(profile, "flash_mb", None) if profile is not None else None
    return sdkconfig_defaults(caps, flash.value if hasattr(flash, "value") else flash)


_GCC_DIAG = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<kind>error|warning|fatal error):\s*(?P<msg>.+)$",
                       re.M)


class IdfUnavailable(RuntimeError):
    """ESP-IDF 不可用（没装、版本不对、环境取不到）。"""


def idf_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FF_IDF_PATH") or env.get("IDF_PATH") or DEFAULT_IDF).expanduser()


@dataclass(frozen=True)
class IdfEnv:
    root: Path
    env: dict[str, str]
    python: str
    version: str

    @classmethod
    def load(cls, root: Path | None = None, *, want_version: str = "") -> "IdfEnv":
        root = Path(root) if root else idf_path()
        tools = root / "tools" / "idf_tools.py"
        if not tools.is_file():
            raise IdfUnavailable(f"找不到 ESP-IDF：{root}（可用环境变量 FF_IDF_PATH 指定）")
        base = {k: v for k, v in os.environ.items()}
        base["IDF_PATH"] = str(root)
        try:
            out = subprocess.run([sys.executable, str(tools), "export", "--format", "key-value"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=120, env=base)
        except (OSError, subprocess.SubprocessError) as exc:
            raise IdfUnavailable(f"取 ESP-IDF 环境失败：{exc}") from exc
        if out.returncode != 0:
            raise IdfUnavailable("取 ESP-IDF 环境失败（工具链可能没装全，运行 install.sh）："
                                 + (out.stderr or out.stdout).strip()[-400:])
        env = dict(base)
        for line in out.stdout.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            # PATH 里带着字面的 $PATH / %PATH%，换成原来的值
            env[k] = v.replace("$PATH", base.get("PATH", "")).replace("%PATH%", base.get("PATH", ""))
        venv = env.get("IDF_PYTHON_ENV_PATH", "")
        py = Path(venv) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not py.is_file():
            raise IdfUnavailable(f"ESP-IDF 的 Python 环境不存在：{py}")
        version = "v" + env.get("ESP_IDF_VERSION", "") if env.get("ESP_IDF_VERSION") else ""
        if want_version and version and version != want_version:
            raise IdfUnavailable(f"ESP-IDF 版本是 {version}，硬件档案要求 {want_version}")
        return cls(root, env, str(py), version)

    def idf(self, args: list[str], cwd: Path, timeout: float = BUILD_TIMEOUT_S) -> subprocess.CompletedProcess:
        return subprocess.run([self.python, str(self.root / "tools" / "idf.py"), *args], cwd=str(cwd),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=self.env)


# ---------------------------------------------------------------- 工程

def hw_config_header(profile: HardwareProfile) -> str:
    """把硬件档案写成 hw_config.h：代码只能用这里的宏，不许直接写 GPIO 数字（合规检查据此判定）。"""
    lines = ["/* FirmForge 按硬件档案生成，不要手改。代码里的引脚和器件地址一律用这里的宏。 */",
             "#pragma once", ""]
    for sig, fact in sorted(profile.pins.items()):
        name = "FF_PIN_" + re.sub(r"[^A-Za-z0-9]", "_", sig).upper()
        lines.append(f"#define {name} {fact.value}")
    lines.append("")
    for d in profile.devices:
        if d.address is not None:
            lines.append(f"#define FF_{d.name.upper()}_ADDR 0x{d.address:02X}  /* {d.part} */")
        for k, f in sorted(d.params.items()):
            v = f.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue                                   # 只有数值能写成宏
            text = f"0x{v:02X}" if isinstance(v, int) else f"{v!r}f"
            note = f.note[:120].replace("*/", "")
            lines.append(f"#define {param_macro(d.name, k)} {text}  /* {f.origin}：{note} */")
    return "\n".join(lines) + "\n"


@dataclass
class Diagnostic:
    file: str
    line: int
    kind: str
    message: str

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.kind}: {self.message}"


@dataclass
class BuildResult:
    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    log_tail: str = ""
    elf: Path | None = None
    skipped: str = ""               # 不为空表示没编译成（ESP-IDF 不可用），原因写在这里

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.kind != "warning"]


def parse_diagnostics(log: str, project: Path) -> list[Diagnostic]:
    """只保留工程 main/ 里的报错和警告（ESP-IDF 自己的源码不算 Agent 的错）。"""
    out, seen = [], set()
    main_dir = (Path(project) / "main").resolve().as_posix()
    for m in _GCC_DIAG.finditer(log):
        f = m.group("file").strip()
        try:
            full = Path(f).resolve().as_posix()
        except OSError:
            full = f
        if not (full.startswith(main_dir) or f.startswith("main/") or f.startswith("../main/")):
            continue
        rel = "main/" + full[len(main_dir):].lstrip("/") if full.startswith(main_dir) else f.removeprefix("../")
        key = (rel, int(m.group("line")), m.group("msg"))
        if key in seen:
            continue
        seen.add(key)
        out.append(Diagnostic(rel, int(m.group("line")), m.group("kind"), m.group("msg").strip()))
    return out


class IdfProject:
    """一个可反复编译的 ESP-IDF 工程：Agent 的代码放进 main/，硬件档案生成 main/include/hw_config.h。"""

    def __init__(self, root: Path, idf: IdfEnv, *, target: str = "esp32s3", name: str = "ff_app",
                 requires: tuple[str, ...] = DEFAULT_REQUIRES, whole_archive: bool = False):
        self.root, self.idf, self.target, self.name = Path(root), idf, target, name
        self.requires = tuple(dict.fromkeys(requires))
        # 测试程序必须保留全部目标文件：TEST_CASE 靠构造函数登记，没人引用的文件会被链接器丢掉，结果一个用例都不跑
        self.whole_archive = whole_archive

    def write(self, profile: HardwareProfile, files: dict[str, str]) -> None:
        """files：main/ 下的相对路径 → 内容，例如 {"app_main.c": ..., "mpu6050.c": ..., "include/mpu6050.h": ...}。
        上一次的源文件先清掉，build/ 保留（增量编译）。"""
        main = self.root / "main"
        if main.is_dir():
            for p in sorted(main.rglob("*"), reverse=True):
                if p.is_file():
                    p.unlink()
        (main / "include").mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)
        top = (f"cmake_minimum_required(VERSION 3.22)\ninclude($ENV{{IDF_PATH}}/tools/cmake/project.cmake)\n"
               f"project({self.name})\n")
        _write_if_changed(self.root / "CMakeLists.txt", top)
        _write_if_changed(self.root / "sdkconfig.defaults", sdkconfig_for(self.target, profile))
        for rel, content in files.items():
            rel = rel.replace("\\", "/").lstrip("/")
            if rel.startswith("..") or "/../" in rel:
                raise ValueError(f"文件路径不能跳出 main/：{rel}")
            p = main / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        (main / "include" / "hw_config.h").write_text(hw_config_header(profile), encoding="utf-8")
        srcs = sorted(p.relative_to(main).as_posix() for p in main.rglob("*.c"))
        quoted = " ".join(f'"{s}"' for s in srcs)
        cmake = (f"idf_component_register(SRCS {quoted}\n"
                 f"                       INCLUDE_DIRS \".\" \"include\"\n"
                 f"                       PRIV_REQUIRES {' '.join(self.requires)}"
                 + ("\n                       WHOLE_ARCHIVE" if self.whole_archive else "") + ")\n")
        (main / "CMakeLists.txt").write_text(cmake, encoding="utf-8")

    def build(self, timeout: float = BUILD_TIMEOUT_S) -> BuildResult:
        if not (self.root / "sdkconfig").is_file():
            r = self.idf.idf(["set-target", self.target], self.root, timeout)
            if r.returncode != 0:
                return BuildResult(False, log_tail=(r.stdout + r.stderr)[-3000:])
        try:
            r = self.idf.idf(["build"], self.root, timeout)
        except subprocess.TimeoutExpired:
            return BuildResult(False, log_tail=f"编译超过 {timeout:.0f} 秒")
        log = r.stdout + "\n" + r.stderr
        diags = parse_diagnostics(log, self.root)
        elf = self.root / "build" / f"{self.name}.elf"
        return BuildResult(r.returncode == 0, diags, log[-3000:], elf if elf.is_file() else None)


def _write_if_changed(path: Path, text: str) -> None:
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")

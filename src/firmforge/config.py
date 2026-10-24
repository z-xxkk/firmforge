"""外部工具与运行环境配置。

所有外部命令都从 Toolchain 取得，每个命令都可以用环境变量覆盖，
例如 FF_CC=gcc-13、FF_CPPCHECK=/opt/cppcheck/bin/cppcheck。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field, fields
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


def prompts_dir() -> Path:
    """提示词模板目录：优先当前目录下的 prompts/，否则用安装目录。"""
    cwd = Path.cwd() / "prompts"
    return cwd if cwd.is_dir() else project_root() / "prompts"


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


# ---------------------------------------------------------------- .env 与 firmforge.toml

def load_dotenv(path: Path | str, env: dict[str, str] | None = None) -> list[str]:
    """读取 .env（KEY=VALUE 每行一个）。已存在的环境变量不覆盖。返回新设置的变量名（不返回值）。"""
    env = os.environ if env is None else env
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and value and key not in env:
            env[key] = value
            loaded.append(key)
    return loaded


def load_default_dotenv() -> list[str]:
    """依次尝试当前目录和安装目录下的 .env。"""
    names: list[str] = []
    for base in (Path.cwd(), project_root()):
        names += load_dotenv(base / ".env")
    return names


class SettingsError(ValueError):
    """firmforge.toml 内容不合法或缺少必要配置。"""


@dataclass(frozen=True)
class LLMSettings:
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url_env: str = "DEEPSEEK_BASE_URL"
    timeout_s: float = 120
    max_retries: int = 3


@dataclass(frozen=True)
class PeakSchedule:
    """高峰时段：工作日的若干时间窗（本地时间 = UTC + utc_offset_hours），周末全天为空闲。"""
    utc_offset_hours: float = 8
    windows: tuple[tuple[int, int], ...] = ()      # (起始分钟, 结束分钟)，左闭右开

    @classmethod
    def parse(cls, utc_offset_hours: float, peak_hours: list[str]) -> "PeakSchedule":
        windows = []
        for item in peak_hours:
            try:
                start, end = (x.strip() for x in item.split("-"))
                sh, sm = (int(v) for v in start.split(":"))
                eh, em = (int(v) for v in end.split(":"))
            except ValueError as exc:
                raise SettingsError(f"peak_hours 格式应为 \"HH:MM-HH:MM\"，收到 {item!r}") from exc
            windows.append((sh * 60 + sm, eh * 60 + em))
        return cls(float(utc_offset_hours), tuple(windows))

    def is_peak(self, ts: float) -> bool:
        import datetime as _dt

        tz = _dt.timezone(_dt.timedelta(hours=self.utc_offset_hours))
        t = _dt.datetime.fromtimestamp(ts, tz)
        if t.weekday() >= 5:
            return False
        minute = t.hour * 60 + t.minute
        return any(start <= minute < end for start, end in self.windows)


@dataclass(frozen=True)
class ModelTier:
    name: str = ""
    price_in_per_mtok: float = 0.0          # 高峰价（没有分时价时就是唯一价格）
    price_out_per_mtok: float = 0.0
    price_in_offpeak_per_mtok: float | None = None
    price_out_offpeak_per_mtok: float | None = None
    thinking: bool | None = None            # None 表示不传，沿用服务端默认
    max_tokens: int | None = None

    def cost(self, prompt_tokens: int, completion_tokens: int, *, ts: float | None = None,
             schedule: PeakSchedule | None = None) -> float:
        p_in, p_out = self.price_in_per_mtok, self.price_out_per_mtok
        offpeak = (self.price_in_offpeak_per_mtok is not None and self.price_out_offpeak_per_mtok is not None)
        if offpeak and ts is not None and schedule is not None and not schedule.is_peak(ts):
            p_in, p_out = self.price_in_offpeak_per_mtok, self.price_out_offpeak_per_mtok
        return (prompt_tokens * p_in + completion_tokens * p_out) / 1e6

    def extra_body(self) -> dict:
        """随请求发送的模型专属参数。"""
        return {} if self.thinking is None else {"thinking": {"type": "enabled" if self.thinking else "disabled"}}


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings = field(default_factory=LLMSettings)
    models: dict[str, ModelTier] = field(default_factory=dict)   # 档位名 -> ModelTier
    concurrency: dict = field(default_factory=dict)
    loop: dict = field(default_factory=dict)
    scheduler: dict = field(default_factory=dict)
    pricing: PeakSchedule | None = None
    source: Path | None = None

    def tier(self, name: str) -> ModelTier:
        tier = self.models.get(name)
        if tier is None:
            raise SettingsError(f"firmforge.toml 里没有 [models.{name}]")
        if not tier.name:
            raise SettingsError(f"请先在 firmforge.toml 的 [models.{name}] 里填写 name（可用 `firmforge models` 查看型号）")
        return tier


DEFAULT_BASE_URL = "https://api.deepseek.com"


def settings_path() -> Path:
    cwd = Path.cwd() / "firmforge.toml"
    return cwd if cwd.is_file() else project_root() / "firmforge.toml"


def load_settings(path: Path | str | None = None) -> Settings:
    import tomllib

    path = Path(path) if path else settings_path()
    if not path.is_file():
        raise SettingsError(f"找不到配置文件 {path}")
    with path.open("rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(f"{path} 解析失败：{exc}") from exc
    llm = data.get("llm", {})
    def opt_float(m: dict, key: str) -> float | None:
        return None if m.get(key) is None else float(m[key])

    models = {}
    for name, m in data.get("models", {}).items():
        thinking = m.get("thinking")
        if thinking is not None and not isinstance(thinking, bool):
            raise SettingsError(f"[models.{name}] thinking 必须是 true 或 false")
        models[name] = ModelTier(
            name=str(m.get("name", "")), price_in_per_mtok=float(m.get("price_in_per_mtok", 0.0)),
            price_out_per_mtok=float(m.get("price_out_per_mtok", 0.0)),
            price_in_offpeak_per_mtok=opt_float(m, "price_in_offpeak_per_mtok"),
            price_out_offpeak_per_mtok=opt_float(m, "price_out_offpeak_per_mtok"),
            thinking=thinking, max_tokens=int(m["max_tokens"]) if m.get("max_tokens") else None)
    pr = data.get("pricing")
    pricing = PeakSchedule.parse(pr.get("utc_offset_hours", 8), pr.get("peak_hours", [])) if pr else None
    return Settings(
        llm=LLMSettings(llm.get("api_key_env", "DEEPSEEK_API_KEY"), llm.get("base_url_env", "DEEPSEEK_BASE_URL"),
                        float(llm.get("timeout_s", 120)), int(llm.get("max_retries", 3))),
        models=models,
        concurrency=dict(data.get("concurrency", {})),
        loop=dict(data.get("loop", {})),
        scheduler=dict(data.get("scheduler", {})),
        pricing=pricing,
        source=path,
    )

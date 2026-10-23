"""评测配置（ARCHITECTURE §10）。阶段 2 只有 b1、b2；b3、b4、full 等在阶段 3、4 加入。"""

from __future__ import annotations

from ..orchestrator.baseline import BASELINES, BaselineConfig

LATER = {"b3": 3, "b4": 3, "full": 3, "full-no-exp": 4, "full-no-esc": 4, "full-no-review": 4}


class ConfigError(ValueError):
    pass


def get_config(name: str) -> BaselineConfig:
    name = name.strip().lower()
    if name in BASELINES:
        return BASELINES[name]
    if name in LATER:
        raise ConfigError(f"配置 {name} 要到阶段 {LATER[name]} 才实现，目前可用：{', '.join(BASELINES)}")
    raise ConfigError(f"未知配置 {name}，目前可用：{', '.join(BASELINES)}")


def parse_configs(text: str) -> list[BaselineConfig]:
    names = [x for x in (s.strip() for s in text.split(",")) if x]
    if not names:
        raise ConfigError("至少要给一个配置")
    return [get_config(n) for n in names]

"""评测配置（ARCHITECTURE §10）：b1、b2、b5 是单 Agent，b3、b4、full 是多 Agent；消融配置在阶段 4 加入。"""

from __future__ import annotations

from ..orchestrator.baseline import BASELINES, BaselineConfig
from ..orchestrator.engine import MULTI, MultiConfig

RunConfig = BaselineConfig | MultiConfig
ALL = {"b1": BASELINES["b1"], "b2": BASELINES["b2"], "b3": MULTI["b3"], "b4": MULTI["b4"],
       "full": MULTI["full"], "b5": BASELINES["b5"]}
LATER = {"full-no-exp": 4, "full-no-esc": 4, "full-no-review": 4}


class ConfigError(ValueError):
    pass


def get_config(name: str) -> RunConfig:
    name = name.strip().lower()
    if name in ALL:
        return ALL[name]
    if name in LATER:
        raise ConfigError(f"配置 {name} 要到阶段 {LATER[name]} 才实现，目前可用：{', '.join(ALL)}")
    raise ConfigError(f"未知配置 {name}，目前可用：{', '.join(ALL)}")


def parse_configs(text: str) -> list[RunConfig]:
    names = [x for x in (s.strip().lower() for s in text.split(",")) if x]
    if not names:
        raise ConfigError("至少要给一个配置")
    if len(set(names)) != len(names):
        raise ConfigError("配置列表里有重复")
    if "b5" in names and ("full" not in names or names.index("full") > names.index("b5")):
        raise ConfigError("b5 的 token 预算取自同一实验里 full 的平均值，必须和 full 一起跑，且排在 full 后面")
    return [get_config(n) for n in names]

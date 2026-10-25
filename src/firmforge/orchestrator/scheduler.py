"""调度器（ARCHITECTURE §7）：先做哪个就绪节点，交给哪个 Agent。

策略接口：choose(node_id, candidates, stats, load) -> (agent, score_breakdown)

    serial       基线 B3：一次只跑一个节点；固定用指定的 Agent（没指定时用该角色注册的第一个）
    round_robin  基线 B4：并行；Agent 轮流
    random       对照：并行；随机（固定种子）
    smart        完整系统：并行；score = p̂ − α·cost_norm − β·load + γ·sqrt(ln(N+1)/(n_a+1))

每次分配的得分明细随 node_assigned 事件发出，界面上可以看到"为什么选它"。
"""

from __future__ import annotations

import math
import random
from typing import Callable

from ..agents.registry import AgentDef
from .dag import Plan

Stats = dict[str, dict]          # agent 名 -> {attempts, successes, total_cost, ...}（store.agent_stats）
Load = dict[str, int]            # agent 名 -> 正在处理的节点数


class Strategy:
    name = ""
    parallel = True

    def choose(self, node_id: str, candidates: list[AgentDef], stats: Stats, load: Load) -> tuple[AgentDef, dict]:
        raise NotImplementedError


def _only(candidates: list[AgentDef], strategy: str) -> tuple[AgentDef, dict] | None:
    if not candidates:
        raise ValueError("没有可用的候选 Agent")
    if len(candidates) == 1:
        return candidates[0], {"strategy": strategy, "reason": "该角色只有这一个 Agent"}
    return None


class Serial(Strategy):
    name = "serial"
    parallel = False

    def __init__(self, fixed: dict[str, str] | None = None):
        self.fixed = dict(fixed or {})        # 角色 -> 固定使用的 Agent 名

    def choose(self, node_id, candidates, stats, load):
        one = _only(candidates, self.name)
        if one:
            return one
        want = self.fixed.get(candidates[0].role)
        for c in candidates:
            if c.name == want:
                return c, {"strategy": self.name, "reason": f"固定分配给 {c.name}"}
        return candidates[0], {"strategy": self.name, "reason": "固定用该角色注册的第一个 Agent"}


class RoundRobin(Strategy):
    name = "round_robin"

    def __init__(self):
        self._next: dict[str, int] = {}

    def choose(self, node_id, candidates, stats, load):
        one = _only(candidates, self.name)
        if one:
            return one
        role = candidates[0].role
        i = self._next.get(role, 0)
        self._next[role] = i + 1
        c = candidates[i % len(candidates)]
        return c, {"strategy": self.name, "reason": f"轮流分配，第 {i + 1} 次轮到 {c.name}"}


class Random(Strategy):
    name = "random"

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def choose(self, node_id, candidates, stats, load):
        one = _only(candidates, self.name)
        if one:
            return one
        c = self._rng.choice(candidates)
        return c, {"strategy": self.name, "reason": f"随机选中 {c.name}"}


class Smart(Strategy):
    name = "smart"

    def __init__(self, *, alpha: float = 0.3, beta: float = 0.2, gamma: float = 0.5, concurrency: int = 4,
                 list_price: Callable[[AgentDef], float] = lambda a: 1.0):
        self.alpha, self.beta, self.gamma = alpha, beta, gamma
        self.concurrency = max(1, concurrency)
        self.list_price = list_price          # 没有实测成本时，用模型档位的标价（元 / 百万 token）作相对成本

    def choose(self, node_id, candidates, stats, load):
        one = _only(candidates, self.name)
        if one:
            return one
        rows = {c.name: stats.get(c.name) or {} for c in candidates}
        n_total = sum(int(r.get("attempts") or 0) for r in rows.values())
        measured = all(int(r.get("attempts") or 0) > 0 and float(r.get("total_cost") or 0) > 0 for r in rows.values())
        if measured:
            costs = {k: float(r["total_cost"]) / int(r["attempts"]) for k, r in rows.items()}
            basis = "实测平均成本"
        else:
            costs = {c.name: self.list_price(c) for c in candidates}
            basis = "模型标价（还没有全部候选的实测成本）"
        max_cost = max(costs.values()) or 1.0
        table = {}
        for c in candidates:
            r = rows[c.name]
            n_a = int(r.get("attempts") or 0)
            succ = int(r.get("successes") or 0)
            p_hat = (succ + 1) / (n_a + 2)
            cost_norm = costs[c.name] / max_cost if max_cost > 0 else 0.0
            ld = load.get(c.name, 0) / self.concurrency
            explore = math.sqrt(math.log(n_total + 1) / (n_a + 1))
            score = p_hat - self.alpha * cost_norm - self.beta * ld + self.gamma * explore
            table[c.name] = {"p_hat": round(p_hat, 4), "cost_norm": round(cost_norm, 4), "load": round(ld, 4),
                             "explore": round(explore, 4), "score": round(score, 4), "n": n_a, "successes": succ}
        order = {c.name: i for i, c in enumerate(candidates)}
        best = max(candidates, key=lambda c: (table[c.name]["score"], -order[c.name]))
        b = table[best.name]
        reason = (f"得分最高 {best.name} = {b['score']:.3f}（成功率 {b['p_hat']:.2f} − {self.alpha}×成本 {b['cost_norm']:.2f}"
                  f" − {self.beta}×负载 {b['load']:.2f} + {self.gamma}×探索 {b['explore']:.2f}）")
        return best, {"strategy": self.name, "reason": reason, "weights": {"alpha": self.alpha, "beta": self.beta,
                      "gamma": self.gamma}, "N": n_total, "cost_basis": basis, "candidates": table,
                      "chosen": best.name}


STRATEGIES = ("serial", "round_robin", "random", "smart")


def make_strategy(name: str, *, scheduler_cfg: dict | None = None, concurrency: int = 4,
                  list_price: Callable[[AgentDef], float] = lambda a: 1.0,
                  fixed: dict[str, str] | None = None) -> Strategy:
    cfg = scheduler_cfg or {}
    if name == "serial":
        return Serial(fixed)
    if name == "round_robin":
        return RoundRobin()
    if name == "random":
        return Random(int(cfg.get("seed", 42)))
    if name == "smart":
        return Smart(alpha=float(cfg.get("alpha", 0.3)), beta=float(cfg.get("beta", 0.2)),
                     gamma=float(cfg.get("gamma", 0.5)), concurrency=concurrency, list_price=list_price)
    raise ValueError(f"未知调度策略 {name}，可选：{', '.join(STRATEGIES)}")


def prioritize(ready: list[str], plan: Plan) -> list[str]:
    """就绪节点按到终点的最长路径降序（关键路径优先），相同时按规划里的顺序。"""
    cp = plan.critical_path()
    pos = {m.id: i for i, m in enumerate(plan.modules)}
    return sorted(ready, key=lambda x: (-cp[x], pos[x]))

"""事件总线（ARCHITECTURE §2.8）：每个事件写入 events.jsonl，同时推送给订阅者。

界面只读事件，不直接访问引擎内部状态，所以回放和实时显示走同一条路径。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

EVENT_TYPES = {
    # 运行级
    "run_started", "plan_ready", "human_wait", "human_done", "run_finished",
    # 节点级
    "node_ready", "node_assigned", "node_done", "node_failed",
    # Agent 调用
    "agent_started", "agent_finished",
    # 判定与循环
    "verify_started", "verify_finished", "diagnosis", "escalated", "rollback",
    "format_retry",                 # Agent 没按格式交文件，当场补要（嵌入式新流程）
    "design_rejected",              # 设计分层没通过检查，带着问题重做（嵌入式新流程；界面显示理由）
    "wiring_ready",                 # 这个项目的接线定下来了（DECISIONS 第 108 条）
    "wiring_checked",               # 上板时外接器件有没有应答（实测确认接线）
    "chip_detected",                # 第一次上板前识别板子上的芯片（esptool 实测），对不上就不烧录
    "step_started",                 # 解释器开始执行主流程里的一步（DECISIONS 第 117 条）
    "review", "review_reverted",    # 审查 Agent 的意见；按意见改坏了退回通过的版本
    "human_reverted",               # 按人的意见改坏了已经通过的模块，退回通过的版本
    "hardware_check",               # 诊断说硬件问题时平台的接线自检结果（第 124 条）
    "mutation_test", "hardened_reverted",   # 变异体补测试的结果；补强后没修好、交付补强前的版本（第 125 条）
    "app_checked", "firmware_ready",
    "wiring_selfcheck",                     # 接线一步的自检结果（第 134 条第 3 阶段）        # 集成：主程序上板跑两轮的核对结果；完整工程已交付（第 131 条）
    # 经验库
    "experience_saved", "experience_used",
    # 工具（DECISIONS 第 63 条）
    "tool_called", "tool_result",
    # 规则流程（DECISIONS 第 66 条第 ②③⑧ 步）
    "rules_started", "rules_proposed", "rule_rejected", "rules_round_done", "rules_need_human", "rule_trusted",
    "rule_renamed",
    "rules_finished",
}

Subscriber = Callable[[dict], None]


class EventBus:
    def __init__(self, run_id: str, path: Path | None = None, clock: Callable[[], float] = time.time):
        self.run_id = run_id
        self.path = Path(path) if path else None
        self._clock = clock
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        self._subs.append(fn)

    def emit(self, type: str, node_id: str | None = None, **data) -> dict:
        if type not in EVENT_TYPES:
            raise ValueError(f"未知事件类型：{type}")
        event = {"ts": round(self._clock(), 3), "run_id": self.run_id, "type": type,
                 "node_id": node_id, "data": data}
        line = json.dumps(event, ensure_ascii=False)
        with self._lock:
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            subs = list(self._subs)
        for fn in subs:
            fn(event)
        return event

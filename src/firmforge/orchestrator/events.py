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
    # 经验库
    "experience_saved", "experience_used",
    # 工具（DECISIONS 第 63 条）
    "tool_called", "tool_result",
    # 规则流程（DECISIONS 第 66 条第 ②③⑧ 步）
    "rules_started", "rules_proposed", "rule_rejected", "rules_round_done", "rules_need_human", "rule_trusted",
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

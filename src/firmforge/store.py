"""SQLite 存储（ARCHITECTURE §2.10）：runs、node_attempts、agent_stats（能力画像）、experiences（经验库）。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_DB = Path(".ff_data") / "firmforge.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    task_id TEXT NOT NULL,
    config TEXT NOT NULL,
    verdict TEXT,
    total_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    wall_s REAL
);
CREATE TABLE IF NOT EXISTS node_attempts (
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    agent TEXT,
    verdict TEXT,
    failed_layer TEXT,
    signature TEXT,
    tokens INTEGER DEFAULT 0,
    latency_s REAL,
    PRIMARY KEY (run_id, node_id, attempt)
);
CREATE TABLE IF NOT EXISTS agent_stats (
    agent TEXT NOT NULL,
    version TEXT NOT NULL,
    task_type TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    first_pass INTEGER DEFAULT 0,
    successes INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_latency_s REAL DEFAULT 0,
    total_cost REAL DEFAULT 0,
    PRIMARY KEY (agent, version, task_type)
);
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    layer TEXT NOT NULL,
    pattern TEXT NOT NULL,
    fix TEXT NOT NULL,
    source_run TEXT,
    created_at REAL,
    uses INTEGER DEFAULT 0,
    helped INTEGER DEFAULT 0
);
"""


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _exec(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self._conn.execute(sql, args)
            self._conn.commit()

    def execute(self, sql: str, args: tuple = ()) -> int:
        """执行一条写语句，返回 lastrowid。"""
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    def start_run(self, run_id: str, task_id: str, config: str | dict) -> None:
        cfg = config if isinstance(config, str) else json.dumps(config, ensure_ascii=False)
        self._exec("INSERT INTO runs (id, created_at, task_id, config) VALUES (?, ?, ?, ?)",
                   (run_id, time.time(), task_id, cfg))

    def finish_run(self, run_id: str, verdict: str, total_tokens: int, cost: float, wall_s: float) -> None:
        self._exec("UPDATE runs SET verdict=?, total_tokens=?, cost=?, wall_s=? WHERE id=?",
                   (verdict, total_tokens, cost, wall_s, run_id))

    def add_attempt(self, run_id: str, node_id: str, attempt: int, agent: str, verdict: str,
                    failed_layer: str | None, signature: str | None, tokens: int, latency_s: float) -> None:
        self._exec("INSERT OR REPLACE INTO node_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (run_id, node_id, attempt, agent, verdict, failed_layer, signature, tokens, latency_s))

    def add_agent_stat(self, agent: str, version: str, task_type: str, *, first_pass: bool, success: bool,
                       tokens: int, latency_s: float, cost: float) -> None:
        """记一次"Agent 负责一个模块"的结果：尝试 1 次，首次即通过、最终通过各记 0 或 1。"""
        self._exec(
            "INSERT INTO agent_stats VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent, version, task_type) DO UPDATE SET attempts = attempts + 1, "
            "first_pass = first_pass + excluded.first_pass, successes = successes + excluded.successes, "
            "total_tokens = total_tokens + excluded.total_tokens, "
            "total_latency_s = total_latency_s + excluded.total_latency_s, "
            "total_cost = total_cost + excluded.total_cost",
            (agent, version, task_type, int(first_pass), int(success), tokens, latency_s, cost))

    def agent_stats(self, task_type: str) -> dict[str, dict]:
        """某类任务上各 Agent 的累计画像（同名各版本合并）。"""
        rows = self.query("SELECT agent, SUM(attempts) AS attempts, SUM(first_pass) AS first_pass, "
                          "SUM(successes) AS successes, SUM(total_tokens) AS total_tokens, "
                          "SUM(total_latency_s) AS total_latency_s, SUM(total_cost) AS total_cost "
                          "FROM agent_stats WHERE task_type = ? GROUP BY agent", (task_type,))
        return {r["agent"]: r for r in rows}

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]


class FrozenStore(Store):
    """只读用的数据库（评测复测时冻结调度成绩和经验库，DECISIONS 第 119 条）：照常查询，所有写操作什么都不做。
    数据库文件本身不动，所以同一份调优结果可以反复拿来复测。"""

    def _exec(self, sql: str, args: tuple = ()) -> None:
        return None

    def execute(self, sql: str, args: tuple = ()) -> int:
        return 0

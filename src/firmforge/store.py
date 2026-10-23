"""SQLite 存储（ARCHITECTURE §2.10）。阶段 2 只有 runs 与 node_attempts 两张表。"""

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

    def query(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

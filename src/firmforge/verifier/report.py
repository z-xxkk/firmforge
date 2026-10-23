"""判定器报告的数据结构（ARCHITECTURE §2.3）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"
INCOMPLETE = "incomplete"

LOG_LIMIT = 4000
HIDDEN_LAYER = "hidden_tests"


@dataclass
class Diagnostic:
    severity: str            # error | warning | info
    message: str
    key: str                 # 归一化后的键，用于错误签名
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict:
        return {"severity": self.severity, "file": self.file, "line": self.line,
                "message": self.message, "key": self.key}


@dataclass
class LayerResult:
    layer: str
    title: str
    status: str = PASS
    duration_s: float = 0.0
    complete: bool = True
    signature: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    log: str = ""

    def failing_keys(self) -> list[str]:
        return [d.key for d in self.diagnostics if d.severity != "info"]

    def to_dict(self) -> dict:
        log = self.log
        if len(log) > LOG_LIMIT:
            log = log[:LOG_LIMIT] + "\n…（已截断）"
        return {
            "layer": self.layer, "title": self.title, "status": self.status,
            "duration_s": round(self.duration_s, 3), "complete": self.complete,
            "signature": self.signature,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "metrics": self.metrics, "notes": self.notes, "log": log,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LayerResult":
        diags = [Diagnostic(x["severity"], x["message"], x["key"], x.get("file"), x.get("line"))
                 for x in d.get("diagnostics", [])]
        return cls(d["layer"], d["title"], d["status"], d.get("duration_s", 0.0),
                   d.get("complete", True), d.get("signature"), diags,
                   d.get("metrics", {}), d.get("notes", []), d.get("log", ""))


@dataclass
class Report:
    layers: list[LayerResult] = field(default_factory=list)
    duration_s: float = 0.0

    def layer(self, name: str) -> LayerResult | None:
        for lr in self.layers:
            if lr.layer == name:
                return lr
        return None

    @property
    def failed_layer(self) -> str | None:
        for lr in self.layers:
            if lr.status == FAIL:
                return lr.layer
        return None

    @property
    def verdict(self) -> str:
        if self.failed_layer is not None:
            return FAIL
        if not self.layers:
            return INCOMPLETE
        if any(lr.status == SKIPPED or not lr.complete for lr in self.layers):
            return INCOMPLETE
        return PASS

    @property
    def signature(self) -> str | None:
        name = self.failed_layer
        return self.layer(name).signature if name else None

    def for_agent(self) -> "Report":
        """给 Agent 看的报告：一定不含隐藏测试层。"""
        return Report([lr for lr in self.layers if lr.layer != HIDDEN_LAYER], self.duration_s)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "failed_layer": self.failed_layer,
            "signature": self.signature, "duration_s": round(self.duration_s, 3),
            "layers": [lr.to_dict() for lr in self.layers],
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict) -> "Report":
        return cls([LayerResult.from_dict(x) for x in d.get("layers", [])], d.get("duration_s", 0.0))


__all__ = ["Diagnostic", "LayerResult", "Report", "PASS", "FAIL", "SKIPPED", "INCOMPLETE"]

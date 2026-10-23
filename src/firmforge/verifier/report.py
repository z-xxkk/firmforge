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

    def to_markdown(self, title: str = "验证报告") -> str:
        marks = {PASS: "通过", FAIL: "失败", SKIPPED: "跳过"}
        head = f"结论：**{self.verdict}**"
        if self.failed_layer:
            head += f"，首个失败层 `{self.failed_layer}`，签名 `{self.signature}`"
        lines = [f"# {title}", "", head, "", "| 层 | 结果 | 指标 | 备注 |", "|---|---|---|---|"]
        for lr in self.layers:
            metrics = "，".join(f"{k}={v}" for k, v in lr.metrics.items() if not isinstance(v, dict))
            extra = "" if lr.complete or lr.status == SKIPPED else "（不完整）"
            notes = "；".join(lr.notes).replace("|", "/")
            lines.append(f"| {lr.title} `{lr.layer}` | {marks.get(lr.status, lr.status)}{extra} | {metrics} | {notes} |")
        for lr in self.layers:
            diags = [d for d in lr.diagnostics if d.severity != "info"]
            if diags:
                lines += ["", f"## {lr.title}的问题", ""]
                for d in diags[:20]:
                    loc = f"{d.file}:{d.line}: " if d.file and d.line else (f"{d.file}: " if d.file else "")
                    lines.append(f"- {loc}{d.message}")
        return "\n".join(lines) + "\n"


OBJECTIVE_LAYER_NAMES = ("host_compile", "cross_compile", "resources", "static_check", "unit_tests")


def attempt_score(report: Report) -> float:
    """尝试得分（ARCHITECTURE §8）= 通过的客观层数（0–5）+ L5 测试通过比例。"""
    score = float(sum(1 for lr in report.layers if lr.layer in OBJECTIVE_LAYER_NAMES and lr.status == PASS))
    ut = report.layer("unit_tests")
    if ut is not None:
        total = ut.metrics.get("total") or 0
        failed = ut.metrics.get("failed") or 0
        if total > 0:
            score += max(0, total - failed) / total
    return score


__all__ = ["Diagnostic", "LayerResult", "Report", "PASS", "FAIL", "SKIPPED", "INCOMPLETE", "attempt_score"]

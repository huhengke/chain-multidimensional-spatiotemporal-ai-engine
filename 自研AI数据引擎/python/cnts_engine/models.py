from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class QueryPlan:
    datasets: list[str]
    start: str
    end: str
    metrics: list[str]
    filters: list[dict[str, Any]] = field(default_factory=list)
    group_by: list[str] = field(default_factory=list)
    comparisons: list[str] = field(default_factory=list)
    merge_mode: Literal[
        "sum_total", "separate", "total_and_breakdown", "skip_empty"
    ] = "separate"
    detail_policy: Literal["allow_fallback", "aggregates_only"] = "allow_fallback"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["plan_version"] = 1
        payload["time_range"] = {"start": payload.pop("start"), "end": payload.pop("end")}
        return payload


@dataclass(slots=True)
class PendingRecommendation:
    recommendation_id: str
    field: str
    role: Literal["common", "outer", "growth", "ignore"]
    reason: str
    confidence: float
    source: Literal["experience", "model", "rule", "combined"]
    status: Literal["pending_review", "confirmed", "rejected"] = "pending_review"

    def confirm(self) -> None:
        self.status = "confirmed"

    def reject(self) -> None:
        self.status = "rejected"

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import ExportResult
from .normalize import (
    METRIC_SCALES,
    NULL_GROUP,
    UNMAPPED_CATEGORY,
    UNMAPPED_ORG,
    NormalizedRow,
    format_scaled,
    normalize_record,
)
from .sqlite_source import SqliteOilDailySource


GROUP_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "day": ("day",),
    "month": ("month",),
    "bigareacode": ("bigareacode",),
    "branchcode": ("branchcode",),
    "areacode": ("areacode",),
    "oucode": ("oucode",),
    "category": ("category",),
    "gascode": ("gascode",),
    "month_bigareacode": ("month", "bigareacode"),
    "month_bigareacode_category": ("month", "bigareacode", "category"),
}


@dataclass(slots=True)
class Aggregate:
    record_count: int = 0
    metrics: dict[str, int] = field(
        default_factory=lambda: {name: 0 for name in METRIC_SCALES}
    )

    def add(self, row: NormalizedRow) -> None:
        self.record_count += 1
        for metric, value in row.scaled_metrics.items():
            self.metrics[metric] += value

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_count": self.record_count,
            "metrics": {
                metric: format_scaled(value, METRIC_SCALES[metric])
                for metric, value in self.metrics.items()
            },
        }


class ControlAccumulator:
    def __init__(self) -> None:
        self.total = Aggregate()
        self.groups: dict[str, dict[tuple[str, ...], Aggregate]] = {
            name: defaultdict(Aggregate) for name in GROUP_DEFINITIONS
        }

    def add(self, row: NormalizedRow) -> None:
        self.total.add(row)
        values = dict(row.payload)
        values["month"] = str(values["day"])[:7]
        for name, fields in GROUP_DEFINITIONS.items():
            key = tuple(
                NULL_GROUP if values.get(field) is None else str(values[field])
                for field in fields
            )
            self.groups[name][key].add(row)

    def to_dict(self) -> dict[str, Any]:
        group_sets: dict[str, list[dict[str, Any]]] = {}
        for name, fields in GROUP_DEFINITIONS.items():
            rows = []
            for key, aggregate in sorted(self.groups[name].items()):
                rows.append(
                    {
                        "dimensions": dict(zip(fields, key)),
                        **aggregate.to_dict(),
                    }
                )
            group_sets[name] = rows
        return {
            "metric_scales": METRIC_SCALES,
            "total": self.total.to_dict(),
            "group_sets": group_sets,
        }


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _load_candidates(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    recommendations = payload.get("recommendations", [])
    by_code: dict[str, dict[str, Any]] = {}
    for recommendation in recommendations:
        if recommendation.get("status") != "pending_review":
            raise ValueError("shadow migration only accepts pending_review candidates")
        code = str(recommendation.get("match", {}).get("universalcode", "")).strip()
        if not code or code in by_code:
            raise ValueError("candidate UniversalCode must be non-empty and unique")
        by_code[code] = recommendation
    return payload, by_code


def _quality_event(
    database: Path,
    row: NormalizedRow,
    field: str,
    raw_value: Any,
    normalized_value: str,
    reason_code: str,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "dataset": "oil_sales_daily_shadow",
        "source_file": str(database),
        "source_row_id": row.source_id,
        "time": row.payload["day"],
        "field": field,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "reason_code": reason_code,
        "candidate_mapping": None if candidate is None else candidate["candidate_mapping"],
        "candidate_recommendation_id": (
            None if candidate is None else candidate["recommendation_id"]
        ),
        "evidence": None if candidate is None else candidate["evidence"],
        "status": "unresolved" if candidate is None else "pending_review",
    }


def export_sqlite_to_jsonl(
    database: str | Path,
    run_directory: str | Path,
    candidate_config: str | Path,
    *,
    chunk_size: int = 10_000,
) -> ExportResult:
    run_directory = Path(run_directory).resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    records_path = run_directory / "oil-sales-daily.jsonl"
    quality_path = run_directory / "quality-sidecar.jsonl"
    controls_path = run_directory / "source-controls.json"
    recommendations_path = run_directory / "pending-recommendations.json"
    candidate_payload, candidates = _load_candidates(Path(candidate_config).resolve())

    controls = ControlAccumulator()
    quality_count = 0
    org_unmapped = 0
    category_unmapped = 0
    candidate_stats: dict[str, dict[str, Any]] = {
        code: {"rows": 0, "min_day": None, "max_day": None}
        for code in candidates
    }

    with SqliteOilDailySource(database) as source:
        before = source.fingerprint()
        with records_path.open("w", encoding="utf-8", newline="\n") as records, quality_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as quality:
            for raw_row in source.iter_rows(chunk_size):
                try:
                    row = normalize_record(raw_row)
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f"source row {raw_row['id']} failed normalization: {error}") from error
                records.write(
                    json.dumps(row.payload, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
                controls.add(row)

                if row.organization_unmapped:
                    org_unmapped += 1
                    universalcode = row.payload.get("universalcode")
                    candidate = candidates.get(str(universalcode))
                    if candidate is not None:
                        stats = candidate_stats[str(universalcode)]
                        stats["rows"] += 1
                        day = str(row.payload["day"])
                        stats["min_day"] = min(stats["min_day"] or day, day)
                        stats["max_day"] = max(stats["max_day"] or day, day)
                    event = _quality_event(
                        source.database,
                        row,
                        "organization_path",
                        row.raw_organization,
                        UNMAPPED_ORG,
                        "source_organization_placeholder",
                        candidate,
                    )
                    quality.write(
                        json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    quality_count += 1

                if row.category_unmapped:
                    category_unmapped += 1
                    event = _quality_event(
                        source.database,
                        row,
                        "category",
                        row.raw_category,
                        UNMAPPED_CATEGORY,
                        "source_category_placeholder",
                        None,
                    )
                    quality.write(
                        json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    quality_count += 1
        after = source.fingerprint()

    if before != after:
        raise RuntimeError("source SQLite changed during shadow export")
    if controls.total.record_count != before.row_count:
        raise RuntimeError(
            f"export row count {controls.total.record_count} does not match source {before.row_count}"
        )

    pending_rows = sum(stats["rows"] for stats in candidate_stats.values())
    recommendations = []
    for recommendation in candidate_payload.get("recommendations", []):
        enriched = dict(recommendation)
        code = str(recommendation["match"]["universalcode"])
        enriched["observed"] = candidate_stats[code]
        recommendations.append(enriched)

    _write_json_atomic(
        controls_path,
        {
            "source_before": before.to_dict(),
            "source_after": after.to_dict(),
            "quality": {
                "quality_event_count": quality_count,
                "organization_unmapped_rows": org_unmapped,
                "category_unmapped_rows": category_unmapped,
                "pending_review_rows": pending_rows,
            },
            **controls.to_dict(),
        },
    )
    _write_json_atomic(
        recommendations_path,
        {
            "version": candidate_payload.get("version", 1),
            "dataset": candidate_payload.get("dataset", "oil_sales_daily_shadow"),
            "recommendations": recommendations,
        },
    )
    return ExportResult(
        records_path=records_path,
        quality_path=quality_path,
        controls_path=controls_path,
        recommendations_path=recommendations_path,
        row_count=controls.total.record_count,
        quality_event_count=quality_count,
        organization_unmapped_rows=org_unmapped,
        category_unmapped_rows=category_unmapped,
        pending_review_rows=pending_rows,
        source_before=before,
        source_after=after,
    )

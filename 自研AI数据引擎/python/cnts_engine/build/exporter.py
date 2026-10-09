from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import FactSchema, SourceBinding
from .normalize import format_scaled, normalize_fact_row
from .source import SourceSnapshot, SqliteTableSource


@dataclass(frozen=True, slots=True)
class FactExportResult:
    dataset_id: str
    records_path: Path
    controls_path: Path
    quality_path: Path
    row_count: int
    min_day: str
    max_day: str
    distinct_days: int
    metric_controls: dict[str, str]
    source_fingerprint: str
    source_before: SourceSnapshot
    source_after: SourceSnapshot
    quality_event_count: int


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def export_fact_dataset(
    database: str | Path,
    schema: FactSchema,
    binding: SourceBinding,
    run_directory: str | Path,
    *,
    chunk_size: int = 10_000,
) -> FactExportResult:
    schema.validate_binding(binding)
    if binding.time_column is None:
        raise ValueError("fact source binding has no time column")
    run_directory = Path(run_directory).resolve()
    run_directory.mkdir(parents=True, exist_ok=True)
    records_path = run_directory / f"{schema.dataset_id}.jsonl"
    controls_path = run_directory / "source-controls.json"
    quality_path = run_directory / "quality-sidecar.jsonl"
    source_columns = (
        binding.time_column,
        *schema.dimensions,
        *(metric.name for metric in schema.metrics),
    )
    totals = {metric.name: 0 for metric in schema.metrics}
    days: set[str] = set()
    row_count = 0
    quality_count = 0
    content_digest = hashlib.sha256()

    with SqliteTableSource(database, binding, source_columns) as source:
        before = source.snapshot()
        with records_path.open("w", encoding="utf-8", newline="\n") as records, quality_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as quality:
            for raw_row in source.iter_rows(chunk_size=chunk_size):
                try:
                    payload, scaled_metrics, events = normalize_fact_row(
                        raw_row, schema, binding
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    source_key = raw_row[binding.key_column]
                    raise ValueError(
                        f"{schema.dataset_id} source row {source_key!r} failed normalization: {exc}"
                    ) from exc
                line = (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                )
                records.write(line)
                content_digest.update(line.encode("utf-8"))
                row_count += 1
                day = str(payload[schema.time_field])
                days.add(day)
                for metric, scaled in scaled_metrics.items():
                    totals[metric] += scaled
                for event in events:
                    quality.write(
                        json.dumps(
                            {
                                "dataset": schema.dataset_id,
                                "source_table": binding.table,
                                "source_key": raw_row[binding.key_column],
                                "time": day,
                                "status": "unresolved",
                                **event,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    quality_count += 1
        after = source.snapshot()

    if before != after:
        raise RuntimeError(f"source changed during export: {schema.dataset_id}")
    if row_count != before.row_count:
        raise RuntimeError(
            f"exported {row_count} rows but source contains {before.row_count}: {schema.dataset_id}"
        )
    if not days:
        raise RuntimeError(f"source contains no time values: {schema.dataset_id}")
    metric_controls = {
        metric.name: format_scaled(totals[metric.name], metric.scale)
        for metric in schema.metrics
    }
    controls = {
        "dataset_id": schema.dataset_id,
        "source_before": before.to_dict(),
        "source_after": after.to_dict(),
        "source_fingerprint": content_digest.hexdigest(),
        "row_count": row_count,
        "time_granularity": binding.time_granularity,
        "time_range": {"start": min(days), "end": max(days)},
        "distinct_days": len(days),
        "metric_controls": metric_controls,
        "quality_event_count": quality_count,
    }
    _write_json_atomic(controls_path, controls)
    return FactExportResult(
        dataset_id=schema.dataset_id,
        records_path=records_path,
        controls_path=controls_path,
        quality_path=quality_path,
        row_count=row_count,
        min_day=min(days),
        max_day=max(days),
        distinct_days=len(days),
        metric_controls=metric_controls,
        source_fingerprint=content_digest.hexdigest(),
        source_before=before,
        source_after=after,
        quality_event_count=quality_count,
    )

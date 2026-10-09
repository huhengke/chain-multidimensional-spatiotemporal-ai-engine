from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    database: str
    bytes: int
    modified_ns: int
    wal_bytes: int
    wal_modified_ns: int
    row_count: int
    min_id: int | None
    max_id: int | None
    min_day: str | None
    max_day: str | None
    distinct_days: int
    page_count: int
    schema_version: int
    data_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExportResult:
    records_path: Path
    quality_path: Path
    controls_path: Path
    recommendations_path: Path
    row_count: int
    quality_event_count: int
    organization_unmapped_rows: int
    category_unmapped_rows: int
    pending_review_rows: int
    source_before: SourceFingerprint
    source_after: SourceFingerprint

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for field in (
            "records_path",
            "quality_path",
            "controls_path",
            "recommendations_path",
        ):
            payload[field] = str(payload[field])
        return payload

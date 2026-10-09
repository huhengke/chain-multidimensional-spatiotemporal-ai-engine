from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .models import FactSchema, SourceBinding


UNMAPPED_VALUE = "__UNMAPPED__"


def normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "*" or text.upper() == "NULL":
        return None
    return text


def normalize_time(value: Any, source_format: str) -> str:
    text = normalize_optional(value)
    if text is None:
        raise ValueError("time value is null")
    if source_format == "iso_day":
        return date.fromisoformat(text[:10]).isoformat()
    if source_format == "iso_month":
        return datetime.strptime(text[:7], "%Y-%m").date().replace(day=1).isoformat()
    if source_format == "d/m/yyyy":
        return datetime.strptime(text.split()[0], "%d/%m/%Y").date().isoformat()
    raise ValueError(f"unsupported source time format: {source_format}")


def decimal_to_scaled(value: Any, scale: int) -> int:
    text = normalize_optional(value)
    if text is None:
        raise ValueError("metric value is null")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {text!r}") from exc
    if not number.is_finite():
        raise ValueError(f"non-finite decimal: {text!r}")
    scaled = number.scaleb(scale)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"decimal {text!r} exceeds scale {scale}")
    return int(integral)


def format_scaled(value: int, scale: int) -> str:
    if scale == 0:
        return str(value)
    negative = value < 0
    whole, fraction = divmod(abs(value), 10**scale)
    return f"{'-' if negative else ''}{whole}.{fraction:0{scale}d}"


def normalize_fact_row(
    row: Mapping[str, Any],
    schema: FactSchema,
    binding: SourceBinding,
) -> tuple[dict[str, Any], dict[str, int], list[dict[str, Any]]]:
    if binding.time_column is None or binding.time_format is None:
        raise ValueError("fact binding has no time declaration")
    payload: dict[str, Any] = {
        schema.time_field: normalize_time(row[binding.time_column], binding.time_format)
    }
    quality: list[dict[str, Any]] = []
    normalized_common = {
        field: normalize_optional(row[field]) for field in schema.common_dimensions
    }
    for group in binding.coherence_groups:
        members = tuple(field for field in group if field in normalized_common)
        if members and any(normalized_common[field] is None for field in members):
            for field in members:
                quality.append(
                    {
                        "field": field,
                        "raw_value": row[field],
                        "normalized_value": UNMAPPED_VALUE,
                        "reason_code": "incomplete_common_dimension_group",
                    }
                )
                normalized_common[field] = UNMAPPED_VALUE
    for field, value in normalized_common.items():
        if value is None:
            quality.append(
                {
                    "field": field,
                    "raw_value": row[field],
                    "normalized_value": UNMAPPED_VALUE,
                    "reason_code": "missing_common_dimension",
                }
            )
            normalized_common[field] = UNMAPPED_VALUE
    payload.update(normalized_common)
    for field in schema.outer_dimensions:
        payload[field] = normalize_optional(row[field])

    scaled_metrics: dict[str, int] = {}
    for metric in schema.metrics:
        scaled = decimal_to_scaled(row[metric.name], metric.scale)
        scaled_metrics[metric.name] = scaled
        payload[metric.name] = format_scaled(scaled, metric.scale)
    return payload, scaled_metrics, quality

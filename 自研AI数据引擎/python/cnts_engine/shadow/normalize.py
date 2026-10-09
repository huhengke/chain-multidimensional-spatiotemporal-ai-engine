from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


UNMAPPED_ORG = "__UNMAPPED_ORG__"
UNMAPPED_CATEGORY = "__UNMAPPED_CATEGORY__"
NULL_GROUP = "<null>"

COMMON_ORG_FIELDS = ("bigareacode", "branchcode", "areacode", "oucode")
ORG_NAME_FIELDS = ("shortname", "branchname", "areaname", "bigareaname")
OUTER_FIELDS = (
    "category",
    "gascode",
    "universalcode",
    "salesmode",
    "checkmode",
    *ORG_NAME_FIELDS,
    "price",
    "guns",
    "isoperate",
    "datatype",
    "remark",
)
METRIC_SCALES = {"salesvol": 6, "qty": 2, "amount": 2}


@dataclass(frozen=True, slots=True)
class NormalizedRow:
    source_id: int
    payload: dict[str, Any]
    scaled_metrics: dict[str, int]
    organization_unmapped: bool
    category_unmapped: bool
    raw_organization: dict[str, Any]
    raw_category: Any


def normalize_optional(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "*" or text.upper() == "NULL":
        return None
    return text


def normalize_day(value: Any) -> str:
    text = normalize_optional(value)
    if text is None:
        raise ValueError("day is null")
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
    except ValueError as error:
        raise ValueError(f"invalid day {text!r}") from error


def decimal_to_scaled(value: Any, scale: int) -> int:
    text = normalize_optional(value)
    if text is None:
        raise ValueError("numeric value is null")
    try:
        decimal = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"invalid decimal {text!r}") from error
    if not decimal.is_finite():
        raise ValueError(f"non-finite decimal {text!r}")
    scaled = decimal.scaleb(scale)
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"decimal {text!r} exceeds scale {scale}")
    return int(integral)


def format_scaled(value: int, scale: int) -> str:
    if scale == 0:
        return str(value)
    negative = value < 0
    absolute = abs(value)
    factor = 10**scale
    whole, fraction = divmod(absolute, factor)
    return f"{'-' if negative else ''}{whole}.{fraction:0{scale}d}"


def normalize_record(row: Mapping[str, Any]) -> NormalizedRow:
    source_id = int(row["id"])
    day = normalize_day(row["day"])
    raw_org = {
        field: row[field]
        for field in (*COMMON_ORG_FIELDS, *ORG_NAME_FIELDS)
    }
    normalized_org = {
        field: normalize_optional(row[field]) for field in COMMON_ORG_FIELDS
    }
    organization_unmapped = any(value is None for value in normalized_org.values())
    if organization_unmapped:
        normalized_org = {field: UNMAPPED_ORG for field in COMMON_ORG_FIELDS}

    payload: dict[str, Any] = {"day": day, **normalized_org}
    for field in OUTER_FIELDS:
        payload[field] = normalize_optional(row[field])
    if organization_unmapped:
        for field in ORG_NAME_FIELDS:
            payload[field] = UNMAPPED_ORG

    raw_category = row["category"]
    category_unmapped = payload["category"] is None
    if category_unmapped:
        payload["category"] = UNMAPPED_CATEGORY

    scaled_metrics: dict[str, int] = {}
    for metric, scale in METRIC_SCALES.items():
        scaled = decimal_to_scaled(row[metric], scale)
        scaled_metrics[metric] = scaled
        payload[metric] = format_scaled(scaled, scale)

    return NormalizedRow(
        source_id=source_id,
        payload=payload,
        scaled_metrics=scaled_metrics,
        organization_unmapped=organization_unmapped,
        category_unmapped=category_unmapped,
        raw_organization=raw_org,
        raw_category=raw_category,
    )

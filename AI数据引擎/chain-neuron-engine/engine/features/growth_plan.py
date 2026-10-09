# -*- coding: utf-8 -*-
"""Build the physical wisdom-tree growth plan from reviewed dataset metadata.

The registry remains the semantic contract for queryable fields.  The physical
tree deliberately materializes a smaller, stable path: contiguous time levels
form the trunk and low-cardinality business dimensions form recursive branches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from engine.storage.dataset_registry import DatasetSpec, registry


TIME_LEVEL_NAMES = frozenset({
    "time", "date", "day", "week", "weekofyear", "month", "yyyymm",
    "year", "season", "quarter", "period", "datetime", "timestamp",
})

_BRANCH_SEMANTIC_TYPES = frozenset({
    "org_code", "org_name", "location_code", "location_name",
    "category", "product_code", "product_name", "channel", "payment",
})

_EXCLUDED_TOKENS = (
    "order_id", "trade_no", "billno", "phone", "mobile", "address",
    "receiver", "user_id", "json", "remark", "abstract", "coupon_no",
    "delivery_order", "creator_id", "modify_id", "cancel_id", "pickup_id",
)

_BUSINESS_TOKENS = (
    "org", "company", "unit", "branch", "area", "province", "city",
    "county", "country", "store", "station", "oucode", "ou_code",
    "category", "product", "gascode", "gas_code", "channel", "pay_mode",
)

_HIERARCHY_PRIORITY = (
    ("bigarea", 10),
    ("big_area", 10),
    ("province", 20),
    ("city", 30),
    ("branch", 40),
    ("county", 50),
    ("country", 50),
    ("area", 55),
    ("company", 60),
    ("unit", 65),
    ("oucode", 70),
    ("ou_code", 70),
    ("store", 75),
    ("station", 75),
    ("category", 90),
    ("product_type", 95),
    ("product", 100),
    ("gascode", 105),
    ("gas_code", 105),
    ("channel", 120),
    ("pay_mode", 130),
)

MAX_AUTO_BRANCH_DEPTH = 8


@dataclass(frozen=True)
class GrowthPlan:
    dataset: str
    trunk_levels: tuple[str, ...]
    branch_levels: tuple[str, ...]
    source: str

    @property
    def physical_levels(self) -> tuple[str, ...]:
        return (*self.trunk_levels, *self.branch_levels)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "source": self.source,
            "trunk_levels": list(self.trunk_levels),
            "branch_levels": list(self.branch_levels),
            "physical_levels": list(self.physical_levels),
        }


def _normalized(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _time_prefix(spec: DatasetSpec) -> tuple[str, ...]:
    levels: list[str] = []
    time_format = _normalized(spec.time_format)
    has_day = "d" in time_format
    for level in spec.levels:
        normalized = _normalized(level)
        if normalized not in TIME_LEVEL_NAMES:
            break
        # 月粒度声明不能把 week/day 伪造成真实物理时间层；否则空值会
        # 截断后续组织分枝。日粒度数据仍保留周、日等派生层。
        if normalized in {"week", "weekofyear", "day", "date"} and not has_day:
            break
        levels.append(level)
    return tuple(levels)


def _feature_metadata(spec: DatasetSpec) -> dict[str, dict[str, Any]]:
    raw = spec.capabilities.get("feature_layers")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for layer in ("base", "trunk", "branch"):
        for item in raw.get(layer) or []:
            if not isinstance(item, dict):
                continue
            for key in (item.get("name"), item.get("source"), item.get("label")):
                normalized = _normalized(key)
                if normalized:
                    result.setdefault(normalized, item)
    return result


def _metadata_for_level(
    spec: DatasetSpec,
    metadata: dict[str, dict[str, Any]],
    level: str,
) -> dict[str, Any]:
    value_key = spec.level_value_key.get(level, level)
    source = spec.column_sources.get(value_key, value_key)
    for key in (level, value_key, source):
        item = metadata.get(_normalized(key))
        if item is not None:
            return item
    return {}


def _is_high_cardinality_identifier(name: str) -> bool:
    return any(token in name for token in _EXCLUDED_TOKENS)


def _is_branch_candidate(name: str, item: dict[str, Any]) -> bool:
    if _is_high_cardinality_identifier(name):
        return False
    role = _normalized(item.get("role"))
    semantic_type = _normalized(item.get("semantic_type"))
    if role in {"metric", "time", "ignore"}:
        return False
    if semantic_type in _BRANCH_SEMANTIC_TYPES:
        return True
    return any(token in name for token in _BUSINESS_TOKENS)


def _priority(name: str, item: dict[str, Any], original_index: int) -> tuple[int, int, int]:
    hierarchy = 200
    for token, value in _HIERARCHY_PRIORITY:
        if token in name:
            hierarchy = value
            break
    semantic_type = _normalized(item.get("semantic_type"))
    semantic = {
        "org_code": 0,
        "location_code": 1,
        "org_name": 2,
        "location_name": 3,
        "category": 4,
        "product_code": 5,
        "product_name": 6,
        "channel": 7,
        "payment": 8,
    }.get(semantic_type, 20)
    return hierarchy, semantic, original_index


def _dedupe_key(name: str) -> str:
    value = name
    value = re.sub(r"(?:_?ou)?_?(?:code|name|id)$", "", value)
    value = re.sub(r"_org$", "", value)
    value = re.sub(r"_(?:code|name|id)$", "", value)
    return value or name


def _configured_dimensions(spec: DatasetSpec, non_time: list[str]) -> tuple[str, ...] | None:
    raw = spec.capabilities.get("growth")
    if not isinstance(raw, dict) or not isinstance(raw.get("dimensions"), list):
        return None
    by_name = {_normalized(level): level for level in non_time}
    selected: list[str] = []
    for item in raw["dimensions"]:
        level = by_name.get(_normalized(item))
        if level and level not in selected:
            selected.append(level)
    return tuple(selected[:MAX_AUTO_BRANCH_DEPTH])


def build_growth_plan(spec: DatasetSpec) -> GrowthPlan:
    trunk = _time_prefix(spec)
    non_time = list(spec.levels[len(trunk):])
    configured = _configured_dimensions(spec, non_time)
    if configured is not None:
        return GrowthPlan(spec.id, trunk, configured, "declared")

    metadata = _feature_metadata(spec)
    candidates: list[tuple[tuple[int, int, int], str, str]] = []
    for index, level in enumerate(non_time):
        normalized = _normalized(level)
        item = _metadata_for_level(spec, metadata, level)
        if not _is_branch_candidate(normalized, item):
            continue
        candidates.append((_priority(normalized, item, index), level, _dedupe_key(normalized)))

    candidates.sort(key=lambda item: item[0])
    selected: list[str] = []
    seen_hierarchies: set[str] = set()
    for _, level, hierarchy_key in candidates:
        if hierarchy_key in seen_hierarchies:
            continue
        seen_hierarchies.add(hierarchy_key)
        selected.append(level)
        if len(selected) >= MAX_AUTO_BRANCH_DEPTH:
            break

    return GrowthPlan(spec.id, trunk, tuple(selected), "metadata_inferred")


def growth_plan_for_dataset(dataset: str) -> GrowthPlan:
    return build_growth_plan(registry.require(dataset))


def physical_levels_for_dataset(dataset: str) -> tuple[str, ...]:
    return growth_plan_for_dataset(dataset).physical_levels


def time_prefix_count(dataset: str) -> int:
    return len(growth_plan_for_dataset(dataset).trunk_levels)

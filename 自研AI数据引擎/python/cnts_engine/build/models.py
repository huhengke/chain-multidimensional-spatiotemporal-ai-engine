from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATASET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_TIME_FORMATS = {"iso_day", "iso_month", "d/m/yyyy"}


def _identifier(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"{name} is not a safe SQL identifier: {text!r}")
    return text


def _dataset_id(value: Any) -> str:
    text = str(value or "").strip()
    if not _DATASET_RE.fullmatch(text):
        raise ValueError(f"invalid dataset id: {text!r}")
    return text


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    name: str
    scale: int
    unit: str


@dataclass(frozen=True, slots=True)
class FactSchema:
    dataset_id: str
    time_field: str
    common_dimensions: tuple[str, ...]
    outer_dimensions: tuple[str, ...]
    growth_dimensions: tuple[str, ...]
    metrics: tuple[MetricDefinition, ...]
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> "FactSchema":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("fact schema must be a JSON object")
        metrics = tuple(
            MetricDefinition(
                name=_identifier(item.get("name"), "metric name"),
                scale=int(item.get("scale")),
                unit=str(item.get("unit") or ""),
            )
            for item in raw.get("metrics", [])
        )
        schema = cls(
            dataset_id=_dataset_id(raw.get("dataset_id")),
            time_field=_identifier(raw.get("time_field"), "time field"),
            common_dimensions=tuple(
                _identifier(item, "common dimension")
                for item in raw.get("common_dimensions", [])
            ),
            outer_dimensions=tuple(
                _identifier(item, "outer dimension")
                for item in raw.get("outer_dimensions", [])
            ),
            growth_dimensions=tuple(
                _identifier(item, "growth dimension")
                for item in raw.get("growth_dimensions", [])
            ),
            metrics=metrics,
            raw=raw,
        )
        schema.validate()
        return schema

    @property
    def dimensions(self) -> tuple[str, ...]:
        return self.common_dimensions + self.outer_dimensions

    @property
    def metric_scales(self) -> dict[str, int]:
        return {metric.name: metric.scale for metric in self.metrics}

    def validate(self) -> None:
        common = set(self.common_dimensions)
        outer = set(self.outer_dimensions)
        metric_names = {metric.name for metric in self.metrics}
        if not self.metrics:
            raise ValueError("fact schema must declare metrics")
        if len(common) != len(self.common_dimensions):
            raise ValueError("common dimensions contain duplicates")
        if len(outer) != len(self.outer_dimensions):
            raise ValueError("outer dimensions contain duplicates")
        if len(metric_names) != len(self.metrics):
            raise ValueError("metrics contain duplicates")
        if len(set(self.growth_dimensions)) != len(self.growth_dimensions):
            raise ValueError("growth dimensions contain duplicates")
        if common & outer or common & metric_names or outer & metric_names:
            raise ValueError("schema roles overlap")
        if self.time_field in common | outer | metric_names:
            raise ValueError("time field must not overlap dimensions or metrics")
        if not set(self.growth_dimensions).issubset(common):
            raise ValueError("growth dimensions must be common dimensions")
        if any(metric.scale < 0 or metric.scale > 12 for metric in self.metrics):
            raise ValueError("metric scale must be between 0 and 12")

    def validate_binding(self, binding: "SourceBinding") -> None:
        if binding.kind != "fact" or binding.dataset_id != self.dataset_id:
            raise ValueError("fact schema and source binding do not match")
        common = set(self.common_dimensions)
        for group in binding.coherence_groups:
            unknown = sorted(set(group) - common)
            if unknown:
                raise ValueError(
                    "coherence group fields must be common dimensions: "
                    + ", ".join(unknown)
                )


@dataclass(frozen=True, slots=True)
class SourceBinding:
    dataset_id: str
    kind: Literal["fact", "dimension"]
    table: str
    key_column: str
    time_column: str | None = None
    time_format: str | None = None
    time_granularity: Literal["none", "day", "month"] = "none"
    coherence_groups: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SourceBinding":
        dataset_id = _dataset_id(raw.get("dataset_id"))
        kind = str(raw.get("kind") or "")
        if kind not in {"fact", "dimension"}:
            raise ValueError(f"invalid source kind for {dataset_id}: {kind}")
        time_column = raw.get("time_column")
        time_format = raw.get("time_format")
        granularity = str(raw.get("time_granularity") or "none")
        if granularity not in {"none", "day", "month"}:
            raise ValueError(f"invalid time granularity for {dataset_id}: {granularity}")
        if kind == "fact":
            time_column = _identifier(time_column, "time column")
            if time_format not in _TIME_FORMATS:
                raise ValueError(f"invalid source time format for {dataset_id}: {time_format}")
            if granularity == "none":
                raise ValueError(f"fact source {dataset_id} requires time granularity")
        else:
            time_column = None
            time_format = None
            granularity = "none"
        groups = tuple(
            tuple(_identifier(field, "coherence field") for field in group)
            for group in raw.get("coherence_groups", [])
        )
        return cls(
            dataset_id=dataset_id,
            kind=kind,  # type: ignore[arg-type]
            table=_identifier(raw.get("table"), "table"),
            key_column=_identifier(raw.get("key_column"), "key column"),
            time_column=time_column,
            time_format=time_format,
            time_granularity=granularity,  # type: ignore[arg-type]
            coherence_groups=groups,
        )


def load_source_bindings(path: str | Path) -> dict[str, SourceBinding]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or int(raw.get("contract_version", 0)) != 1:
        raise ValueError("source binding contract_version must be 1")
    result: dict[str, SourceBinding] = {}
    for item in raw.get("datasets", []):
        if not isinstance(item, dict):
            raise ValueError("source binding entry must be an object")
        binding = SourceBinding.from_dict(item)
        if binding.dataset_id in result:
            raise ValueError(f"duplicate source binding: {binding.dataset_id}")
        result[binding.dataset_id] = binding
    return result

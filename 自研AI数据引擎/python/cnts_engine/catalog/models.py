from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping


REQUIRED_DATASET_KINDS: dict[str, str] = {
    "oil_master": "dimension",
    "non_oil_sales": "fact",
    "oil_sales_plan": "fact",
    "oil_sales_daily": "fact",
    "oil_monthly_sales": "fact",
}

REQUIRED_ASSETS = frozenset(
    {
        "foundation_features",
        "organization_bridge",
        "time_rules",
        "experience_library",
        "query_contract",
        "topology_schema",
    }
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIME_GRANULARITIES = {"none", "day", "month"}


class CatalogModelError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogModelError(f"{name} must be an object")
    return {str(key): item for key, item in value.items()}


def _text(value: Any, name: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CatalogModelError(f"{name} must not be empty")
    return result


def _identifier(value: Any, name: str) -> str:
    result = _text(value, name)
    if not _ID_RE.fullmatch(result):
        raise CatalogModelError(f"{name} has an invalid identifier: {result}")
    return result


def _sha256(value: Any, name: str) -> str:
    result = _text(value, name).lower()
    if not _SHA256_RE.fullmatch(result):
        raise CatalogModelError(f"{name} must be a lowercase SHA-256 hex digest")
    return result


def _hex_id(value: Any, name: str) -> str:
    result = _text(value, name).lower()
    if len(result) < 16 or len(result) > 128 or not _HEX_RE.fullmatch(result):
        raise CatalogModelError(f"{name} must be a hexadecimal identifier")
    return result


def _relative_path(value: Any, name: str) -> str:
    raw = _text(value, name).replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise CatalogModelError(f"{name} must be a contained relative path")
    if ":" in path.parts[0]:
        raise CatalogModelError(f"{name} must not contain a drive prefix")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: str
    end: str

    def __post_init__(self) -> None:
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
        except ValueError as exc:
            raise CatalogModelError("time_range must use YYYY-MM-DD dates") from exc
        if start > end:
            raise CatalogModelError("time_range.start must not be after time_range.end")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TimeRange":
        data = _mapping(raw, "time_range")
        return cls(
            start=_text(data.get("start"), "time_range.start"),
            end=_text(data.get("end"), "time_range.end"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"start": self.start, "end": self.end}


@dataclass(frozen=True, slots=True)
class CatalogAsset:
    asset_id: str
    relative_path: str
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _identifier(self.asset_id, "asset_id"))
        object.__setattr__(
            self, "relative_path", _relative_path(self.relative_path, "asset.relative_path")
        )
        object.__setattr__(
            self, "content_hash", _sha256(self.content_hash, "asset.content_hash")
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogAsset":
        data = _mapping(raw, "asset")
        return cls(
            asset_id=data.get("asset_id", ""),
            relative_path=data.get("relative_path", ""),
            content_hash=data.get("content_hash", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True, slots=True)
class CatalogDataset:
    logical_id: str
    kind: Literal["fact", "dimension"]
    relative_path: str
    version_id: str
    schema_id: str
    schema_hash: str
    manifest_hash: str
    row_count: int
    source_fingerprint: str
    time_granularity: Literal["none", "day", "month"] = "none"
    time_range: TimeRange | None = None
    metric_controls: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_id", _identifier(self.logical_id, "logical_id"))
        if self.kind not in {"fact", "dimension"}:
            raise CatalogModelError(f"invalid dataset kind: {self.kind}")
        object.__setattr__(
            self,
            "relative_path",
            _relative_path(self.relative_path, f"datasets.{self.logical_id}.relative_path"),
        )
        object.__setattr__(self, "version_id", _text(self.version_id, "version_id"))
        object.__setattr__(self, "schema_id", _hex_id(self.schema_id, "schema_id"))
        object.__setattr__(self, "schema_hash", _sha256(self.schema_hash, "schema_hash"))
        object.__setattr__(self, "manifest_hash", _sha256(self.manifest_hash, "manifest_hash"))
        object.__setattr__(
            self,
            "source_fingerprint",
            _sha256(self.source_fingerprint, "source_fingerprint"),
        )
        if isinstance(self.row_count, bool) or int(self.row_count) < 0:
            raise CatalogModelError("row_count must be a non-negative integer")
        object.__setattr__(self, "row_count", int(self.row_count))
        if self.time_granularity not in _TIME_GRANULARITIES:
            raise CatalogModelError(f"invalid time_granularity: {self.time_granularity}")

        controls: dict[str, str] = {}
        for name, raw_value in sorted(self.metric_controls.items()):
            metric = _text(name, "metric control name")
            value = _text(raw_value, f"metric_controls.{metric}")
            try:
                Decimal(value)
            except InvalidOperation as exc:
                raise CatalogModelError(
                    f"metric_controls.{metric} must be an exact decimal string"
                ) from exc
            controls[metric] = value
        object.__setattr__(self, "metric_controls", controls)

        if self.kind == "fact":
            if self.time_granularity == "none" or self.time_range is None:
                raise CatalogModelError("fact datasets require a time range and granularity")
            if not controls:
                raise CatalogModelError("fact datasets require metric controls")
        else:
            if self.time_granularity != "none" or self.time_range is not None:
                raise CatalogModelError("dimension datasets cannot declare a time range")
            if controls:
                raise CatalogModelError("dimension datasets cannot declare metric controls")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogDataset":
        data = _mapping(raw, "dataset")
        raw_range = data.get("time_range")
        return cls(
            logical_id=data.get("logical_id", ""),
            kind=str(data.get("kind", "")),  # type: ignore[arg-type]
            relative_path=data.get("relative_path", ""),
            version_id=data.get("version_id", ""),
            schema_id=data.get("schema_id", ""),
            schema_hash=data.get("schema_hash", ""),
            manifest_hash=data.get("manifest_hash", ""),
            row_count=data.get("row_count", -1),
            source_fingerprint=data.get("source_fingerprint", ""),
            time_granularity=str(data.get("time_granularity", "none")),  # type: ignore[arg-type]
            time_range=TimeRange.from_dict(raw_range) if raw_range is not None else None,
            metric_controls={
                str(key): str(value)
                for key, value in _mapping(
                    data.get("metric_controls", {}), "metric_controls"
                ).items()
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_id": self.logical_id,
            "kind": self.kind,
            "relative_path": self.relative_path,
            "version_id": self.version_id,
            "schema_id": self.schema_id,
            "schema_hash": self.schema_hash,
            "manifest_hash": self.manifest_hash,
            "row_count": self.row_count,
            "source_fingerprint": self.source_fingerprint,
            "time_granularity": self.time_granularity,
            "time_range": self.time_range.to_dict() if self.time_range else None,
            "metric_controls": dict(self.metric_controls),
        }


@dataclass(frozen=True, slots=True)
class CatalogManifest:
    catalog_id: str
    contract_version: int
    created_at: str
    datasets: dict[str, CatalogDataset]
    assets: dict[str, CatalogAsset]
    catalog_hash: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "catalog_id", _identifier(self.catalog_id, "catalog_id"))
        if isinstance(self.contract_version, bool) or int(self.contract_version) < 1:
            raise CatalogModelError("contract_version must be a positive integer")
        object.__setattr__(self, "contract_version", int(self.contract_version))
        try:
            datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CatalogModelError("created_at must be an ISO-8601 timestamp") from exc

        datasets = dict(self.datasets)
        for logical_id, dataset in datasets.items():
            if logical_id != dataset.logical_id:
                raise CatalogModelError(
                    f"dataset key {logical_id} does not match {dataset.logical_id}"
                )
        object.__setattr__(self, "datasets", datasets)
        object.__setattr__(self, "assets", dict(self.assets))
        if self.catalog_hash:
            object.__setattr__(
                self, "catalog_hash", _sha256(self.catalog_hash, "catalog_hash")
            )

    @classmethod
    def create(
        cls,
        *,
        catalog_id: str,
        datasets: Mapping[str, CatalogDataset],
        assets: Mapping[str, CatalogAsset],
        contract_version: int = 1,
        created_at: str | None = None,
    ) -> "CatalogManifest":
        draft = cls(
            catalog_id=catalog_id,
            contract_version=contract_version,
            created_at=created_at or utc_now(),
            datasets=dict(datasets),
            assets=dict(assets),
        )
        return cls(**{**draft.__dict_for_copy(), "catalog_hash": draft.computed_hash()})

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogManifest":
        data = _mapping(raw, "catalog")
        datasets = {
            str(key): CatalogDataset.from_dict(_mapping(value, f"datasets.{key}"))
            for key, value in _mapping(data.get("datasets", {}), "datasets").items()
        }
        assets = {
            str(key): CatalogAsset.from_dict(_mapping(value, f"assets.{key}"))
            for key, value in _mapping(data.get("assets", {}), "assets").items()
        }
        manifest = cls(
            catalog_id=data.get("catalog_id", ""),
            contract_version=data.get("contract_version", 0),
            created_at=data.get("created_at", ""),
            datasets=datasets,
            assets=assets,
            catalog_hash=data.get("catalog_hash", ""),
        )
        if not manifest.catalog_hash:
            raise CatalogModelError("catalog_hash must not be empty")
        if manifest.catalog_hash != manifest.computed_hash():
            raise CatalogModelError("catalog_hash does not match catalog contents")
        return manifest

    def __dict_for_copy(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "contract_version": self.contract_version,
            "created_at": self.created_at,
            "datasets": self.datasets,
            "assets": self.assets,
        }

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload = {
            "catalog_id": self.catalog_id,
            "contract_version": self.contract_version,
            "created_at": self.created_at,
            "datasets": {
                key: value.to_dict() for key, value in sorted(self.datasets.items())
            },
            "assets": {
                key: value.to_dict() for key, value in sorted(self.assets.items())
            },
        }
        if include_hash:
            payload["catalog_hash"] = self.catalog_hash
        return payload

    def computed_hash(self) -> str:
        return canonical_hash(self.to_dict(include_hash=False))

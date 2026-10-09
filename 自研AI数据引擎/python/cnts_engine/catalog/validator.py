from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import (
    REQUIRED_ASSETS,
    REQUIRED_DATASET_KINDS,
    CatalogDataset,
    CatalogManifest,
    canonical_hash,
    sha256_file,
)


class CatalogValidationError(ValueError):
    pass


FactVerifier = Callable[[Path, str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class CatalogValidationReport:
    catalog_id: str
    catalog_hash: str
    datasets: tuple[str, ...]
    assets: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "catalog_id": self.catalog_id,
            "catalog_hash": self.catalog_hash,
            "datasets": list(self.datasets),
            "assets": list(self.assets),
        }


class CatalogValidator:
    def __init__(
        self,
        data_root: str | Path,
        *,
        fact_verifier: FactVerifier | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.fact_verifier = fact_verifier or self._verify_fact_native

    def validate(self, catalog: CatalogManifest) -> CatalogValidationReport:
        if catalog.catalog_hash != catalog.computed_hash():
            raise CatalogValidationError("catalog hash does not match catalog contents")

        missing = sorted(set(REQUIRED_DATASET_KINDS) - set(catalog.datasets))
        if missing:
            raise CatalogValidationError(
                f"catalog is missing required datasets: {', '.join(missing)}"
            )
        for logical_id, expected_kind in REQUIRED_DATASET_KINDS.items():
            actual = catalog.datasets[logical_id]
            if actual.kind != expected_kind:
                raise CatalogValidationError(
                    f"dataset {logical_id} must be {expected_kind}, got {actual.kind}"
                )

        missing_assets = sorted(REQUIRED_ASSETS - set(catalog.assets))
        if missing_assets:
            raise CatalogValidationError(
                f"catalog is missing required assets: {', '.join(missing_assets)}"
            )

        for name, asset in sorted(catalog.assets.items()):
            path = self._resolve(asset.relative_path)
            if not path.is_file():
                raise CatalogValidationError(f"asset {name} is not a readable file: {path}")
            actual_hash = sha256_file(path)
            if actual_hash != asset.content_hash:
                raise CatalogValidationError(
                    f"asset {name} hash mismatch: expected {asset.content_hash}, got {actual_hash}"
                )

        for logical_id, dataset in sorted(catalog.datasets.items()):
            if dataset.kind == "fact":
                self._validate_fact(dataset)
            else:
                self._validate_dimension(dataset)

        return CatalogValidationReport(
            catalog_id=catalog.catalog_id,
            catalog_hash=catalog.catalog_hash,
            datasets=tuple(sorted(catalog.datasets)),
            assets=tuple(sorted(catalog.assets)),
        )

    def _validate_fact(self, dataset: CatalogDataset) -> None:
        root = self._resolve(dataset.relative_path)
        if not root.is_dir():
            raise CatalogValidationError(
                f"fact dataset {dataset.logical_id} root does not exist: {root}"
            )
        try:
            manifest = dict(self.fact_verifier(root, dataset.version_id))
        except Exception as exc:
            raise CatalogValidationError(
                f"fact dataset {dataset.logical_id} generation verification failed: {exc}"
            ) from exc

        expected = {
            "dataset_id": dataset.logical_id,
            "generation_id": dataset.version_id,
            "schema_id": dataset.schema_id,
            "row_count": dataset.row_count,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise CatalogValidationError(
                    f"fact dataset {dataset.logical_id} {key} mismatch: "
                    f"expected {value!r}, got {manifest.get(key)!r}"
                )
        schema = manifest.get("schema")
        if not isinstance(schema, Mapping) or canonical_hash(schema) != dataset.schema_hash:
            raise CatalogValidationError(
                f"fact dataset {dataset.logical_id} schema hash mismatch"
            )

        provenance_path = root / "provenance" / f"{dataset.version_id}.json"
        self._validate_provenance(dataset, provenance_path)

    def _validate_dimension(self, dataset: CatalogDataset) -> None:
        root = self._resolve(dataset.relative_path)
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise CatalogValidationError(
                f"dimension dataset {dataset.logical_id} manifest is missing: {manifest_path}"
            )
        if sha256_file(manifest_path) != dataset.manifest_hash:
            raise CatalogValidationError(
                f"dimension dataset {dataset.logical_id} manifest hash mismatch"
            )
        manifest = self._read_json(manifest_path, f"dimension {dataset.logical_id}")
        expected = {
            "dataset_id": dataset.logical_id,
            "asset_id": dataset.version_id,
            "schema_id": dataset.schema_id,
            "row_count": dataset.row_count,
            "source_fingerprint": dataset.source_fingerprint,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise CatalogValidationError(
                    f"dimension dataset {dataset.logical_id} {key} mismatch: "
                    f"expected {value!r}, got {manifest.get(key)!r}"
                )
        schema = manifest.get("schema")
        if not isinstance(schema, Mapping) or canonical_hash(schema) != dataset.schema_hash:
            raise CatalogValidationError(
                f"dimension dataset {dataset.logical_id} schema hash mismatch"
            )
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise CatalogValidationError(
                f"dimension dataset {dataset.logical_id} must declare data files"
            )
        for raw_file in files:
            if not isinstance(raw_file, Mapping):
                raise CatalogValidationError("dimension file entry must be an object")
            name = str(raw_file.get("name") or "")
            path = self._resolve_child(root, name)
            if not path.is_file():
                raise CatalogValidationError(f"dimension data file is missing: {path}")
            if path.stat().st_size != int(raw_file.get("bytes", -1)):
                raise CatalogValidationError(f"dimension data file size mismatch: {path}")
            if sha256_file(path) != str(raw_file.get("sha256") or ""):
                raise CatalogValidationError(f"dimension data file hash mismatch: {path}")

    def _validate_provenance(self, dataset: CatalogDataset, path: Path) -> None:
        if not path.is_file():
            raise CatalogValidationError(
                f"fact dataset {dataset.logical_id} provenance is missing: {path}"
            )
        if sha256_file(path) != dataset.manifest_hash:
            raise CatalogValidationError(
                f"fact dataset {dataset.logical_id} provenance hash mismatch"
            )
        data = self._read_json(path, f"fact provenance {dataset.logical_id}")
        expected = {
            "dataset_id": dataset.logical_id,
            "generation_id": dataset.version_id,
            "schema_id": dataset.schema_id,
            "schema_hash": dataset.schema_hash,
            "row_count": dataset.row_count,
            "source_fingerprint": dataset.source_fingerprint,
            "time_granularity": dataset.time_granularity,
            "time_range": dataset.time_range.to_dict() if dataset.time_range else None,
            "metric_controls": dataset.metric_controls,
        }
        for key, value in expected.items():
            if data.get(key) != value:
                raise CatalogValidationError(
                    f"fact dataset {dataset.logical_id} provenance {key} mismatch"
                )

    def _resolve(self, relative_path: str) -> Path:
        path = (self.data_root / Path(relative_path)).resolve()
        if path != self.data_root and self.data_root not in path.parents:
            raise CatalogValidationError(f"path escapes catalog data root: {relative_path}")
        return path

    @staticmethod
    def _resolve_child(root: Path, name: str) -> Path:
        raw = Path(name)
        if raw.is_absolute() or not name or ".." in raw.parts:
            raise CatalogValidationError(f"invalid dimension data file path: {name}")
        path = (root / raw).resolve()
        if root != path and root not in path.parents:
            raise CatalogValidationError(f"dimension data file escapes asset root: {name}")
        return path

    @staticmethod
    def _read_json(path: Path, label: str) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogValidationError(f"cannot read {label}: {exc}") from exc
        if not isinstance(data, dict):
            raise CatalogValidationError(f"{label} must be a JSON object")
        return data

    @staticmethod
    def _verify_fact_native(root: Path, generation_id: str) -> Mapping[str, Any]:
        from cnts_engine.client import CntsClient

        return CntsClient(root).verify_generation(generation_id)

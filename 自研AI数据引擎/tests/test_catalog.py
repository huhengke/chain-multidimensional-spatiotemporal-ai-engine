from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cnts_engine.catalog import (
    REQUIRED_ASSETS,
    CatalogAsset,
    CatalogConflictError,
    CatalogDataset,
    CatalogManifest,
    CatalogModelError,
    CatalogStore,
    CatalogValidationError,
    CatalogValidator,
    TimeRange,
    canonical_hash,
    sha256_file,
)


FACT_DATASETS = (
    "non_oil_sales",
    "oil_sales_plan",
    "oil_sales_daily",
    "oil_monthly_sales",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _build_complete_catalog(tmp_path: Path):
    data_root = tmp_path / "data"
    native_manifests: dict[tuple[str, str], dict[str, Any]] = {}
    datasets: dict[str, CatalogDataset] = {}

    dimension_root = data_root / "datasets" / "oil_master" / "assets" / "oil-master-v1"
    records_path = dimension_root / "records.jsonl"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text('{"material_code":"60518726"}\n', encoding="utf-8")
    dimension_schema = {
        "dataset_id": "oil_master",
        "schema_major": 1,
        "primary_key": "material_code",
        "columns": ["type_code", "material_code", "material_name"],
    }
    dimension_source = canonical_hash({"source": "oil-master-fixture"})
    dimension_manifest = {
        "dataset_id": "oil_master",
        "asset_id": "oil-master-v1",
        "schema_id": "a" * 32,
        "schema": dimension_schema,
        "row_count": 1,
        "source_fingerprint": dimension_source,
        "files": [
            {
                "name": "records.jsonl",
                "bytes": records_path.stat().st_size,
                "sha256": sha256_file(records_path),
            }
        ],
    }
    dimension_manifest_path = dimension_root / "manifest.json"
    _write_json(dimension_manifest_path, dimension_manifest)
    datasets["oil_master"] = CatalogDataset(
        logical_id="oil_master",
        kind="dimension",
        relative_path="datasets/oil_master/assets/oil-master-v1",
        version_id="oil-master-v1",
        schema_id="a" * 32,
        schema_hash=canonical_hash(dimension_schema),
        manifest_hash=sha256_file(dimension_manifest_path),
        row_count=1,
        source_fingerprint=dimension_source,
    )

    for index, logical_id in enumerate(FACT_DATASETS, start=1):
        version_id = f"00000000-0000-4000-8000-{index:012d}"
        schema = {
            "dataset_id": logical_id,
            "schema_major": 1,
            "time_field": "day",
            "time_format": "%Y-%m-%d",
            "common_dimensions": ["company"],
            "outer_dimensions": ["category"],
            "growth_dimensions": ["company"],
            "growth_threshold": 1000,
            "metrics": [{"name": "amount", "scale": 2, "unit": "CNY"}],
        }
        source_fingerprint = canonical_hash({"source": logical_id})
        dataset_root = data_root / "datasets" / logical_id
        (dataset_root / "generations" / version_id).mkdir(parents=True)
        native_manifests[(str(dataset_root.resolve()), version_id)] = {
            "dataset_id": logical_id,
            "generation_id": version_id,
            "schema_id": f"{index:b}".rjust(32, "0"),
            "row_count": index * 100,
            "schema": schema,
        }
        granularity = "day" if logical_id in {"non_oil_sales", "oil_sales_daily"} else "month"
        controls = {"amount": f"{index * 1000}.00"}
        provenance = {
            "dataset_id": logical_id,
            "generation_id": version_id,
            "schema_id": f"{index:b}".rjust(32, "0"),
            "schema_hash": canonical_hash(schema),
            "row_count": index * 100,
            "source_fingerprint": source_fingerprint,
            "time_granularity": granularity,
            "time_range": {"start": "2026-01-01", "end": "2026-09-30"},
            "metric_controls": controls,
        }
        provenance_path = dataset_root / "provenance" / f"{version_id}.json"
        _write_json(provenance_path, provenance)
        datasets[logical_id] = CatalogDataset(
            logical_id=logical_id,
            kind="fact",
            relative_path=f"datasets/{logical_id}",
            version_id=version_id,
            schema_id=f"{index:b}".rjust(32, "0"),
            schema_hash=canonical_hash(schema),
            manifest_hash=sha256_file(provenance_path),
            row_count=index * 100,
            source_fingerprint=source_fingerprint,
            time_granularity=granularity,
            time_range=TimeRange("2026-01-01", "2026-09-30"),
            metric_controls=controls,
        )

    assets: dict[str, CatalogAsset] = {}
    for name in REQUIRED_ASSETS:
        path = data_root / "assets" / f"{name}.json"
        _write_json(path, {"asset": name, "version": 1})
        assets[name] = CatalogAsset(
            asset_id=f"{name}-v1",
            relative_path=f"assets/{name}.json",
            content_hash=sha256_file(path),
        )

    catalog = CatalogManifest.create(
        catalog_id="cnts-test-v1",
        created_at="2026-10-04T08:00:00+00:00",
        datasets=datasets,
        assets=assets,
    )

    def verify(root: Path, generation_id: str) -> dict[str, Any]:
        return native_manifests[(str(root.resolve()), generation_id)]

    return data_root, catalog, verify


def test_complete_catalog_validates_and_publishes_immutably(tmp_path: Path) -> None:
    data_root, catalog, verify = _build_complete_catalog(tmp_path)
    validator = CatalogValidator(data_root, fact_verifier=verify)
    report = validator.validate(catalog)

    assert report.datasets == tuple(sorted({"oil_master", *FACT_DATASETS}))
    assert report.catalog_hash == catalog.computed_hash()

    store = CatalogStore(tmp_path / "catalogs")
    store.publish(catalog, validator)
    loaded = store.load(catalog.catalog_id, expected_hash=catalog.catalog_hash)
    assert loaded == catalog
    assert store.publish(catalog, validator) == report

    changed = CatalogManifest.create(
        catalog_id=catalog.catalog_id,
        created_at="2026-10-04T09:00:00+00:00",
        datasets=catalog.datasets,
        assets=catalog.assets,
    )
    with pytest.raises(CatalogConflictError, match="immutable"):
        store.publish(changed, validator)


def test_missing_required_dataset_and_asset_are_rejected(tmp_path: Path) -> None:
    data_root, catalog, verify = _build_complete_catalog(tmp_path)
    validator = CatalogValidator(data_root, fact_verifier=verify)

    missing_dataset = CatalogManifest.create(
        catalog_id="missing-dataset",
        created_at=catalog.created_at,
        datasets={
            key: value for key, value in catalog.datasets.items() if key != "oil_sales_plan"
        },
        assets=catalog.assets,
    )
    with pytest.raises(CatalogValidationError, match="oil_sales_plan"):
        validator.validate(missing_dataset)

    missing_asset = CatalogManifest.create(
        catalog_id="missing-asset",
        created_at=catalog.created_at,
        datasets=catalog.datasets,
        assets={
            key: value for key, value in catalog.assets.items() if key != "time_rules"
        },
    )
    with pytest.raises(CatalogValidationError, match="time_rules"):
        validator.validate(missing_asset)


def test_generation_identity_and_provenance_are_pinned(tmp_path: Path) -> None:
    data_root, catalog, verify = _build_complete_catalog(tmp_path)
    target = catalog.datasets["oil_sales_daily"]

    def wrong_generation(root: Path, generation_id: str) -> dict[str, Any]:
        payload = dict(verify(root, generation_id))
        payload["generation_id"] = "11111111-1111-4111-8111-111111111111"
        return payload

    with pytest.raises(CatalogValidationError, match="generation_id mismatch"):
        CatalogValidator(data_root, fact_verifier=wrong_generation).validate(catalog)

    provenance = (
        data_root
        / target.relative_path
        / "provenance"
        / f"{target.version_id}.json"
    )
    provenance.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CatalogValidationError, match="provenance hash mismatch"):
        CatalogValidator(data_root, fact_verifier=verify).validate(catalog)


def test_dimension_file_corruption_is_rejected(tmp_path: Path) -> None:
    data_root, catalog, verify = _build_complete_catalog(tmp_path)
    records = (
        data_root
        / catalog.datasets["oil_master"].relative_path
        / "records.jsonl"
    )
    records.write_text('{"material_code":"tampered"}\n', encoding="utf-8")
    with pytest.raises(CatalogValidationError, match="data file (size|hash) mismatch"):
        CatalogValidator(data_root, fact_verifier=verify).validate(catalog)


def test_catalog_hash_tampering_and_path_escape_are_rejected(tmp_path: Path) -> None:
    _, catalog, _ = _build_complete_catalog(tmp_path)
    raw = catalog.to_dict()
    raw["datasets"]["oil_sales_daily"]["row_count"] += 1
    with pytest.raises(CatalogModelError, match="catalog_hash"):
        CatalogManifest.from_dict(raw)

    with pytest.raises(CatalogModelError, match="relative path"):
        replace(catalog.datasets["oil_sales_daily"], relative_path="../outside")

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cnts_engine.catalog import (
    REQUIRED_DATASET_KINDS,
    CatalogManifest,
    CatalogStore,
    CatalogValidator,
)

from .assets import publish_file_asset, publish_json_asset, publish_text_bundle_asset
from .models import load_source_bindings
from .runner import build_dimension_asset, build_fact_tree


FACT_SCHEMA_FILES = {
    "non_oil_sales": "non-oil-sales.json",
    "oil_sales_plan": "oil-sales-plan.json",
    "oil_sales_daily": "oil-sales-daily.json",
    "oil_monthly_sales": "oil-monthly-sales.json",
}


def build_catalog(
    *,
    legacy_engine_root: str | Path,
    project_root: str | Path,
    data_root: str | Path,
    catalog_id: str,
    cnts_binary: str | Path,
    chunk_size: int = 10_000,
) -> tuple[CatalogManifest, dict[str, Any]]:
    legacy_engine_root = Path(legacy_engine_root).resolve()
    project_root = Path(project_root).resolve()
    data_root = Path(data_root).resolve()
    database = legacy_engine_root / "data" / "engine.db"
    schemas = project_root / "schemas"
    bindings = load_source_bindings(schemas / "legacy-sqlite-bindings.json")
    missing_bindings = sorted(set(REQUIRED_DATASET_KINDS) - set(bindings))
    if missing_bindings:
        raise RuntimeError(
            "source bindings are incomplete: " + ", ".join(missing_bindings)
        )

    datasets = {}
    reports: dict[str, Any] = {}
    dimension = build_dimension_asset(
        database=database,
        schema_path=schemas / "oil-master.json",
        binding=bindings["oil_master"],
        data_root=data_root,
        chunk_size=chunk_size,
    )
    datasets["oil_master"] = dimension.entry
    reports["oil_master"] = {
        "kind": "dimension",
        "version_id": dimension.entry.version_id,
        "row_count": dimension.entry.row_count,
    }

    for logical_id, schema_name in FACT_SCHEMA_FILES.items():
        result = build_fact_tree(
            database=database,
            schema_path=schemas / schema_name,
            binding=bindings[logical_id],
            data_root=data_root,
            run_root=data_root / "runs",
            cnts_binary=cnts_binary,
            chunk_size=chunk_size,
        )
        datasets[logical_id] = result.entry
        reports[logical_id] = {
            "kind": "fact",
            "version_id": result.entry.version_id,
            "row_count": result.entry.row_count,
            "time_range": result.entry.time_range.to_dict()
            if result.entry.time_range
            else None,
            "metric_controls": result.entry.metric_controls,
            "quality_events": result.export.quality_event_count,
        }

    config_root = legacy_engine_root / "engine" / "config"
    assets = {
        "foundation_features": publish_file_asset(
            name="foundation_features",
            source=config_root / "foundation_features.yaml",
            data_root=data_root,
        ),
        "experience_library": publish_file_asset(
            name="experience_library",
            source=config_root / "ai_field_recommendations.yaml",
            data_root=data_root,
        ),
        "time_rules": publish_text_bundle_asset(
            name="time_rules",
            sources={
                "time_derivation_rules.yaml": config_root
                / "time_derivation_rules.yaml",
                "time_calendars.yaml": config_root / "time_calendars.yaml",
            },
            data_root=data_root,
        ),
    }
    for asset_name, file_name in {
        "organization_bridge": "organization-bridge.json",
        "query_contract": "query-contract.json",
        "topology_schema": "topology-contract.json",
    }.items():
        payload = json.loads((schemas / file_name).read_text(encoding="utf-8"))
        assets[asset_name] = publish_json_asset(
            name=asset_name,
            payload=payload,
            data_root=data_root,
        )

    catalog = CatalogManifest.create(
        catalog_id=catalog_id,
        datasets=datasets,
        assets=assets,
    )
    validator = CatalogValidator(data_root)
    validation = CatalogStore(data_root / "catalogs").publish(catalog, validator)
    return catalog, {
        "catalog": catalog.to_dict(),
        "validation": validation.to_dict(),
        "datasets": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and publish one immutable multi-dataset CNTS Catalog"
    )
    parser.add_argument("--legacy-engine-root", required=True)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--catalog-id", required=True)
    parser.add_argument("--cnts-binary", required=True)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    args = parser.parse_args()
    _, report = build_catalog(
        legacy_engine_root=args.legacy_engine_root,
        project_root=args.project_root,
        data_root=args.data_root,
        catalog_id=args.catalog_id,
        cnts_binary=args.cnts_binary,
        chunk_size=args.chunk_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

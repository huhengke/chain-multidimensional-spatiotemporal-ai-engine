from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from cnts_engine.build import (
    FactSchema,
    SourceBinding,
    SqliteTableSource,
    build_dimension_asset,
    build_fact_tree,
    export_fact_dataset,
    load_source_bindings,
)
from cnts_engine.build.normalize import UNMAPPED_VALUE
from cnts_engine.catalog import canonical_hash


ROOT = Path(__file__).parents[1]


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE fact_sales (
            id INTEGER PRIMARY KEY,
            period TEXT NOT NULL,
            company TEXT,
            station TEXT,
            category TEXT,
            amount TEXT NOT NULL
        )
        """
    )
    connection.executemany(
        "INSERT INTO fact_sales VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "2026-08", "HN", "S1", "01", "10.10"),
            (2, "2026-08", "HN", None, "02", "20"),
            (3, "2026-09", "HN", "S2", None, "-0.05"),
        ],
    )
    connection.execute(
        """
        CREATE TABLE dim_product (
            material_code TEXT PRIMARY KEY,
            material_name TEXT,
            type_code TEXT
        )
        """
    )
    connection.executemany(
        "INSERT INTO dim_product VALUES (?, ?, ?)",
        [("P1", "Product 1", "01"), ("P2", "Product 2", "02")],
    )
    connection.commit()
    connection.close()


def _fact_schema(path: Path) -> FactSchema:
    _write_json(
        path,
        {
            "dataset_id": "test_sales",
            "schema_major": 1,
            "time_field": "day",
            "time_format": "%Y-%m-%d",
            "common_dimensions": ["company", "station"],
            "outer_dimensions": ["category"],
            "growth_dimensions": ["company", "station"],
            "growth_threshold": 2,
            "metrics": [{"name": "amount", "scale": 2, "unit": "CNY"}],
        },
    )
    return FactSchema.load(path)


def _fact_binding() -> SourceBinding:
    return SourceBinding.from_dict(
        {
            "dataset_id": "test_sales",
            "kind": "fact",
            "table": "fact_sales",
            "key_column": "id",
            "time_column": "period",
            "time_format": "iso_month",
            "time_granularity": "month",
            "coherence_groups": [["company", "station"]],
        }
    )


@pytest.fixture()
def source_database(tmp_path: Path) -> Path:
    path = tmp_path / "source.db"
    _create_database(path)
    return path


def test_legacy_bindings_cover_the_complete_catalog() -> None:
    bindings = load_source_bindings(ROOT / "schemas" / "legacy-sqlite-bindings.json")
    assert set(bindings) == {
        "oil_master",
        "non_oil_sales",
        "oil_sales_plan",
        "oil_sales_daily",
        "oil_monthly_sales",
    }
    assert bindings["oil_sales_plan"].time_granularity == "month"
    assert bindings["oil_sales_daily"].time_granularity == "day"


def test_schema_rejects_unknown_coherence_fields(tmp_path: Path) -> None:
    schema = _fact_schema(tmp_path / "schema.json")
    binding = SourceBinding.from_dict(
        {
            "dataset_id": "test_sales",
            "kind": "fact",
            "table": "fact_sales",
            "key_column": "id",
            "time_column": "period",
            "time_format": "iso_month",
            "time_granularity": "month",
            "coherence_groups": [["company", "not_a_common_dimension"]],
        }
    )
    with pytest.raises(ValueError, match="coherence group fields"):
        schema.validate_binding(binding)


def test_generic_source_is_read_only_and_keyset_paginated(
    source_database: Path,
) -> None:
    binding = _fact_binding()
    with SqliteTableSource(
        source_database,
        binding,
        ("period", "company", "station", "category", "amount"),
    ) as source:
        assert [row["id"] for row in source.iter_rows(chunk_size=1)] == [1, 2, 3]
        with pytest.raises(sqlite3.OperationalError):
            source.connection.execute("CREATE TABLE forbidden (id INTEGER)")


def test_fact_export_normalizes_months_and_preserves_exact_controls(
    source_database: Path, tmp_path: Path
) -> None:
    schema = _fact_schema(tmp_path / "schema.json")
    result = export_fact_dataset(
        source_database,
        schema,
        _fact_binding(),
        tmp_path / "run",
        chunk_size=1,
    )

    records = [
        json.loads(line)
        for line in result.records_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["day"] for row in records] == [
        "2026-08-01",
        "2026-08-01",
        "2026-09-01",
    ]
    assert records[1]["company"] == UNMAPPED_VALUE
    assert records[1]["station"] == UNMAPPED_VALUE
    assert records[2]["category"] is None
    assert result.metric_controls == {"amount": "30.05"}
    assert result.row_count == 3
    assert result.distinct_days == 2
    assert result.quality_event_count == 2
    assert len(result.source_fingerprint) == 64


def test_dimension_asset_is_versioned_and_idempotent(
    source_database: Path, tmp_path: Path
) -> None:
    schema_path = tmp_path / "dimension.json"
    _write_json(
        schema_path,
        {
            "dataset_id": "test_product",
            "kind": "dimension",
            "schema_major": 1,
            "primary_key": "material_code",
            "columns": ["material_code", "material_name", "type_code"],
        },
    )
    binding = SourceBinding.from_dict(
        {
            "dataset_id": "test_product",
            "kind": "dimension",
            "table": "dim_product",
            "key_column": "material_code",
        }
    )
    first = build_dimension_asset(
        database=source_database,
        schema_path=schema_path,
        binding=binding,
        data_root=tmp_path / "data",
        chunk_size=1,
    )
    second = build_dimension_asset(
        database=source_database,
        schema_path=schema_path,
        binding=binding,
        data_root=tmp_path / "data",
        chunk_size=2,
    )
    assert first.entry == second.entry
    assert first.entry.row_count == 2
    assert first.asset_root.is_dir()
    assert json.loads(first.manifest_path.read_text(encoding="utf-8"))["files"][0][
        "sha256"
    ]


def test_fact_runner_writes_catalog_provenance(
    source_database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema_path = tmp_path / "schema.json"
    schema = _fact_schema(schema_path)
    generation_id = "11111111-1111-4111-8111-111111111111"
    schema_id = "1" * 32

    def fake_builder(schema_file: Path, records: Path, root: Path) -> dict:
        assert schema_file == schema_path.resolve()
        assert records.is_file()
        (root / "generations" / generation_id).mkdir(parents=True)
        return {"generation_id": generation_id}

    def fake_verify(self, requested: str) -> dict:
        assert requested == generation_id
        return {
            "dataset_id": "test_sales",
            "generation_id": generation_id,
            "schema_id": schema_id,
            "row_count": 3,
            "schema": schema.raw,
        }

    monkeypatch.setattr("cnts_engine.build.runner.CntsClient.verify_generation", fake_verify)
    result = build_fact_tree(
        database=source_database,
        schema_path=schema_path,
        binding=_fact_binding(),
        data_root=tmp_path / "data",
        run_root=tmp_path / "runs",
        native_builder=fake_builder,
        chunk_size=1,
    )

    provenance = json.loads(result.provenance_path.read_text(encoding="utf-8"))
    assert provenance["generation_id"] == generation_id
    assert provenance["schema_hash"] == canonical_hash(schema.raw)
    assert provenance["metric_controls"] == {"amount": "30.05"}
    assert result.entry.time_granularity == "month"
    assert not result.export.records_path.exists()

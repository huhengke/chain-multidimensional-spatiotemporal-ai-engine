from __future__ import annotations

import json
from pathlib import Path


SCHEMA_ROOT = Path(__file__).parents[1] / "schemas"
FACT_SCHEMAS = {
    "non_oil_sales": "non-oil-sales.json",
    "oil_sales_plan": "oil-sales-plan.json",
    "oil_sales_daily": "oil-sales-daily.json",
    "oil_monthly_sales": "oil-monthly-sales.json",
}


def test_fact_schemas_preserve_three_physical_zones() -> None:
    for dataset_id, filename in FACT_SCHEMAS.items():
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        common = set(schema["common_dimensions"])
        outer = set(schema["outer_dimensions"])
        metrics = {metric["name"] for metric in schema["metrics"]}

        assert schema["dataset_id"] == dataset_id
        assert schema["time_field"] == "day"
        assert schema["growth_threshold"] == 1000
        assert set(schema["growth_dimensions"]).issubset(common)
        assert common.isdisjoint(outer)
        assert common.isdisjoint(metrics)
        assert outer.isdisjoint(metrics)
        assert metrics


def test_oil_growth_chain_includes_product_classification() -> None:
    expected_tail = ["category", "gascode"]
    for filename in (
        "oil-sales-plan.json",
        "oil-sales-daily.json",
        "oil-monthly-sales.json",
    ):
        schema = json.loads((SCHEMA_ROOT / filename).read_text(encoding="utf-8"))
        assert schema["growth_dimensions"][-2:] == expected_tail


def test_oil_master_is_a_dimension_asset_not_a_fact_tree() -> None:
    schema = json.loads((SCHEMA_ROOT / "oil-master.json").read_text(encoding="utf-8"))
    assert schema["kind"] == "dimension"
    assert schema["primary_key"] == "material_code"
    assert "metrics" not in schema
    assert "time_field" not in schema

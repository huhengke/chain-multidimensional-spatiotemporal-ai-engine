# -*- coding: utf-8 -*-
import unittest

from engine.features.growth_plan import build_growth_plan
from engine.storage.dataset_registry import parse_spec


def make_spec(*, growth=None):
    capabilities = {
        "feature_layers": {
            "base": [],
            "trunk": [
                {"source": "OrderId", "name": "order_id", "role": "dimension", "semantic_type": "other"},
                {"source": "CompanyCode", "name": "company_code", "role": "dimension", "semantic_type": "org_code"},
                {"source": "StationCode", "name": "station_code", "role": "dimension", "semantic_type": "location_code"},
                {"source": "Category", "name": "category", "role": "dimension", "semantic_type": "category"},
            ],
            "branch": [{"source": "Amount", "name": "amount", "role": "metric"}],
        },
    }
    if growth is not None:
        capabilities["growth"] = growth
    return parse_spec({
        "id": "growth_demo",
        "kind": "generic",
        "label": "生长计划测试",
        "fact_table": "fact_growth_demo",
        "time": {"source": "BizDate", "format": "yyyy-mm-dd"},
        "levels": ["year", "month", "day", "order_id", "company_code", "station_code", "category"],
        "level_value_key": {
            "year": "year", "month": "month", "day": "day", "order_id": "order_id",
            "company_code": "company_code", "station_code": "station_code", "category": "category",
        },
        "metrics": [{"name": "amount", "source": "Amount", "column": "amount"}],
        "fact_columns": ["year", "month", "day", "order_id", "company_code", "station_code", "category", "amount"],
        "column_sources": {
            "order_id": "OrderId", "company_code": "CompanyCode",
            "station_code": "StationCode", "category": "Category", "amount": "Amount",
        },
        "capabilities": capabilities,
    })


class GrowthPlanTest(unittest.TestCase):
    def test_metadata_inference_keeps_time_trunk_and_excludes_order_id(self):
        plan = build_growth_plan(make_spec())
        self.assertEqual(plan.trunk_levels, ("year", "month", "day"))
        self.assertNotIn("order_id", plan.branch_levels)
        self.assertEqual(plan.branch_levels, ("company_code", "station_code", "category"))

    def test_reviewed_dimension_order_is_authoritative(self):
        plan = build_growth_plan(make_spec(growth={
            "enabled": True,
            "dimensions": ["category", "company_code"],
        }))
        self.assertEqual(plan.source, "declared")
        self.assertEqual(plan.branch_levels, ("category", "company_code"))

    def test_month_granularity_does_not_materialize_week_before_branches(self):
        spec = make_spec(growth={"enabled": True, "dimensions": ["company_code"]})
        object.__setattr__(spec, "levels", ("year", "season", "month", "week", "company_code"))
        object.__setattr__(spec, "time_format", "yyyymm")
        plan = build_growth_plan(spec)
        self.assertEqual(plan.trunk_levels, ("year", "season", "month"))
        self.assertEqual(plan.branch_levels, ("company_code",))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from engine.storage.adapters.cnts import CntsAdapter


class CntsStorageAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.adapter = CntsAdapter(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _scope_runtime():
        return SimpleNamespace(
            query_contract={
                "datasets": {
                    "oil_master": {
                        "kind": "dimension",
                        "scope": {"role": "dimension"},
                    },
                    "non_oil_sales": {
                        "kind": "fact",
                        "scope": {
                            "role": "actual",
                            "business_domain": "non_oil",
                            "preferred_for": ["day", "month"],
                            "priority": 100,
                        },
                    },
                    "oil_sales_daily": {
                        "kind": "fact",
                        "scope": {
                            "role": "actual",
                            "business_domain": "oil",
                            "preferred_for": ["day"],
                            "priority": 200,
                        },
                    },
                    "oil_monthly_sales": {
                        "kind": "fact",
                        "scope": {
                            "role": "actual",
                            "business_domain": "oil",
                            "preferred_for": ["month"],
                            "priority": 200,
                        },
                    },
                    "oil_sales_plan": {
                        "kind": "fact",
                        "scope": {"role": "plan"},
                    },
                }
            }
        )

    def test_all_actual_month_scope_deduplicates_oil_domain(self) -> None:
        selected, audit = self.adapter._actual_dataset_scope(
            self._scope_runtime(), {"filters": {"year": "2026"}}
        )
        self.assertEqual(selected, ["non_oil_sales", "oil_monthly_sales"])
        self.assertEqual(audit["requested_grain"], "month")
        self.assertEqual(
            audit["decisions"][1]["excluded_overlaps"], ["oil_sales_daily"]
        )

    def test_all_actual_day_scope_uses_daily_oil_tree(self) -> None:
        selected, audit = self.adapter._actual_dataset_scope(
            self._scope_runtime(),
            {"period": {"start": "2026-08-01", "end": "2026-08-03"}},
        )
        self.assertEqual(selected, ["non_oil_sales", "oil_sales_daily"])
        self.assertEqual(audit["requested_grain"], "day")

    def test_exact_organization_name_beats_normalized_child_levels(self) -> None:
        runtime = SimpleNamespace(
            organization_bridge={
                "datasets": {
                    "oil_sales_daily": [
                        {
                            "level": "branch",
                            "code": "bigareacode",
                            "name": "bigareaname",
                        },
                        {
                            "level": "county_company",
                            "code": "branchcode",
                            "name": "branchname",
                        },
                        {"level": "area", "code": "areacode", "name": "areaname"},
                    ]
                }
            },
            display_maps={
                "oil_sales_daily": {
                    "bigareacode": {"100047": "示例区域甲组织层级一"},
                    "branchcode": {"100047001": "示例区域甲组织层级二"},
                    "areacode": {"100047001001": "示例区域甲"},
                }
            },
        )
        resolved = self.adapter._resolve_organization(
            runtime, "oil_sales_daily", "示例区域甲组织层级一", None
        )
        self.assertEqual(
            resolved,
            ("bigareacode", ["100047"], {"100047": "示例区域甲组织层级一"}),
        )

    def test_time_range_outside_catalog_returns_no_segments(self) -> None:
        segments, precision_gap = self.adapter._time_segments(
            {"year": "2024"}, "day", "2026-04-01", "2026-09-29"
        )
        self.assertEqual(segments, [])
        self.assertFalse(precision_gap)

    def test_month_catalog_rejects_partial_month_range(self) -> None:
        segments, precision_gap = self.adapter._time_segments(
            {"date_from": "2026-08-03", "date_to": "2026-08-09"},
            "month",
            "2024-01-01",
            "2026-08-01",
        )
        self.assertEqual(segments, [(date(2026, 8, 3), date(2026, 8, 9))])
        self.assertTrue(precision_gap)


if __name__ == "__main__":
    unittest.main()

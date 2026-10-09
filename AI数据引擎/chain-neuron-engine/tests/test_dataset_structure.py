import unittest

from engine.api.routes.datasets import _build_declaration
from engine.features.structure import (
    build_structure_list_summary,
    build_structure_summary,
    rebuild_plan,
)
from engine.storage.dataset_registry import DatasetRegistry, parse_spec


class DatasetStructureTest(unittest.TestCase):
    def test_same_field_can_be_shared_by_multiple_layers(self):
        declaration = _build_declaration("shared_layers", {
            "kind": "generic",
            "label": "跨层字段",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": [{"key": "store", "source": "Store", "name": "store"}],
            "metrics": [{"source": "Amount", "name": "amount"}],
            "feature_layers": {
                "base": [{"source": "Store", "role": "dimension", "semantic_type": "org_name", "query_uses": ["filter"]}],
                "trunk": [
                    {"source": "Month", "role": "time", "query_uses": ["group_by"]},
                    {"source": "Store", "role": "dimension", "query_uses": ["group_by"]},
                ],
                "branch": [{"source": "Amount", "role": "metric", "query_uses": ["aggregate"]}],
                "dna_keys": ["Month", "Store", "Amount"],
                "reviewed": True,
            },
        })
        DatasetRegistry._validate({"datasets": [declaration]}, "shared.yaml", allow_empty=False)
        spec = parse_spec(declaration)
        structure = build_structure_summary(spec)
        store = next(item for item in structure["feature_layers"] if item["source"] == "Store")
        self.assertEqual(store["layers"], ["base", "trunk"])
        self.assertEqual(structure["dna"]["layer_count"], 3)
        self.assertEqual(structure["dna"]["length"], 3)

    def test_rebuild_plan_maps_structure_scope_to_spiral_depth(self):
        spec = parse_spec({
            "id": "scope_sales",
            "kind": "generic",
            "label": "范围重算",
            "fact_table": "fact_scope_sales",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": ["year", "store"],
            "level_labels": {"year": "年", "store": "定位单元"},
            "level_value_key": {"year": "year", "store": "store"},
            "metrics": [{"name": "amount", "source": "Amount", "column": "amount"}],
            "fact_columns": ["year", "store", "amount"],
            "column_sources": {"store": "Store", "amount": "Amount"},
            "capabilities": {
                "feature_layers": {
                    "base": [{"source": "Store", "role": "dimension", "semantic_type": "org_name"}],
                    "trunk": [{"source": "Store", "role": "dimension"}],
                    "branch": [{"source": "Amount", "role": "metric"}],
                    "dna_keys": ["Month", "Store", "Amount"],
                },
            },
        })
        self.assertEqual(rebuild_plan(spec, "base")["levels"], [])
        self.assertEqual(rebuild_plan(spec, "trunk")["levels"], ["year"])
        self.assertEqual(rebuild_plan(spec, "branch")["levels"], ["store"])
        self.assertEqual(rebuild_plan(spec, "all")["levels"], ["year", "store"])

    def test_declaration_without_feature_layers_uses_explicit_fallback(self):
        spec = parse_spec({
            "id": "fallback_sales",
            "kind": "generic",
            "label": "旧声明",
            "fact_table": "fact_fallback_sales",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": ["year", "store"],
            "level_labels": {"year": "年", "store": "定位单元"},
            "level_value_key": {"year": "year", "store": "store"},
            "metrics": [{"name": "amount", "source": "Amount", "column": "amount"}],
            "fact_columns": ["year", "store", "amount"],
            "column_sources": {"store": "Store", "amount": "Amount"},
        })
        structure = build_structure_summary(spec)
        self.assertEqual(structure["feature_layer_source"], "fallback")
        self.assertGreater(structure["dna"]["length"], 0)

    def test_list_summary_omits_full_structure_payload(self):
        spec = parse_spec({
            "id": "light_sales",
            "kind": "generic",
            "label": "轻量列表摘要",
            "fact_table": "fact_light_sales",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": ["year", "store"],
            "level_labels": {"year": "年", "store": "定位单元"},
            "level_value_key": {"year": "year", "store": "store"},
            "metrics": [{"name": "amount", "source": "Amount", "column": "amount"}],
            "fact_columns": ["year", "store", "amount"],
            "column_sources": {"store": "Store", "amount": "Amount"},
            "capabilities": {
                "feature_layers": {
                    "base": [{"source": "Month", "role": "time"}],
                    "trunk": [{"source": "Store", "role": "dimension"}],
                    "branch": [{"source": "Amount", "role": "metric"}],
                    "dna_keys": ["Month", "Store", "Amount"],
                },
            },
        })
        summary = build_structure_list_summary(spec)
        self.assertNotIn("feature_layers", summary)
        self.assertNotIn("keys", summary["dna"])
        self.assertNotIn("levels", summary["spiral"])
        self.assertFalse(summary["growth"]["stats_available"])


if __name__ == "__main__":
    unittest.main()

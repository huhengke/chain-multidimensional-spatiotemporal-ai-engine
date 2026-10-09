from __future__ import annotations

import unittest
from unittest.mock import patch

from engine.ingestion.field_recommendations import (
    catalog_status,
    load_recommendations,
    match_recommendations,
    normalize_field_name,
)
from engine.ingestion.source_profiler import profile_records


class FieldRecommendationsTest(unittest.TestCase):
    def test_catalog_is_valid_and_non_empty(self):
        status = catalog_status()
        self.assertTrue(status["ok"], status)
        self.assertGreaterEqual(status["active_entries"], 10)
        self.assertEqual(status["entries"], len(load_recommendations()))

    def test_alias_normalization_matches_project_experience(self):
        self.assertEqual(normalize_field_name("Material_Code"), "materialcode")
        matches = match_recommendations("Material_Code")
        self.assertTrue(matches)
        self.assertEqual(matches[0].semantic_type, "product_code")
        self.assertTrue(matches[0].base_candidate)

    def test_profile_exposes_knowledge_recommendation(self):
        with patch("engine.ingestion.source_profiler._request_ai", return_value={}):
            result = profile_records(
                [{"name": "GasCode"}, {"name": "MonthlyAmount"}],
                [
                    {"GasCode": "92", "MonthlyAmount": "10.5"},
                    {"GasCode": "95", "MonthlyAmount": "11.5"},
                ],
                source_name="experience-test",
            )
        by_source = {item["source"]: item for item in result["columns"]}
        self.assertEqual(by_source["GasCode"]["suggestion_source"], "knowledge")
        self.assertEqual(by_source["GasCode"]["semantic_type"], "product_code")
        self.assertTrue(by_source["GasCode"]["base_candidate"])
        self.assertEqual(by_source["GasCode"]["feature_layer"], "base")
        self.assertIn("filter", by_source["GasCode"]["query_uses"])
        self.assertIn("evidence", by_source["GasCode"])
        self.assertEqual(by_source["MonthlyAmount"]["guess_role"], "metric")
        self.assertEqual(by_source["MonthlyAmount"]["feature_layer"], "branch")
        self.assertEqual(result["recommendation_catalog"]["matched_fields"], 2)

    def test_time_field_is_trunk_not_base(self):
        with patch("engine.ingestion.source_profiler._request_ai", return_value={}):
            result = profile_records(
                [{"name": "Month"}],
                [{"Month": "202601"}, {"Month": "202602"}],
                source_name="time-test",
            )
        item = result["columns"][0]
        self.assertEqual(item["guess_role"], "time")
        self.assertFalse(item["base_candidate"])
        self.assertEqual(item["feature_layer"], "trunk")

    def test_high_confidence_knowledge_wins_over_conflicting_ai(self):
        with patch(
            "engine.ingestion.source_profiler._request_ai",
            return_value={
                "MaterialName": {
                    "role": "metric",
                    "reason": "模型误判",
                    "semantic_type": "other",
                    "base_candidate": False,
                    "feature_layer": "branch",
                    "query_uses": ["aggregate"],
                }
            },
        ):
            result = profile_records(
                [{"name": "MaterialName"}],
                [{"MaterialName": "类别代码甲"}, {"MaterialName": "类别代码丙"}],
                source_name="conflict-test",
            )
        item = result["columns"][0]
        self.assertEqual(item["guess_role"], "dimension")
        self.assertTrue(item["ai_conflict"])
        self.assertTrue(item["needs_manual_review"])


if __name__ == "__main__":
    unittest.main()

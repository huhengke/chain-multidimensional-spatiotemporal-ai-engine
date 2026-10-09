from __future__ import annotations

import unittest

from engine.api.routes.rules import _rule_suggestions, _sanitize_suggestions


class RuleRecommendationsTest(unittest.TestCase):
    def test_fallback_generates_editable_common_rules(self):
        suggestions = _rule_suggestions({"idea": "把月份转换为季节和季度，并标记双11"})
        names = {item["name"] for item in suggestions}
        self.assertEqual(names, {"season", "quarter", "holiday"})
        season = next(item for item in suggestions if item["name"] == "season")
        self.assertEqual(season["mapping"]["3,4,5"], "春季")
        holiday = next(item for item in suggestions if item["name"] == "holiday")
        self.assertEqual(holiday["rules"][0]["condition"], "month=11 and day=11")
        self.assertEqual(holiday["status"], "pending_review")

    def test_unknown_idea_does_not_invent_rules(self):
        self.assertEqual(_rule_suggestions({"idea": "只分析客户画像"}), [])

    def test_quarter_mapping_is_not_mistaken_for_mixed_semantics(self):
        suggestions = _sanitize_suggestions([
            {
                "name": "quarter",
                "source": "month",
                "precision": "month",
                "mapping": {"1,2,3": "第一季度"},
            }
        ])
        self.assertEqual(suggestions[0]["mapping"]["1,2,3"], "第一季度")

    def test_fallback_supports_standard_time_generator_rules(self):
        suggestions = _rule_suggestions({"idea": "增加上半年、单月和二十四节气"})
        names = {item["name"] for item in suggestions}
        self.assertEqual(names, {"half_year", "month_parity", "solar_term"})
        solar_term = next(item for item in suggestions if item["name"] == "solar_term")
        self.assertEqual(solar_term["source"], "date")
        self.assertTrue(any(item["value"] == "清明" for item in solar_term["rules"]))


if __name__ == "__main__":
    unittest.main()

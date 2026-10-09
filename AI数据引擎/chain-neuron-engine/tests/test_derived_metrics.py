# -*- coding: utf-8 -*-
"""派生指标口径单测：自然日天数、日均分母、完成率/时间进度/欠进度。"""
from __future__ import annotations

import unittest
from datetime import date

from engine.query.derived_metrics import (
    daily_average,
    grain_scoped_conditions,
    months_in_span,
    period_span,
    plan_progress,
)

AS_OF = date(2026, 9, 29)


def span(conditions, resolved=None):
    return period_span(conditions, resolved, as_of=AS_OF)


class PeriodSpanTest(unittest.TestCase):
    def test_month_uses_real_calendar_days(self):
        # 用户口径：按月取该月真实天数，不能一律 31
        cases = {
            "202601": 31, "202602": 28, "202402": 29, "202604": 30, "202608": 31,
            "202612": 31,
        }
        for value, days in cases.items():
            with self.subTest(month=value):
                self.assertEqual(span({"month": value})["period_days"], days)

    def test_month_list_accumulates_each_month(self):
        result = span({"year": "2024", "month": ["202401", "202402"]})
        self.assertEqual(result["period_days"], 60)  # 2024 闰年 31+29
        self.assertEqual(result["date_from"], "2024-01-01")
        self.assertEqual(result["date_to"], "2024-02-29")

    def test_year_only(self):
        self.assertEqual(span({"year": "2024"})["period_days"], 366)
        self.assertEqual(span({"year": "2025"})["period_days"], 365)

    def test_week_and_day(self):
        week = span({"week": "2026W17"})
        self.assertEqual((week["date_from"], week["date_to"]), ("2026-04-20", "2026-04-26"))
        self.assertEqual(week["period_days"], 7)
        day = span({"day": "2026-04-05"})
        self.assertEqual(day["basis_days"], 1)

    def test_in_progress_month_uses_elapsed_days_only(self):
        result = span({"month": "202609"})
        self.assertEqual(result["period_days"], 30)
        self.assertEqual((result["elapsed_days"], result["basis_days"]), (29, 29))
        self.assertTrue(result["in_progress"])

    def test_future_period_has_no_basis_days(self):
        result = span({"month": "202612"})
        self.assertEqual(result["basis_days"], 0)

    def test_relative_current_month_is_unclipped_for_progress(self):
        resolved = {"kind": "current_month", "label": "本月",
                    "date_from": "2026-09-01", "date_to": "2026-09-29"}
        result = span({}, resolved)
        self.assertEqual(result["period_days"], 30)
        self.assertEqual(result["elapsed_days"], 29)

    def test_unsupported_shapes_return_none_instead_of_guessing(self):
        for conditions in ({}, {"year": "2026", "season": "春季"},
                           {"year": "2026", "quarter": "Q1"},
                           {"month": "13"}, {"week": "2026-17"}):
            with self.subTest(conditions=conditions):
                self.assertIsNone(span(conditions))


class DailyAverageTest(unittest.TestCase):
    def test_keeps_source_precision_per_metric(self):
        result = daily_average(
            {"monthlysales": "95969.98863", "monthlyamount": "1032539898.79"}, 31
        )
        self.assertEqual(result["monthlysales"], "3095.80608")
        self.assertEqual(result["monthlyamount"], "33307738.67")

    def test_no_division_when_days_unknown(self):
        self.assertEqual(daily_average({"monthlysales": "10.00"}, 0), {})

    def test_non_numeric_values_are_skipped(self):
        self.assertEqual(daily_average({"remark": "未填写"}, 3), {})


class PlanProgressTest(unittest.TestCase):
    def test_completed_period_full_time_progress(self):
        result = plan_progress("84279.00", "95969.98863", span({"month": "202608"}))
        self.assertEqual(
            (result["completion_rate"], result["time_progress"], result["gap_points"]),
            ("113.87", "100.00", "13.87"),
        )
        self.assertEqual(result["status"], "on_track")

    def test_in_progress_period_reports_gap_points_and_tons(self):
        result = plan_progress("100.00", "30.000", span({"month": "202609"}))
        self.assertEqual((result["completion_rate"], result["time_progress"]),
                         ("30.00", "96.67"))
        self.assertEqual(result["gap_points"], "-66.67")
        self.assertEqual(result["status"], "behind")
        self.assertEqual(result["expected_by_progress"], "96.667")
        self.assertEqual(result["gap_value"], "-66.667")

    def test_zero_plan_is_not_a_number(self):
        result = plan_progress("0", "30", span({"month": "202608"}))
        self.assertFalse(result["available"])
        self.assertIn("计划为 0", result["message"])


class GrainScopedConditionsTest(unittest.TestCase):
    def test_months_in_span_covers_cross_year(self):
        self.assertEqual(
            months_in_span("2025-11-01", "2026-02-01"),
            ["202511", "202512", "202601", "202602"],
        )

    def test_day_grain_keeps_conditions_untouched(self):
        conditions = {"date_from": "2026-08-01", "date_to": "2026-08-15"}
        scoped, reason = grain_scoped_conditions(
            conditions, span(conditions), day_grain=True
        )
        self.assertEqual(reason, "")
        self.assertEqual(scoped, conditions)

    def test_month_aligned_range_folds_to_whole_months(self):
        # 「本月」编译后是被今天截断的区间，自然期间仍是整月
        current = span({}, {"label": "本月", "date_from": "2026-09-01",
                            "date_to": "2026-09-29"})
        scoped, reason = grain_scoped_conditions(
            {"relative_period": "本月"}, current, day_grain=False
        )
        self.assertEqual(reason, "")
        self.assertEqual(scoped, {"month": ["202609"]})

    def test_partial_month_range_is_refused_instead_of_inflated(self):
        conditions = {"date_from": "2026-08-01", "date_to": "2026-08-15"}
        scoped, reason = grain_scoped_conditions(
            conditions, span(conditions), day_grain=False
        )
        self.assertIn("不是完整自然月", reason)
        self.assertEqual(scoped, conditions)

    def test_week_on_month_grain_is_refused(self):
        conditions = {"week": "2026W17"}
        _scoped, reason = grain_scoped_conditions(
            conditions, span(conditions), day_grain=False
        )
        self.assertIn("日/周粒度", reason)

    def test_plain_month_condition_needs_no_fold(self):
        conditions = {"month": "202608"}
        scoped, reason = grain_scoped_conditions(
            conditions, span(conditions), day_grain=False
        )
        self.assertEqual((reason, scoped), ("", conditions))


if __name__ == "__main__":
    unittest.main()

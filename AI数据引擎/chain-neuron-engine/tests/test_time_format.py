# -*- coding: utf-8 -*-
"""日粒度时间格式（yyyy-mm-dd）识别、解析与派生回归。

覆盖 2026-09-29 的缺陷：Excel 日期单元格落成 `2026-04-01 00:00:00` 文本后，
画像/向导/清洗三条链路都认不出日粒度，导致“上周/某一天”类问题无法导入。
"""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.derivation.time_derivation_engine import TimeDerivationEngine
from engine.ingestion.cleaner import (
    DAY_LEVEL_FORMATS,
    TIME_FORMATS,
    CleanError,
    canonical_time_value,
    parse_time,
)
from engine.ingestion.excel_reader import _ColStat, _guess_time_format, default_derive_for
from engine.ingestion.source_profiler import _rule_role, _time_format, _time_like, _text

ISO_SAMPLES = [
    "2026-04-01 00:00:00",
    "2026-04-02",
    "2026/4/3",
    "2026年4月4日",
    "20260405",
]


class TimeFormatTest(unittest.TestCase):
    def test_yyyy_mm_dd_is_a_declared_format(self) -> None:
        self.assertIn("yyyy-mm-dd", TIME_FORMATS)
        self.assertIn("yyyy-mm-dd", DAY_LEVEL_FORMATS)

    def test_parse_time_accepts_text_date_variants(self) -> None:
        for text in ISO_SAMPLES:
            self.assertEqual(parse_time(text, "yyyy-mm-dd"), ("2026", "04"), text)
        self.assertEqual(parse_time(datetime(2026, 4, 1), "yyyy-mm-dd"), ("2026", "04"))
        self.assertEqual(parse_time(20260401.0, "yyyy-mm-dd"), ("2026", "04"))

    def test_canonical_time_is_iso_date(self) -> None:
        self.assertEqual(canonical_time_value("2026-04-01 00:00:00", "yyyy-mm-dd"), "2026-04-01")
        self.assertEqual(canonical_time_value("2026-04-02", "yyyy-mm-dd"), "2026-04-02")
        self.assertEqual(canonical_time_value("2026/4/3", "yyyy-mm-dd"), "2026-04-03")
        self.assertEqual(canonical_time_value("2026年4月4日", "yyyy-mm-dd"), "2026-04-04")
        self.assertEqual(canonical_time_value("20260405", "yyyy-mm-dd"), "2026-04-05")
        self.assertEqual(canonical_time_value(datetime(2026, 12, 31, 8, 30), "yyyy-mm-dd"), "2026-12-31")

    def test_invalid_dates_are_rejected(self) -> None:
        for bad in ("2026-13-01", "2026-02-30", "abc", "2026-04"):
            with self.assertRaises(CleanError, msg=bad):
                canonical_time_value(bad, "yyyy-mm-dd")

    def test_probe_stats_guess_day_granularity(self) -> None:
        stat = _ColStat()
        for v in ("2026-04-01 00:00:00", "2026-04-02 00:00:00", "2026-04-03"):
            stat.feed(v)
        self.assertEqual(_guess_time_format(stat), "yyyy-mm-dd")
        self.assertEqual(stat.time_max(), 1.0)
        self.assertEqual(
            default_derive_for("yyyy-mm-dd"),
            ["year", "season", "month", "quarter", "half_year", "month_parity", "week", "day"],
        )

    def test_month_only_column_stays_month_granularity(self) -> None:
        stat = _ColStat()
        for v in ("202401", "202402", "202403"):
            stat.feed(v)
        self.assertEqual(_guess_time_format(stat), "yyyymm")
        self.assertEqual(
            default_derive_for("yyyymm"),
            ["year", "season", "month", "quarter", "half_year", "month_parity"],
        )

    def test_profiler_marks_workday_as_time_with_day_format(self) -> None:
        role, reason = _rule_role("WorkDay", [_text(v) for v in ISO_SAMPLES])
        self.assertEqual(role, "time", reason)
        self.assertEqual(_time_format([_text(v) for v in ISO_SAMPLES]), "yyyy-mm-dd")
        self.assertTrue(_time_like("2026-04-01 00:00:00"))

    def test_day_layer_is_iso_date_and_breaks_at_month_granularity(self) -> None:
        engine = TimeDerivationEngine()
        self.assertEqual(engine.derive_date(date(2026, 4, 15)).get("day"), "2026-04-15")
        # 月粒度源派生不出“日/周”：留空即断链，上层不会长出假的日节点
        month_derived = engine.derive_month("2026", "04")
        self.assertIsNone(month_derived.get("day"))
        self.assertIsNone(month_derived.get("week"))

    def test_standard_month_rules_are_available(self) -> None:
        engine = TimeDerivationEngine()
        april = engine.derive_month("2026", "04")
        self.assertEqual(april.get("season"), "春季")
        self.assertEqual(april.get("quarter"), "Q2")
        self.assertEqual(april.get("half_year"), "上半年")
        self.assertEqual(april.get("month_parity"), "双月")

    def test_solar_term_uses_day_precision_only(self) -> None:
        engine = TimeDerivationEngine()
        self.assertEqual(engine.derive_date(date(2026, 4, 5)).get("solar_term"), "清明")
        self.assertIsNone(engine.derive_month("2026", "04").get("solar_term"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""有数据天数观测（#40）单测：滞后口径要能被真实天数核对。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import Column, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from engine.query import unified
from engine.storage.dataset_registry import DatasetSpec, MetricSpec

SPEC = DatasetSpec(
    id="cov_daily",
    label="观测-日报",
    levels=("month", "day"),
    level_value_key={"month": "yyyymm", "day": "day"},
    metrics=(MetricSpec(name="salesvol", source="业务吨数", column="salesvol"),),
)


class DayCoverageTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session = Session(self.engine)
        self.table = Table(
            "t_cov_daily", MetaData(),
            Column("yyyymm", String), Column("day", String), Column("salesvol", String),
        )
        self.table.create(self.engine)
        self.addCleanup(self.session.close)

    def _rows(self, rows):
        self.session.execute(self.table.insert(), rows)
        self.session.commit()

    def _coverage(self, conditions):
        with patch.object(unified, "get_dynamic_table", lambda spec: self.table):
            return unified.observed_day_coverage(self.session, SPEC, conditions)

    def test_counts_only_days_present_in_facts(self):
        self._rows([
            {"yyyymm": "202609", "day": "2026-09-01", "salesvol": "1"},
            {"yyyymm": "202609", "day": "2026-09-01", "salesvol": "2"},
            {"yyyymm": "202609", "day": "2026-09-29", "salesvol": "1"},
            {"yyyymm": "202608", "day": "2026-08-31", "salesvol": "1"},
        ])
        self.assertEqual(
            self._coverage({"month": "202609"}),
            {"days": 2, "through": "2026-09-29"},
        )

    def test_conditions_use_the_same_compiled_where_as_the_totals(self):
        """观测口径打在编译后的同一份条件上：组织层级一过滤不能把全量天数算进来。"""
        spec = DatasetSpec(
            id="cov_daily",
            levels=("bigarea", "day"),
            level_value_key={"bigarea": "bigareacode", "day": "day"},
            metrics=(MetricSpec(name="salesvol", source="业务吨数", column="salesvol"),),
        )
        table = Table(
            "t_cov_org", MetaData(),
            Column("bigareacode", String), Column("day", String), Column("salesvol", String),
        )
        table.create(self.engine)
        self.session.execute(table.insert(), [
            {"bigareacode": "100047", "day": "2026-09-01", "salesvol": "1"},
            {"bigareacode": "100048", "day": "2026-09-02", "salesvol": "1"},
            {"bigareacode": "100048", "day": "2026-09-03", "salesvol": "1"},
        ])
        self.session.commit()
        with patch.object(unified, "get_dynamic_table", lambda spec_: table):
            result = unified.observed_day_coverage(self.session, spec, {"bigarea": "100047"})
        self.assertEqual(result, {"days": 1, "through": "2026-09-01"})

    def test_dataset_without_day_level_has_no_observable_coverage(self):
        self._rows([{"yyyymm": "202609", "day": "2026-09-01", "salesvol": "1"}])
        spec = DatasetSpec(
            id="cov_monthly",
            levels=("month",),
            level_value_key={"month": "yyyymm"},
            metrics=(MetricSpec(name="salesvol", source="业务吨数", column="salesvol"),),
        )
        with patch.object(unified, "get_dynamic_table", lambda _spec: self.table):
            self.assertEqual(unified.observed_day_coverage(self.session, spec, {}), {})

    def test_empty_period_reports_no_coverage(self):
        self._rows([{"yyyymm": "202609", "day": "2026-09-01", "salesvol": "1"}])
        self.assertEqual(self._coverage({"month": "202607"}), {})


if __name__ == "__main__":
    unittest.main()

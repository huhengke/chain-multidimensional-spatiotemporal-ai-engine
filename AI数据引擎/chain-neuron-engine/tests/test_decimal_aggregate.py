# -*- coding: utf-8 -*-
"""SQLite 十进制聚合精度回归。"""
from __future__ import annotations

import unittest

from sqlalchemy import text

from engine.storage.database import engine


class DecimalAggregateTest(unittest.TestCase):
    def test_decimal_sum_does_not_introduce_float_tail(self) -> None:
        with engine.connect() as connection:
            result = connection.execute(text("""
                WITH values_table(value) AS (
                    SELECT '0.1' UNION ALL SELECT '0.2' UNION ALL SELECT NULL
                )
                SELECT decimal_sum(value) FROM values_table
            """)).scalar_one()
        self.assertEqual(result, "0.3")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from datetime import date
from unittest.mock import patch

from agent.python_agent.skills.intent import IntentRecognizer
from agent.python_agent.skills.sales_query import _resolved_year_label, _scope_text
from engine.query.unified import _rehost_org_slots, _shift_time_mapping
from engine.query.time_foundation import compile_time_conditions


class _FakeLLM:
    backend = "test"

    def __init__(self, payload: dict):
        self.payload = payload

    def chat(self, messages, expect_json=False):
        return json.dumps(self.payload, ensure_ascii=False)


class FoundationSemanticsTest(unittest.TestCase):
    def test_half_year_is_compiled_to_months(self):
        result = compile_time_conditions(
            conditions={"year": "2026", "season": "上半年"},
            filters={"year": "2026", "season": "上半年"},
        )
        self.assertEqual(
            result["conditions"]["month"],
            ["202601", "202602", "202603", "202604", "202605", "202606"],
        )
        self.assertNotIn("season", result["conditions"])
        self.assertEqual(result["filters"]["yyyymm"], result["conditions"]["month"])

    def test_real_season_is_preserved(self):
        result = compile_time_conditions({"year": "2026", "season": "夏季"})
        self.assertEqual(result["conditions"], {"year": "2026", "season": "夏季"})

    def test_month_parity_is_compiled_to_standard_months(self):
        result = compile_time_conditions(
            conditions={"year": "2026", "month_parity": "单月"},
            filters={"year": "2026", "month_parity": "单月"},
        )
        self.assertEqual(
            result["conditions"]["month"],
            ["202601", "202603", "202605", "202607", "202609", "202611"],
        )
        self.assertNotIn("month_parity", result["conditions"])
        self.assertNotIn("month_parity", result["filters"])

    def test_dynamic_filters_are_promoted_to_route_conditions(self):
        result = compile_time_conditions(
            filters={"month": ["202601", "202602"]}
        )
        self.assertEqual(result["conditions"]["month"], ["202601", "202602"])
        self.assertEqual(result["filters"]["yyyymm"], ["202601", "202602"])

    def test_previous_week_is_compiled_to_a_complete_week(self):
        result = compile_time_conditions(
            conditions={"year": "2026", "relative_period": "previous_week"},
            filters={"year": "2026", "relative_period": "previous_week"},
        )
        start = date.fromisoformat(result["conditions"]["date_from"])
        end = date.fromisoformat(result["conditions"]["date_to"])
        self.assertEqual((end - start).days, 6)
        self.assertEqual(start.weekday(), 0)
        self.assertEqual(end.weekday(), 6)
        self.assertEqual(result["audit"]["resolved_period"]["label"], "上周")

    def test_month_ordinal_week_is_compiled_to_monday_aligned_span(self):
        """月内序数周：首周是残周（1 日起至第一个周日），之后周一起算整周。"""
        third = compile_time_conditions(
            conditions={"relative_period": "month_week:202604:3"},
            filters={"relative_period": "month_week:202604:3"},
        )
        self.assertEqual(
            (third["conditions"]["date_from"], third["conditions"]["date_to"]),
            ("2026-04-13", "2026-04-19"),
        )
        first = compile_time_conditions(
            conditions={"relative_period": "month_week:202604:一"}, filters={}
        )
        # 2026-04-01 是周三，所以第 1 周只有 4/1～4/5 五天
        self.assertEqual(
            (first["conditions"]["date_from"], first["conditions"]["date_to"]),
            ("2026-04-01", "2026-04-05"),
        )
        self.assertEqual(first["audit"]["resolved_period"]["label"], "2026年4月第1周")
        self.assertNotIn("relative_period", first["conditions"])

    def test_month_ordinal_week_beyond_the_month_is_not_invented(self):
        result = compile_time_conditions(
            conditions={"relative_period": "month_week:202604:6"}, filters={}
        )
        self.assertNotIn("date_from", result["conditions"])
        self.assertTrue(result["audit"]["warnings"])

    def test_comparison_date_shift_rewrites_year_condition(self):
        current = {
            "year": "2026",
            "relative_period": "previous_week",
            "date_from": "2026-09-21",
            "date_to": "2026-09-27",
        }
        yoy, _, _ = _shift_time_mapping(current, "yoy")
        mom, _, _ = _shift_time_mapping(current, "mom")
        self.assertEqual(yoy["year"], "2025")
        self.assertEqual(yoy["date_from"], "2025-09-21")
        self.assertEqual(mom["year"], "2026")
        self.assertEqual(mom["date_from"], "2026-09-14")

    def test_logic_month_overrides_duplicate_physical_yyyymm(self):
        result = compile_time_conditions(
            conditions={"yyyymm": "202605", "month": "202505"},
            filters={"yyyymm": "202605", "month": "202505"},
        )
        self.assertEqual(result["conditions"], {"month": "202505"})
        self.assertEqual(result["filters"]["yyyymm"], "202505")

    def test_relative_year_compiles_to_year_condition_not_date_range(self):
        """相对年份要落成 year 层级条件：编译成日期区间会让月粒度数据集整条拒答。"""
        previous = compile_time_conditions(
            conditions={"relative_period": "previous_year"}, filters={}
        )
        self.assertEqual(previous["conditions"], {"year": "2025"})
        self.assertNotIn("date_from", previous["conditions"])
        self.assertNotIn("relative_period", previous["conditions"])
        self.assertEqual(previous["audit"]["resolved_year"]["label"], "去年")
        current = compile_time_conditions(
            conditions={"relative_period": "current_year"}, filters={}
        )
        self.assertEqual(current["conditions"]["year"], str(date.today().year))
        following = compile_time_conditions(
            conditions={"relative_period": "next_year"}, filters={}
        )
        self.assertEqual(following["conditions"]["year"], str(date.today().year + 1))

    def test_relative_year_with_explicit_month_is_composed_by_the_engine(self):
        """"去年8月"：Agent 只给相对年份和裸月份，YYYYMM 由引擎配出来。"""
        result = compile_time_conditions(
            conditions={"relative_period": "previous_year", "month": "8"}, filters={}
        )
        self.assertEqual(result["conditions"]["month"], "202508")
        self.assertEqual(result["conditions"]["year"], "2025")
        self.assertEqual(result["filters"]["yyyymm"], "202508")

    def test_bare_month_without_year_anchors_to_today(self):
        result = compile_time_conditions(conditions={"month": "8"}, filters={})
        self.assertEqual(result["conditions"]["month"], f"{date.today().year}08")
        future = compile_time_conditions(conditions={"month": "12"}, filters={})
        self.assertEqual(
            future["conditions"]["month"], f"{date.today().year - 1}12"
        )

    def test_explicit_year_wins_over_relative_year(self):
        result = compile_time_conditions(
            conditions={"year": "2024", "relative_period": "previous_year"}, filters={}
        )
        self.assertEqual(result["conditions"]["year"], "2024")
        self.assertTrue(result["audit"]["warnings"])

    def test_answer_prefix_shows_engine_year_basis(self):
        """答案前缀露出引擎换算的年份口径，裸月份按提问原词显示。"""
        batch_plan = {"resolved_year": {"label": "去年", "year": "2025"}}
        single_plan = {"time_foundation": {"resolved_year": {"label": "今年"}}}
        self.assertEqual(_resolved_year_label(batch_plan), "去年")
        self.assertEqual(_resolved_year_label(single_plan), "今年")
        self.assertEqual(
            _scope_text(
                {"month": "8", "relative_period": "previous_year"},
                {},
                _resolved_year_label(batch_plan),
            ),
            "去年 8月 ",
        )

    def test_unmapped_org_slot_is_rehosted_to_declared_level(self):
        """`station` 在英文列名数据集上落位到声明的 org_name 层级，而不是整条拒答。"""
        with patch("engine.query.unified.rehost_org_conditions") as rehost:
            rehost.return_value = (
                {"store_name": ["示例服务单元甲"]},
                [{"requested_key": "station", "level": "store_name"}],
                {"station"},
            )
            conditions, unmapped, evidence, no_value = _rehost_org_slots(
                None, None, {"year": "2025"}, ["station"], {"station": "示例定位单元丙"}
            )
        self.assertEqual(conditions["store_name"], ["示例服务单元甲"])
        self.assertNotIn("station", conditions)
        self.assertEqual((unmapped, no_value, len(evidence)), ([], {}, 1))
        # 取值必须来自声明列的真查结果，不能把提问原词直接写进条件
        self.assertEqual(rehost.call_args.args[2], {"station": "示例定位单元丙"})

    def test_org_slot_without_declaration_stays_unmapped(self):
        """没有基座声明承接时保持原样报错，不猜层级。"""
        with patch("engine.query.unified.rehost_org_conditions") as rehost:
            rehost.return_value = ({}, [], set())
            conditions, unmapped, evidence, no_value = _rehost_org_slots(
                None, None, {"year": "2025"}, ["station"], {"station": "示例定位单元丙"}
            )
        self.assertEqual((unmapped, evidence, no_value), (["station"], [], {}))
        self.assertEqual(conditions, {"year": "2025"})

    def test_org_slot_with_declaration_but_missing_value_reports_value_error(self):
        """有声明却查不到值：按"取值不存在"回传，和"不认识条件"区分开。"""
        with patch("engine.query.unified.rehost_org_conditions") as rehost:
            rehost.return_value = ({}, [], {"station"})
            conditions, unmapped, evidence, no_value = _rehost_org_slots(
                None, None, {}, ["station"], {"station": "不存在的站名"}
            )
        self.assertEqual(unmapped, [])
        self.assertEqual((conditions, evidence), ({}, []))
        self.assertEqual(no_value, {"station": ["不存在的站名"]})

    def test_model_cannot_override_dataset_or_half_year(self):
        recognizer = IntentRecognizer(
            _FakeLLM({
                "intent": "sales_query",
                "dataset": "oil_monthly_sales",
                "conditions": {"year": "2026", "season": "上半年"},
                "base_filters": {"product": "业务域乙"},
            })
        )
        result = recognizer.recognize("2026年上半年业务域乙的业务金额是多少？有多少笔记录？")
        self.assertEqual(result["dataset"], "non_oil_sales")
        self.assertEqual(
            result["conditions"]["month"],
            ["202601", "202602", "202603", "202604", "202605", "202606"],
        )
        self.assertNotIn("season", result["conditions"])
        self.assertNotIn("product", result["base_filters"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""统一注册表查询回归测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import Column, MetaData, String, Table, create_engine, event
from sqlalchemy.orm import Session

from engine.api.schemas_unified import UnifiedQueryRequest
from engine.features.contracts import build_dataset_contract
from engine.query.unified import (
    _fact_aggregate_actual,
    _request_filters,
    _should_use_fact_aggregate,
    execute_unified_query,
)
from engine.storage.dataset_registry import DatasetSpec, MetricSpec, registry
from engine.storage.database import _DecimalSum, session_scope


class UnifiedQueryTest(unittest.TestCase):
    def test_registered_dataset_is_the_only_query_dispatch(self) -> None:
        request = UnifiedQueryRequest(
            dataset="oil_monthly_sales",
            filters={"year": "2024"},
            metrics=["monthlyamount"],
        )
        with session_scope() as session:
            result = execute_unified_query(
                session, request.model_dump(), "unittest-registered-query"
            )
        self.assertEqual(result["query_plan"]["dataset"], "oil_monthly_sales")
        # 运行库会按拓扑迁移流程清空，测试不能依赖某次手工导入的历史总额。
        # 有数据时验证指标契约；空库时验证统一入口返回可解释的空结果。
        actual = result["actual"]
        if actual.get("available"):
            self.assertIn("monthlyamount", actual["aggregations"])
        else:
            self.assertEqual(actual["aggregations"], {})
        self.assertIsNone(result["plan"])

    def test_dynamic_query_request_accepts_dataset_and_base_filters(self) -> None:
        request = UnifiedQueryRequest(
            dataset="non_oil_sales",
            filters={"store_name": "测试站"},
            base_filters={"product": "类别甲"},
            group_by=["store_name"],
        )
        self.assertEqual(request.dataset, "non_oil_sales")
        self.assertEqual(request.base_filters["product"], "类别甲")

    def test_cross_dataset_request_accepts_comparison_protocol(self) -> None:
        request = UnifiedQueryRequest(
            datasets=["oil_monthly_sales", "non_oil_sales"],
            conditions={"month": "202605"},
            filters={"yyyymm": "202605"},
            base_filters={"organization": "示例区域甲"},
            comparisons=["yoy", "mom"],
        )
        self.assertEqual(request.datasets, ["oil_monthly_sales", "non_oil_sales"])
        self.assertEqual(request.conditions["month"], "202605")
        self.assertEqual(request.comparisons, ["yoy", "mom"])

    def test_unified_orchestration_uses_injected_dataset_executor(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_executor(_session, request, trace_id):
            dataset = request["dataset"]
            calls.append((dataset, trace_id))
            return {
                "trace_id": trace_id,
                "query_plan": {
                    "dataset": dataset,
                    "label": dataset,
                    "metric_labels": {"amount": "业务金额"},
                },
                "actual": {
                    "dataset": dataset,
                    "available": True,
                    "record_count": 1,
                    "aggregations": {"amount": "10.00"},
                },
                "plan": None,
                "warnings": [],
                "timing_ms": {"total": 0.1},
            }

        request = {
            "datasets": ["oil_sales_daily", "non_oil_sales"],
            "filters": {"month": "202608"},
        }
        with session_scope() as session:
            result = execute_unified_query(
                session,
                request,
                "adapter-test",
                dataset_executor=fake_executor,
            )

        self.assertEqual([item[0] for item in calls], request["datasets"])
        self.assertEqual(result["actual"]["dataset_count"], 2)
        self.assertEqual(result["actual"]["totals"]["sales_amount"], "20.00")

    def test_period_start_end_compiles_to_date_range(self) -> None:
        filters = _request_filters({
            "period": {"start": "2026-06-01", "end": "2026-06-07"},
        })
        self.assertEqual(filters["date_from"], "2026-06-01")
        self.assertEqual(filters["date_to"], "2026-06-07")

    def test_explicit_filters_override_period_range(self) -> None:
        filters = _request_filters({
            "period": {"start": "2026-06-01", "end": "2026-06-07"},
            "filters": {"date_from": "2026-06-02"},
        })
        self.assertEqual(filters["date_from"], "2026-06-02")
        self.assertEqual(filters["date_to"], "2026-06-07")

    def test_filter_below_group_uses_exact_fact_aggregate(self) -> None:
        spec = registry.require("oil_sales_daily")
        with session_scope() as session:
            self.assertTrue(_should_use_fact_aggregate(
                session,
                spec,
                {"month": "202608", "category": "01"},
                ["day"],
                False,
            ))
            self.assertTrue(_should_use_fact_aggregate(
                session,
                spec,
                {"month": "202608", "gascode": "60518726"},
                ["day"],
                False,
            ))
            self.assertFalse(_should_use_fact_aggregate(
                session,
                spec,
                {"month": "202608"},
                ["day"],
                False,
            ))
            self.assertTrue(_should_use_fact_aggregate(
                session,
                spec,
                {"month": "202608"},
                ["bigareacode", "category"],
                False,
            ))

    def test_fact_aggregate_groups_by_all_requested_dimensions(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)

        @event.listens_for(engine, "connect")
        def _register_decimal_sum(dbapi_connection, _connection_record) -> None:
            dbapi_connection.create_aggregate("decimal_sum", 1, _DecimalSum)

        table = Table(
            "fact_multi_group",
            MetaData(),
            Column("bigareacode", String),
            Column("category", String),
            Column("salesvol", String),
        )
        table.create(engine)
        session = Session(engine)
        self.addCleanup(engine.dispose)
        self.addCleanup(session.close)
        session.execute(table.insert(), [
            {"bigareacode": "100047", "category": "01", "salesvol": "10.5"},
            {"bigareacode": "100047", "category": "01", "salesvol": "1.5"},
            {"bigareacode": "100047", "category": "02", "salesvol": "5"},
            {"bigareacode": "100048", "category": "01", "salesvol": "7"},
        ])
        session.commit()
        spec = DatasetSpec(
            id="multi_group",
            label="复合分组测试",
            fact_table="fact_multi_group",
            levels=("bigareacode", "category"),
            level_labels={"bigareacode": "组织层级一", "category": "产品大类"},
            level_value_key={
                "bigareacode": "bigareacode",
                "category": "category",
            },
            metrics=(
                MetricSpec(
                    name="salesvol",
                    source="业务吨数",
                    column="salesvol",
                    label="业务吨数",
                ),
            ),
        )
        display_conditions = {
            "bigareacode": {
                "value_labels": {"100047": "示例区域甲", "100048": "示例区域丁"},
            },
            "category": {
                "value_labels": {"01": "类别甲", "02": "类别乙"},
            },
        }
        with patch(
            "engine.query.unified.get_dynamic_table", return_value=table
        ):
            actual = _fact_aggregate_actual(
                session,
                spec,
                {},
                ["bigareacode", "category"],
                display_conditions,
            )

        self.assertEqual(actual["record_count"], 4)
        self.assertEqual(actual["aggregations"]["salesvol"], "24.0")
        self.assertEqual(len(actual["nodes"]), 3)
        first = actual["nodes"][0]
        self.assertEqual(
            first["dimensions"],
            {"bigareacode": "100047", "category": "01"},
        )
        self.assertEqual(
            first["display_dimensions"],
            {"bigareacode": "示例区域甲", "category": "类别甲"},
        )
        self.assertEqual(first["aggregations"]["salesvol"], "12.0")

    def test_contract_exposes_machine_output_scope(self) -> None:
        contract = build_dataset_contract(registry.require("oil_sales_daily"))
        aggregate = contract["business_aggregates"]["oil_machine_output"]
        self.assertEqual(aggregate["label"], "指定类别合计")
        self.assertEqual(
            aggregate["combined_condition"], {"category": ["01", "02"]}
        )


if __name__ == "__main__":
    unittest.main()

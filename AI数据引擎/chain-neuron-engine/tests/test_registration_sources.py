# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from engine.api.routes import datasources
from engine.api.routes.datasets import _build_declaration
from engine.features.contracts import build_dataset_contract
from engine.ingestion.batch_importer import BatchImporter
from engine.ingestion.connectors.base import validate_table_identifier
from engine.ingestion.source_profiler import _rule_role, profile_records
from engine.storage.dataset_registry import parse_spec


class RegistrationSourcesTest(unittest.TestCase):
    class FakeConnector:
        def list_tables(self):
            return [{"name": "sales_daily", "schema": "public"}]

        def describe_table(self, table):
            return [{"name": "month", "type": "varchar", "nullable": "NO"}]

        def fetch_rows(self, table, limit=None):
            yield {"month": "202401", "amount": "1.2"}

        def close(self):
            pass

    def test_database_table_discovery_uses_connector_without_external_db(self):
        connector = self.FakeConnector()
        with patch.object(datasources, "get_datasource_config", return_value={"id": "fake", "type": "fake"}), \
             patch.object(datasources, "create_connector", return_value=connector):
            tables = datasources.list_tables("fake")
            preview = datasources.preview_table("fake", "public.sales_daily", limit=10)
        self.assertEqual(tables["items"][0]["name"], "sales_daily")
        self.assertEqual(preview["rows"][0]["month"], "202401")

    def test_batch_importer_database_source_iterates_dict_rows(self):
        task = SimpleNamespace(
            source_kind="database",
            source_ref={"datasource_id": "fake", "table": "sales_daily"},
        )
        importer = object.__new__(BatchImporter)
        connector = self.FakeConnector()
        with patch("engine.ingestion.batch_importer.get_datasource_config", return_value={"id": "fake"}), \
             patch("engine.ingestion.batch_importer.create_connector", return_value=connector):
            rows = list(importer._source_rows(task))
        self.assertEqual(rows, [(2, {"month": "202401", "amount": "1.2"})])

    def test_database_declaration_is_preserved(self):
        spec = parse_spec({
            "id": "db_sales",
            "kind": "generic",
            "label": "数据库业务表",
            "source": {
                "kind": "database",
                "datasource_id": "postgres_dw",
                "table": "public.sales_daily",
            },
            "fact_table": "fact_db_sales",
            "time": {"source": "sale_month", "format": "yyyymm"},
            "levels": ["store"],
            "level_labels": {"store": "定位单元"},
            "level_value_key": {"store": "store"},
            "metrics": [{"name": "amount", "source": "amount", "column": "amount"}],
            "fact_columns": ["store", "amount"],
            "fingerprint": ["store"],
            "column_sources": {"store": "store", "amount": "amount"},
        })
        self.assertEqual(spec.source["kind"], "database")
        self.assertEqual(spec.source["table"], "public.sales_daily")

    def test_profile_suggests_time_dimension_metric_and_ignore(self):
        with patch("engine.ingestion.source_profiler._request_ai", return_value={}):
            result = profile_records(
                [{"name": "month"}, {"name": "store"}, {"name": "amount"}, {"name": "empty"}],
                [
                    {"month": "202401", "store": "A", "amount": "1.2", "empty": None},
                    {"month": "202402", "store": "B", "amount": "2.3", "empty": ""},
                ],
                source_name="fake:table",
            )
        roles = {column["source"]: column["guess_role"] for column in result["columns"]}
        self.assertEqual(roles["month"], "time")
        self.assertEqual(roles["store"], "dimension")
        self.assertEqual(roles["amount"], "metric")
        self.assertEqual(roles["empty"], "ignore")
        self.assertEqual(result["analysis_source"], "knowledge")
        self.assertGreaterEqual(result["recommendation_catalog"]["matched_fields"], 1)

    def test_rule_profile_keeps_identifier_fields_as_dimensions(self):
        values = ["1001", "1002", "1003", "1004"]
        self.assertEqual(_rule_role("GasCode", values)[0], "dimension")
        self.assertEqual(_rule_role("OUCode", values)[0], "dimension")
        self.assertEqual(_rule_role("StoreID", values)[0], "dimension")
        self.assertEqual(_rule_role("Flag", values)[0], "dimension")
        self.assertEqual(_rule_role("Amount", values)[0], "metric")

    def test_registration_declaration_can_mark_generic_base_features(self):
        declaration = _build_declaration("base_sales", {
            "kind": "generic",
            "label": "基座业务",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": [{"key": "product", "source": "ProductName", "name": "product_name"}],
            "metrics": [{"source": "Amount", "name": "amount"}],
            "base": {
                "enabled": True,
                "features": [
                    {"source": "ProductName", "semantic_type": "product_name"},
                ],
            },
        })
        self.assertTrue(declaration["base"]["enabled"])
        self.assertEqual(declaration["base"]["features"][0]["name"], "product_name")

    def test_registration_declaration_preserves_universal_feature_layers(self):
        declaration = _build_declaration("layered_sales", {
            "kind": "generic",
            "label": "通用分层测试",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": [{"key": "store", "source": "Store", "name": "store"}],
            "metrics": [{"source": "Amount", "name": "amount"}],
            "feature_layers": {
                "base": [{"source": "Store", "role": "dimension", "semantic_type": "org_name", "query_uses": ["filter"]}],
                "trunk": [
                    {"source": "Month", "role": "time", "semantic_type": "time", "query_uses": ["group_by"]},
                    {"source": "Store", "role": "dimension", "query_uses": ["group_by"]},
                ],
                "branch": [{"source": "Amount", "role": "metric", "query_uses": ["aggregate"]}],
                "dna_keys": ["Month", "Store", "Amount"],
                "reviewed": True,
            },
        })
        layers = declaration["capabilities"]["feature_layers"]
        self.assertTrue(layers["reviewed"])
        self.assertEqual([item["source"] for item in layers["trunk"]], ["Month", "Store"])
        self.assertEqual(parse_spec(declaration).capabilities["feature_layers"]["branch"][0]["role"], "metric")

    def test_feature_layer_dna_keys_match_confirmed_layers_when_base_is_empty(self):
        declaration = _build_declaration("fallback_layers", {
            "kind": "generic",
            "label": "基座未启用时的分层回退",
            "time": {"source": "Month", "format": "yyyymm"},
            "levels": [{"key": "store", "source": "Store", "name": "store"}],
            "metrics": [{"source": "Amount", "name": "amount"}],
            "feature_layers": {
                "base": [],
                "trunk": [
                    {"source": "Month", "role": "time", "query_uses": ["filter", "group_by"]},
                    {"source": "Store", "role": "dimension", "query_uses": ["group_by"]},
                ],
                "branch": [{"source": "Amount", "role": "metric", "query_uses": ["aggregate"]}],
                "dna_keys": ["Month", "Store", "Amount"],
                "reviewed": True,
            },
        })
        layers = declaration["capabilities"]["feature_layers"]
        self.assertEqual(layers["dna_keys"], ["Month", "Store", "Amount"])
        self.assertEqual({item["source"] for layer in ("base", "trunk", "branch") for item in layers[layer]},
                         {"Month", "Store", "Amount"})

    def test_contract_exposes_base_declaration(self):
        spec = parse_spec({
            "id": "base_dim",
            "kind": "dimension",
            "label": "基座维表",
            "dim_table": "dim_base_dim",
            "primary_key": "Code",
            "columns": [
                {"source": "Code", "name": "code", "label": "编码"},
                {"source": "Name", "name": "name", "label": "名称"},
            ],
            "base": {
                "enabled": True,
                "features": [{"source": "Code", "name": "code", "semantic_type": "product_code"}],
            },
        })
        contract = build_dataset_contract(spec)
        self.assertTrue(contract["base"]["enabled"])
        self.assertEqual(contract["base"]["features"][0]["semantic_type"], "product_code")

    def test_table_identifier_rejects_sql(self):
        self.assertEqual(validate_table_identifier("public.sales_daily"), "public.sales_daily")
        with self.assertRaises(ValueError):
            validate_table_identifier("sales;DROP TABLE users")

    def test_contract_exposes_roles(self):
        spec = parse_spec({
            "id": "db_sales",
            "kind": "generic",
            "label": "数据库业务表",
            "source": {"kind": "database", "datasource_id": "ds", "table": "sales"},
            "fact_table": "fact_db_sales",
            "time": {"source": "month", "format": "yyyymm"},
            "levels": ["store"],
            "level_labels": {"store": "定位单元"},
            "level_value_key": {"store": "store"},
            "metrics": [{"name": "amount", "source": "amount", "column": "amount"}],
            "fact_columns": ["store", "amount"],
            "column_sources": {"store": "store", "amount": "amount"},
        })
        contract = build_dataset_contract(spec)
        self.assertEqual(contract["source"]["kind"], "database")
        self.assertEqual({field["role"] for field in contract["fields"]}, {"time", "dimension", "metric"})


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""组织编码前缀桥的判定规则单测（#32）。

桥的价值全在"宁缺不扩"的守卫上：本地编码列答不上的组织名，只允许换成
其它数据集真查到的组织编码在前缀展开后**本列真实存在、且明显小于整棵树**
的节点集合。这里用内存表把每条守卫单独钉住，不依赖项目基座数据。
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Column, MetaData, Numeric, String, Table, create_engine
from sqlalchemy.orm import Session

from engine.query import level_resolution
from engine.storage.dataset_registry import DatasetSpec, MetricSpec


def _plan_spec() -> DatasetSpec:
    """只有变长组织编码、没有可承接组织名的数据集（计划表形态）。"""
    return DatasetSpec(
        id="bridge_plan",
        label="桥-计划",
        levels=("unit", "year"),
        level_labels={"unit": "单位编码", "year": "年"},
        level_value_key={"unit": "unitcode", "year": "year"},
        metrics=(MetricSpec(name="plan", source="目标量", column="monsalesplan"),),
    )


def _fact_spec() -> DatasetSpec:
    """有「名称列↔编码列」词根配对的数据集（组织层级一月报表形态）。"""
    return DatasetSpec(
        id="bridge_fact",
        label="桥-实际",
        levels=("bigarea", "year"),
        level_labels={"bigarea": "组织层级一", "year": "年"},
        level_value_key={"bigarea": "bigareacode", "year": "year"},
        metrics=(MetricSpec(name="amount", source="金额", column="monthlyamount"),),
    )


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session = Session(self.engine)
        self.plan_spec = _plan_spec()
        self.fact_spec = _fact_spec()
        self.specs: dict[str, DatasetSpec] = {
            self.plan_spec.id: self.plan_spec,
            self.fact_spec.id: self.fact_spec,
        }
        self.tables: dict[str, Table] = {}
        self._metadata = MetaData()
        level_resolution._CODE_INFO_CACHE.clear()
        level_resolution._BRIDGE_CACHE.clear()
        self.addCleanup(self.session.close)

    # ---- 脚手架 ----
    def _table(self, spec_id: str, columns: dict[str, str]) -> Table:
        table = Table(
            f"t_{spec_id}",
            self._metadata,
            *[
                Column(name, String if kind == "s" else Numeric)
                for name, kind in columns.items()
            ],
        )
        table.create(self.engine)
        self.tables[spec_id] = table
        return table

    def _insert(self, spec_id: str, rows: list[dict[str, str]]):
        self.session.execute(self.tables[spec_id].insert(), rows)
        self.session.commit()

    @contextmanager
    def _patched(self):
        """注册表与物理表都指向内存脚手架：桥的取值必须全部来自这张小表。"""
        fake_registry = SimpleNamespace(specs=self.specs, revision=0)
        with patch.object(level_resolution, "registry", fake_registry), \
                patch.object(level_resolution, "get_dynamic_table", lambda spec: self.tables[spec.id]):
            yield

    def _bridge(self, terms: list[str], level: str = "unit"):
        with self._patched():
            return level_resolution.bridge_org_codes(self.session, self.plan_spec, level, terms)

    def _standard_world(self):
        """计划列只有变长组织编码（组织层级一 6 位 + 下辖定位单元 15 位）；实际表有名称→编码。"""
        self._table("bridge_plan", {"unitcode": "s", "year": "s", "monsalesplan": "n"})
        self._insert("bridge_plan", [
            {"unitcode": "100047", "year": "2024"},
            {"unitcode": "100047001001001", "year": "2024"},
            {"unitcode": "100047001001002", "year": "2024"},
            {"unitcode": "100048", "year": "2024"},
            {"unitcode": "100048001001001", "year": "2024"},
            {"unitcode": "100048001001002", "year": "2024"},
            {"unitcode": "100049001001001", "year": "2024"},
        ])
        self._table("bridge_fact", {
            "bigareacode": "s", "bigareaname": "s", "year": "s", "monthlyamount": "n",
        })
        self._insert("bridge_fact", [
            {"bigareacode": "100047", "bigareaname": "示例区域甲组织层级一", "year": "2024"},
            {"bigareacode": "100047001", "bigareaname": "示例区域甲组织层级三", "year": "2024"},
            {"bigareacode": "100048", "bigareaname": "示例区域丁组织层级一", "year": "2024"},
        ])

    # ---- 主路径 ----
    def test_prefix_expansion_uses_other_datasets_real_codes(self):
        self._standard_world()
        result = self._bridge(["示例区域甲组织层级一"])
        self.assertEqual(
            result["codes"],
            ["100047", "100047001001001", "100047001001002"],
        )
        self.assertEqual(result["unresolved"], [])
        evidence = result["evidence"][0]
        self.assertEqual(evidence["bridge_code"], "100047")
        self.assertEqual(evidence["source_dataset"], "bridge_fact")
        self.assertEqual(evidence["matched_via"], "bigareaname")
        self.assertEqual(evidence["expanded_nodes"], 3)

    def test_maximal_prefix_wins_over_child_codes(self):
        """同一提问同时命中组织层级一码和它下辖组织层级三码时只保留最粗前缀。"""
        self._standard_world()
        result = self._bridge(["示例区域甲"])
        self.assertEqual([item["bridge_code"] for item in result["evidence"]], ["100047"])
        self.assertEqual(
            result["codes"],
            ["100047", "100047001001001", "100047001001002"],
        )

    def test_codes_absent_from_target_column_do_not_expand(self):
        """桥到的编码在本列没有节点时不编造取值。"""
        self._standard_world()
        self._insert("bridge_fact", [{"bigareacode": "100099", "bigareaname": "洋浦组织层级一"}])
        result = self._bridge(["洋浦组织层级一"])
        self.assertEqual(result["codes"], [])
        self.assertEqual(result["unresolved"], ["洋浦组织层级一"])

    def test_unmatched_term_stays_unresolved(self):
        self._standard_world()
        result = self._bridge(["不存在的组织层级一"])
        self.assertEqual(result["codes"], [])
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["unresolved"], ["不存在的组织层级一"])

    # ---- 守卫：宁缺不扩 ----
    def test_expansion_covering_whole_column_is_refused(self):
        """展开等于整列取值时与「条件被静默丢掉」无法区分，不桥。"""
        self._table("bridge_plan", {"unitcode": "s", "year": "s", "monsalesplan": "n"})
        self._insert("bridge_plan", [
            {"unitcode": "100047"},
            {"unitcode": "100047001001001"},
            {"unitcode": "100047001001002"},
        ])
        self._table("bridge_fact", {"bigareacode": "s", "bigareaname": "s", "monthlyamount": "n"})
        self._insert("bridge_fact", [{"bigareacode": "100047", "bigareaname": "示例区域甲组织层级一"}])
        result = self._bridge(["示例区域甲组织层级一"])
        self.assertEqual(result["codes"], [])
        self.assertEqual(result["unresolved"], ["示例区域甲组织层级一"])

    def test_expansion_over_node_cap_is_refused(self):
        self._standard_world()
        with patch.object(level_resolution, "_BRIDGE_MAX_NODES", 2):
            result = self._bridge(["示例区域甲组织层级一"])
        self.assertEqual(result["codes"], [])

    def test_fixed_width_column_is_not_a_hierarchy(self):
        self._table("bridge_plan", {"unitcode": "s", "year": "s", "monsalesplan": "n"})
        self._insert("bridge_plan", [{"unitcode": "000047"}, {"unitcode": "000048"}])
        self._table("bridge_fact", {"bigareacode": "s", "bigareaname": "s", "monthlyamount": "n"})
        self._insert("bridge_fact", [{"bigareacode": "000047", "bigareaname": "示例区域甲组织层级一"}])
        with self._patched():
            self.assertIsNone(level_resolution._code_column_info(self.session, self.plan_spec, "unit"))
        result = self._bridge(["示例区域甲组织层级一"])
        self.assertEqual(result["codes"], [])

    def test_non_digit_column_is_not_a_code(self):
        self._table("bridge_plan", {"unitcode": "s", "year": "s", "monsalesplan": "n"})
        self._insert("bridge_plan", [{"unitcode": "HK-001"}, {"unitcode": "SY-002"}])
        with self._patched():
            self.assertIsNone(level_resolution._code_column_info(self.session, self.plan_spec, "unit"))

    def test_dimension_sources_are_not_bridged(self):
        self._standard_world()
        dim = DatasetSpec(
            id="bridge_dim",
            kind="dimension",
            label="桥-维表",
            levels=("bigarea",),
            level_value_key={"bigarea": "bigareacode"},
        )
        self.specs[dim.id] = dim
        self._table("bridge_dim", {"bigareacode": "s", "bigareaname": "s"})
        self._insert("bridge_dim", [{"bigareacode": "100049", "bigareaname": "示例组织丁"}])
        result = self._bridge(["示例组织丁"])
        self.assertEqual(result["codes"], [])

    # ---- 列配对与层级判定 ----
    def test_name_column_pairs_by_stem_only(self):
        self._standard_world()
        with self._patched():
            table = self.tables["bridge_fact"]
            self.assertEqual(
                level_resolution._name_column_for(self.fact_spec, table, "bigarea"),
                "bigareaname",
            )
            # 计划列没有 unitname 配对列，就不参与桥的名称解析
            self.assertIsNone(
                level_resolution._name_column_for(self.plan_spec, self.tables["bridge_plan"], "unit")
            )

    def test_org_level_detection(self):
        self.assertTrue(level_resolution._is_org_level(self.plan_spec, "unit"))
        spec = DatasetSpec(
            id="bridge_gas",
            levels=("gas",),
            level_labels={"gas": "产品"},
            level_value_key={"gas": "gas_code"},
        )
        self.assertFalse(level_resolution._is_org_level(spec, "gas"))

    def test_codes_for_names_scans_each_name_column(self):
        self._standard_world()
        with self._patched():
            pairs = level_resolution._source_org_pairs(self.fact_spec)
            self.assertEqual(pairs, [("bigarea", "bigareaname", "bigareacode")])
            found = level_resolution._codes_for_names(
                self.session, self.tables["bridge_fact"], pairs, ["示例区域丁"]
            )
        self.assertEqual(list(found), ["100048"])


if __name__ == "__main__":
    unittest.main()

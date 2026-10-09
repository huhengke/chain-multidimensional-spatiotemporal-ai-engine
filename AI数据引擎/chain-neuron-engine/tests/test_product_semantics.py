# -*- coding: utf-8 -*-
"""产品语义落层规则单测（#45）。

产品主数据恢复后，基座可以把「类别代码甲/类别甲」这类说法翻译成目标数据集的层级条件。
危险不在"翻译不出来"，而在"翻错了层/错用了列"：物料分类编码（92#、0、150799）
与事实表里的杂码偶然能对上，单次提问看起来是命中了，实际筛出来的是别的产品。
这里用内存表把判定依据钉住——**整列编码域相通**才算数，偶然相交不算。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import Column, MetaData, String, Table, create_engine
from sqlalchemy.orm import Session

from engine.query import foundation
from engine.storage.dataset_registry import DatasetSpec, MetricSpec


def _master_spec() -> DatasetSpec:
    """基座主数据：同时声明了物料分类编码与物料编码两条 product_code。"""
    return DatasetSpec(
        id="master_ds",
        kind="dimension",
        label="产品基础",
        dim_table="t_master_ds",
        levels=(),
        base={"enabled": True, "features": [
            {"source": "MaterialName", "name": "material_name",
             "semantic_type": "product_name"},
            {"source": "MaterialClass", "name": "material_class",
             "semantic_type": "product_code"},
            {"source": "MaterialCode", "name": "material_code",
             "semantic_type": "product_code"},
        ]},
    )


def _fact_spec() -> DatasetSpec:
    """事实数据集：产品大类（category）与产品编码（gascode）两层。"""
    return DatasetSpec(
        id="fact_ds",
        kind="generic",
        label="业务数据甲",
        fact_table="t_fact_ds",
        levels=("year", "category", "gascode"),
        level_labels={"year": "年", "category": "产品大类", "gascode": "产品编码"},
        level_value_key={"year": "year", "category": "category", "gascode": "gascode"},
        column_sources={"year": "Year", "category": "Category", "gascode": "GasCode"},
        metrics=(MetricSpec(name="amount", source="金额", column="amount"),),
    )


CODE_FEATURES = [
    {"source": "MaterialClass", "name": "material_class", "semantic_type": "product_code"},
    {"source": "MaterialCode", "name": "material_code", "semantic_type": "product_code"},
]


class ProductResolutionTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", future=True)
        self.session = Session(self.engine)
        self.metadata = MetaData()
        self.master_spec = _master_spec()
        self.fact_spec = _fact_spec()
        self.tables: dict[str, Table] = {}
        self.addCleanup(self.session.close)

    def _create(self, name: str, columns: list[str]) -> Table:
        table = Table(name, self.metadata, *[Column(column, String) for column in columns])
        table.create(self.engine)
        return table

    def _rows(self, table: Table, rows: list[dict[str, str]]) -> None:
        self.session.execute(table.insert(), rows)
        self.session.commit()

    def _resolve(self, matched: list[dict[str, str]]):
        """把注册表与物理表都指向内存脚手架：取值只能来自这两张小表。"""
        with patch.object(foundation, "get_dim_source",
                          lambda _ds: SimpleNamespace(table=self.tables["master"])), \
             patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            return foundation._resolve_product_condition(
                self.session, self.master_spec, self.fact_spec, CODE_FEATURES, matched
            )

    def test_grade_lands_on_the_product_code_level(self):
        self.tables["master"] = self._create("t_master_ds", ["material_code", "material_class", "material_name"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["master"], [
            {"material_code": "60518726", "material_class": "92#", "material_name": "类别代码甲通用类别甲(ⅥB)"},
        ])
        self._rows(self.tables["fact"], [
            {"category": "01", "gascode": "60518726"},
            {"category": "02", "gascode": "60514943"},
        ])
        resolved = self._resolve([
            {"material_code": "60518726", "material_class": "92#", "material_name": "类别代码甲通用类别甲(ⅥB)"},
        ])
        self.assertIsNotNone(resolved)
        level, values, evidence = resolved
        self.assertEqual(level, "gascode")
        self.assertEqual(values, ["60518726"])
        self.assertEqual(evidence["base_feature"], "material_code")
        self.assertEqual(evidence["resolution"], "product_value_intersection")

    def test_incidental_class_code_overlap_is_not_a_basis(self):
        """物料分类编码与事实表杂码偶然相等，选择依据必须是整列相通规模。"""
        self.tables["master"] = self._create("t_master_ds", ["material_code", "material_class", "material_name"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["master"], [
            # 类别乙两行：分类码 0 / 150799 恰好也是事实表里出现过的杂码
            {"material_code": "60205886", "material_class": "0", "material_name": "类别代码戊"},
            {"material_code": "60514943", "material_class": "150799", "material_name": "0号通用类别乙(Ⅵ)"},
            # 物料编码列与事实表相通的行更多，整列相关性由这些行决定
            {"material_code": "60518726", "material_class": "92#", "material_name": "类别代码甲通用类别甲(ⅥB)"},
            {"material_code": "60518727", "material_class": "95#", "material_name": "类别代码丙通用类别甲(ⅥB)"},
            {"material_code": "60523537", "material_class": "98#", "material_name": "示例品牌类别代码丁通用类别甲(ⅥB)"},
        ])
        self._rows(self.tables["fact"], [
            {"category": "02", "gascode": "60205886"},
            {"category": "02", "gascode": "60514943"},
            {"category": "02", "gascode": "0"},
            {"category": "02", "gascode": "150799"},
            {"category": "01", "gascode": "60518726"},
            {"category": "01", "gascode": "60518727"},
            {"category": "01", "gascode": "60523537"},
        ])
        resolved = self._resolve([
            {"material_code": "60205886", "material_class": "0", "material_name": "类别代码戊"},
            {"material_code": "60514943", "material_class": "150799", "material_name": "0号通用类别乙(Ⅵ)"},
        ])
        self.assertIsNotNone(resolved)
        level, values, evidence = resolved
        self.assertEqual(evidence["base_feature"], "material_code")
        self.assertEqual(level, "gascode")
        # 绝不是偶然相交的分类码 0 / 150799
        self.assertEqual(values, ["60205886", "60514943"])
        self.assertGreater(evidence["domain_overlap"], 2)

    def test_no_overlapping_domain_returns_none(self):
        """库里没有对应的产品编码时不猜层：交回上层按"取值不存在"显式失败。"""
        self.tables["master"] = self._create("t_master_ds", ["material_code", "material_class", "material_name"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["master"], [
            {"material_code": "603040060185261", "material_class": "93", "material_name": "类别代码乙通用类别甲(粤Ⅳ)"},
        ])
        self._rows(self.tables["fact"], [{"category": "01", "gascode": "60518726"}])
        self.assertIsNone(self._resolve([
            {"material_code": "603040060185261", "material_class": "93", "material_name": "类别代码乙通用类别甲(粤Ⅳ)"},
        ]))

    def test_target_levels_prefer_the_finest_product_level(self):
        levels = foundation._target_levels(self.fact_spec, "product")
        self.assertEqual(levels[0], "gascode")
        self.assertIn("category", levels)

    def test_zero_padded_category_term_lands_on_the_category_level(self):
        """主数据大类码 `2` 与事实表大类 `02` 是同一编码域，落层要归一比较。

        逐字比较会让大类层的整列相通恒为 0，品类只能靠物料编码展开，计划表里
        `category=02` 而 gascode 不是物料编码的行就被筛掉了。
        """
        self.tables["master"] = self._create("t_master_ds", ["type_code", "type_name", "material_code"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["master"], [
            {"type_code": "1", "type_name": "类别甲", "material_code": "60518726"},
            {"type_code": "2", "type_name": "类别乙", "material_code": "60514943"},
        ])
        self._rows(self.tables["fact"], [
            {"category": "01", "gascode": "60518726"},
            {"category": "02", "gascode": "0"},          # 编码列不是物料编码，展开会漏掉
            {"category": "02", "gascode": "60514943"},
        ])
        matched = [{"type_code": "2", "type_name": "类别乙", "material_code": "60514943"}]
        with patch.object(foundation, "get_dim_source",
                          lambda _ds: SimpleNamespace(table=self.tables["master"])), \
             patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            resolved = foundation._category_term_condition(
                self.session, self.master_spec, self.fact_spec,
                [
                    {"source": "TypeCode", "name": "type_code", "semantic_type": "category"},
                    {"source": "TypeName", "name": "type_name", "semantic_type": "category"},
                    {"source": "MaterialCode", "name": "material_code",
                     "semantic_type": "product_code"},
                ],
                {"product": "类别乙"}, matched,
            )
        self.assertIsNotNone(resolved)
        level, values, evidence = resolved
        self.assertEqual(level, "category")
        # 下发的是事实表里真实存在的那个写法，不是主数据侧的 `2`
        self.assertEqual(values, ["02"])
        self.assertEqual(evidence["base_feature"], "type_code")
        self.assertEqual(evidence["resolution"], "category_value_intersection")

    def test_a_grade_is_not_a_category_term(self):
        """「类别代码甲」不属于任何大类名，不能被摊成一整个汽产品类。"""
        self.tables["master"] = self._create("t_master_ds", ["type_code", "type_name", "material_code"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["master"], [
            {"type_code": "1", "type_name": "类别甲", "material_code": "60518726"},
        ])
        self._rows(self.tables["fact"], [{"category": "01", "gascode": "60518726"}])
        with patch.object(foundation, "get_dim_source",
                          lambda _ds: SimpleNamespace(table=self.tables["master"])), \
             patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            resolved = foundation._category_term_condition(
                self.session, self.master_spec, self.fact_spec,
                [
                    {"source": "TypeCode", "name": "type_code", "semantic_type": "category"},
                    {"source": "TypeName", "name": "type_name", "semantic_type": "category"},
                ],
                {"product": "类别代码甲"},
                [{"type_code": "1", "type_name": "类别甲", "material_code": "60518726"}],
            )
        self.assertIsNone(resolved)

    def test_verbatim_fact_value_is_not_run_through_the_expansion(self):
        """请求值本身就是事实表取值时直接按那一层过滤：短码喂给相交只会筛成空集。"""
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["fact"], [
            {"category": "01", "gascode": "60518726"},
            {"category": "02", "gascode": "60185923"},
        ])
        with patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            resolved = foundation._verbatim_product_condition(
                self.session, self.fact_spec, ["01"]
            )
        self.assertIsNotNone(resolved)
        level, values, evidence = resolved
        self.assertEqual(level, "category")
        self.assertEqual(values, ["01"])
        self.assertEqual(evidence["resolution"], "verbatim_fact_value")

    def test_verbatim_prefers_the_layer_whose_domain_the_value_fits(self):
        """短码在两层都逐字存在时按整列编码宽度挑层：落错层就是 231 行 vs 80615 行。

        计划表 gascode 串进了 `01`（大类码写法），整列却是 8 位物料编码域；
        category 层全部取值都是两位补零。`01` 属于后者的域。
        """
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["fact"], [
            {"category": "01", "gascode": "60206059"},
            {"category": "01", "gascode": "60206060"},
            {"category": "01", "gascode": "01"},      # 串进来的杂码
            {"category": "02", "gascode": "60205886"},
        ])
        with patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            resolved = foundation._verbatim_product_condition(
                self.session, self.fact_spec, ["01"]
            )
        self.assertIsNotNone(resolved)
        level, values, evidence = resolved
        self.assertEqual(level, "category")
        self.assertEqual(values, ["01"])

    def test_verbatim_requires_every_requested_value_in_the_same_level(self):
        self.tables["master"] = self._create("t_master_ds", ["material_code"])
        self.tables["fact"] = self._create("t_fact_ds", ["category", "gascode"])
        self._rows(self.tables["fact"], [
            {"category": "01", "gascode": "60518726"},
            {"category": "02", "gascode": "60514943"},
        ])
        with patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            partial = foundation._verbatim_product_condition(
                self.session, self.fact_spec, ["01", "99"]
            )
            name_term = foundation._verbatim_product_condition(
                self.session, self.fact_spec, ["类别甲"]
            )
        self.assertIsNone(partial)      # 部分命中不接管，交给展开或取值校验
        self.assertIsNone(name_term)    # 名字仍走主数据展开

    def _resolve_with(self, features, matched):
        with patch.object(foundation, "get_dim_source",
                          lambda _ds: SimpleNamespace(table=self.tables["master"])), \
             patch.object(foundation, "get_dynamic_table",
                          lambda _spec: self.tables["fact"]):
            return foundation._resolve_product_condition(
                self.session, self.master_spec, self.fact_spec, features, matched
            )


if __name__ == "__main__":
    unittest.main()

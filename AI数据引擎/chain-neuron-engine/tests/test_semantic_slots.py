from __future__ import annotations

import time
import unittest
from contextlib import contextmanager

from agent.python_agent.skills import semantic_slots
from agent.python_agent.skills.semantic_slots import extract_slots
from agent.python_agent.skills.intent import IntentRecognizer

# 日粒度数据集能力由引擎注册表声明；单测里按同一形状喂入快照，
# 使断言不依赖引擎是否在跑，也不新建任何静态别名配置。
ENGINE_SNAPSHOT = {
    "业务域甲月报": "oil_monthly_sales",
    "业务数据甲": "oil_monthly_sales",
    "业务域甲日报": "oil_sales_daily",
    "业务域甲计划": "oil_sales_plan",
    "业务域乙明细": "non_oil_sales",
    "业务域乙": "non_oil_sales",
    "业务数据乙": "non_oil_sales",
}
DAY_GRAINED = {
    "non_oil_sales": "业务域乙明细",
    "oil_sales_daily": "业务域甲日报",
}


@contextmanager
def registry_snapshot():
    """临时替换数据集能力缓存（模拟引擎注册表快照）。"""
    saved = dict(semantic_slots._LABELS_CACHE)
    semantic_slots._LABELS_CACHE.update(
        at=time.monotonic(),
        map=dict(ENGINE_SNAPSHOT),
        by_id=semantic_slots._ids_by_label(ENGINE_SNAPSHOT),
        day_grained=dict(DAY_GRAINED),
    )
    try:
        yield
    finally:
        semantic_slots._LABELS_CACHE.clear()
        semantic_slots._LABELS_CACHE.update(saved)


class SemanticSlotsTest(unittest.TestCase):
    def test_exact_oil_grade_is_preserved(self):
        result = extract_slots("查询2025年类别代码甲业务数量")
        self.assertEqual(result["base_filters"]["product"], "类别代码甲")
        self.assertNotIn("category", result["filters"])

    def test_multiple_grades_are_preserved(self):
        result = extract_slots("查询2025年类别代码甲、类别代码丙和类别代码丁业务")
        self.assertEqual(
            result["base_filters"]["product"], ["类别代码甲", "类别代码丙", "类别代码丁"]
        )

    def test_oil_grade_never_becomes_a_direct_condition(self):
        """类别代码原语只交给基座：数据集没有「类别代码」层级，自己下发会整条拒答。"""
        result = extract_slots("查询2025年类别代码甲业务数量")
        self.assertNotIn("grade", result["conditions"])
        self.assertNotIn("grade", result["filters"])

    def test_unknown_grade_is_not_broadened_to_the_whole_category(self):
        """库里没有的类别代码不能说成"整个类别甲类"：原语下传，由引擎显式失败。"""
        result = extract_slots("2024年类别代码乙的业务金额是多少？")
        self.assertEqual(result["base_filters"]["product"], "类别代码乙")

    def test_grade_next_to_fuel_word_survives_date_stripping(self):
        """「8月0号类别乙」的时间剥离会吃掉数字，类别代码必须从原句里认出来。"""
        result = extract_slots("2026年8月0号类别乙的业务数量？")
        self.assertEqual(result["base_filters"]["product"], "0号")

    def test_category_is_forwarded_to_base_without_dropping_detail_filter(self):
        result = extract_slots("查询2025年类别甲业务")
        self.assertEqual(result["base_filters"]["product"], "类别甲")
        self.assertEqual(result["filters"]["category"], "01")

    def test_unqualified_sales_uses_all_actual_scope_and_comparisons(self):
        result = extract_slots(
            "示例区域甲2026年5月的业务、同比（去年5月）和环比（和上个月）"
        )
        self.assertEqual(result["dataset_scope"], "all_actual")
        self.assertEqual(result["datasets"], [])
        self.assertEqual(result["base_filters"]["organization"], "示例区域甲")
        self.assertEqual(result["comparisons"], ["yoy", "mom"])

    def test_previous_week_with_actual_and_plan_keeps_both_datasets(self):
        with registry_snapshot():
            result = extract_slots(
                "2026年上周各组织层级一的业务数据甲数量，加入类别乙、类别甲、合计、环比、同比、计划、日均业务数量和欠进度"
            )
        self.assertEqual(result["conditions"]["relative_period"], "previous_week")
        self.assertEqual(result["filters"]["relative_period"], "previous_week")
        self.assertEqual(result["dataset_scope"], "explicit_multi")
        # 月粒度产品/计划数据集保留（如实报告粒度缺口），并补上同域的日粒度承接方
        self.assertEqual(
            result["datasets"], ["oil_monthly_sales", "oil_sales_plan", "oil_sales_daily"]
        )
        self.assertEqual(result["group_by"], "big_area")
        self.assertEqual(result["comparisons"], ["yoy", "mom"])

    def test_gasoline_diesel_total_is_scoped_to_two_categories(self):
        with registry_snapshot():
            result = extract_slots(
                "用日报的类别甲、类别乙和指定类别合计计算2026年8月基础业务数量"
            )
        self.assertEqual(result["base_filters"]["product"], ["类别甲", "类别乙"])
        self.assertEqual(result["group_by"], "category")

    def test_explicit_date_becomes_day_range_instead_of_month(self):
        with registry_snapshot():
            result = extract_slots("2026年4月1日业务数据甲金额是多少？")
        self.assertEqual(
            result["conditions"],
            {"date_from": "2026-04-01", "date_to": "2026-04-01"},
        )
        self.assertNotIn("yyyymm", result["filters"])
        self.assertEqual(
            result["datasets"], ["oil_monthly_sales", "oil_sales_daily"]
        )

    def test_date_range_in_same_question_spans_both_ends(self):
        result = extract_slots("2026年4月1日到4月5日业务金额")
        self.assertEqual(
            (result["conditions"]["date_from"], result["conditions"]["date_to"]),
            ("2026-04-01", "2026-04-05"),
        )

    def test_day_grained_widening_stays_in_same_business_domain(self):
        # 点名"业务域乙"时它自己已是日粒度数据集，不需要也不该被产品侧带上
        with registry_snapshot():
            non_oil = extract_slots("2026年4月1日业务数据乙金额是多少？")
            oil = extract_slots("2026年4月1日业务数据甲金额是多少？")
        self.assertEqual(non_oil["datasets"], ["non_oil_sales"])
        self.assertEqual(oil["datasets"], ["oil_monthly_sales", "oil_sales_daily"])

    def test_date_digits_are_not_fuel_grades_or_locations(self):
        # "2026-04-01"里的 0 曾被当成 0 号类别乙，"日各""上周"曾被当成地点
        result = extract_slots("2026年4月1日各产品业务数据甲金额是多少？")
        self.assertNotIn("grade", result["conditions"])
        self.assertNotIn("organization", result["base_filters"])
        self.assertEqual(result["group_by"], "gas_code")
        week = extract_slots("上周业务数据甲金额是多少？")
        self.assertNotIn("organization", week["base_filters"])
        self.assertEqual(week["conditions"], {"relative_period": "previous_week"})

    def test_grouping_and_question_words_are_not_locations(self):
        # 提问/分组用词曾被当成 organization 下发，基座无此取值即阻断整条查询
        for question in (
            "2024年各月业务数量如何？",
            "2024年全年业务金额是多少？",
            "查询2025年类别甲业务吨数",
        ):
            self.assertNotIn(
                "organization", extract_slots(question)["base_filters"], question
            )

    def test_dataset_names_are_not_locations(self):
        # 数据集中文名指的是哪一棵树，不是地点：「业务域甲日报」曾被剥成
        # "日报"作为 organization 下传，把明明有数据的期间答成"没有数据"
        with registry_snapshot():
            for question in (
                "2026年9月业务域甲日报的日均业务数量是多少？",
                "2024年业务域甲月报的业务金额是多少？",
                "2026年8月业务域乙明细的日均业务数量",
            ):
                result = extract_slots(question)
                self.assertNotIn("organization", result["base_filters"], question)

    def test_plan_and_actual_wording_is_not_a_location(self):
        # 「业务域甲计划」是数据集名、「实际/完成」是侧别与提问用词：4 字窗口
        # 曾把连接词连同它们切成"和实际完"当 organization 下传，
        # 引擎按取值不存在丢过滤并回写一条无意义「提示」。
        with registry_snapshot():
            for question in (
                "2026年8月业务域甲计划和实际完成情况？",
                "2026年8月业务域甲计划和实际完成情况如何？",
                "2026年8月和9月的业务金额是多少？",
            ):
                self.assertNotIn(
                    "organization", extract_slots(question)["base_filters"], question
                )
            # 真组织名同句照旧提取，收口噪声不牺牲过滤
            named = extract_slots("2026年8月示例区域甲组织层级一业务域甲计划和实际完成情况？")
        self.assertEqual(
            named["conditions"].get("big_area") or named["base_filters"].get("organization"),
            "示例区域甲组织层级一",
        )

    def test_derived_metrics_use_the_named_dataset_as_actual_side(self):
        # 「2026年9月业务域甲日报的日均业务数量」的数据在日报（月报只到 202608），
        # 实际侧固定用月报会把有数据的期间答成"没有实际数据"
        with registry_snapshot():
            daily = extract_slots("2026年9月业务域甲日报的日均业务数量是多少？")
            monthly = extract_slots("2026年8月产品日均业务数量是多少？")
            plan_named = extract_slots("2023年计划完成率")
        self.assertEqual(daily["intent"], "plan_analysis")
        self.assertEqual(daily["actual_dataset"], "oil_sales_daily")
        self.assertEqual(daily["conditions"], {"month": "202609"})
        self.assertEqual(monthly["actual_dataset"], "oil_monthly_sales")
        # 点名计划表只改变计划侧取数，实际侧仍回落月报
        self.assertEqual(plan_named["actual_dataset"], "oil_monthly_sales")

    def test_day_grained_actual_side_keeps_the_explicit_day_range(self):
        # 日粒度实际表有真日值：折算成整月会让分子变成全月、分母仍是区间天数
        with registry_snapshot():
            daily = extract_slots(
                "2026年4月13日到4月19日业务域甲日报的日均业务数量"
            )
            monthly = extract_slots("2026年4月13日到4月19日产品日均业务数量")
        self.assertEqual(daily["conditions"]["date_from"], "2026-04-13")
        self.assertEqual(daily["conditions"]["date_to"], "2026-04-19")
        self.assertNotIn("month", daily["conditions"])
        self.assertEqual(monthly["conditions"]["month"], "202604")

    def test_month_ordinal_week_is_a_period_not_a_location(self):
        # "第三周"曾被当成地点下传，基座无此取值即阻断整条查询
        result = extract_slots("2026年4月第三周业务数据甲金额是多少？")
        self.assertEqual(
            result["conditions"]["relative_period"], "month_week:202604:3"
        )
        self.assertNotIn("month", result["conditions"])
        self.assertNotIn("organization", result["base_filters"])

    def test_real_station_name_still_becomes_location(self):
        result = extract_slots("2024年示例定位单元丙的业务记录有哪些？")
        self.assertEqual(result["base_filters"]["organization"], "示例定位单元丙")
        self.assertTrue(result["detail"])

    def test_clear_sales_question_uses_rule_fast_path(self):
        class FailingLLM:
            backend = "should_not_be_called"

            def chat(self, *_args, **_kwargs):
                raise AssertionError("明确业务问题不应调用模型")

        result = IntentRecognizer(FailingLLM()).recognize(
            "2024年总业务金额是多少？"
        )
        self.assertEqual(result["llm_backend"], "rules")
        self.assertEqual(result["dataset"], "oil_monthly_sales")
        self.assertEqual(result["conditions"], {"year": "2024"})


class ConsumedSemanticSlotsTest(unittest.TestCase):
    """模型把规则已消费的语义词当过滤值下发时，必须在进引擎前剔除（#39）。"""

    class _StubLLM:
        backend = "stub"

        def __init__(self, payload):
            self.payload = payload

        def chat(self, *_args, **_kwargs):
            import json

            return json.dumps(self.payload, ensure_ascii=False)

    def _recognize(self, question, payload):
        with registry_snapshot():
            return IntentRecognizer(self._StubLLM(payload)).recognize(question)

    def test_progress_word_is_not_a_filter_value(self):
        question = "2026年8月示例区域甲组织层级一的欠进度怎么样？"
        payload = {
            "intent": "sales_query",
            "dataset": "oil_monthly_sales",
            "conditions": {"year": "2026", "month": "202608", "check_mode": "欠进度"},
            "filters": {"checkmode": "欠进度情况"},
            "base_filters": {"organization": "示例区域甲组织层级一"},
        }
        result = self._recognize(question, payload)
        self.assertNotIn("check_mode", result["conditions"])
        self.assertNotIn("checkmode", result["filters"])
        # 派生指标本身仍由规则槽位给出，剔除幻觉值不会把问题答成普通合计
        self.assertEqual(result["intent"], "plan_analysis")
        self.assertIn("progress_gap", result["derived_metrics"])

    def test_group_and_dataset_words_are_not_filter_values(self):
        question = "2024年业务域甲月报各月的业务金额是多少？"
        payload = {
            "intent": "sales_query",
            "conditions": {"month": "各月", "dataset": "业务域甲月报"},
            "base_filters": {"product": "类别甲"},
        }
        result = self._recognize(question, payload)
        self.assertNotIn("month", result["conditions"])
        self.assertNotIn("dataset", result["conditions"])
        # 「各月」是分组要求，由规则槽位给出 group_by；模型自造的 product
        # 规则没识别出来时同样不收（既有口径，与剔除幻觉同源）。
        self.assertEqual(result["group_by"], "month")
        self.assertNotIn("product", result["base_filters"])

    def test_real_values_are_kept(self):
        question = "2025年用移动支付买了多少？"
        payload = {
            "intent": "sales_query",
            "conditions": {"checkmode": "012007", "payment": "移动支付"},
        }
        result = self._recognize(question, payload)
        self.assertEqual(result["conditions"].get("checkmode"), "012007")
        self.assertEqual(result["conditions"].get("payment"), "移动支付")


class UnresolvedClarifyTest(unittest.TestCase):
    """一个可查锚点都没有的文本判为 unresolved，由编排层澄清而不是查全量。"""

    class _NeverLLM:
        backend = "never"

        def chat(self, *_args, **_kwargs):
            raise AssertionError("无锚点问题不应转派模型")

    def test_garbage_and_chitchat_are_unresolved(self):
        with registry_snapshot():
            for question in ("哈哈哈", "你好", "今天天气怎么样", "asdfghjkl", "？",
                             "帮我写一首诗", ""):
                with self.subTest(question=question):
                    self.assertTrue(extract_slots(question).get("unresolved"))

    def test_any_query_anchor_cancels_the_clarify(self):
        # 每类锚点各留一条：期间 / 组织 / 产品 / 定位单元 / 明细 / 数据集名 / 分组 / 同环比
        with registry_snapshot():
            for question in ("2024年", "示例区域甲组织层级一", "类别代码甲", "示例定位单元丙",
                             "把明细列出来", "业务域甲月报", "各组织层级一",
                             "2024年业务金额同比增长多少？"):
                with self.subTest(question=question):
                    self.assertFalse(extract_slots(question).get("unresolved"))

    def test_unresolved_short_circuits_before_the_model(self):
        result = IntentRecognizer(self._NeverLLM()).recognize("哈哈哈")
        self.assertTrue(result["unresolved"])
        self.assertEqual(result["llm_backend"], "clarify")

    def test_unresolved_does_not_swallow_the_location_candidate(self):
        # 地点候选未经引擎辨真伪，不能算锚点：否则「哈哈哈」会被当成组织条件去查全量
        with registry_snapshot():
            slots = extract_slots("哈哈哈")
        self.assertEqual(slots["base_filters"].get("organization"), "哈哈哈")
        self.assertTrue(slots["unresolved"])


if __name__ == "__main__":
    unittest.main()

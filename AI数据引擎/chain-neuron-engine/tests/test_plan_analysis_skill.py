"""派生指标技能：实际侧数据集与对比指标都从引擎声明里取，Agent 不写死列名。

引擎（/plan-derivation）负责全部天数与除法，这里用桩通道只验三件事：
下发给引擎的数据集与指标是否点名正确、文案是否说明实际侧、量纲不可比时
是否如实不编一个完成率。
"""
from __future__ import annotations

import unittest

from agent.python_agent.skills.plan_analysis import PlanAnalysisSkill

CONTRACTS = {
    "oil_monthly_sales": {
        "label": "业务域甲月报",
        "metrics": [
            {"name": "monthlysales", "label": "业务吨数"},
            {"name": "monthlyqty", "label": "业务升数"},
            {"name": "monthlyamount", "label": "业务金额"},
        ],
    },
    "oil_sales_daily": {
        "label": "业务域甲日报",
        "metrics": [
            {"name": "salesvol", "label": "业务吨数"},
            {"name": "qty", "label": "业务升数"},
            {"name": "amount", "label": "业务金额"},
        ],
    },
    "oil_sales_plan": {
        "label": "业务域甲计划",
        "metrics": [{"name": "monsalesplan", "label": "吨数业务计划"}],
    },
    "non_oil_sales": {
        "label": "业务域乙明细",
        "metrics": [{"name": "total_amount", "label": "业务金额"}],
    },
}


class StubChannel:
    def __init__(self):
        self.payloads: list[dict] = []

    def dataset_contract(self, dataset: str) -> dict:
        return CONTRACTS[dataset]

    def confirm_base_terms(self, datasets, base_filters):
        return dict(base_filters or {}), []

    def plan_derivation(self, payload: dict) -> dict:
        self.payloads.append(payload)
        actual = payload["actual_dataset"]
        metrics = CONTRACTS[actual]["metrics"]
        totals = {m["name"]: "95969.98863" for m in metrics}
        return {
            "period": {"label": "2026年9月", "basis_days": 29,
                       "period_days": 30, "elapsed_days": 29},
            "precision": "day" if actual == "oil_sales_daily" else "month",
            "metric_labels": {m["name"]: m["label"] for m in metrics},
            "actual": {
                "dataset": actual,
                "record_count": 40690,
                "aggregations": totals,
                "daily_average": dict.fromkeys(totals, "3309.31"),
            },
            "plan": {
                "metric_labels": {
                    m["name"]: m["label"] for m in CONTRACTS["oil_sales_plan"]["metrics"]
                }
            },
            "progress": {
                "available": True, "plan": "84279.00", "actual": "95969.99",
                "completion_rate": "113.87", "time_progress": "96.67",
                "gap_points": "17.20", "gap_value": "16000.00", "status": "on_track",
            },
        }


class PlanAnalysisSkillTest(unittest.TestCase):
    def setUp(self):
        self.channel = StubChannel()
        self.skill = PlanAnalysisSkill()

    def intent(self, dataset: str, question_words: str = "日均") -> dict:
        return {
            "intent": "plan_analysis",
            "dataset": "oil_sales_plan",
            "actual_dataset": dataset,
            "conditions": {"month": "202609"},
            "base_filters": {},
            "derived_metrics": ["daily_avg"] if question_words == "日均" else ["daily_avg", "progress_gap"],
        }

    def test_named_dataset_becomes_the_actual_side(self):
        result = self.skill.run(
            "2026年9月业务域甲日报的日均业务数量是多少？",
            self.intent("oil_sales_daily"),
            self.channel,
        )
        payload = self.channel.payloads[0]
        self.assertEqual(payload["actual_dataset"], "oil_sales_daily")
        # 实际侧的吨列由引擎标签挑出（日报是 salesvol，不是月报的 monthlysales）
        self.assertNotIn("progress_metric", payload)
        self.assertIn("实际侧业务域甲日报", result.answer)
        self.assertIn("业务吨数 95969.98863 吨", result.answer)
        self.assertIn("日均 3309.31 吨", result.answer)

    def test_plan_side_compares_the_same_ton_metric_of_each_dataset(self):
        intent = self.intent("oil_sales_daily", "欠进度")
        self.skill.run("2026年9月业务域甲日报的欠进度", intent, self.channel)
        payload = self.channel.payloads[0]
        self.assertEqual(payload["progress_metric"], "salesvol")
        self.assertEqual(payload["plan_metric"], "monsalesplan")
        self.assertEqual(payload["plan_dataset"], "oil_sales_plan")

    def test_dataset_without_a_ton_metric_reports_no_completion_rate(self):
        intent = self.intent("non_oil_sales", "完成率")
        result = self.skill.run("2026年9月业务域乙明细完成率", intent, self.channel)
        payload = self.channel.payloads[0]
        self.assertNotIn("plan_dataset", payload)
        self.assertIn("未计算完成率与欠进度", result.answer)


if __name__ == "__main__":
    unittest.main()

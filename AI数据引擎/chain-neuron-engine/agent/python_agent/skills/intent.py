# -*- coding: utf-8 -*-
"""意图识别：LLM 结构化输出 + 规则降级。"""
from __future__ import annotations

import json
import re
from typing import Any

from agent.python_agent.llm_client import LLMClient
from agent.python_agent.skills.semantic_slots import (
    consumed_semantic_matcher,
    extract_slots,
)

INTENT_PROMPT = """你是通用事实数据引擎的意图解析器。只输出 JSON，不要解释。
schema:
{
  "intent": "sales_query|plan_analysis|data_import|rule_config|chat",
  "dataset": "registered dataset id（明确指定业务类型时填写；否则保留默认值并使用 dataset_scope=all_actual）",
  "conditions": {"year":"2024","season":"春季","month":"202401","relative_period":"previous_week","big_area":"","area":"","station":"","gas_code":"","check_mode":""},
  "datasets": ["registered dataset id"],
  "dataset_scope": "single|all_actual|plan|explicit_multi",
  "base_filters": {"product":"类别甲","organization":"示例区域甲"},
  "group_by": "year|season|month|big_area|area|station|gas_code|null",
  "comparisons": ["yoy", "mom"],
  "detail": false
}
规则：
- 问总量/排名/趋势用 sales_query，detail=false；问具体单据明细 detail=true；
- 未明确“产品/业务域乙/计划”等业务类型时，业务查询使用 dataset_scope=all_actual，覆盖当前启用的全部通用事实数据集；维表/主数据和计划类数据不自动纳入；
- “同比”定义为当前期间与上一年度同一期间，“环比”定义为当前期间与紧邻上一个期间；把它们放入 comparisons；
- “各月/各组织层级一/各产品”把对应层填到 group_by；
- 计划完成率/计划对比用 plan_analysis，dataset=oil_sales_plan；如果问题同时要求业务实际、计划、日均或欠进度，使用 datasets=[oil_monthly_sales,oil_sales_plan]、dataset_scope=explicit_multi；
- 月份一律 YYYYMM（如 202401）。
- 如果问题点名了已注册数据集/数据源，必须输出对应 dataset/datasets；产品、组织、类别等跨数据集语义放入 base_filters，交给引擎基座翻译，不要自行改写成事实表编码。
- 时间、数据集和“产品”基座槽位由本地确定性解析器复核；不要把“上半年/下半年”输出成 season，必须交给时间基座转换为月份集合。
用户问题："""


class IntentRecognizer:
    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    def recognize(self, question: str) -> dict[str, Any]:
        rule_data = extract_slots(question)
        # 规则层一个锚点都没有时不转派模型：模型只会替乱码猜一套条件，
        # 既不确定、又让两套 Agent 分叉。直接短路成澄清反问。
        if rule_data.get("unresolved"):
            data = dict(rule_data)
            data["llm_backend"] = "clarify"
            return data
        if _can_use_rule_fast_path(question, rule_data):
            data = dict(rule_data)
            data.setdefault("conditions", {})
            data.setdefault("filters", {})
            data.setdefault("base_filters", {})
            data.setdefault("datasets", [])
            data.setdefault("dataset_scope", "single")
            data.setdefault("comparisons", [])
            data.setdefault("group_by", None)
            data["llm_backend"] = "rules"
            return data
        text = self.llm.chat(
            [{"role": "user", "content": INTENT_PROMPT + question}],
            expect_json=True,
        )
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            data = {}
        # 规整
        data.setdefault("intent", "chat")
        data.setdefault("conditions", {})
        data.setdefault("filters", {})
        data.setdefault("base_filters", {})
        data.setdefault("datasets", [])
        data.setdefault("dataset_scope", "single")
        data.setdefault("comparisons", [])
        data.setdefault("group_by", None)
        data = _merge_rule_slots(question, rule_data, data)
        data["llm_backend"] = self.llm.backend
        return data


def _can_use_rule_fast_path(question: str, rule_data: dict[str, Any]) -> bool:
    """明确的时间/业务查询直接走确定性槽位，复杂语义仍交给模型。"""
    if rule_data.get("intent") not in {"sales_query", "plan_analysis"}:
        return False
    if rule_data.get("detail"):
        return False
    if not any(
        token in question
        for token in (
            "业务", "业务金额", "业务金额", "业务数量", "业务数量", "记录", "金额",
            "吨", "升", "完成率", "计划", "同比", "环比",
        )
    ):
        return False
    # 已经能由规则解析出时间、分组或基座语义时，模型没有额外必要。
    return bool(
        rule_data.get("conditions")
        or rule_data.get("base_filters")
        or rule_data.get("group_by")
        or "总" in question
    )


def _merge_rule_slots(
    question: str,
    rule_data: dict[str, Any],
    model_data: dict[str, Any],
) -> dict[str, Any]:
    """合并模型与确定性槽位，保护基座入口不被模型幻觉覆盖。"""
    merged = dict(model_data)

    def _present(value: Any) -> bool:
        if value in (None, "", [], {}, ()):  # 模型常用空字符串填槽位
            return False
        return str(value).strip().lower() not in {"null", "none"}

    def _clean_mapping(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items() if _present(item)}

    rule_intent = str(rule_data.get("intent") or "")
    if rule_intent not in {"chat", ""}:
        merged["intent"] = rule_intent

    default_dataset = "oil_sales_plan" if ("计划" in question or "指标" in question) else "oil_monthly_sales"
    rule_dataset = str(rule_data.get("dataset") or default_dataset)
    merged["dataset"] = rule_dataset
    merged["dataset_scope"] = str(
        rule_data.get("dataset_scope")
        or ("plan" if "计划" in question or "指标" in question else "single")
    )
    merged["datasets"] = [
        str(value).strip() for value in (rule_data.get("datasets") or [])
        if str(value).strip()
    ]
    merged["comparisons"] = [
        str(value).strip().lower()
        for value in (rule_data.get("comparisons") or [])
        if str(value).strip().lower() in {"yoy", "mom"}
    ]
    # 日均/欠进度要算哪个派生指标由规则槽位决定：模型常把它们当普通业务查询，
    # 于是只回合计、不报期间天数口径。
    merged["derived_metrics"] = [
        str(value) for value in (rule_data.get("derived_metrics") or [])
        if str(value).strip() in {"daily_avg", "progress_gap"}
    ]
    # 期间是不是 Agent 替用户默认的，也必须由规则槽位说，模型不知道这件事。
    merged["period_defaulted"] = bool(rule_data.get("period_defaulted"))

    model_conditions = _clean_mapping(merged.get("conditions"))
    rule_conditions = _clean_mapping(rule_data.get("conditions"))
    # 模型把规则已经消费掉的语义词又当成过滤值下发时（「欠进度」→checkmode），
    # 在进引擎前剔除：引擎按"取值不存在"显式拒答会把整条进度问题说废。
    # 剔除后 Python 的槽位就等于 Harness 的规则槽位，所以不额外写提示行，
    # 否则两套答案会因为这个只有模型才有的分支而逐字比对失败。
    consumed = consumed_semantic_matcher(question)
    model_conditions = {
        key: value for key, value in model_conditions.items()
        if not consumed(value)
    }
    # 时间槽位必须以规则解析/时间基座为准。尤其是模型经常把半年误写成 season。
    # 定位单元名同理：模型改写成物理列名或截断站名都会让过滤静默失效，
    # 只有确定性槽位给出的 station 能保证两套 Agent 给出同一个数。
    protected_time = {"year", "month", "yyyymm", "season", "quarter", "relative_period", "date_from", "date_to"}
    for key in protected_time | {"station"}:
        if key in rule_conditions:
            model_conditions[key] = rule_conditions[key]
        elif key in model_conditions:
            model_conditions.pop(key, None)
    merged["conditions"] = model_conditions

    model_filters = _clean_mapping(merged.get("filters"))
    rule_filters = _clean_mapping(rule_data.get("filters"))
    model_filters = {
        key: value for key, value in model_filters.items()
        if not consumed(value)
    }
    protected_filters = {"year", "month", "yyyymm", "season", "quarter", "relative_period", "date_from", "date_to", "category", "grade", "gas_code", "station"}
    for key in protected_filters:
        if key in rule_filters:
            model_filters[key] = rule_filters[key]
        elif key in model_filters:
            model_filters.pop(key, None)
    merged["filters"] = model_filters

    model_base = _clean_mapping(merged.get("base_filters"))
    rule_base = _clean_mapping(rule_data.get("base_filters"))
    model_base = {
        key: value for key, value in model_base.items()
        if not consumed(value)
    }
    # 产品语义是最容易被 LLM 错投的基座入口：规则明确识别就使用规则值，
    # 规则未识别则不接受模型自行捏造的 product，防止基座错误阻断/放大查询。
    if "product" in rule_base:
        model_base["product"] = rule_base["product"]
    else:
        model_base.pop("product", None)
    for key, value in rule_base.items():
        if key != "product":
            model_base[key] = value
    merged["base_filters"] = model_base

    if rule_data.get("group_by") is not None:
        merged["group_by"] = rule_data["group_by"]
    elif str(merged.get("group_by") or "").strip().lower() in {"null", "none", ""}:
        merged["group_by"] = None
    if rule_data.get("detail"):
        merged["detail"] = True
    if rule_data.get("datasets"):
        merged["datasets"] = list(rule_data["datasets"])
    return merged


def _mock_intent(question: str) -> dict[str, Any]:
    """保留入口名，内部转用语义槽位提取。"""
    return extract_slots(question)

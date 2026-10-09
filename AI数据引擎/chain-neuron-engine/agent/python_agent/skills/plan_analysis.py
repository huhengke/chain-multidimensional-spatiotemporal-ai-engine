# -*- coding: utf-8 -*-
"""计划分析技能：期间天数、日均、完成率、时间进度与欠进度全部由引擎用
Decimal 计算（`/api/v1/engine/plan-derivation`），本模块只做排版。"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from agent.python_agent.engine_channel_client import EngineChannelClient
from agent.python_agent.skills.base import BaseSkill, SkillResult
from agent.python_agent.skills.sales_query import _metric_label, _metric_unit
from agent.python_agent.skills.semantic_slots import DEFAULT_ACTUAL_DATASET, PLAN_DATASET

# 完成率要同量纲对比：实际侧与计划侧都按引擎声明的指标标签挑「吨」量纲的那一列，
# 不在 Agent 里写死列名（月报 monthlysales / 日报 salesvol 标签同为「业务吨数」）。
TON_MARK = "吨"


class PlanAnalysisSkill(BaseSkill):
    name = "plan_analysis"

    def run(self, question: str, intent: dict,
            channel: EngineChannelClient) -> SkillResult:
        conditions = dict(intent.get("conditions") or {})
        base_filters = dict(intent.get("base_filters") or {})
        derived = [str(value) for value in (intent.get("derived_metrics") or [])]
        actual_dataset = str(intent.get("actual_dataset") or DEFAULT_ACTUAL_DATASET)
        actual_label, actual_metric = _ton_metric(channel, actual_dataset)
        plan_label, plan_metric = _ton_metric(channel, PLAN_DATASET)
        comparable = bool(actual_metric and plan_metric)
        needs_plan = ("progress_gap" in derived
                      or any(word in question for word in ("计划", "完成率")))

        payload: dict = {"actual_dataset": actual_dataset, "conditions": conditions}
        notes: list[str] = []
        incomparable = (
            f"计划与欠进度：{actual_label}没有与{plan_label}同为{TON_MARK}量纲的可比指标，"
            "未计算完成率与欠进度。"
        )
        if needs_plan and comparable:
            actual_base, actual_notes = channel.confirm_base_terms(
                [actual_dataset], base_filters
            )
            notes = list(actual_notes)
            # 计划侧不做"探测不到就丢条件"：丢了组织条件等于把「示例区域甲的计划」答成
            # 全公司的计划，比拒答危险（与 #42 同一类）。原条件交给引擎，引擎判不
            # 了会如实回"未执行查询"，完成率显式缺失而不是算出一个错的数。
            payload.update({
                "base_filters": actual_base,
                "plan_dataset": PLAN_DATASET,
                "plan_base_filters": base_filters,
                "plan_metric": plan_metric,
                "progress_metric": actual_metric,
            })
        else:
            actual_base, actual_notes = channel.confirm_base_terms(
                [actual_dataset], base_filters
            )
            notes = list(dict.fromkeys(actual_notes))
            payload["base_filters"] = actual_base

        data = channel.plan_derivation(payload)
        if data.get("unsupported"):
            return SkillResult(
                answer=str(data.get("message") or "本次条件无法确认，未执行派生计算。")
                + _suffix(notes),
                data=data,
                thought_chain=[],
            )

        period = data.get("period") or {}
        head = (
            f"{period.get('label') or '当前期间'}"
            f"（{_basis_text(period, data.get('precision'))}，实际侧{actual_label}）"
        )
        notice = str(data.get("notice") or "")
        actual_line = _actual_lines(head, data, question, intent, actual_metric)
        lines = [
            actual_line or notice
            or f"{head}该期间没有符合条件的实际数据，日均与进度不适用。"
        ]
        if needs_plan:
            lines.append(
                _progress_line(data.get("progress") or {}, data, plan_metric)
                if comparable else incomparable
            )
        if notice and notice not in lines:
            lines.append(notice)
        if intent.get("period_defaulted"):
            # 提问没给时间时按当前自然月摊算是**我们的口径**，不是用户的问法，
            # 必须写明，否则"本月欠进度"会被读成"全年欠进度"。
            lines.append(
                f"说明：提问未指定期间，本次按当前自然月"
                f"（{period.get('calendar_label') or period.get('label') or '本月'}）摊算。"
            )
        return SkillResult(
            answer="\n".join(line for line in lines if line) + _suffix(notes),
            data=data,
            thought_chain=[],
        )


def _ton_metric(channel: EngineChannelClient, dataset: str) -> tuple[str, str]:
    """从引擎声明里取该数据集的「吨」量纲指标：返回 (数据集中文名, 指标名)。

    指标名一律不写死在 Agent 里；改名或新增数据集只认引擎给的标签。
    """
    contract = channel.dataset_contract(dataset) or {}
    label = str(contract.get("label") or dataset)
    for metric in contract.get("metrics") or []:
        text = f"{metric.get('label') or ''}{metric.get('name') or ''}"
        if TON_MARK in text:
            return label, str(metric.get("name") or "")
    return label, ""


def _basis_text(period: dict, precision: str | None) -> str:
    days = int(period.get("basis_days") or 0)
    total = int(period.get("period_days") or 0)
    note = "日粒度真值" if precision == "day" else "月粒度均摊估算"
    if int(period.get("elapsed_days") or 0) < total:
        return f"已过 {days} 天/共 {total} 天，{note}"
    return f"{days} 天，{note}"


def _actual_lines(head: str, data: dict, question: str, intent: dict,
                  default_metric: str) -> str:
    actual = data.get("actual") or {}
    if not actual.get("record_count"):
        return ""
    labels = data.get("metric_labels") or {}
    totals = actual.get("aggregations") or {}
    daily = actual.get("daily_average") or {}
    wanted = [
        str(value) for value in (intent.get("metrics") or []) if str(value).strip()
    ] or _metrics_by_mark(question, labels) or [default_metric]
    keys = [key for key in wanted if key in totals] or list(totals)[:1]
    parts = []
    for key in keys:
        label = str(_metric_label(key, labels))
        unit = _metric_unit(key, label)
        text = f"{label} {totals[key]}{unit}"
        if key in daily:
            text += f"，日均 {daily[key]}{unit}"
        parts.append(text)
    parts.append(f"明细记录 {actual.get('record_count', 0)} 条")
    return f"{head}" + "；".join(parts) + "。"


def _metrics_by_mark(question: str, labels: dict[str, Any]) -> list[str]:
    """按提问里的量纲词在引擎指标标签中挑列，Agent 侧不写死任何列名。"""
    marks = (("金额", ("金额", "业务金额")), ("吨", ("吨",)), ("升", ("升",)))
    for word, candidates in marks:
        if word not in question:
            continue
        picked = [
            str(name) for name, label in labels.items()
            if any(mark in str(label or "") for mark in candidates)
        ]
        if picked:
            return picked
    return []


def _progress_line(progress: dict, data: dict, plan_metric: str) -> str:
    """进度文案只排版引擎给的数字：口径是「计划 X / 实际 Y → 完成率、时间进度、欠进度」。"""
    if not progress:
        return ""
    if not progress.get("available"):
        return f"计划与欠进度：{progress.get('message') or '该期间没有可比的计划数据。'}"
    plan = data.get("plan") or {}
    labels = plan.get("metric_labels") or {}
    label = str(_metric_label(plan_metric, labels)) or "业务计划"
    unit = _metric_unit(plan_metric, label)
    gap_points = Decimal(str(progress.get("gap_points") or "0"))
    gap_value = Decimal(str(progress.get("gap_value") or "0"))
    behind = progress.get("status") == "behind"
    ton_label = "缺口" if behind else "超出"
    return (
        f"{label} {progress.get('plan')}{unit}，实际 {progress.get('actual')}{unit}，"
        f"完成率 {progress.get('completion_rate')}%，时间进度 {progress.get('time_progress')}%，"
        f"{'欠进度' if behind else '进度超出'} {abs(gap_points)} 个百分点"
        f"（{ton_label} {abs(gap_value)}{unit}）。"
    )


def _suffix(notes: list[str]) -> str:
    items = [str(note) for note in (notes or []) if str(note).strip()]
    return "\n提示：" + "；".join(dict.fromkeys(items)) if items else ""

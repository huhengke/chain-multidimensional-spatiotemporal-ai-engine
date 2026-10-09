# -*- coding: utf-8 -*-
"""查询路由：明细查询、聚合查询（含路由可视化路径）、计划派生指标。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from engine.api.schemas_api import (
    AggregationsQueryRequest,
    PlanDerivationRequest,
    RecordsQueryRequest,
)
from engine.core.errors import AppException, BadRequestError
from engine.features.contracts import build_dataset_contract
from engine.ingestion.cleaner import DAY_LEVEL_FORMATS
from engine.query.derived_metrics import (
    daily_average,
    grain_scoped_conditions,
    period_span,
    plan_progress,
)
from engine.query.foundation import compile_base_filters
from engine.query.time_foundation import compile_time_conditions
from engine.query.unified import (
    _fact_aggregate_actual,
    _should_use_fact_aggregate,
    observed_day_coverage,
)
from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, get_db
from engine.storage.dataset_registry import registry
from engine.storage.adapters.base import StorageAdapter
from engine.storage.adapters.registry import storage_adapters
from engine.storage.dimensions import LEVELS_BY_DATASET
from engine.storage.spiral_store import SpiralStore
from engine.storage.tree_router import TreeRouter

router = APIRouter(prefix="/api/v1/engine", tags=["query"])


def _active_storage() -> tuple[RuntimeProfileContext, StorageAdapter]:
    try:
        context = resolve_active_profile()
        return context, storage_adapters.require(context.storage_type)
    except RuntimeProfileError as exc:
        raise AppException(
            "E4501",
            "活动 AI 数据引擎运行配置不可用",
            detail={"error": str(exc)},
            http_status=503,
        ) from exc


def _runtime_contract(
    session: Session,
    dataset: str,
    context: RuntimeProfileContext,
    adapter: StorageAdapter,
) -> dict[str, Any]:
    if context.storage_type == "legacy_sqlite":
        return build_dataset_contract(registry.require(dataset))
    return adapter.dataset_contract(session, dataset, context)


def _merge_condition(existing: object, incoming: object) -> object:
    """合并层级条件；已有条件与基座翻译均存在时取交集。"""
    if existing in (None, "", []):
        return incoming
    left = existing if isinstance(existing, list) else [existing]
    right = incoming if isinstance(incoming, list) else [incoming]
    intersection = [value for value in left if value in right]
    return intersection


def _require_spiral_dataset(dataset: str) -> None:
    """闸门：数据集必须已注册且参与螺旋树（新增数据集自动放行）。"""
    spec = registry.get(dataset)
    if spec is None or not spec.is_spiral:
        raise BadRequestError(
            f"未知数据集：{dataset}",
            detail={"known": registry.spiral_ids()},
        )


def _node_brief(store: SpiralStore, node_id: str) -> dict:
    node = store.get_node(node_id)
    if node is None:
        return {"node_id": node_id}
    return {
        "node_id": node.node_id,
        "level": node.level,
        "level_name": node.level_name,
        "node_value": node.node_value,
        "dimensions": node.dimensions,
        "record_count": node.record_count,
        "aggregations": node.aggregations,
        "grown": node.grown,
    }


@router.post("/records/query")
def records_query(
    body: RecordsQueryRequest,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "CNTS 活动 Catalog 不支持旧 records/query 明细接口；请使用统一查询接口。",
            detail={**context.response_metadata(), "fallback": "forbidden"},
        )
    _require_spiral_dataset(body.dataset)

    store = SpiralStore(session)
    time_compiled = compile_time_conditions(filters=body.filters)
    query_filters = time_compiled["filters"]
    data = store.query_records(
        body.dataset, query_filters, body.page, body.page_size
    )

    # 路由可视化：仅在有收敛条件时遍历（防止全树展开）
    trace = None
    if query_filters:
        level_names = set(LEVELS_BY_DATASET[body.dataset])
        conditions = {k: v for k, v in query_filters.items() if k in level_names}
        if "store_id" in query_filters:
            conditions["station"] = query_filters["store_id"]
        if "yyyymm" in query_filters:
            conditions["month"] = query_filters["yyyymm"]
        trace = TreeRouter(store).route(
            body.dataset, conditions, is_aggregate=False
        ).to_dict()

    return {**data, "route": trace, "time_foundation": time_compiled["audit"]}


def _merge_nodes_by_value(nodes: list[dict]) -> list[dict]:
    """按 node_value 合并同值节点（Decimal 求和）。

    分组层的物理节点按 season/month × 分组值组织，不存在“年×分组值”节点；
    当时间上层未完全指定时（如只给年份），同一分组值会跨多个父路径出现。
    合并在引擎层完成且只用 decimal.Decimal —— Agent/LLM 不参与加减。
    """
    merged: dict[str, dict] = {}
    order: list[str] = []
    for n in nodes:
        key = str(n.get("node_value"))
        if key not in merged:
            n = dict(n)
            n["merged_from"] = 1
            merged[key] = n
            order.append(key)
            continue
        target = merged[key]
        target["record_count"] = (target.get("record_count") or 0) + (n.get("record_count") or 0)
        tgt_aggs = target.get("aggregations") or {}
        for ak, av in (n.get("aggregations") or {}).items():
            tgt_aggs[ak] = str(
                Decimal(str(tgt_aggs.get(ak, "0"))) + Decimal(str(av))
            )
        target["merged_from"] += 1
    return [merged[k] for k in order]


@router.post("/aggregations/query")
def aggregations_query(
    body: AggregationsQueryRequest,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        result = adapter.execute_query(
            session,
            {
                "dataset": body.dataset,
                "conditions": dict(body.conditions or {}),
                "filters": dict(body.conditions or {}),
                "base_filters": dict(body.base_filters or {}),
                "group_by": [body.group_by] if body.group_by else [],
                "comparisons": [],
                "detail": False,
            },
            f"aggregate-{uuid4().hex[:12]}",
            context,
        )
        actual = result.get("actual") or {}
        return {
            "dataset": body.dataset,
            "mode": "aggregate",
            "group_by": body.group_by,
            "nodes": actual.get("nodes") or [],
            "group_merge": {
                "physical_nodes": len(actual.get("nodes") or []),
                "merged_nodes": len(actual.get("nodes") or []),
            },
            "route": actual.get("route"),
            "execution": "cnts_generation",
            "record_count": int(actual.get("record_count") or 0),
            "aggregations": dict(actual.get("aggregations") or {}),
            "base_filters": body.base_filters,
            "base_decision": actual.get("base_decision"),
            "time_foundation": (result.get("query_plan") or {}).get("time_foundation"),
            **context.response_metadata(),
        }
    _require_spiral_dataset(body.dataset)

    store = SpiralStore(session)
    router_obj = TreeRouter(store)
    time_compiled = compile_time_conditions(conditions=body.conditions)
    conditions = time_compiled["conditions"]
    base_decision = None
    if body.base_filters:
        compiled, base_decision = compile_base_filters(
            session, body.dataset, body.base_filters
        )
        if base_decision.get("blocked"):
            return {
                "dataset": body.dataset,
                "mode": "aggregate",
                "group_by": body.group_by,
                "nodes": [],
                "group_merge": {"physical_nodes": 0, "merged_nodes": 0},
                "route": None,
                "base_filters": body.base_filters,
                "base_decision": base_decision,
                "time_foundation": time_compiled["audit"],
                "warnings": base_decision.get("warnings") or [],
            }
        for key, value in compiled.items():
            conditions[key] = _merge_condition(conditions.get(key), value)
    spec = registry.require(body.dataset)
    groups = [body.group_by] if body.group_by else []
    if _should_use_fact_aggregate(session, spec, conditions, groups, False):
        actual = _fact_aggregate_actual(
            session, spec, conditions, groups,
            (base_decision or {}).get("display_conditions"),
        )
        actual["base_decision"] = base_decision
        return {
            "dataset": body.dataset,
            "mode": "aggregate",
            "group_by": body.group_by,
            "nodes": actual["nodes"],
            "group_merge": {
                "physical_nodes": 0,
                "merged_nodes": len(actual["nodes"]),
            },
            "route": actual["route"],
            "execution": "fact_aggregate",
            "execution_reason": "exact_scope_required",
            "record_count": actual["record_count"],
            "aggregations": actual["aggregations"],
            "base_filters": body.base_filters,
            "base_decision": base_decision,
            "time_foundation": time_compiled["audit"],
        }
    trace = router_obj.route(
        body.dataset, conditions, is_aggregate=True, group_by=body.group_by
    )
    nodes = [_node_brief(store, nid) for nid in trace.target_node_ids]

    # 同值合并是通用的：分组列表去重求和；无分组时（如只指定 branch + 年）
    # 把跨 season/month 父路径的同名实体收敛为一个聚合值，避免调用方只取首节点
    before = len(nodes)
    nodes = _merge_nodes_by_value(nodes)
    group_merge = {"physical_nodes": before, "merged_nodes": len(nodes)}

    if not nodes:
        # 修剪后目标层可能不再有节点（低于生长阈值/深层未物化）：
        # 用注册表驱动的事实表聚合兜底，数值仍全部由引擎 Decimal 累加。
        actual = _fact_aggregate_actual(
            session, registry.require(body.dataset),
            conditions, [body.group_by] if body.group_by else [],
            (base_decision or {}).get("display_conditions"),
        )
        return {
            "dataset": body.dataset,
            "mode": "aggregate",
            "group_by": body.group_by,
            "nodes": actual["nodes"],
            "group_merge": {
                "physical_nodes": 0, "merged_nodes": len(actual["nodes"]),
            },
            "route": actual["route"],
            "fallback": "fact_aggregate",
            "record_count": actual["record_count"],
            "aggregations": actual["aggregations"],
            "base_filters": body.base_filters,
            "base_decision": base_decision,
            "time_foundation": time_compiled["audit"],
        }

    return {
        "dataset": body.dataset,
        "mode": "aggregate",
        "group_by": body.group_by,
        "nodes": nodes,
        "group_merge": group_merge,
        "route": trace.to_dict(),
        "base_filters": body.base_filters,
        "base_decision": base_decision,
        "time_foundation": time_compiled["audit"],
    }


def _unified_totals(
    session: Session,
    dataset: str,
    conditions: dict[str, Any],
    base_filters: dict[str, Any] | None,
    context: RuntimeProfileContext,
    adapter: StorageAdapter,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """派生指标取数走统一查询：条件键归一、取值编译、基座判定都只有一份实现。

    同时带回编译后的条件：观测口径（有数据的天数）必须打在取数口径用的
    同一份 WHERE 上，自己再拼一次过滤等于开出第二份实现。
    """
    request = {
        "dataset": dataset,
        "conditions": dict(conditions or {}),
        "filters": dict(conditions or {}),
        "base_filters": dict(base_filters or {}),
        "group_by": [],
        "comparisons": [],
        "detail": False,
    }
    result = adapter.execute_query(
        session,
        request,
        f"derived-{uuid4().hex[:12]}",
        context,
    )
    compiled = (result.get("query_plan") or {}).get("compiled_conditions") or {}
    return result.get("actual") or {}, compiled


def _unified_message(actual: dict[str, Any]) -> str:
    if actual.get("unsupported_conditions"):
        return str(actual.get("unsupported_message") or "本次条件无法在该数据集确认，未执行查询。")
    if actual.get("precision_gap"):
        return str(actual.get("precision_gap_message") or "该数据集的时间精度不足以回答本次期间。")
    return ""


def _scoped_totals(
    session: Session,
    dataset: str,
    conditions: dict[str, Any] | None,
    base_filters: dict[str, Any] | None,
    span: dict[str, Any],
    context: RuntimeProfileContext,
    adapter: StorageAdapter,
) -> dict[str, Any]:
    """取一个数据集在期间内的合计；月粒度数据集先按整月折算。"""
    contract = _runtime_contract(session, dataset, context, adapter)
    # 日粒度判定与清洗/统一查询用同一个格式集合："dd" in "d/m/yyyy" 是假，
    # 会把业务域乙这类日粒度数据集误判成月粒度，日均摊算与精度说明都跟着错。
    time_format = str((contract.get("time") or {}).get("format") or "")
    day_grain = time_format in DAY_LEVEL_FORMATS or time_format in {
        "yyyy-mm-dd",
        "%Y-%m-%d",
    }
    scoped, reason = grain_scoped_conditions(conditions, span, day_grain)
    if reason:
        return {"message": f"数据集 {contract.get('label') or dataset}：{reason}"}
    actual, compiled = _unified_totals(
        session,
        dataset,
        scoped,
        base_filters,
        context,
        adapter,
    )
    message = _unified_message(actual)
    if message:
        return {"message": message}
    months = scoped.get("month")
    payload = {
        "totals": dict(actual.get("aggregations") or {}),
        "record_count": int(actual.get("record_count") or 0),
        "months": months if isinstance(months, list) else [],
        "grain": "day" if day_grain else "month",
    }
    # 只有日粒度数据集能观测"哪些天真的有数"：月粒度缺月已经被整月折算口径管住了。
    if day_grain and payload["record_count"]:
        if context.storage_type == "legacy_sqlite":
            payload["coverage"] = observed_day_coverage(
                session, registry.require(dataset), compiled
            )
        else:
            coverage = adapter.execute_query(
                session,
                {
                    "dataset": dataset,
                    "conditions": dict(scoped),
                    "filters": dict(scoped),
                    "base_filters": dict(base_filters or {}),
                    "group_by": ["day"],
                    "comparisons": [],
                    "detail": False,
                },
                f"coverage-{uuid4().hex[:12]}",
                context,
            )
            day_values = sorted(
                {
                    str((node.get("dimensions") or {}).get("day") or "")
                    for node in ((coverage.get("actual") or {}).get("nodes") or [])
                    if str((node.get("dimensions") or {}).get("day") or "")
                }
            )
            payload["coverage"] = {
                "days": len(day_values),
                "through": day_values[-1] if day_values else None,
            }
    return payload


@router.post("/plan-derivation")
def plan_derivation(
    body: PlanDerivationRequest,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """日均业务数量 / 完成率 / 时间进度 / 欠进度。

    期间天数与所有除法都在引擎侧用 Decimal 完成，Agent 只排版：分母用真实
    自然日天数（按月取该月实际天数、多月累加），进行中期间只按已发生天数摊。
    """
    context, adapter = _active_storage()
    as_of = None
    if body.as_of:
        try:
            as_of = date.fromisoformat(str(body.as_of).strip())
        except ValueError:
            raise BadRequestError(f"as_of 必须是 yyyy-mm-dd：{body.as_of}")

    time_compiled = compile_time_conditions(conditions=body.conditions)
    span = period_span(
        time_compiled["conditions"],
        (time_compiled["audit"] or {}).get("resolved_period"),
        as_of=as_of,
    )
    if span is None:
        return {
            "unsupported": True,
            "message": (
                "日均与欠进度需要先确定自然日区间，本次时间条件"
                f"（{body.conditions or '无'}）无法解析为年/月/周/日期间。"
            ),
            **context.response_metadata(),
        }

    scoped_actual = _scoped_totals(
        session,
        body.actual_dataset,
        body.conditions,
        body.base_filters,
        span,
        context,
        adapter,
    )
    if scoped_actual.get("message"):
        return {
            "unsupported": True,
            "message": scoped_actual["message"],
            "period": span,
            **context.response_metadata(),
        }

    basis_days = int(span["basis_days"])
    actual_totals = scoped_actual["totals"]
    record_count = scoped_actual["record_count"]
    coverage = scoped_actual.get("coverage") or {}
    observed_days = int(coverage.get("days") or 0)
    actual_contract = _runtime_contract(
        session, body.actual_dataset, context, adapter
    )
    result: dict[str, Any] = {
        "period": span,
        "precision": scoped_actual["grain"],
        "metric_labels": {
            str(metric.get("name")): str(metric.get("label") or metric.get("name"))
            for metric in (actual_contract.get("metrics") or [])
        },
        "actual": {
            "dataset": body.actual_dataset,
            "record_count": record_count,
            "aggregations": actual_totals,
            "daily_average": daily_average(actual_totals, basis_days),
            "basis_days": basis_days,
            "months": scoped_actual["months"],
            "days_with_data": observed_days or None,
            "data_through": str(coverage.get("through") or "") or None,
            # 无数据 ≠ 零业务数量：没有命中记录时日均不能摊成 0。
            "has_data": record_count > 0,
        },
    }
    if basis_days <= 0:
        result["notice"] = (
            f"{span['label']}没有已发生天数，日均与时间进度不适用。"
        )
    elif not record_count:
        result["notice"] = (
            f"{span['label']}在该数据集没有符合条件的实际数据，日均不适用。"
        )
    elif scoped_actual["months"]:
        folded = "、".join(scoped_actual["months"])
        result["notice"] = (
            f"{span['label']}在月粒度数据集上按整月（{folded}）取数，"
            f"日均分母取 {basis_days} 个自然日。"
        )
    elif 0 < observed_days < basis_days:
        # 分母按日历已过天数摊、时间进度按日历走，都是选定口径，不改数；
        # 但数据滞后必须写在答案里，否则「日均 3352 吨」会被读成「每天真卖 3352 吨」，
        # 差一天的欠进度也会被读成真欠了一天。
        result["notice"] = (
            f"数据仅到 {coverage['through']}（期间内 {observed_days} 天有实际数据），"
            f"日均按 {basis_days} 个自然日摊算，时间进度按日历口径计算。"
        )

    if body.plan_dataset and body.plan_metric and body.progress_metric:
        if not record_count:
            result["progress"] = {
                "available": False,
                "message": (
                    f"{span['label']}没有符合条件的实际数据，"
                    "不按 0 计算完成率与欠进度。"
                ),
            }
        else:
            scoped_plan = _scoped_totals(
                session,
                body.plan_dataset,
                body.conditions,
                body.plan_base_filters
                if body.plan_base_filters is not None
                else body.base_filters,
                span,
                context,
                adapter,
            )
            if scoped_plan.get("message"):
                result["progress"] = {
                    "available": False,
                    "message": scoped_plan["message"],
                }
            else:
                plan_totals = scoped_plan["totals"]
                result["plan"] = {
                    "dataset": body.plan_dataset,
                    "record_count": scoped_plan["record_count"],
                    "aggregations": plan_totals,
                    "months": scoped_plan["months"],
                    "has_data": bool(scoped_plan["record_count"]),
                    "metric_labels": {
                        str(metric.get("name")): str(metric.get("label") or metric.get("name"))
                        for metric in (
                            _runtime_contract(
                                session, body.plan_dataset, context, adapter
                            ).get("metrics")
                            or []
                        )
                    },
                }
                result["progress"] = plan_progress(
                    plan_totals.get(body.plan_metric),
                    actual_totals.get(body.progress_metric),
                    span,
                )
    result.update(context.response_metadata())
    return result

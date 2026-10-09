# -*- coding: utf-8 -*-
"""统一注册表查询：基座翻译 → 时间编译 → 中通拓扑路由 → 聚合直读。"""
from __future__ import annotations

import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from engine.core.errors import BadRequestError
from engine.features.registry import profile_dataset
from engine.ingestion.cleaner import DAY_LEVEL_FORMATS
from engine.query.foundation import compile_base_filters, rehost_org_conditions
from engine.query.experience import (
    actual_sales_scope,
    business_aggregates_for_dataset,
    comparison_definitions,
)
from engine.query.level_resolution import (
    TIME_LEVELS,
    display_map,
    level_hint,
    resolve_condition_keys,
    resolve_group_by,
    resolve_level_values,
)
from engine.query.time_foundation import compile_time_conditions
from engine.storage.dataset_registry import registry
from engine.storage.fact_tables import get_dim_source
from engine.storage.dynamic_table import get_dynamic_table
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_store import SpiralStore
from engine.storage.tree_router import TreeRouter


def _row_mapping(row: Any) -> dict[str, Any]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return {str(key): value for key, value in mapping.items()}
    return {
        str(key): getattr(row, key, None)
        for key in dir(row)
        if not key.startswith("_")
        and not callable(getattr(row, key, None))
    }


def _value_matches(actual: Any, wanted: Any) -> bool:
    if actual is None:
        return False
    text = str(actual)
    values = wanted if isinstance(wanted, (list, tuple, set)) else [wanted]
    return any(
        str(value) == text or str(value).lower() in text.lower()
        for value in values
    )


def _dynamic_where_clauses(spec, table, conditions: dict[str, Any]) -> list[Any]:
    """把逻辑层级键映射为注册表声明的物理列。"""
    clauses: list[Any] = []
    for key, value in conditions.items():
        if key in {"date_from", "date_to"}:
            column = table.c.get("__time")
            if column is None or value in (None, "", []):
                continue
            clauses.append(column >= value if key == "date_from" else column <= value)
            continue
        physical = spec.level_value_key.get(key, key)
        column = table.c.get(physical)
        if column is None or value in (None, "", []):
            continue
        values = value if isinstance(value, (list, tuple, set)) else [value]
        values = [item for item in values if item not in (None, "")]
        if not values:
            continue
        clauses.append(column.in_(list(values)) if len(values) > 1 else column == values[0])
    return clauses


def _column_value_hits(
    session: Session, table, column_name: str, texts: list[str]
) -> list[str]:
    """某物理列里真实存在的取值（先整值相等，再包含回退，不猜值）。"""
    column = table.c[column_name]
    found: set[str] = set()
    rows = session.execute(
        select(func.distinct(column))
        .where(column.in_(texts), column.isnot(None), column != "")
        .limit(200)
    ).all()
    found.update(str(row[0]) for row in rows if row[0] not in (None, ""))
    for term in [value for value in texts if value not in found]:
        rows = session.execute(
            select(func.distinct(column))
            .where(column.contains(term), column.isnot(None), column != "")
            .limit(200)
        ).all()
        found.update(str(row[0]) for row in rows if row[0] not in (None, ""))
    return sorted(found)


def _compile_level_values(
    session: Session, spec, conditions: dict[str, Any]
) -> dict[str, list[str]]:
    """把名称型条件取值编译成该层级列上真实可过滤的值（不猜值）。

    编码层级（如 `bigareacode`）存的是编码，而自然语言给的是名称；配对关系由
    事实表自身的函数依赖推出（见 level_resolution），编译不出来就回传，
    由调用方显式报错。
    """
    unresolved: dict[str, list[str]] = {}
    table = get_dynamic_table(spec)
    for key in list(conditions):
        if key in {"date_from", "date_to"}:
            continue
        value = conditions[key]
        values = value if isinstance(value, (list, tuple, set)) else [value]
        texts = [str(item).strip() for item in values if str(item).strip()]
        if not texts:
            continue
        if key not in spec.levels:
            # 未建层的附加事实列（支付方式、渠道等）：值必须真在列里，
            # 否则宁可报错，也不能让条件被树路由静默忽略。
            if key.casefold() in TIME_LEVELS:
                continue
            physical = spec.level_value_key.get(key, key)
            if table.c.get(physical) is None:
                continue
            hits = _column_value_hits(session, table, physical, texts)
            if hits:
                conditions[key] = hits[0] if len(hits) == 1 else hits
            else:
                unresolved[key] = texts
            continue
        if key.casefold() in TIME_LEVELS:
            continue
        resolved = resolve_level_values(session, spec, key, texts)
        if resolved["codes"]:
            codes = resolved["codes"]
            conditions[key] = codes[0] if len(codes) == 1 else codes
        if resolved["unresolved"]:
            unresolved[key] = resolved["unresolved"]
    return unresolved


def _rehost_org_slots(
    session: Session,
    spec,
    conditions: dict[str, Any],
    unmapped: list[str],
    originals: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[dict[str, Any]], dict[str, list[str]]]:
    """归一不到层级的组织语义槽，按基座声明再落位一次。

    `station` 这类调用方槽名在英文列名数据集上（业务域乙 102 层的 label 就是列名）
    对不上任何层级，但数据集声明的 `org_name` 特征列能承接同一个值：落位成功就
    换成真实层级继续查。落不了位时区分两种失败——有声明却查不到值按"取值不
    存在"报错，压根没声明才留在"不认识这个条件"里，两者业务含义不同。

    `originals` 是归一前的条件：`resolve_condition_keys` 会把落不了的键整个摘掉，
    取值只能从归一前的那份里拿。
    """
    if not unmapped:
        return conditions, unmapped, [], {}
    requested = {key: originals.get(key) for key in unmapped}
    resolved, evidence, supported = rehost_org_conditions(session, spec, requested)
    if not evidence and not supported:
        return conditions, unmapped, [], {}
    remaining: list[str] = []
    no_value: dict[str, list[str]] = {}
    for key in unmapped:
        hit = next((item for item in evidence if str(item.get("requested_key")) == key), None)
        level = str((hit or {}).get("level") or "")
        if hit is None or level not in resolved:
            if key in supported:
                no_value[key] = _unique_texts(requested.get(key))
            else:
                remaining.append(key)
            continue
        conditions.pop(key, None)
        value = resolved[level]
        existing = conditions.get(level)
        conditions[level] = (
            value if existing in (None, "", []) else _merge_condition(existing, value)
        )
    return conditions, remaining, evidence, no_value


def _unsupported_message(
    spec,
    unmapped_conditions: list[str],
    unmapped_groups: list[str],
    unresolved_values: dict[str, list[str]],
    conflicts: dict[str, list[list[str]]] | None = None,
) -> str:
    parts: list[str] = []
    if unmapped_conditions:
        parts.append("、".join(unmapped_conditions) + " 既不是该数据集的层级，也没有可承接的物理列")
    if unmapped_groups:
        parts.append("分组维度 " + "、".join(unmapped_groups) + " 无法映射到层级")
    if unresolved_values:
        parts.append(
            "；".join(
                f"{key}={value} 在该数据集的真实取值中不存在"
                for key, values in unresolved_values.items()
                for value in values
            )
        )
    if conflicts:
        parts.append(
            "；".join(
                f"{key} 的直接条件 {left} 与基座翻译条件 {right} 取值不同，无法同时成立"
                for key, (left, right) in conflicts.items()
            )
        )
    return (
        f"数据集 {spec.label} 无法执行本次条件：" + "；".join(parts)
        + f"。可用层级：{level_hint(spec)}。"
    )


def observed_day_coverage(
    session: Session, spec, conditions: dict[str, Any]
) -> dict[str, Any]:
    """期间内真实有数据的天数与最后一天——日均分母的观测口径。

    分母按日历已过天数摊是既定口径，但数据滞后时"30 天摊出的日均"会偏低，
    答案必须能说清实际只有 29 天有数。过滤条件用的是统一查询编译后的同一份，
    不另开一条过滤实现，否则观测口径和取数口径会各自漂移。
    只有声明了日层级的数据集可观测；月粒度缺整月由折算口径管着，不在这里补。
    """
    if "day" not in spec.levels:
        return {}
    physical = str(spec.level_value_key.get("day") or "day").strip()
    table = get_dynamic_table(spec)
    column = table.c.get(physical)
    if column is None:
        return {}
    clauses = _dynamic_where_clauses(spec, table, conditions)
    statement = select(func.count(func.distinct(column)), func.max(column)).select_from(table)
    if clauses:
        statement = statement.where(*clauses)
    try:
        days, latest = session.execute(statement).one()
    except OperationalError:
        return {}
    if not days:
        return {}
    return {"days": int(days), "through": str(latest or "").strip()}


def _dynamic_order_count(session: Session, spec, conditions: dict[str, Any]) -> int | None:
    """按注册声明的记录主键计算去重记录数。"""
    order_key = str(spec.order_key or "").strip()
    if not order_key:
        return None
    table = get_dynamic_table(spec)
    order_column = table.c.get(order_key)
    if order_column is None:
        return None
    clauses = _dynamic_where_clauses(spec, table, conditions)
    populated = select(func.count(order_column)).select_from(table)
    statement = select(func.count(func.distinct(order_column))).select_from(table)
    if clauses:
        populated = populated.where(*clauses)
        statement = statement.where(*clauses)
    try:
        if int(session.execute(populated).scalar_one() or 0) == 0:
            return None
        return int(session.execute(statement).scalar_one() or 0)
    except OperationalError:
        return None


def _sum_node_aggregations(nodes: list[dict[str, Any]]) -> dict[str, str]:
    totals: dict[str, Decimal] = {}
    for node in nodes:
        for key, value in (node.get("aggregations") or {}).items():
            if value in (None, ""):
                continue
            totals[key] = totals.get(key, Decimal("0")) + Decimal(str(value))
    return {key: format(value, "f") for key, value in totals.items()}


def _batch_totals(result_by_dataset: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """汇总跨通用事实数据集的同语义指标，并保留无命中数据集。"""
    sales_amount = Decimal("0")
    record_count = 0
    order_count = 0
    has_amount = False
    has_order_count = False
    with_data: list[str] = []
    without_data: list[str] = []
    precision_gap: list[str] = []
    unsupported: list[str] = []
    for dataset, item in result_by_dataset.items():
        current = item.get("current") or {}
        if current.get("unsupported_conditions"):
            unsupported.append(dataset)
        elif current.get("precision_gap"):
            precision_gap.append(dataset)
        elif current.get("available"):
            with_data.append(dataset)
        else:
            without_data.append(dataset)
        record_count += int(current.get("record_count") or 0)
        if current.get("order_count") is not None:
            order_count += int(current.get("order_count") or 0)
            has_order_count = True
        aggregations = current.get("aggregations") or {}
        labels = item.get("metric_labels") or {}
        for key, value in aggregations.items():
            key_text = f"{key} {labels.get(key) or key}".lower()
            if any(token in key_text for token in ("amount", "revenue", "业务金额", "业务金额")) and not any(
                token in key_text for token in ("real", "pay", "实收", "支付")
            ):
                sales_amount += Decimal(str(value or "0"))
                has_amount = True
                break
    totals: dict[str, Any] = {
        "datasets_with_data": with_data,
        "datasets_without_data": without_data,
        "datasets_precision_gap": precision_gap,
        "datasets_unsupported": unsupported,
        "record_count": record_count,
    }
    if has_amount:
        totals["sales_amount"] = format(sales_amount, "f")
    if has_order_count:
        totals["order_count"] = order_count
    return totals


def _merge_condition(existing: Any, incoming: Any) -> Any:
    if existing in (None, "", []):
        return incoming
    left = existing if isinstance(existing, (list, tuple, set)) else [existing]
    right = incoming if isinstance(incoming, (list, tuple, set)) else [incoming]
    return sorted({str(value) for value in left} & {str(value) for value in right})


def _request_filters(request: dict[str, Any]) -> dict[str, Any]:
    filters = dict(request.get("filters") or {})
    conditions = request.get("conditions") or {}
    if isinstance(conditions, dict):
        for key, value in conditions.items():
            filters.setdefault(key, value)
    period = request.get("period")
    if period not in (None, "", "current_month"):
        if isinstance(period, dict):
            # Agent 的日粒度计划使用 {start, end}。统一入口必须在
            # 时间基座编译前换成公共的 date_from/date_to，否则会
            # 丢失日期范围并退化为默认月查询。显式 filters/conditions
            # 优先，便于已编译的调用方覆盖 period。
            start = period.get("start", period.get("date_from"))
            end = period.get("end", period.get("date_to"))
            if start not in (None, ""):
                filters.setdefault("date_from", str(start).strip())
            if end not in (None, ""):
                filters.setdefault("date_to", str(end).strip())
            raw = period.get("value")
        else:
            raw = period
        text = str(raw or "").strip().replace("-", "").replace("/", "")
        if len(text) == 6 and text.isdigit():
            filters.setdefault("month", text)
        elif len(text) == 4 and text.isdigit():
            filters.setdefault("year", text)
    return filters


def _unique_texts(values: Any) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        raw = values
    else:
        raw = [values]
    result: list[str] = []
    for value in raw:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _shift_yyyymm(value: str, comparison: str) -> str | None:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if len(text) != 6 or not text.isdigit():
        return None
    try:
        current = datetime.strptime(text, "%Y%m")
    except ValueError:
        return None
    if comparison == "yoy":
        year = current.year - 1
        return f"{year:04d}{current.month:02d}"
    if comparison == "mom":
        month = current.month - 1
        year = current.year
        if month == 0:
            month, year = 12, year - 1
        return f"{year:04d}{month:02d}"
    return None


def _shift_time_mapping(
    mapping: dict[str, Any],
    comparison: str,
) -> tuple[dict[str, Any], str | None, str | None]:
    """平移一份请求条件，返回 (新条件, 期间值, 警告)。"""
    result = dict(mapping)
    date_from = str(result.get("date_from") or "").strip()
    date_to = str(result.get("date_to") or "").strip()
    if date_from and date_to:
        try:
            start = date.fromisoformat(date_from)
            end = date.fromisoformat(date_to)
        except ValueError:
            return result, None, f"无法解析 {comparison} 的日期范围"
        if comparison == "yoy":
            try:
                shifted_start = start.replace(year=start.year - 1)
                shifted_end = end.replace(year=end.year - 1)
            except ValueError:  # 2 月 29 日落在非闰年时回退到 2 月 28 日
                shifted_start = start.replace(year=start.year - 1, day=28)
                shifted_end = end.replace(year=end.year - 1, day=28)
        elif comparison == "mom":
            if start.day == 1 and end.day == monthrange(end.year, end.month)[1]:
                year, month = end.year, end.month - 1
                if month == 0:
                    year, month = year - 1, 12
                shifted_start = date(year, month, 1)
                shifted_end = date(year, month, monthrange(year, month)[1])
            else:
                days = (end - start).days + 1
                shifted_start = start - timedelta(days=days)
                shifted_end = end - timedelta(days=days)
        else:
            return result, None, f"不支持的比较类型：{comparison}"
        result["date_from"] = shifted_start.isoformat()
        result["date_to"] = shifted_end.isoformat()
        # 日期范围是相对时间编译后的权威条件。同比/环比平移后，不能继续
        # 携带原来的 year/month 等层级条件，否则会出现“日期已平移到 2025，
        # 但 year 仍是 2026”的互相矛盾查询。
        for key in ("relative_period", "month", "yyyymm", "quarter", "season"):
            result.pop(key, None)
        if shifted_start.year == shifted_end.year:
            result["year"] = str(shifted_start.year)
        else:
            result.pop("year", None)
        return result, f"{result['date_from']}..{result['date_to']}", None
    months = _unique_texts(result.get("month") or result.get("yyyymm"))
    if months:
        shifted = [_shift_yyyymm(value, comparison) for value in months]
        shifted = [value for value in shifted if value]
        if len(shifted) != len(months):
            return result, None, f"无法为 {comparison} 生成完整月份比较期间"
        key = "month" if result.get("month") not in (None, "", []) else "yyyymm"
        result[key] = shifted[0] if len(shifted) == 1 else shifted
        return result, shifted[0] if len(shifted) == 1 else ",".join(shifted), None

    years = _unique_texts(result.get("year"))
    if years and comparison == "yoy":
        shifted = [str(int(value) - 1) for value in years if value.isdigit()]
        if len(shifted) != len(years):
            return result, None, "无法为同比生成完整年份比较期间"
        result["year"] = shifted[0] if len(shifted) == 1 else shifted
        return result, shifted[0] if len(shifted) == 1 else ",".join(shifted), None

    if comparison == "mom":
        return result, None, "环比需要明确的月份期间，当前年份/季节范围未生成环比"
    return result, None, "当前请求缺少可平移的时间期间，未生成比较查询"


def _period_variants(
    request: dict[str, Any],
    comparisons: list[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, str], dict[str, Any] | None, list[str]]:
    """为统一引擎生成 current/yoy/mom 查询变体。

    第三个返回值是时间基座的完整审计（含语义期间「2026年4月第3周」与
    相对年份「去年=2025」）：子查询拿到的已经是编译后的 date_from/date_to
    或 year，期间口径只能在这里透传，否则批量答案会退化成裸日期区间，
    与单数据集路径口径不一致。
    """
    current = dict(request)
    current["comparisons"] = []
    variants = {"current": current}
    period_values: dict[str, str] = {}
    warnings: list[str] = []
    raw = _request_filters(request)
    compiled_current = compile_time_conditions(
        conditions=dict(request.get("conditions") or {}),
        filters=raw,
    )
    time_audit = compiled_current["audit"]
    normalized_conditions = compiled_current["conditions"]
    normalized_filters = compiled_current["filters"]
    if normalized_conditions:
        current["conditions"] = normalized_conditions
    if normalized_filters:
        current["filters"] = normalized_filters
    raw = {**normalized_filters, **normalized_conditions}
    # "没有任何时间条件才默认当前月"。日期区间（显式日期或"上周"编译后的
    # date_from/date_to）本身就是明确期间，再注入当前月会把跨月的日粒度
    # 查询整体清零（实测 2026-04-01 单日查询被加上 month=202609）。
    has_period = bool(
        _unique_texts(raw.get("month") or raw.get("yyyymm"))
        or _unique_texts(raw.get("year"))
        or (raw.get("date_from") and raw.get("date_to"))
    )
    if not has_period:
        raw["month"] = datetime.now().strftime("%Y%m")
        current["filters"] = {
            **dict(request.get("filters") or {}),
            "month": raw["month"],
        }
        period_values["current"] = raw["month"]
    else:
        months = _unique_texts(raw.get("month") or raw.get("yyyymm"))
        years = _unique_texts(raw.get("year"))
        period_values["current"] = months[0] if len(months) == 1 else (
            ",".join(months) if months else (years[0] if len(years) == 1 else ",".join(years))
        )
    if raw.get("date_from") and raw.get("date_to"):
        period_values["current"] = f"{raw['date_from']}..{raw['date_to']}"

    for comparison in comparisons:
        if comparison not in {"yoy", "mom"}:
            warnings.append(f"不支持的比较类型：{comparison}")
            continue
        variant = dict(request)
        variant["comparisons"] = []
        # 使用已由时间基座编译的 current 条件，避免 comparison 重新面对
        # “previous_week/current_week” 这样的语义词而误报缺少月份。
        original_conditions = dict(current.get("conditions") or {})
        original_filters = dict(current.get("filters") or {})
        shifted_conditions, condition_period, warning = _shift_time_mapping(
            original_conditions or raw, comparison
        )
        shifted_filters, filter_period, warning2 = _shift_time_mapping(
            original_filters or raw, comparison
        )
        warning = warning or warning2
        if warning and not condition_period and not filter_period:
            warnings.append(f"{comparison}：{warning}")
            continue
        variant["conditions"] = shifted_conditions
        variant["filters"] = shifted_filters
        variants[comparison] = variant
        period_values[comparison] = condition_period or filter_period or ""
    return variants, period_values, time_audit, warnings


def _comparison_definitions_for_period(
    comparisons: list[str],
    periods: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """让审计说明跟随实际粒度，不把周比较描述成月份比较。"""
    definitions = comparison_definitions()
    current = str(periods.get("current") or "").strip()
    result: dict[str, dict[str, Any]] = {}
    for key in comparisons:
        item = dict(definitions.get(key, {}))
        compared = str(periods.get(key) or "").strip()
        if current and compared and ".." in current and ".." in compared:
            if key == "yoy":
                item["definition"] = (
                    f"当前日期范围 {current} 与上一年度相同日期范围 {compared} 比较。"
                )
            elif key == "mom":
                item["definition"] = (
                    f"当前日期范围 {current} 与紧邻上一完整同粒度日期范围 {compared} 比较。"
                )
            item["period_type"] = "date_range"
        result[key] = item
    return result


def _comparison_metrics(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """按统一指标返回基期、差额和变化率，避免 Agent 自己重算业务数值。"""
    result: dict[str, Any] = {}
    current_values = current.get("aggregations") or {}
    previous_values = previous.get("aggregations") or {}
    for key in sorted(set(current_values) | set(previous_values)):
        current_value = Decimal(str(current_values.get(key) or "0"))
        previous_value = Decimal(str(previous_values.get(key) or "0"))
        delta = current_value - previous_value
        rate = None if previous_value == 0 else (delta / previous_value * Decimal("100"))
        result[key] = {
            "current": format(current_value, "f"),
            "previous": format(previous_value, "f"),
            "delta": format(delta, "f"),
            "rate": format(rate, ".2f") if rate is not None else None,
        }
    for key in ("record_count", "order_count"):
        if key not in current and key not in previous:
            continue
        current_value = Decimal(str(current.get(key) or "0"))
        previous_value = Decimal(str(previous.get(key) or "0"))
        delta = current_value - previous_value
        rate = None if previous_value == 0 else (delta / previous_value * Decimal("100"))
        result[key] = {
            "current": format(current_value, "f"),
            "previous": format(previous_value, "f"),
            "delta": format(delta, "f"),
            "rate": format(rate, ".2f") if rate is not None else None,
        }
    return result


def _fact_aggregate_actual(
    session: Session,
    spec,
    conditions: dict[str, Any],
    group_by: list[str],
    display_conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """拓扑未物化或目标节点过多时的通用事实表聚合。

    这不是旧 ORM 分支：仍按 DatasetSpec 的动态事实表、字段映射和指标
    契约执行，只是避免沿着包含大量空字段的超长 DNA 路径逐节点展开。
    """
    table = get_dynamic_table(spec)
    clauses = _dynamic_where_clauses(spec, table, conditions)
    metric_columns = [
        (metric, table.c.get(metric.column))
        for metric in spec.metrics
        if table.c.get(metric.column) is not None
    ]
    group_specs = []
    for group_key in group_by:
        physical = spec.level_value_key.get(group_key, group_key)
        column = table.c.get(physical)
        if column is None:
            raise BadRequestError(
                f"数据集 {spec.label} 的分组维度 {group_key} 缺少物理列 {physical}"
            )
        group_specs.append((group_key, column))
    metric_sums = [
        func.decimal_sum(column).label(f"metric_{index}")
        for index, (_metric, column) in enumerate(metric_columns)
    ]
    aggregations: dict[str, str] = {}
    if group_specs:
        group_columns = [column for _key, column in group_specs]
        statement = (
            select(*group_columns, func.count(), *metric_sums)
            .select_from(table)
            .group_by(*group_columns)
            .order_by(*group_columns)
        )
        if clauses:
            statement = statement.where(*clauses)
        grouped_rows = session.execute(statement).all()
        nodes = []
        record_count = 0
        for row in grouped_rows:
            dimensions = {
                group_key: str(row[index] if row[index] is not None else "未填写")
                for index, (group_key, _column) in enumerate(group_specs)
            }
            bucket_count = int(row[len(group_specs)] or 0)
            bucket_aggregations: dict[str, str] = {}
            record_count += bucket_count
            metric_offset = len(group_specs) + 1
            for index, (metric, _column) in enumerate(
                metric_columns, start=metric_offset
            ):
                metric_value = row[index]
                if metric_value is None:
                    continue
                normalized = Decimal(str(metric_value))
                bucket_aggregations[metric.name] = format(normalized, "f")
                aggregations[metric.name] = format(
                    Decimal(aggregations.get(metric.name) or "0") + normalized,
                    "f",
                )
            value = " / ".join(dimensions.values())
            identity = ":".join(
                f"{key}={dimensions[key]}" for key, _column in group_specs
            )
            nodes.append({
                "node_id": f"fact:{spec.id}:{identity}",
                "level": len(group_specs),
                "level_name": "+".join(key for key, _column in group_specs),
                "node_value": value,
                "dimensions": dimensions,
                "record_count": bucket_count,
                "aggregations": bucket_aggregations,
                "grown": False,
            })
        # 每个分组维度独立补齐编码展示名；多维查询不能只
        # 翻译第一维，否则 category=01/02 仍无法显示类别甲/类别乙。
        display_maps: dict[str, dict[str, str]] = {}
        for group_key, _column in group_specs:
            display_condition = (display_conditions or {}).get(group_key) or {}
            names = {
                str(raw_value): str(label)
                for raw_value, label in (display_condition.get("value_labels") or {}).items()
                if raw_value not in (None, "") and label not in (None, "")
            }
            if not names:
                names = display_map(
                    session,
                    spec,
                    group_key,
                    [node["dimensions"][group_key] for node in nodes],
                )
            display_maps[group_key] = names
        for node in nodes:
            node["display_dimensions"] = {
                group_key: display_maps[group_key].get(raw_value, raw_value)
                for group_key, raw_value in node["dimensions"].items()
            }
            node["display_value"] = " / ".join(node["display_dimensions"].values())
    else:
        nodes = []
        statement = select(func.count(), *metric_sums).select_from(table)
        if clauses:
            statement = statement.where(*clauses)
        row = session.execute(statement).one()
        record_count = int(row[0] or 0)
        for index, (metric, _column) in enumerate(metric_columns, start=1):
            value = row[index]
            if value is not None:
                aggregations[metric.name] = format(Decimal(str(value)), "f")
    route = {
        "dataset": spec.id,
        "mode": "fact_aggregate",
        "conditions": conditions,
        "group_by": group_by,
        "target_node_ids": [],
        "query_path": ["foundation", "middle_inner", "middle_store", "outer_feature", "fact_result"],
        "inner_node_ids": [],
        "middle_store_node_ids": [],
        "outer_feature_keys": list(conditions.keys()),
        "physical_time_buckets": [],
        "dna_is_topology_not_query_level": True,
        # 与 TreeRouter.RouteResult.to_dict() 保持同一形状：前端路由可视化
        # 直接读取这些字段，缺一个就会抛错而不是优雅降级。
        "pruned_node_count": 0,
        "pruned_record_count": 0,
        "collapsed_messages": [],
        "steps": [{
            "level": 0,
            "level_name": "fact_table",
            "level_label": "动态事实表聚合",
            "value": spec.fact_table,
            "status": "active" if record_count else "pruned",
            "message": "基座过滤后按事实表精确聚合；中通内链负责时间剪枝，DNA 仅是物理数据链拓扑",
            "phase": "fact_result",
            "record_count": record_count,
        }],
    }
    actual: dict[str, Any] = {
        "dataset": spec.id,
        "available": record_count > 0,
        "nodes": [],
        "record_count": record_count,
        "aggregations": aggregations,
        "route": route,
        "base_decision": {},
    }
    if group_specs:
        actual["nodes"] = nodes
        actual["available"] = bool(nodes)
    if spec.order_key:
        order_count = _dynamic_order_count(session, spec, conditions)
        if order_count is not None:
            actual["order_key"] = spec.order_key
            actual["order_count"] = order_count
    return actual


def _should_use_fact_aggregate(
    session: Session,
    spec,
    conditions: dict[str, Any],
    group_by: list[str],
    detail: bool,
) -> bool:
    """按拓扑物化情况选择聚合路径，保持通用性和查询可用性。"""
    if not spec.is_spiral:
        return False
    from engine.features.growth_plan import build_growth_plan

    physical_order = tuple(build_growth_plan(spec).physical_levels)
    physical_levels = set(physical_order)
    if conditions.get("date_from") and conditions.get("date_to"):
        return True
    # 树路由一次只能定位一个分组层。两个以上维度必须回到
    # 同一张动态事实表做精确复合 GROUP BY，不能丢弃后续维度。
    if len(group_by) > 1:
        return True
    # 未建层的附加事实列（支付方式/渠道等）只能按条件过滤：螺旋树不认识它们，
    # 走树路由会把条件静默丢掉，答成未过滤的总额。
    table = get_dynamic_table(spec)
    extra_columns = [
        key for key in conditions
        if key not in {"date_from", "date_to"} and key not in spec.levels
        and table.c.get(spec.level_value_key.get(key, key)) is not None
    ]
    if extra_columns and not detail:
        return True
    materialized_condition_keys = {
        key for key in conditions
        if key in spec.levels and key not in {"date_from", "date_to"}
    }
    if any(key not in physical_levels for key in materialized_condition_keys):
        return True
    if any(key not in physical_levels for key in group_by):
        return True
    if group_by:
        # 父层周存只代表该父节点下的完整子树。若过滤条件位于分组层之下，
        # 直接读取父层周存会把未命中的子分枝也算进去。例如按 day 分组、
        # category=01 过滤时，日节点仍是全品类总额。此类作用域必须回到
        # 动态事实表按同一注册契约精确过滤后再聚合。
        group_index = physical_order.index(group_by[0])
        if any(
            key in physical_levels
            and physical_order.index(key) > group_index
            and value not in (None, "", [])
            for key, value in conditions.items()
        ):
            return True
    if group_by or detail:
        return False
    target_levels = [
        key for key in conditions
        if key in spec.levels and key not in {"year", "season", "month", "quarter", "yyyymm"}
    ]
    if not target_levels:
        return False
    # 目标层没有节点通常是前置空值导致的物理拓扑中断；节点过多则逐节点
    # 展开成本高，二者都使用同一注册表驱动的事实表聚合。
    counts = session.execute(
        select(SpiralNodeORM.level_name, func.count(SpiralNodeORM.node_id))
        .where(
            SpiralNodeORM.dataset == spec.id,
            SpiralNodeORM.level_name.in_(target_levels),
        )
        .group_by(SpiralNodeORM.level_name)
    ).all()
    by_level = {str(level): int(count or 0) for level, count in counts}
    return any(by_level.get(level, 0) == 0 or by_level.get(level, 0) > 512 for level in target_levels)


def _node_payload(store: SpiralStore, node_id: str) -> dict[str, Any] | None:
    node = store.get_node(node_id)
    if node is None:
        return None
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


def _execute_registered_query(
    session: Session,
    request: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    dataset = str(request.get("dataset") or "oil_monthly_sales").strip()
    spec = registry.require(dataset)
    raw_filters = _request_filters(request)
    time_compiled = compile_time_conditions(
        conditions=raw_filters,
        filters=raw_filters,
    )
    direct_filters = dict(time_compiled["conditions"])
    base_filters = dict(request.get("base_filters") or {})
    products = _unique_texts(request.get("products"))
    if products and "product" not in base_filters:
        base_filters["product"] = products
    if request.get("product") and "product" not in base_filters:
        base_filters["product"] = request["product"]
    compiled, base_decision = compile_base_filters(session, dataset, base_filters)
    conditions = dict(direct_filters)
    warnings = list(time_compiled["audit"].get("warnings") or [])
    warnings.extend(base_decision.get("warnings") or [])
    conflicts: dict[str, list[list[str]]] = {}
    for key, value in compiled.items():
        if key not in conditions or conditions[key] in (None, "", []):
            conditions[key] = value
        else:
            merged = _merge_condition(conditions[key], value)
            if merged == []:
                # 同键异值不是"没有条件"：空列表在 WHERE 构造里会被跳过，
                # 整条查询静默扩成全量（比拒答危险得多），必须显式拒答。
                conflicts[key] = [_unique_texts(conditions[key]), _unique_texts(value)]
                conditions.pop(key, None)
                warnings.append(f"直接条件与基座翻译条件冲突：{key}")
            else:
                conditions[key] = merged

    group_by = request.get("group_by") or []
    if isinstance(group_by, str):
        group_by = [group_by]
    # 条件键与分组维度必须先归一到注册表真实层级：过去未知键被
    # `_dynamic_where_clauses` 静默跳过，"示例区域甲组织层级一"于是答成全公司总额。
    pre_resolved = dict(conditions)
    conditions, unmapped_conditions = resolve_condition_keys(spec, conditions)
    conditions, unmapped_conditions, org_evidence, org_no_value = _rehost_org_slots(
        session, spec, conditions, unmapped_conditions, pre_resolved
    )
    group_by, unmapped_groups = resolve_group_by(spec, list(group_by))
    unresolved_values = _compile_level_values(session, spec, conditions)
    for key, values in org_no_value.items():
        unresolved_values.setdefault(key, values)
    metrics = request.get("metrics") or [metric.name for metric in spec.metrics]
    query_plan = {
        "dataset": dataset,
        "label": spec.label,
        "kind": spec.kind,
        "filters": raw_filters,
        "base_filters": base_filters,
        "compiled_conditions": conditions,
        "group_by": group_by,
        "metrics": metrics,
        "metric_labels": {
            metric.name: (metric.label or metric.name)
            for metric in spec.metrics
        },
        "business_aggregates": business_aggregates_for_dataset(dataset),
        "time_foundation": time_compiled["audit"],
    }
    resolved_period = time_compiled["audit"].get("resolved_period")
    if resolved_period:
        query_plan["resolved_period"] = resolved_period
    if org_evidence:
        # 条件键被换成真实层级必须留痕：答案口径问的是 station，过滤跑的是 store_name。
        query_plan["semantic_resolution"] = {"conditions": org_evidence}
    if unmapped_conditions or unmapped_groups or unresolved_values or conflicts:
        message = _unsupported_message(
            spec, unmapped_conditions, unmapped_groups, unresolved_values, conflicts
        )
        warnings.append(message)
        query_plan["unsupported"] = {
            "conditions": unmapped_conditions,
            "group_by": unmapped_groups,
            "values": unresolved_values,
            "conflicts": conflicts,
            "message": message,
            "available_levels": {
                level: spec.level_labels.get(level, level) for level in spec.levels
            },
        }
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": {
                "dataset": dataset,
                "available": False,
                "unsupported_conditions": True,
                "unsupported_message": message,
                "nodes": [],
                "record_count": 0,
                "aggregations": {},
                "base_decision": base_decision,
            },
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }
    if (
        conditions.get("date_from")
        and conditions.get("date_to")
        and spec.time_format not in DAY_LEVEL_FORMATS
    ):
        resolved = resolved_period or {
            "label": "该日期范围",
            "date_from": conditions.get("date_from"),
            "date_to": conditions.get("date_to"),
        }
        message = (
            f"数据集 {spec.label} 当前只声明 {spec.time_format} 月粒度时间字段 "
            f"{spec.time_source}，无法精确回答{resolved.get('label') or '该时间范围'} "
            f"（{resolved.get('date_from')} 至 {resolved.get('date_to')}）；请重新注册/导入日粒度日期字段。"
        )
        warnings.append(message)
        query_plan["time_capability"] = {
            "requested_precision": "day",
            "declared_precision": "month",
            "supported": False,
            "message": message,
        }
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": {
                "dataset": dataset,
                "available": False,
                "precision_gap": True,
                "precision_gap_message": message,
                "nodes": [],
                "record_count": 0,
                "aggregations": {},
                "base_decision": base_decision,
            },
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }
    # 完整能力画像体积较大且不是普通查询的必需数据；调试/契约调用可显式请求。
    if request.get("include_profile"):
        query_plan["dataset_profile"] = profile_dataset(session, dataset)

    if base_decision.get("blocked"):
        # 基座只对当前数据集判定：条件确认不了就说确认不了，
        # 不能退化成"该数据集没有命中数据"（批量里会把有数据的集
        # 数据集谎报成空结果）。
        message = base_decision.get("message") or (
            f"数据集 {spec.label} 无法确认本次基座语义条件，未执行查询。"
        )
        warnings.append(message)
        query_plan["unsupported"] = {
            "base_filters": dict(base_decision.get("filters") or {}),
            "reason": base_decision.get("reason"),
            "message": message,
        }
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": {
                "dataset": dataset,
                "available": False,
                "unsupported_conditions": True,
                "unsupported_message": message,
                "nodes": [],
                "record_count": 0,
                "aggregations": {},
                "base_decision": base_decision,
            },
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }

    if spec.kind == "dimension":
        source = get_dim_source(dataset)
        physical_by_logical = {
            column.source: column.name for column in spec.columns
        }
        items = []
        for row in source.all_rows(session):
            item = _row_mapping(row)
            if all(
                _value_matches(item.get(physical_by_logical.get(key, key)), value)
                for key, value in conditions.items()
            ):
                items.append(item)
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": {
                "dataset": dataset,
                "available": bool(items),
                "items": items,
                "record_count": len(items),
                "base_decision": base_decision,
            },
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }

    if not spec.is_spiral:
        raise BadRequestError(f"数据集 {dataset} 不支持统一拓扑查询")
    if _should_use_fact_aggregate(
        session,
        spec,
        conditions,
        group_by,
        bool(request.get("detail")),
    ):
        actual = _fact_aggregate_actual(
            session, spec, conditions, group_by,
            base_decision.get("display_conditions"),
        )
        actual["base_decision"] = base_decision
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": actual,
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }
    store = SpiralStore(session)
    route = TreeRouter(store).route(
        dataset,
        conditions,
        is_aggregate=not bool(request.get("detail")),
        group_by=group_by[0] if group_by else None,
    )
    nodes = [
        payload for node_id in route.target_node_ids
        if (payload := _node_payload(store, node_id)) is not None
    ]
    if group_by:
        # 树里兄弟节点的顺序是导入写入顺序（202401、202402、202412…），
        # 分组答案必须按分组值排好，否则逐月/逐周的对照读不出来。
        nodes.sort(key=lambda node: str(node.get("node_value") or ""))
    if not nodes:
        # 修剪后分组层可能不再有节点（低于生长阈值）：注册表驱动的事实表
        # 聚合兜底，保证阈值只改变“树长多深”，不改变“能不能答”。
        actual = _fact_aggregate_actual(
            session, spec, conditions, group_by,
            base_decision.get("display_conditions"),
        )
        actual["base_decision"] = base_decision
        actual["fallback"] = "fact_aggregate"
        if request.get("detail"):
            actual["records"] = store.query_records(
                dataset, conditions, page=1, page_size=50
            )
        return {
            "trace_id": trace_id,
            "query_plan": query_plan,
            "actual": actual,
            "plan": None,
            "warnings": list(dict.fromkeys(warnings)),
            "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
        }
    actual: dict[str, Any] = {
        "dataset": dataset,
        "available": bool(nodes),
        "nodes": nodes,
        "record_count": sum(int(node.get("record_count") or 0) for node in nodes),
        "aggregations": _sum_node_aggregations(nodes),
        "route": route.to_dict(),
        "base_decision": base_decision,
    }
    if spec.order_key:
        actual["order_key"] = spec.order_key
        order_count = _dynamic_order_count(session, spec, conditions)
        if order_count is not None:
            actual["order_count"] = order_count
        else:
            warnings.append(
                f"已声明记录主键 {spec.order_key}，但当前范围没有可用记录数"
            )
    if request.get("detail"):
        # 明细的数值口径必须和随行的明细记录同源：拓扑路由为了展示会在候选
        # 分叉过多时折叠，折叠处的浅层节点并不受更深层条件约束，直接对其聚合
        # 会把整段时间当成答案。
        scoped = _fact_aggregate_actual(
            session, spec, conditions, group_by,
            base_decision.get("display_conditions"),
        )
        actual["record_count"] = scoped["record_count"]
        actual["aggregations"] = scoped["aggregations"]
        actual["available"] = bool(scoped["record_count"]) or bool(nodes)
        actual["totals_source"] = "fact_aggregate"
        if group_by:
            actual["nodes"] = scoped["nodes"]
        if scoped.get("order_count") is not None:
            actual["order_count"] = scoped["order_count"]
        actual["records"] = store.query_records(dataset, conditions, page=1, page_size=50)
    return {
        "trace_id": trace_id,
        "query_plan": query_plan,
        "actual": actual,
        "plan": None,
        "warnings": list(dict.fromkeys(warnings)),
        "timing_ms": {"total": round((time.perf_counter() - started) * 1000, 3)},
    }


def execute_unified_query(
    session: Session,
    request: dict[str, Any],
    trace_id: str,
    *,
    dataset_executor: Callable[
        [Session, dict[str, Any], str], dict[str, Any]
    ] | None = None,
) -> dict[str, Any]:
    """统一查询唯一入口：支持单数据集兼容模式和跨数据集批量模式。"""
    execute_dataset = dataset_executor or _execute_registered_query
    datasets = _unique_texts(request.get("datasets"))
    if not datasets and str(request.get("dataset_scope") or "").strip() == "all_actual":
        # 业务范围口径取自经验库（与 Agent 端枚举同一份声明），不在代码里
        # 另写一套"什么算通用事实"，否则两边会各自漂移。
        scope = actual_sales_scope()
        kinds = {
            str(value).strip()
            for value in (scope.get("include_kinds") or ["generic"])
        }
        excluded = [
            str(value).strip().casefold()
            for value in (scope.get("exclude_terms") or [])
        ]
        datasets = [
            spec.id for spec in registry.specs.values()
            if spec.kind in kinds
            and not any(
                term and term in " ".join(
                    [spec.label, *spec.aliases]
                ).casefold() for term in excluded
            )
        ]
    if not datasets:
        datasets = [str(request.get("dataset") or "oil_monthly_sales").strip()]
    comparisons = []
    for value in request.get("comparisons") or []:
        text = str(value).strip().lower()
        if text and text not in comparisons:
            comparisons.append(text)

    # 保留原有单数据集响应契约，避免旧调用方无意义地迁移。
    if len(datasets) == 1 and not comparisons:
        request = dict(request)
        request["dataset"] = datasets[0]
        return execute_dataset(session, request, trace_id)

    started = time.perf_counter()
    variants, period_values, time_audit, planning_warnings = _period_variants(
        request, comparisons
    )
    result_by_dataset: dict[str, dict[str, Any]] = {}
    all_warnings = list(planning_warnings)
    timings: dict[str, float] = {}
    query_plans: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        period_results: dict[str, dict[str, Any]] = {}
        for period_name, variant in variants.items():
            variant = dict(variant)
            variant["dataset"] = dataset
            variant["datasets"] = []
            started_one = time.perf_counter()
            result = execute_dataset(
                session,
                variant,
                f"{trace_id}:{dataset}:{period_name}",
            )
            period_results[period_name] = result
            timings[f"{dataset}.{period_name}"] = round(
                (time.perf_counter() - started_one) * 1000, 3
            )
            all_warnings.extend(result.get("warnings") or [])
        current_result = period_results["current"]
        current_actual = current_result.get("actual") or {}
        comparisons_payload: dict[str, Any] = {}
        for comparison in comparisons:
            previous_result = period_results.get(comparison)
            if not previous_result:
                continue
            previous_actual = previous_result.get("actual") or {}
            comparisons_payload[comparison] = {
                "period": period_values.get(comparison),
                "actual": previous_actual,
                "metrics": _comparison_metrics(current_actual, previous_actual),
            }
        result_by_dataset[dataset] = {
            "dataset": dataset,
            "label": (current_result.get("query_plan") or {}).get("label", dataset),
            "metric_labels": (current_result.get("query_plan") or {}).get("metric_labels") or {},
            "periods": {
                name: (result.get("actual") or {})
                for name, result in period_results.items()
            },
            "current": current_actual,
            "comparisons": comparisons_payload,
        }
        query_plans[dataset] = {
            name: result.get("query_plan") or {}
            for name, result in period_results.items()
        }

    batch_plan: dict[str, Any] = {
        "datasets": datasets,
        "comparisons": comparisons,
        "periods": period_values,
        "comparison_definitions": _comparison_definitions_for_period(
            comparisons, period_values
        ),
        "queries": query_plans,
    }
    # 期间口径标签（相对日期区间、相对年份）由时间基座在编译阶段给出，
    # 子查询只剩编译后的条件，因此必须在批量计划顶层透传。
    for audit_key in ("resolved_period", "resolved_year"):
        if time_audit.get(audit_key):
            batch_plan[audit_key] = time_audit[audit_key]

    return {
        "trace_id": trace_id,
        "query_plan": batch_plan,
        "actual": {
            "datasets": result_by_dataset,
            "totals": _batch_totals(result_by_dataset),
            "dataset_count": len(result_by_dataset),
            "available": any(
                bool(item.get("current", {}).get("available"))
                for item in result_by_dataset.values()
            ),
        },
        "plan": None,
        "warnings": list(dict.fromkeys(all_warnings)),
        "timing_ms": {
            "total": round((time.perf_counter() - started) * 1000, 3),
            **timings,
        },
    }

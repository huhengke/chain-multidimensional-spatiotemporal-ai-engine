# -*- coding: utf-8 -*-
"""查询时间基座：把自然时间语义编译为数据集可执行条件。

时间语义属于公共基座，不应由每个业务分枝单独猜测。这里不负责解析
自然语言全文，只负责把已经进入 QueryPlan 的时间槽位做确定性归一化，
并保留原始值与审计信息，供日报、计划和动态数据集共用。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

from engine.core.config import ENGINE_CONFIG_DIR, load_yaml_config


_HALF_YEAR_MONTHS = {
    "上半年": tuple(range(1, 7)),
    "下半年": tuple(range(7, 13)),
    "first_half": tuple(range(1, 7)),
    "second_half": tuple(range(7, 13)),
    "h1": tuple(range(1, 7)),
    "h2": tuple(range(7, 13)),
}
_QUARTER_MONTHS = {
    "一季度": tuple(range(1, 4)),
    "二季度": tuple(range(4, 7)),
    "三季度": tuple(range(7, 10)),
    "四季度": tuple(range(10, 13)),
    "q1": tuple(range(1, 4)),
    "q2": tuple(range(4, 7)),
    "q3": tuple(range(7, 10)),
    "q4": tuple(range(10, 13)),
}
_MONTH_PARITY_MONTHS = {
    "单月": (1, 3, 5, 7, 9, 11),
    "双月": (2, 4, 6, 8, 10, 12),
    "奇数月": (1, 3, 5, 7, 9, 11),
    "偶数月": (2, 4, 6, 8, 10, 12),
    "odd_month": (1, 3, 5, 7, 9, 11),
    "even_month": (2, 4, 6, 8, 10, 12),
}
_TIME_KEYS = {
    "year", "month", "yyyymm", "season", "quarter", "period",
    "month_parity", "relative_period", "date_from", "date_to",
}

_RELATIVE_PERIOD_ALIASES = {
    "本周": "current_week",
    "这周": "current_week",
    "本星期": "current_week",
    "这星期": "current_week",
    "上周": "previous_week",
    "上星期": "previous_week",
    "前一周": "previous_week",
    "下周": "next_week",
    "下星期": "next_week",
    "下一周": "next_week",
    "本月": "current_month",
    "这个月": "current_month",
    "上月": "previous_month",
    "上个月": "previous_month",
    "前一个月": "previous_month",
    "下月": "next_month",
    "下个月": "next_month",
    # 相对年份：编译成 year 层级条件而不是日期区间。每个数据集都声明了
    # year 层，年粒度条件在月粒度和日粒度上都能精确下推；若编译成
    # date_from/date_to，月粒度数据集会按"日期精度不足"整条拒答。
    "今年": "current_year",
    "本年": "current_year",
    "当年": "current_year",
    "本年度": "current_year",
    "去年": "previous_year",
    "上年": "previous_year",
    "上一年": "previous_year",
    "前一年": "previous_year",
    "明年": "next_year",
    "下年": "next_year",
    "下一年": "next_year",
}

_RELATIVE_YEAR_OFFSETS = {
    "current_year": ("今年", 0),
    "previous_year": ("去年", -1),
    "next_year": ("明年", 1),
}

# 月内序数周（Agent 只下传语义槽，日期换算一律在这里确定性完成）
_MONTH_WEEK_RE = re.compile(r"^month_week:(\d{4})(\d{2}):([一二三四五六1-6])$")
_CN_NUMERALS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
}


def calendar_catalog() -> dict[str, Any]:
    """读取基座日历目录，供 UI/Agent 展示可扩展日历能力。"""
    return load_yaml_config(ENGINE_CONFIG_DIR / "time_calendars.yaml")


def _values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []


def _one_or_list(values: list[str]) -> str | list[str] | None:
    values = list(dict.fromkeys(value for value in values if value))
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _years(conditions: dict[str, Any]) -> list[str]:
    values = _values(conditions.get("year"))
    if values:
        return [value for value in values if len(value) == 4 and value.isdigit()]
    months = _values(conditions.get("month") or conditions.get("yyyymm"))
    return list(dict.fromkeys(value[:4] for value in months if len(value) >= 6 and value[:4].isdigit()))


def _months_for_token(token: str) -> tuple[int, ...] | None:
    value = token.strip().lower().replace(" ", "")
    if value in _HALF_YEAR_MONTHS:
        return _HALF_YEAR_MONTHS[value]
    if value in _QUARTER_MONTHS:
        return _QUARTER_MONTHS[value]
    if value in _MONTH_PARITY_MONTHS:
        return _MONTH_PARITY_MONTHS[value]
    if value in {"1", "2", "3", "4"}:
        return _QUARTER_MONTHS[f"q{value}"]
    return None


def _merge_months(existing: list[str], incoming: list[str]) -> list[str]:
    if not existing:
        return sorted(set(incoming))
    if not incoming:
        return sorted(set(existing))
    return sorted(set(existing).intersection(incoming))


def _anchor_date(conditions: dict[str, Any]) -> date:
    """按明确年份锚定相对时间；没有年份时使用当前系统日期。"""
    today = date.today()
    years = _years(conditions)
    if len(years) != 1:
        return today
    try:
        year = int(years[0])
        # 保持月份/日尽可能一致，2 月 29 日在非闰年回退到 2 月 28 日。
        day = min(today.day, 28) if today.month == 2 else today.day
        return date(year, today.month, day)
    except ValueError:
        return today


def _month_shift(year: int, month: int, offset: int) -> tuple[int, int]:
    value = year * 12 + month - 1 + offset
    return value // 12, value % 12 + 1


def _year_for_bare_month(number: int, anchor: date, known_years: list[str]) -> int:
    """裸月份（"8月""去年12月"）配哪一年：显式年份优先，没给年份时
    锚定当前日期，未来月份回看去年——月份没有年份就落不到物理 yyyymm 列。"""
    if len(known_years) == 1:
        return int(known_years[0])
    if anchor != date.today():
        return anchor.year
    return anchor.year - 1 if number > anchor.month else anchor.year


def _compose_bare_months(conditions: dict[str, Any], filters: dict[str, Any],
                         warnings: list[str]) -> None:
    """把裸月份编译成 YYYYMM 下推。

    Agent 只给语义（几月、相对哪一年），补全年份这件事属于日历换算，
    必须在引擎侧做，不能让 "8" 这种值直接去比对物理 yyyymm 列。
    """
    values = _values(conditions.get("month"))
    if not any(re.fullmatch(r"\d{1,2}", value) for value in values):
        return
    anchor = _anchor_date(conditions)
    known_years = _years(conditions)
    composed: list[str] = []
    for value in values:
        if not re.fullmatch(r"\d{1,2}", value):
            composed.append(value)
            continue
        number = int(value)
        if not 1 <= number <= 12:
            warnings.append(f"月份取值不在 1-12 范围内，已忽略：{value}")
            continue
        composed.append(f"{_year_for_bare_month(number, anchor, known_years):04d}{number:02d}")
    merged = _one_or_list(composed)
    if merged is None:
        conditions.pop("month", None)
        filters.pop("yyyymm", None)
        return
    conditions["month"] = merged
    filters["yyyymm"] = merged
    if conditions.get("year") in (None, "", []):
        conditions.pop("year", None)


def _resolve_month_week(raw: str) -> tuple[str, str, str] | None:
    """编译"某月第 N 周"：周恒为周一起算，当月不足一周按实际天数截断。

    口径（与业务方确认过）：第一周从 1 日起到当月第一个周日（可能不足 7 天），
    之后每个周一开启新的一周。所以 2026-04-01（周三）属于第 1 周，
    第 3 周是 2026-04-13 至 2026-04-19。
    """
    match = _MONTH_WEEK_RE.match(raw)
    if not match:
        return None
    year, month, ordinal_text = int(match.group(1)), int(match.group(2)), match.group(3)
    ordinal = _CN_NUMERALS.get(ordinal_text)
    if not (1 <= month <= 12 and ordinal and 1 <= ordinal <= 6):
        return None
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    next_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    if ordinal == 1:
        start = first
        # 首周是残周：只到当月第一个周日；1 日本身是周一时才是完整 7 天
        end = next_monday - timedelta(days=1) if next_monday > first else first + timedelta(days=6)
    else:
        start = next_monday + timedelta(days=7 * (ordinal - 2))
        end = start + timedelta(days=6)
    if start > last:
        return None
    end = min(end, last)
    label = f"{year}年{month}月第{ordinal}周"
    return start.isoformat(), end.isoformat(), label


def _resolve_relative_period(token: Any, conditions: dict[str, Any]) -> tuple[str, str, str] | None:
    """返回 (起始日, 结束日, 中文标签)。周按周一至周日计算。"""
    raw = str(token or "").strip()
    month_week = _resolve_month_week(raw)
    if month_week is not None:
        return month_week
    kind = _RELATIVE_PERIOD_ALIASES.get(raw, raw)
    if kind not in {
        "current_week", "previous_week", "next_week",
        "current_month", "previous_month", "next_month",
    }:
        return None
    anchor = _anchor_date(conditions)
    if kind.endswith("_week"):
        monday = anchor - timedelta(days=anchor.weekday())
        offset = {"previous_week": -7, "current_week": 0, "next_week": 7}[kind]
        start = monday + timedelta(days=offset)
        end = start + timedelta(days=6)
        # 当前周只到今天，避免把未来日期误当作已发生数据。
        if kind == "current_week":
            end = min(end, date.today()) if anchor == date.today() else end
        label = {"previous_week": "上周", "current_week": "本周", "next_week": "下周"}[kind]
    else:
        offset = {"previous_month": -1, "current_month": 0, "next_month": 1}[kind]
        year, month = _month_shift(anchor.year, anchor.month, offset)
        start = date(year, month, 1)
        next_year, next_month = _month_shift(year, month, 1)
        end = date(next_year, next_month, 1) - timedelta(days=1)
        if kind == "current_month" and anchor == date.today():
            end = date.today()
        label = {"previous_month": "上月", "current_month": "本月", "next_month": "下月"}[kind]
    return start.isoformat(), end.isoformat(), label


def compile_time_conditions(
    conditions: dict[str, Any] | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """编译时间条件。

    ``上半年``/``下半年`` 和季度永远转换为物理月份集合；四季等业务
    季节值仍保留为 season，因为它们可能是数据源自身的业务字段。返回
    的 ``audit`` 可直接写入 QueryPlan，避免模型输出覆盖确定性结果。
    """
    original_conditions = dict(conditions or {})
    original_filters = dict(filters or {})
    normalized_conditions = dict(original_conditions)
    normalized_filters = dict(original_filters)
    warnings: list[str] = []
    resolved_period: dict[str, Any] | None = None
    resolved_year_period: dict[str, Any] | None = None
    # filters 也可能来自 Agent 的动态查询入口；先把物理月份键提升为
    # 公共条件，保证动态分枝不会因为接口形态不同而漏掉时间过滤。
    if "month" not in normalized_conditions and "yyyymm" in normalized_conditions:
        normalized_conditions["month"] = normalized_conditions.pop("yyyymm")
    elif "month" in normalized_conditions and "yyyymm" in normalized_conditions:
        # Agent 可能同时携带逻辑 month 和物理 yyyymm；逻辑 month 是当前
        # 期间的权威值，不能让两个键在同一请求里形成互相冲突的 AND 条件。
        normalized_conditions.pop("yyyymm", None)
    if "month" not in normalized_conditions and "month" in normalized_filters:
        normalized_conditions["month"] = normalized_filters["month"]
    if "season" not in normalized_conditions and "season" in normalized_filters:
        normalized_conditions["season"] = normalized_filters["season"]
    if "relative_period" not in normalized_conditions and "relative_period" in normalized_filters:
        normalized_conditions["relative_period"] = normalized_filters["relative_period"]

    relative = normalized_conditions.get("relative_period")
    year_kind = str(relative or "").strip()
    if year_kind in _RELATIVE_YEAR_OFFSETS:
        # 相对年份编译成 year 层级条件：不产生日期区间，因此也不写
        # resolved_period（日均分母要由 year 条件换算整年天数）。
        label, offset = _RELATIVE_YEAR_OFFSETS[year_kind]
        explicit_years = _values(normalized_conditions.get("year"))
        if explicit_years:
            warnings.append(f"「{label}」与显式年份 {explicit_years[0]} 同时出现，按显式年份计算")
            resolved_year = explicit_years[0]
        else:
            resolved_year = f"{_anchor_date(normalized_conditions).year + offset:04d}"
            normalized_conditions["year"] = resolved_year
            normalized_filters["year"] = resolved_year
        normalized_conditions.pop("relative_period", None)
        normalized_filters.pop("relative_period", None)
        resolved_year_period = {
            "kind": year_kind,
            "label": label,
            "year": resolved_year,
            "precision": "year",
        }
        relative = None
    if relative:
        resolved = _resolve_relative_period(relative, normalized_conditions)
        if resolved is None:
            warnings.append(
                f"月内序数周超出该月范围：{relative}"
                if str(relative).startswith("month_week:")
                else f"未识别相对时间语义：{relative}"
            )
        else:
            date_from, date_to, label = resolved
            normalized_conditions["date_from"] = date_from
            normalized_conditions["date_to"] = date_to
            normalized_filters["date_from"] = date_from
            normalized_filters["date_to"] = date_to
            resolved_period = {
                "kind": str(relative),
                "label": label,
                "date_from": date_from,
                "date_to": date_to,
                "precision": "day",
            }
        normalized_conditions.pop("relative_period", None)
        normalized_filters.pop("relative_period", None)
    _compose_bare_months(normalized_conditions, normalized_filters, warnings)
    calendar_value = normalized_conditions.get("calendar") or normalized_conditions.get("event")
    if calendar_value:
        requested_calendar = str(calendar_value).strip()
        matched_calendar = None
        for key, item in (calendar_catalog().get("calendars") or {}).items():
            aliases = [str(item.get("label") or ""), *(item.get("aliases") or [])]
            if requested_calendar == key or requested_calendar in aliases:
                matched_calendar = (key, item)
                break
        if matched_calendar is None:
            warnings.append(f"未注册时间日历语义：{requested_calendar}")
        else:
            key, item = matched_calendar
            month_hint = [int(value) for value in (item.get("month_hint") or [])]
            if month_hint:
                years_for_calendar = _years(normalized_conditions)
                if years_for_calendar:
                    compiled_months = [
                        f"{year}{month:02d}"
                        for year in years_for_calendar
                        for month in month_hint
                    ]
                    normalized_conditions["month"] = _one_or_list(compiled_months)
                    normalized_filters["yyyymm"] = _one_or_list(compiled_months)
            if item.get("requires_calendar_mapping"):
                warnings.append(
                    f"时间日历 {key} 需要日历映射后才能精确下推，当前只保留语义审计"
                )
    years = _years(normalized_conditions)
    half_or_quarter = (
        _values(normalized_conditions.get("season"))
        + _values(normalized_conditions.get("quarter"))
        + _values(normalized_conditions.get("month_parity"))
    )
    month_numbers: list[int] = []
    consumed_keys: set[str] = set()
    for token in half_or_quarter:
        parsed = _months_for_token(token)
        if parsed:
            month_numbers.extend(parsed)
            if token in _HALF_YEAR_MONTHS:
                consumed_keys.add("season")
            elif token in _QUARTER_MONTHS:
                consumed_keys.add("quarter")
            else:
                consumed_keys.add("month_parity")

    if month_numbers:
        if not years:
            warnings.append("时间半年度/季度缺少唯一年份，未生成 YYYYMM 条件")
        else:
            compiled_months = [f"{year}{month:02d}" for year in years for month in month_numbers]
            existing_months = _values(
                normalized_conditions.get("month") or normalized_conditions.get("yyyymm")
            )
            merged = _merge_months(existing_months, compiled_months)
            normalized_conditions["month"] = _one_or_list(merged)
            normalized_conditions.pop("yyyymm", None)
            for key in consumed_keys:
                normalized_conditions.pop(key, None)

            filter_months = _values(
                normalized_filters.get("yyyymm") or normalized_filters.get("month")
            )
            normalized_filters["yyyymm"] = _one_or_list(
                _merge_months(filter_months, compiled_months)
            )
            normalized_filters.pop("month", None)
            for key in consumed_keys:
                normalized_filters.pop(key, None)

    if normalized_filters.get("month") not in (None, "", []):
        normalized_filters["yyyymm"] = normalized_filters.pop("month")

    # 只有时间基座消费的上/下半年/季度值才会被删除；春季/夏季等真实
    # 业务季节仍交给数据集自身的 season 字段处理。
    for mapping in (normalized_conditions, normalized_filters):
        for key in list(mapping):
            if key in _TIME_KEYS and mapping[key] in (None, "", []):
                mapping.pop(key, None)

    if normalized_conditions.get("month") is None:
        normalized_conditions.pop("month", None)
    if normalized_filters.get("yyyymm") is None:
        normalized_filters.pop("yyyymm", None)

    return {
        "conditions": normalized_conditions,
        "filters": normalized_filters,
        "audit": {
            "original_conditions": original_conditions,
            "original_filters": original_filters,
            "normalized_conditions": normalized_conditions,
            "normalized_filters": normalized_filters,
            "compiler": "time_foundation.v1",
            "resolved_period": resolved_period,
            "resolved_year": resolved_year_period,
            "warnings": warnings,
        },
    }

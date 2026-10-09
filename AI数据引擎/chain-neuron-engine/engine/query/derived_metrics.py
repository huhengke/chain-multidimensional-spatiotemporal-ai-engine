# -*- coding: utf-8 -*-
"""期间自然日口径与派生指标（日均业务数量 / 完成率 / 时间进度 / 欠进度）。

数字全部在引擎侧用 Decimal 计算，Agent 只排版。口径（与业务方确认）：

- 分母恒为**真实自然日天数**：按月计取该月实际天数（2 月 28/29、4 月 30…），
  多月累加各月天数，不做「一律 ÷31」这种对短月不公平的摊法；
- 「本月 / 本周」这类进行中期间只按已发生的天数计日均，时间进度=已过天数÷期间天数，
  期间已过完时两者自然一致；
- 月粒度数据集的日均是「按月均摊」的估算，调用方必须把 ``precision`` 一并展示，
  不能当日本身有真值来读。
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

Segment = tuple[date, date]

_CURRENT_LABELS = {"本月", "本周"}
_TWO_DP = Decimal("0.01")


def _values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _parse_year(value: Any) -> int | None:
    for text in _values(value):
        if re.fullmatch(r"\d{4}", text):
            return int(text)
    return None


def _parse_month(text: str, year_hint: int | None) -> tuple[int, int] | None:
    if re.fullmatch(r"\d{6}", text):
        year, month = int(text[:4]), int(text[4:])
    else:
        match = re.fullmatch(r"(\d{4})[-/.]?(\d{1,2})", text)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
        elif re.fullmatch(r"\d{1,2}", text) and year_hint:
            year, month = year_hint, int(text)
        else:
            return None
    if not 1 <= month <= 12:
        return None
    return year, month


def _parse_week(text: str) -> Segment | None:
    """接受 ``2026W17`` / ``2026-W17`` / ``2026_17``，周恒为周一至周日。"""
    match = re.fullmatch(r"(\d{4})[-_]?W(\d{1,2})", text, re.IGNORECASE)
    if not match:
        return None
    year, week = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    return monday, monday + timedelta(days=6)


def _parse_day(text: str) -> date | None:
    for candidate in (text, f"{text[:4]}-{text[4:6]}-{text[6:]}" if re.fullmatch(r"\d{8}", text) else ""):
        if not candidate:
            continue
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _dedupe(segments: list[Segment]) -> list[Segment]:
    return sorted(dict.fromkeys(segments))


def _segments(conditions: dict[str, Any]) -> tuple[list[Segment], str] | None:
    """把时间条件解析成自然期间分段；形状不认识就返回 None（不猜区间）。"""
    year_hint = _parse_year(conditions.get("year"))

    days = _values(conditions.get("day"))
    if days:
        parsed = [_parse_day(text) for text in days]
        if any(item is None for item in parsed):
            return None
        segments = _dedupe([(item, item) for item in parsed])
        return segments, segments[0][0].isoformat()

    weeks = _values(conditions.get("week"))
    if weeks:
        parsed = [_parse_week(text) for text in weeks]
        if any(item is None for item in parsed):
            return None
        segments = _dedupe(parsed)
        return segments, "、".join(
            f"{start.isocalendar()[0]}年第{start.isocalendar()[1]}周" for start, _ in segments
        )

    months = _values(conditions.get("month")) + _values(conditions.get("yyyymm"))
    if months:
        parsed: list[Segment] = []
        for text in months:
            value = _parse_month(text, year_hint)
            if value is None:
                return None
            year, month = value
            parsed.append((date(year, month, 1), date(year, month, monthrange(year, month)[1])))
        ordered = _dedupe(parsed)
        return ordered, "、".join(f"{start.year}年{start.month}月" for start, _ in ordered)

    date_from = _values(conditions.get("date_from"))
    date_to = _values(conditions.get("date_to"))
    if date_from and date_to:
        start = _parse_day(date_from[0])
        end = _parse_day(date_to[0])
        if start is None or end is None or start > end:
            return None
        return [(start, end)], f"{start.isoformat()} 至 {end.isoformat()}"

    # 季节/季度/日历这类语义期间没有内置的天数表：宁可不给日均，
    # 也不能退化成"整年 ÷ 365"这种把范围放大的错数。
    if any(conditions.get(key) for key in ("season", "quarter", "calendar", "event")):
        return None

    if year_hint:
        return [(date(year_hint, 1, 1), date(year_hint, 12, 31))], f"{year_hint}年"
    return None


def _natural_end(start: date, label: str, clipped_end: date) -> date:
    """``本月``/``本周`` 的编译结果只到基准日，进度要按未截断的自然期间算。"""
    if label == "本月":
        return date(start.year, start.month, monthrange(start.year, start.month)[1])
    if label == "本周":
        return start + timedelta(days=6)
    return clipped_end


def _calendar_anchor(start: date, end: date) -> str:
    """期间的绝对月份。「本月」这类相对标签不带年月，答题时要能落到具体月。"""
    if (start.year, start.month) == (end.year, end.month):
        return f"{start.year}年{start.month}月"
    return ""


def _count_days(segments: list[Segment], limit: date) -> int:
    total = 0
    for start, end in segments:
        if start > limit:
            continue
        total += (min(end, limit) - start).days + 1
    return total


def period_span(
    conditions: dict[str, Any],
    resolved_period: dict[str, Any] | None = None,
    as_of: date | None = None,
) -> dict[str, Any] | None:
    """返回期间口径；``basis_days`` 是日均分母，``period_days`` 是进度分母。"""
    today = as_of or date.today()
    if resolved_period:
        raw_from = str(resolved_period.get("date_from") or "")
        raw_to = str(resolved_period.get("date_to") or "")
        start, end = _parse_day(raw_from), _parse_day(raw_to)
        if start and end and start <= end:
            label = str(resolved_period.get("label") or f"{start.isoformat()} 至 {end.isoformat()}")
            natural_end = _natural_end(start, label, end)
            segments = [(start, end)]
            natural = [(start, natural_end)]
        else:
            return None
    else:
        parsed = _segments(conditions or {})
        if parsed is None:
            return None
        segments, label = parsed
        natural = segments

    period_from = min(start for start, _ in natural)
    period_to = max(end for _, end in natural)
    period_days = sum((end - start).days + 1 for start, end in natural)
    elapsed_days = _count_days(natural, today)
    return {
        "label": label,
        "calendar_label": _calendar_anchor(period_from, period_to),
        "date_from": period_from.isoformat(),
        "date_to": period_to.isoformat(),
        "period_days": period_days,
        "elapsed_days": elapsed_days,
        "basis_days": min(elapsed_days, period_days),
        "in_progress": period_to > today,
        "as_of": today.isoformat(),
    }


def months_in_span(date_from: str, date_to: str) -> list[str]:
    """区间覆盖的自然月列表（yyyymm），供月粒度数据集按整月取数。"""
    start = date.fromisoformat(str(date_from))
    end = date.fromisoformat(str(date_to))
    if start > end:
        return []
    months: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year}{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return months


def _observed_decimals(text: str) -> int:
    _sign, _digits, exponent = Decimal(text).as_tuple()
    return max(0, -exponent)


def _quantum(*values: str) -> Decimal:
    places = max([2] + [_observed_decimals(value) for value in values if value])
    return Decimal(1).scaleb(-places)


def _quotient(value: Decimal, divisor: Decimal, quantum: Decimal) -> str:
    return str((value / divisor).quantize(quantum, rounding=ROUND_HALF_UP))


def daily_average(totals: dict[str, Any], days: int) -> dict[str, str]:
    """指标合计 ÷ 自然天数；天数不足时返回空，由调用方如实说明而不是摊成 0。"""
    if days <= 0:
        return {}
    result: dict[str, str] = {}
    for key, value in (totals or {}).items():
        text = str(value or "").strip()
        if not text:
            continue
        try:
            number = Decimal(text)
        except InvalidOperation:
            continue
        result[key] = _quotient(number, Decimal(days), _quantum(text))
    return result


def grain_scoped_conditions(
    conditions: dict[str, Any] | None,
    span: dict[str, Any],
    day_grain: bool,
) -> tuple[dict[str, Any], str]:
    """按数据集时间粒度归一取数条件，返回 ``(条件, 不可用原因)``。

    月粒度数据集没有日字段：期间折算为覆盖的**整月**取值，日均分母仍用真实
    自然日天数（不是一律 ÷31）；日/周条件在月粒度上摊不出等价口径，
    显式说明而不是悄悄答成整月。
    """
    source = dict(conditions or {})
    if day_grain:
        return source, ""
    if any(source.get(key) for key in ("day", "week")):
        return source, (
            "本次期间是日/周粒度，月粒度数据集没有对应字段，无法摊算日均与进度；"
            "请改用日粒度数据集提问。"
        )
    if not any(source.get(key) for key in ("date_from", "date_to", "relative_period")):
        return source, ""
    months = months_in_span(span["date_from"], span["date_to"])
    start = date.fromisoformat(span["date_from"])
    end = date.fromisoformat(span["date_to"])
    if start.day != 1 or end != date(end.year, end.month, monthrange(end.year, end.month)[1]):
        # 非整月区间在月粒度上只能取到整月合计：拿整月分子除区间分母会把
        # 日均放大，宁可不给。
        return source, (
            f"月粒度数据集只能按整月摊算，本次区间"
            f"（{span['date_from']} 至 {span['date_to']}）不是完整自然月；"
            "请把区间对齐到整月，或改用日粒度数据集提问。"
        )
    folded = {
        key: value
        for key, value in source.items()
        if key not in {
            "date_from", "date_to", "relative_period", "day", "week",
            "month", "yyyymm",
        }
    }
    folded["month"] = months
    return folded, ""


def plan_progress(
    plan_value: Any,
    actual_value: Any,
    span: dict[str, Any],
) -> dict[str, Any]:
    """完成率、时间进度、欠进度（百分点差）与缺口量。"""
    plan_text = str(plan_value or "0").strip() or "0"
    actual_text = str(actual_value or "0").strip() or "0"
    try:
        plan = Decimal(plan_text)
        actual = Decimal(actual_text)
    except InvalidOperation:
        return {"available": False, "message": "计划或实际的取值不是可用数值，未计算欠进度。"}
    period_days = int(span.get("period_days") or 0)
    elapsed_days = int(span.get("elapsed_days") or 0)
    if plan == 0:
        return {"available": False, "message": "该期间计划为 0 或缺失，无法计算完成率与欠进度。"}
    if period_days <= 0:
        return {"available": False, "message": "该期间没有可用的自然日天数，无法计算时间进度。"}

    quantum = _quantum(plan_text, actual_text)
    rate = (actual / plan * Decimal("100")).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    progress = (
        Decimal(elapsed_days) * Decimal("100") / Decimal(period_days)
    ).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    expected = Decimal(_quotient(plan * Decimal(elapsed_days), Decimal(period_days), quantum))
    gap_points = (rate - progress).quantize(_TWO_DP, rounding=ROUND_HALF_UP)
    return {
        "available": True,
        "plan": str(plan),
        "actual": str(actual),
        "completion_rate": str(rate),
        "time_progress": str(progress),
        "gap_points": str(gap_points),
        "expected_by_progress": str(expected),
        "gap_value": str((actual - expected).quantize(quantum, rounding=ROUND_HALF_UP)),
        "status": "on_track" if gap_points >= 0 else "behind",
        "unit_decimals": str(quantum),
    }

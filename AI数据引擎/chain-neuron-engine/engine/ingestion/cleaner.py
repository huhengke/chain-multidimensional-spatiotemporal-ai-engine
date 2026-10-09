# -*- coding: utf-8 -*-
"""清洗/标准化：脏值处理、Decimal 转换、时间解析。"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# 脏值集合
DIRTY_VALUES = {"*", "NULL", "null", "None", "none", "", "-"}

_YYYYMM_RE = re.compile(r"^(\d{4})(\d{2})$")
_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_YYYY_SEP_MM_RE = re.compile(r"^(\d{4})[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?日?$")
# ISO 文本日期：2026-04-01、2026/4/1、2026年4月1日、2026-04-01 00:00:00
_YYYY_SEP_MD_RE = re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[ T]\d.*)?$")
# Excel 区域设置导出的日在前文本日期：2/1/2025、25/9/2025 15:24:43
_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:[ T]\d.*)?$")

# 支持的时间声明格式（通用数据集 time.format）
TIME_FORMATS = ("yyyymm", "yyyymmdd", "yyyy-mm", "yyyy-mm-dd", "d/m/yyyy")

# 日粒度格式集合：这些格式才能派生“周/日”层级（月粒度只能到月）
DAY_LEVEL_FORMATS = frozenset({"yyyymmdd", "yyyy-mm-dd", "d/m/yyyy"})


class CleanError(ValueError):
    """清洗失败（携带字段与值，供错误报告）。"""


def clean_text(value) -> str | None:
    """去除空白；脏值返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if text in DIRTY_VALUES:
        return None
    return text


def to_decimal(value, field_name: str = "value") -> Decimal:
    """文本数值 → Decimal；失败抛 CleanError。

    禁止用 float 中转（防止精度丢失）。
    """
    if value is None:
        raise CleanError(f"{field_name} 为空")
    text = str(value).strip().replace(",", "")
    if text in DIRTY_VALUES:
        raise CleanError(f"{field_name} 为脏值：{text}")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise CleanError(f"{field_name} 不是合法数字：{text}")
    if not d.is_finite():
        raise CleanError(f"{field_name} 数值非法：{text}")
    return d


def parse_yyyymm(value) -> tuple[str, str]:
    """YYYYMM → (year, month)；校验月份范围。"""
    text = clean_text(value)
    if text is None:
        raise CleanError("年月为空")
    m = _YYYYMM_RE.match(text)
    if not m:
        raise CleanError(f"年月格式错误（应为 YYYYMM）：{text}")
    year, month = m.group(1), m.group(2)
    if not 1 <= int(month) <= 12:
        raise CleanError(f"月份超出范围：{month}")
    return year, month


def parse_time(value, fmt: str = "yyyymm") -> tuple[str, str]:
    """时间值 → (year, month)，按声明格式解析（通用数据集用）。

    支持格式（`TIME_FORMATS`）：
    - `yyyymm`     ：202401
    - `yyyymmdd`   ：20240131（日被忽略）
    - `yyyy-mm`    ：2024-01 / 2024/01 / 2024年1月
    - `yyyy-mm-dd` ：2024-01-31 / 2024/1/31 / 2024年1月31日 / 2024-01-31 00:00:00
    - `d/m/yyyy`   ：2/1/2025 / 25/9/2025 15:24:43（日在前，Excel 西语区域导出）

    兼容 Excel 数值单元格（202401）与日期单元格（datetime/date）。
    返回年份、月份均为定长字符串（4 位 / 2 位），与内置数据集口径一致。
    """
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}", f"{value.month:02d}"

    if isinstance(value, bool):
        raise CleanError(f"时间类型非法：{value}")

    if isinstance(value, (int, float)):
        num = Decimal(str(value))
        if num != num.to_integral_value():
            raise CleanError(f"时间不是合法年月：{value}")
        text = str(num.to_integral_value())
    else:
        text = clean_text(value)

    if text is None:
        raise CleanError("时间为空")

    fmt = (fmt or "yyyymm").strip().lower()
    if fmt == "yyyymm":
        return parse_yyyymm(text)

    if fmt == "yyyymmdd":
        m = _YYYYMMDD_RE.match(text)
    elif fmt == "yyyy-mm":
        m = _YYYY_SEP_MM_RE.match(text)
    elif fmt == "yyyy-mm-dd":
        # 日期单元格在 Excel 里有时以数值/纯数字文本落库，容忍 8 位紧凑写法。
        m = _YYYY_SEP_MD_RE.match(text) or _YYYYMMDD_RE.match(text)
    elif fmt == "d/m/yyyy":
        d = _DMY_RE.match(text)
        if not d:
            raise CleanError(f"时间格式错误（应为 {fmt}）：{text}")
        year, month = d.group(3), d.group(2)
        if not 1 <= int(month) <= 12:
            raise CleanError(f"月份超出范围：{month}")
        return year, f"{int(month):02d}"
    else:
        raise CleanError(
            f"不支持的时间格式：{fmt}（可选：{'、'.join(TIME_FORMATS)}）"
        )
    if not m:
        raise CleanError(f"时间格式错误（应为 {fmt}）：{text}")

    year, month = m.group(1), m.group(2)
    if not 1 <= int(month) <= 12:
        raise CleanError(f"月份超出范围：{month}")
    return year, f"{int(month):02d}"


def canonical_time_value(value, fmt: str = "yyyymm") -> str:
    """将声明时间统一保存为可比较的文本键。

    月度来源保存为 ``YYYY-MM``；日粒度来源保存为 ``YYYY-MM-DD``。
    事实表因此可以在不依赖旧 ORM 的情况下执行日范围过滤，同时保留
    ``year/month/yyyymm`` 等公共时间派生字段。
    """
    year, month = parse_time(value, fmt)
    fmt = (fmt or "yyyymm").strip().lower()
    if fmt == "yyyymm":
        return f"{year}-{month}"
    if fmt == "yyyy-mm":
        return f"{year}-{month}"
    if fmt == "yyyymmdd":
        text = clean_text(value)
        if text is None:
            raise CleanError("时间为空")
        if isinstance(value, (datetime, date)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        digits = re.sub(r"\D", "", text)
        try:
            return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
        except ValueError as exc:
            raise CleanError(f"日期格式错误：{text}") from exc
    if fmt == "yyyy-mm-dd":
        if isinstance(value, (datetime, date)):
            return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
        text = clean_text(value)
        if text is None:
            raise CleanError("时间为空")
        match = _YYYY_SEP_MD_RE.match(text) or _YYYYMMDD_RE.match(text)
        if not match:
            # 数值单元格（20260401.0）等非文本日期：按 8 位紧凑日期兜底。
            digits = re.sub(r"\D", "", text)
            if len(digits) >= 8:
                try:
                    return datetime.strptime(digits[:8], "%Y%m%d").date().isoformat()
                except ValueError as exc:
                    raise CleanError(f"日期格式错误：{text}") from exc
            raise CleanError(f"日期格式错误（应为 {fmt}）：{text}")
        try:
            return date(
                int(match.group(1)), int(match.group(2)), int(match.group(3))
            ).isoformat()
        except ValueError as exc:
            raise CleanError(f"日期格式错误：{text}") from exc
    if fmt == "d/m/yyyy":
        text = clean_text(value)
        if text is None:
            raise CleanError("时间为空")
        match = _DMY_RE.match(text)
        if not match:
            raise CleanError(f"日期格式错误：{text}")
        try:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1))).isoformat()
        except ValueError as exc:
            raise CleanError(f"日期格式错误：{text}") from exc
    raise CleanError(f"不支持的时间格式：{fmt}（可选：{'、'.join(TIME_FORMATS)}）")

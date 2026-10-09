# -*- coding: utf-8 -*-
"""GenericMapper：声明驱动的通用数据集行映射（零代码接入新数据源）。

记录字段来源全部取自注册表声明：
- `time`          时间列（Excel 表头）与格式，交给 cleaner.parse_time；
- `derive`        由时间派生的字段（year/month/yyyymm/season/quarter/week/day/half_year/month_parity/solar_term）；
- `column_sources` 记录键 → Excel 表头（未声明时回退同名）；
- `metrics`       指标记录字段与中文名（Decimal 精度红线）。

清洗一律复用 cleaner（`*` / `'NULL'` / 文本数字等脏值已统一处理），
不在本模块新造清洗逻辑。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from engine.derivation.time_derivation_engine import TimeDerivationEngine
from engine.ingestion.cleaner import (
    DAY_LEVEL_FORMATS,
    CleanError,
    canonical_time_value,
    clean_text,
    parse_time,
    to_decimal,
)
from engine.storage.dataset_registry import DatasetSpec


class GenericMapper:
    """按 DatasetSpec 声明把原始行映射为架构标准记录。"""

    def __init__(self, spec: DatasetSpec,
                 derivation: TimeDerivationEngine | None = None):
        self.spec = spec
        self.derivation = derivation or TimeDerivationEngine()
        self.sources: dict[str, str] = dict(spec.column_sources)
        self.required: tuple[str, ...] = tuple(spec.required)
        self.derive: tuple[str, ...] = tuple(spec.derive)
        self.metrics = tuple(spec.metrics)
        # 记录键：事实列声明（含层级取值键、指标、附加事实列，按声明顺序）
        # ∪ 其余派生/层级键兜底。
        keys = {str(v) for v in spec.level_value_key.values()}
        keys.update(m.source for m in self.metrics)
        if spec.order_key:
            keys.add(spec.order_key)
        ordered = [str(c) for c in spec.fact_columns]
        ordered += sorted(keys - set(ordered))
        self.record_keys: tuple[str, ...] = tuple(ordered)

    def _header_of(self, key: str) -> str:
        """记录键 → Excel 表头（未声明映射时回退同名）。"""
        return self.sources.get(key) or key

    def map_row(self, row: dict) -> dict[str, Any]:
        raw_time = row.get(self.spec.time_source)
        year, month = parse_time(raw_time, self.spec.time_format)

        record: dict[str, Any] = {"dataset": self.spec.id}
        # 原始时间统一落到内部物理列，日粒度查询不再只依赖月份派生字段。
        record["__time"] = canonical_time_value(raw_time, self.spec.time_format)
        derived: dict[str, Any] = {}
        fmt = (self.spec.time_format or "yyyymm").strip().lower()
        day_value: Any = None
        if fmt in DAY_LEVEL_FORMATS:
            text = str(record["__time"])
            if len(text) == 10:
                try:
                    day_value = date.fromisoformat(text)
                except ValueError:
                    day_value = None
        if day_value is not None:
            derived = self.derivation.derive_date(day_value)
        else:
            derived = self.derivation.derive_month(year, month)

        derived_keys = set(self.derive)
        for key in self.derive:
            if key == "year":
                value: Any = year
            elif key == "month":
                value = month
            elif key == "yyyymm":
                value = year + month
            else:
                value = derived.get(key)
            if value is None:
                # 规则精度不足（如月粒度源派生“周”）：留空即可，
                # 生长/路由链路按“空值断链”语义在上层自然停止。
                record[key] = ""
                continue
            if value == "":
                raise CleanError(f"{key} 派生失败：{year}{month}")
            record[key] = str(value)

        for key in self.record_keys:
            if key in derived_keys:
                continue
            record[key] = clean_text(row.get(self._header_of(key)))

        for m in self.metrics:
            record[m.source] = to_decimal(
                row.get(self._header_of(m.source)), m.label or m.name
            )

        for key in self.required:
            if record.get(key) in (None, ""):
                raise CleanError(f"{key} 为空")
        return record


def map_dim_row(spec: DatasetSpec, row: dict) -> dict[str, Any]:
    """维表/主数据行映射：按声明列清洗（脏值 → None），主键缺失即失败。"""
    record: dict[str, Any] = {
        col.name: clean_text(row.get(col.source)) for col in spec.columns
    }
    if record.get(spec.primary_column) in (None, ""):
        raise CleanError(f"主键 {spec.primary_key} 为空")
    return record

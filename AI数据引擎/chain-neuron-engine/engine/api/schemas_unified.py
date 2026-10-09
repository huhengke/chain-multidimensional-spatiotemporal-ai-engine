# -*- coding: utf-8 -*-
"""统一查询入口的请求模型。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UnifiedQueryRequest(BaseModel):
    """跨数据集确定性查询计划。"""

    model_config = ConfigDict(extra="forbid")

    intent: str = "sales"
    period: str | dict[str, Any] = Field(
        default="current_month",
        description=(
            "current_month、YYYYMM、YYYY年M月、包含 value 的时间对象，"
            "或 {start: YYYY-MM-DD, end: YYYY-MM-DD} 精确日期范围"
        ),
    )
    location: str | None = Field(default=None, max_length=128)
    product: str | None = Field(default=None, max_length=128)
    grade: str | None = Field(default=None, max_length=16)
    group_by: list[str] = Field(default_factory=list, max_length=8)
    products: list[str] = Field(default_factory=list, max_length=32)
    comparisons: list[str] = Field(default_factory=list, max_length=4)
    sort: dict[str, str] | None = None
    metrics: list[str] = Field(default_factory=list, max_length=16)
    dataset: str = Field(default="oil_monthly_sales", max_length=80)
    datasets: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="一次统一查询的多个注册数据集；为空时使用 dataset",
    )
    dataset_scope: str = Field(
        default="single",
        max_length=32,
        description="single、all_actual 或 plan；all_actual 由引擎枚举当前通用事实",
    )
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="已由 Agent/基座确定的逻辑层级条件",
    )
    filters: dict[str, Any] = Field(default_factory=dict)
    base_filters: dict[str, Any] = Field(default_factory=dict)
    detail: bool = False


class UnifiedQueryResponse(BaseModel):
    """统一查询结果。"""

    trace_id: str
    query_plan: dict[str, Any]
    actual: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    timing_ms: dict[str, float] = Field(default_factory=dict)
    profile_id: str | None = None
    profile_version: int | None = None
    storage_type: str | None = None
    catalog_id: str | None = None

# -*- coding: utf-8 -*-
"""引擎通道 API 的请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from engine.core.schemas import ORMModel


# ---------------- 认证 ----------------

class LoginRequest(ORMModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserInfo(ORMModel):
    username: str
    display_name: str
    role: str


class TokenResponse(ORMModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


# ---------------- 查询 ----------------

class RecordsQueryRequest(ORMModel):
    dataset: str = Field(
        description="当前注册的数据集，如 oil_monthly_sales / oil_sales_plan / non_oil_sales"
    )
    filters: dict[str, Any] = Field(default_factory=dict)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=500)


class AggregationsQueryRequest(ORMModel):
    dataset: str
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "层级条件，键必须是该数据集注册声明里的层级名"
            "（以 `GET /api/v1/engine/datasets/{id}/contract` 返回的 levels 为准，"
            "时间键另支持 year/season/month/week/quarter/relative_period/date_from/date_to）"
        ),
    )
    group_by: str | None = Field(
        default=None,
        description="汇总目标层名，同样取自数据集 contract 的 levels（如 month / bigareacode / gascode）",
    )
    base_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="由通用数据基座翻译的产品/组织等语义条件",
    )


class PlanDerivationRequest(ORMModel):
    actual_dataset: str
    conditions: dict[str, Any] = Field(
        default_factory=dict,
        description="时间条件（year/month/week/day 或 relative_period/date_from-date_to），用于确定自然日区间",
    )
    base_filters: dict[str, Any] = Field(
        default_factory=dict, description="实际侧的基座语义条件"
    )
    plan_dataset: str | None = None
    plan_base_filters: dict[str, Any] | None = Field(
        default=None,
        description="计划侧的基座语义条件；省略时沿用 base_filters（两侧数据集的取值可能不同）",
    )
    plan_metric: str | None = Field(
        default=None, description="计划侧参与完成率的指标名（由调用方按提问选定，引擎不猜）"
    )
    progress_metric: str | None = Field(
        default=None, description="实际侧与 plan_metric 同量纲的指标名"
    )
    as_of: str | None = Field(
        default=None, description="进度基准日 yyyy-mm-dd，默认引擎当天（测试可注入固定日期）"
    )


# ---------------- 生长 ----------------

class GrowRequest(ORMModel):
    dataset: str
    node_id: str | None = None
    threshold: int | None = Field(default=None, ge=1)
    force: bool = False


class RebuildRequest(ORMModel):
    """聚合重算范围；默认仍支持全量，但必须由调用方明确触发。"""

    scope: Literal["all", "base", "trunk", "branch"] = "all"
    node_ids: list[str] = Field(default_factory=list, max_length=200)


# ---------------- 数据源测试 ----------------

class DatasourceTestRequest(ORMModel):
    config: dict[str, Any]


# ---------------- 规则 ----------------

class RuleUpdateRequest(ORMModel):
    yaml_content: str = Field(min_length=1, description="完整的 time_derivation_rules YAML 文本")

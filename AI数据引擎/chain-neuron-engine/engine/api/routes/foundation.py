# -*- coding: utf-8 -*-
"""基座特征配置：查看经验来源、确认 AI 建议、人工维护运行态绑定。"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends

from engine.core.config import get_settings
from engine.core.errors import AppException, BadRequestError, NotFoundError
from engine.query.foundation_config import add_suggestions, decide_suggestion, load_config, save_config
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.adapters.registry import storage_adapters

router = APIRouter(prefix="/api/v1/engine/foundation-features", tags=["foundation-features"])

_BASE_SEMANTICS = {"product_code", "product_name", "alias", "category", "org_code", "org_name"}
_TEMPORAL_WORDS = (
    "上周", "本周", "下周", "上个月", "上月", "本月", "下个月", "下月",
    "日期", "时间", "日均", "同比", "环比", "工作日", "节日", "季节", "季度",
)


def _active_storage():
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


def _require_mutable_foundation() -> None:
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "当前基座资产已固定在活动 CNTS Catalog 中；请修改候选资产并构建新 Catalog，验收后再原子切换。",
            detail={
                **context.response_metadata(),
                "action": "build_new_candidate_catalog",
                "fallback": "forbidden",
            },
        )


def _is_temporal_idea(idea: str) -> bool:
    text = str(idea or "").strip()
    return any(token in text for token in _TEMPORAL_WORDS)


def _is_time_column(column: dict[str, Any]) -> bool:
    if bool(column.get("canonical_time")):
        return True
    semantic = str(column.get("semantic_type") or "").strip().lower()
    role = str(column.get("role") or column.get("feature_role") or "").strip().lower()
    source = f"{column.get('source') or ''} {column.get('name') or ''} {column.get('label') or ''}".lower()
    return semantic == "time" or role == "time" or any(
        token in source for token in ("date", "time", "日期", "时间", "年月", "月份")
    )


def _rule_suggestions(body: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    temporal_idea = _is_temporal_idea(str(body.get("idea") or ""))
    for dataset in body.get("datasets") or []:
        if not isinstance(dataset, dict):
            continue
        dataset_id = str(dataset.get("id") or dataset.get("dataset") or "").strip()
        if not dataset_id:
            continue
        fields = []
        columns = [column for column in dataset.get("columns") or [] if isinstance(column, dict)]
        canonical_columns = [column for column in columns if column.get("canonical_time")]
        for column in (canonical_columns or columns):
            if not isinstance(column, dict):
                continue
            semantic = str(column.get("semantic_type") or "other")
            if temporal_idea:
                if not _is_time_column(column):
                    continue
                semantic = "time"
            elif semantic not in _BASE_SEMANTICS:
                continue
            source = str(column.get("source") or column.get("name") or "").strip()
            if not source:
                continue
            fields.append({
                "source": source,
                "name": str(column.get("name") or source),
                "label": str(column.get("label") or source),
                "semantic_type": semantic,
                "layer": "trunk" if temporal_idea else "base",
                "reason": (
                    "这是当前数据集声明的时间入口，建议归入中通主干；基座只负责把上周、本周、同比、环比等语义翻译为日期范围。"
                    if temporal_idea else
                    "经验库建议该字段用于跨数据集语义过滤、翻译、归一化或组织映射；原始时间序列和指标不进入基座。"
                ),
            })
        if fields:
            suggestions.append({
                "id": f"foundation-{uuid.uuid4().hex[:12]}",
                "dataset": dataset_id,
                "fields": fields,
                "rationale": (
                    str(body.get("idea") or "") + "；时间语义只选择日期/时间字段，不能把组织、商品或指标字段误列为候选。"
                    if temporal_idea else str(body.get("idea") or "基于当前业务想法和字段画像生成基座候选")
                ),
                "source": "rule_fallback",
                "status": "pending_review",
            })
    return suggestions


@router.get("")
def get_foundation_features(
    identity: dict = Depends(allow_user_or_service),
) -> dict[str, Any]:
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.foundation_features(None, context)
    return {**load_config(), **context.response_metadata()}


@router.put("")
def update_foundation_features(
    body: dict[str, Any] = Body(...),
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict[str, Any]:
    _require_mutable_foundation()
    try:
        config = save_config(body)
    except Exception as exc:
        raise BadRequestError(f"基座特征配置保存失败：{exc}") from exc
    return {"config": config, "message": "基座特征配置已保存并热加载"}


@router.post("/analyze")
def analyze_foundation_features(
    body: dict[str, Any] = Body(...),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict[str, Any]:
    _require_mutable_foundation()
    settings = get_settings()
    suggestions: list[dict[str, Any]] = []
    backend = "rule_fallback"
    try:
        response = httpx.post(
            f"{settings.AGENT_BASE_URL.rstrip('/')}/api/v1/analyze/foundation",
            json=body,
            timeout=65,
        )
        if response.is_success:
            payload = response.json()
            if isinstance(payload.get("suggestions"), list):
                suggestions = payload["suggestions"]
                backend = str(payload.get("backend") or "ai")
    except Exception:
        suggestions = []
    if not suggestions:
        suggestions = _rule_suggestions(body)
    config = add_suggestions(suggestions) if suggestions else load_config()
    return {"backend": backend, "suggestions": suggestions, "config": config, "message": "建议已进入待确认区；未确认前不会写入基座运行态"}


@router.post("/suggestions/{suggestion_id}/approve")
def approve_foundation_suggestion(
    suggestion_id: str,
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict[str, Any]:
    _require_mutable_foundation()
    try:
        config = decide_suggestion(suggestion_id, True)
    except KeyError as exc:
        raise NotFoundError("基座建议不存在", suggestion_id) from exc
    return {"config": config, "message": "基座建议已确认并生效"}


@router.post("/suggestions/{suggestion_id}/reject")
def reject_foundation_suggestion(
    suggestion_id: str,
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict[str, Any]:
    _require_mutable_foundation()
    try:
        config = decide_suggestion(suggestion_id, False)
    except KeyError as exc:
        raise NotFoundError("基座建议不存在", suggestion_id) from exc
    return {"config": config, "message": "基座建议已拒绝，未写入运行态绑定"}

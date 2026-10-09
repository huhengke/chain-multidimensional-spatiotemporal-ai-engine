# -*- coding: utf-8 -*-
"""统一确定性查询入口：基座映射 + 业务分枝。"""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from engine.api.schemas_unified import UnifiedQueryRequest, UnifiedQueryResponse
from engine.core.errors import AppException
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, get_db
from engine.storage.adapters.registry import storage_adapters

router = APIRouter(prefix="/api/v1/engine", tags=["unified-query"])


@router.post("/query", response_model=UnifiedQueryResponse)
def unified_query(
    body: UnifiedQueryRequest,
    session: Session = Depends(get_db),
    _identity: dict = Depends(allow_user_or_service),
    x_trace_id: str | None = Header(default=None, alias="X-Trace-Id"),
) -> dict:
    trace_id = x_trace_id.strip() if x_trace_id and x_trace_id.strip() else str(uuid4())
    try:
        profile = resolve_active_profile()
        # The active pointer is authoritative. Missing adapters fail explicitly;
        # query execution never chooses a fallback storage backend.
        adapter = storage_adapters.require(profile.storage_type)
    except RuntimeProfileError as exc:
        raise AppException(
            "E4501",
            "活动 AI 数据引擎运行配置不可用",
            detail={"error": str(exc), "trace_id": trace_id},
            http_status=503,
        ) from exc
    response = adapter.execute_query(
        session,
        body.model_dump(),
        trace_id,
        profile,
    )
    response.update(profile.response_metadata())
    return response

# -*- coding: utf-8 -*-
"""系统参数路由：界面查看/修改运行参数（热生效，带快照）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends

from engine.core.errors import BadRequestError
from engine.core.params import list_params, update_params
from engine.runtime_profiles.guards import require_mutable_legacy
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, require_roles, ROLE_ADMIN

router = APIRouter(prefix="/api/v1/engine/system-params", tags=["system-params"])


@router.get("")
def get_system_params(identity: dict = Depends(allow_user_or_service)) -> dict:
    context = resolve_active_profile()
    return {
        "items": list_params(),
        "read_only": context.storage_type != "legacy_sqlite",
        **context.response_metadata(),
    }


@router.put("")
def put_system_params(
    body: dict[str, Any] = Body(...),
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    require_mutable_legacy("智慧树生长参数")
    raw = body.get("params") if isinstance(body.get("params"), dict) else body
    try:
        applied = update_params(raw)
    except ValueError as e:
        raise BadRequestError(f"系统参数不合法：{e}")
    return {
        "params": applied,
        "items": list_params(),
        "message": "已保存并热加载；生长阈值对下次导入/rebuild 生效，"
                   "已有树结构请在系统参数页点击‘更新 AI 数据引擎-智慧树’",
    }

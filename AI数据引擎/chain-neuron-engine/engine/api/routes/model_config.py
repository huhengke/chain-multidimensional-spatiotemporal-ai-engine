# -*- coding: utf-8 -*-
"""模型供应配置只读接口。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from engine.model_config import public_model_config, update_model_config
from engine.security.deps import allow_user_or_service, require_roles
from engine.security.models import ROLE_ADMIN

router = APIRouter(prefix="/api/v1/engine/model-config", tags=["model-config"])


@router.get("")
def get_model_config(_identity: dict = Depends(allow_user_or_service)) -> dict:
    return public_model_config()


@router.put("")
def put_model_config(
    body: dict = Body(...),
    _identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    try:
        return update_model_config(body)
    except ValueError as exc:
        from engine.core.errors import BadRequestError
        raise BadRequestError(str(exc)) from exc

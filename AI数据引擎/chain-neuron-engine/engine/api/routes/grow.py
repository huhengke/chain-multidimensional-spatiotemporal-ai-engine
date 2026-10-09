# -*- coding: utf-8 -*-
"""生长路由：单节点生长 / 批量生长。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from engine.api.schemas_api import GrowRequest
from engine.runtime_profiles.guards import require_mutable_legacy
from engine.security.deps import allow_user_or_service, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.database import SessionLocal
from engine.storage.growth import GrowthExecutor

router = APIRouter(prefix="/api/v1/engine", tags=["growth"])


@router.post("/grow")
def grow(
    body: GrowRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    require_mutable_legacy("智慧树生长结构")
    session = SessionLocal()
    try:
        executor = GrowthExecutor(session, threshold=body.threshold)
        if body.node_id:
            node = executor.grow_node(body.node_id, force=body.force)
            return {
                "node_id": node.node_id,
                "grown": node.grown,
                "record_count": node.record_count,
                "checksum": node.checksum,
            }
        return executor.grow_all_eligible(body.dataset)
    finally:
        session.close()

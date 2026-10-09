# -*- coding: utf-8 -*-
"""特征治理 API：画像、交叉映射、提案和审批。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime

from engine.core.errors import NotFoundError
from engine.features.registry import (
    create_feature_proposals,
    list_proposals,
    product_mapping_summary,
    profile_dataset,
    sync_product_crosswalk,
)
from engine.runtime_profiles.guards import require_mutable_legacy
from engine.security.deps import allow_user_or_service, get_db, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.models import FeatureProposal, ProductCrosswalk

router = APIRouter(prefix="/api/v1/engine/features", tags=["features"])


@router.get("/summary")
def feature_summary(
    session: Session = Depends(get_db),
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    require_mutable_legacy("旧版特征治理数据")
    return product_mapping_summary(session)


@router.post("/products/rebuild")
def rebuild_products(
    session: Session = Depends(get_db),
    _identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    require_mutable_legacy("旧版特征治理数据")
    counts = sync_product_crosswalk(session)
    proposals = create_feature_proposals(session)
    session.commit()
    return {"crosswalk": counts, "proposals_created": proposals}


@router.get("/proposals")
def get_proposals(
    dataset: str | None = None,
    session: Session = Depends(get_db),
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    require_mutable_legacy("旧版特征治理数据")
    return {"items": list_proposals(session, dataset)}


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: str,
    session: Session = Depends(get_db),
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    require_mutable_legacy("旧版特征治理数据")
    proposal = session.get(FeatureProposal, proposal_id)
    if proposal is None:
        raise NotFoundError("特征提案不存在", proposal_id)
    proposal.status = "approved"
    proposal.reviewed_at = datetime.utcnow()
    proposal.reviewed_by = str(identity.get("username") or identity.get("sub") or "admin")
    row = session.execute(
        select(ProductCrosswalk).where(
            ProductCrosswalk.dataset == proposal.dataset,
            ProductCrosswalk.source_code == str((proposal.payload or {}).get("source_code") or ""),
        )
    ).scalar_one_or_none()
    if row is not None:
        row.status = "approved"
    session.commit()
    return {"proposal_id": proposal_id, "status": proposal.status}


@router.get("/profile/{dataset}")
def dataset_profile(
    dataset: str,
    session: Session = Depends(get_db),
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    require_mutable_legacy("旧版特征治理数据")
    return profile_dataset(session, dataset)

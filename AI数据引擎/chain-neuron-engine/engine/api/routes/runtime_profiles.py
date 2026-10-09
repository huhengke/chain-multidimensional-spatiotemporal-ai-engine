"""Admin API for complete storage-and-dataset runtime profiles."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from engine.api.schemas_runtime_profiles import (
    ProfileActivateRequest,
    ProfileCreateRequest,
    ProfileUpdateRequest,
    ProfileVersionActionRequest,
)
from engine.core.errors import BadRequestError, ConflictError, NotFoundError
from engine.runtime_profiles.errors import (
    RuntimeProfileConflict,
    RuntimeProfileError,
    RuntimeProfileNotFound,
)
from engine.runtime_profiles.resolver import get_runtime_profile_service
from engine.security.deps import allow_user_or_service, require_roles
from engine.security.models import ROLE_ADMIN

router = APIRouter(prefix="/api/v1/engine/runtime-profiles", tags=["runtime-profiles"])


def _actor(identity: dict) -> str:
    return f"{identity['kind']}:{identity['principal']}"


def _translate_error(exc: RuntimeProfileError):
    if isinstance(exc, RuntimeProfileNotFound):
        raise NotFoundError(str(exc)) from exc
    if isinstance(exc, RuntimeProfileConflict):
        raise ConflictError(str(exc)) from exc
    raise BadRequestError(str(exc)) from exc


@router.get("")
def list_runtime_profiles(_identity: dict = Depends(allow_user_or_service)) -> dict:
    return get_runtime_profile_service().list_profiles()


@router.get("/audit")
def list_runtime_profile_audit(
    limit: int = Query(default=200, ge=1, le=1000),
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    return {"items": get_runtime_profile_service().audit(limit)}


@router.get("/{profile_id}")
def get_runtime_profile(
    profile_id: str,
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    try:
        return get_runtime_profile_service().get_profile(profile_id)
    except RuntimeProfileError as exc:
        _translate_error(exc)


@router.post("")
def create_runtime_profile(
    body: ProfileCreateRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    payload = body.model_dump(exclude={"reason"})
    try:
        return get_runtime_profile_service().create_profile(
            payload, actor=_actor(identity), reason=body.reason
        )
    except RuntimeProfileError as exc:
        _translate_error(exc)


@router.put("/{profile_id}")
def update_runtime_profile(
    profile_id: str,
    body: ProfileUpdateRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    payload = body.model_dump(
        exclude={"expected_version", "reason"}, exclude_none=True
    )
    try:
        return get_runtime_profile_service().update_profile(
            profile_id,
            payload,
            expected_version=body.expected_version,
            actor=_actor(identity),
            reason=body.reason,
        )
    except RuntimeProfileError as exc:
        _translate_error(exc)


@router.post("/{profile_id}/verify")
def verify_runtime_profile(
    profile_id: str,
    body: ProfileVersionActionRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    try:
        return get_runtime_profile_service().verify_profile(
            profile_id,
            expected_version=body.expected_version,
            actor=_actor(identity),
            reason=body.reason,
        )
    except RuntimeProfileError as exc:
        _translate_error(exc)


@router.post("/{profile_id}/ready")
def ready_runtime_profile(
    profile_id: str,
    body: ProfileVersionActionRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    try:
        return get_runtime_profile_service().mark_ready(
            profile_id,
            expected_version=body.expected_version,
            actor=_actor(identity),
            reason=body.reason,
        )
    except RuntimeProfileError as exc:
        _translate_error(exc)


@router.post("/{profile_id}/activate")
def activate_runtime_profile(
    profile_id: str,
    body: ProfileActivateRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    if body.confirm_profile_id != profile_id:
        raise BadRequestError("确认的配置档 id 与目标不一致")
    try:
        return get_runtime_profile_service().activate(
            profile_id,
            expected_revision=body.expected_revision,
            actor=_actor(identity),
            reason=body.reason,
        )
    except RuntimeProfileError as exc:
        _translate_error(exc)

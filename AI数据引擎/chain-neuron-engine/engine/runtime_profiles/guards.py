"""Runtime-profile guards shared by legacy-only control-plane routes."""

from __future__ import annotations

from engine.core.errors import AppException, BadRequestError
from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile


def require_mutable_legacy(resource: str) -> RuntimeProfileContext:
    """Reject writes that would otherwise leak from CNTS into the legacy store."""
    try:
        context = resolve_active_profile()
    except RuntimeProfileError as exc:
        raise AppException(
            "E4501",
            "活动 AI 数据引擎运行配置不可用",
            detail={"error": str(exc)},
            http_status=503,
        ) from exc
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            f"当前 {resource} 已固定在活动 CNTS Catalog 中；请修改候选资产并构建新 Catalog，验收后再原子切换。",
            detail={
                **context.response_metadata(),
                "action": "build_new_candidate_catalog",
                "fallback": "forbidden",
            },
        )
    return context

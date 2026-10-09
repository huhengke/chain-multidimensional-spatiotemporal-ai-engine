from __future__ import annotations

import hashlib

from .models import PendingRecommendation


def draft_recommendation(
    field: str,
    role: str,
    reason: str,
    confidence: float,
    source: str,
) -> PendingRecommendation:
    if role not in {"common", "outer", "growth", "ignore"}:
        raise ValueError(f"unsupported role: {role}")
    if source not in {"experience", "model", "rule", "combined"}:
        raise ValueError(f"unsupported source: {source}")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    identity = hashlib.blake2s(
        f"{field}\x1f{role}\x1f{reason}\x1f{source}".encode("utf-8"), digest_size=12
    ).hexdigest()
    return PendingRecommendation(
        recommendation_id=identity,
        field=field,
        role=role,  # type: ignore[arg-type]
        reason=reason,
        confidence=confidence,
        source=source,  # type: ignore[arg-type]
    )

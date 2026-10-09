"""Minimum storage adapter surface used by profile validation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from sqlalchemy.orm import Session

from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.models import RuntimeProfile


class StorageAdapter(ABC):
    storage_type: str

    @abstractmethod
    def health(self, profile: RuntimeProfile) -> dict[str, Any]:
        """Validate that the profile's physical storage can serve reads."""

    def ensure_query_ready(self, profile: RuntimeProfile) -> None:
        status = self.health(profile)
        if status.get("status") != "ok":
            raise RuntimeError(str(status.get("message") or "存储不可用"))

    @abstractmethod
    def execute_query(
        self,
        session: Session,
        request: dict[str, Any],
        trace_id: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        """Execute one unified request against the pinned runtime context."""

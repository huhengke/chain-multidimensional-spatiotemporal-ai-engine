"""Runtime-profile lifecycle service and acceptance gate."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from engine.runtime_profiles.errors import RuntimeProfileStateError
from engine.runtime_profiles.models import AcceptanceSpec, RuntimeProfile, utc_now
from engine.runtime_profiles.store import RuntimeProfileStore
from engine.storage.adapters.registry import storage_adapters


class RuntimeProfileService:
    def __init__(self, store: RuntimeProfileStore) -> None:
        self.store = store

    def list_profiles(self) -> dict[str, Any]:
        pointer, active = self.store.get_active()
        items = [self._public(profile, active.profile_id) for profile in self.store.list()]
        return {"active": self._public(active, active.profile_id), "pointer": pointer, "items": items}

    def get_profile(self, profile_id: str) -> dict[str, Any]:
        _, active = self.store.get_active()
        return self._public(self.store.get(profile_id), active.profile_id)

    def create_profile(self, payload: dict[str, Any], *, actor: str, reason: str) -> dict[str, Any]:
        raw = dict(payload)
        raw["state"] = "draft"
        raw["profile_version"] = 1
        raw["acceptance"] = {"status": "not_run", "checks": {}}
        profile = RuntimeProfile.from_dict(raw)
        return self._public(self.store.create(profile, actor=actor, reason=reason), None)

    def update_profile(
        self,
        profile_id: str,
        payload: dict[str, Any],
        *,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        current = self.store.get(profile_id)
        if current.state not in {"draft", "building", "verifying"}:
            raise RuntimeProfileStateError("只有未锁定的候选配置档可以修改")
        raw = current.to_dict()
        for key in ("display_name", "environment", "storage", "catalog", "semantics", "topology"):
            if key in payload:
                raw[key] = payload[key]
        raw["profile_version"] = current.profile_version + 1
        raw["state"] = "draft"
        raw["acceptance"] = {"status": "not_run", "checks": {}}
        updated = RuntimeProfile.from_dict(raw)
        return self._public(self.store.replace(
            updated,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
        ), None)

    def verify_profile(
        self,
        profile_id: str,
        *,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        current = self.store.get(profile_id)
        if current.profile_version != expected_version:
            return self._replace_version_conflict(current, expected_version)
        if current.state not in {"draft", "building", "verifying"}:
            raise RuntimeProfileStateError("当前状态不能重新验收")
        try:
            adapter = storage_adapters.require(current.storage.type)
            health = adapter.health(current)
            passed = health.get("status") == "ok"
        except Exception as exc:
            health = {"status": "error", "message": str(exc)}
            passed = False
        acceptance = AcceptanceSpec(
            status="passed" if passed else "failed",
            report_id=f"{profile_id}-v{current.profile_version + 1}",
            passed_at=utc_now() if passed else None,
            checks={"storage_health": health},
        )
        updated = current.next_version(state="verifying", acceptance=acceptance)
        saved = self.store.replace(
            updated,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
            action="verify",
        )
        return self._public(saved, None)

    def mark_ready(
        self,
        profile_id: str,
        *,
        expected_version: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        current = self.store.get(profile_id)
        if current.profile_version != expected_version:
            return self._replace_version_conflict(current, expected_version)
        if current.state != "verifying" or current.acceptance.status != "passed":
            raise RuntimeProfileStateError("只有已通过验收的 verifying 配置档可以锁定为 ready")
        updated = current.next_version(state="ready")
        return self._public(self.store.replace(
            updated,
            expected_version=expected_version,
            actor=actor,
            reason=reason,
            action="ready",
        ), None)

    def activate(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        target = self.store.get(profile_id)
        adapter = storage_adapters.require(target.storage.type)
        adapter.ensure_query_ready(target)
        pointer, active = self.store.activate(
            profile_id,
            expected_revision=expected_revision,
            actor=actor,
            reason=reason,
        )
        return {"pointer": pointer, "active": self._public(active, active.profile_id)}

    def audit(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_audit(limit)

    @staticmethod
    def _replace_version_conflict(current: RuntimeProfile, expected: int):
        from engine.runtime_profiles.errors import RuntimeProfileConflict

        raise RuntimeProfileConflict(
            f"配置档版本已变化：期望 v{expected}，当前 v{current.profile_version}"
        )

    @staticmethod
    def _public(profile: RuntimeProfile, active_id: str | None) -> dict[str, Any]:
        payload = profile.to_dict()
        payload["is_active"] = profile.profile_id == active_id
        payload["effective_state"] = "active" if payload["is_active"] else (
            "ready" if profile.state == "active" else profile.state
        )
        return payload

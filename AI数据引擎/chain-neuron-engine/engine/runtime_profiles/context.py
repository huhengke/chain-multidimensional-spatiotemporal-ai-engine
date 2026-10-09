"""Immutable request-scoped runtime-profile context."""

from __future__ import annotations

from dataclasses import dataclass

from engine.runtime_profiles.models import RuntimeProfile


@dataclass(frozen=True)
class RuntimeProfileContext:
    profile_id: str
    profile_version: int
    storage_type: str
    storage_root: str
    catalog_id: str
    catalog_hash: str
    datasets: dict[str, str]

    @classmethod
    def from_profile(cls, profile: RuntimeProfile) -> "RuntimeProfileContext":
        return cls(
            profile_id=profile.profile_id,
            profile_version=profile.profile_version,
            storage_type=profile.storage.type,
            storage_root=profile.storage.root,
            catalog_id=profile.catalog.catalog_id,
            catalog_hash=profile.catalog.catalog_hash,
            datasets=dict(profile.catalog.datasets),
        )

    def response_metadata(self) -> dict[str, str | int]:
        return {
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "storage_type": self.storage_type,
            "catalog_id": self.catalog_id,
        }

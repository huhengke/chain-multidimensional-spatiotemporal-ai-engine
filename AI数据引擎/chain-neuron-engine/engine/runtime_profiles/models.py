"""Validated, serializable runtime-profile domain models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from engine.runtime_profiles.errors import RuntimeProfileError

PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
VALID_STATES = {"draft", "building", "verifying", "ready", "active", "retired"}
VALID_ENVIRONMENTS = {"local", "development", "test", "staging", "production"}
VALID_STORAGE_TYPES = {"legacy_sqlite", "cnts"}
VALID_ACCEPTANCE_STATES = {"not_run", "running", "passed", "failed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeProfileError(f"{name} 必须是对象")
    return {str(key): item for key, item in value.items()}


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeProfileError(f"{name} 不能为空")
    return text


@dataclass(frozen=True)
class StorageSpec:
    type: str
    adapter_version: str
    root: str
    contract_version: int = 1

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "StorageSpec":
        data = _mapping(raw, "storage")
        storage_type = _required_text(data.get("type"), "storage.type")
        if storage_type not in VALID_STORAGE_TYPES:
            raise RuntimeProfileError(f"不支持的存储类型：{storage_type}")
        try:
            contract_version = int(data.get("contract_version", 1))
        except (TypeError, ValueError) as exc:
            raise RuntimeProfileError("storage.contract_version 必须是正整数") from exc
        if contract_version < 1:
            raise RuntimeProfileError("storage.contract_version 必须是正整数")
        return cls(
            type=storage_type,
            adapter_version=_required_text(
                data.get("adapter_version", "1.0.0"), "storage.adapter_version"
            ),
            root=_required_text(data.get("root"), "storage.root"),
            contract_version=contract_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "adapter_version": self.adapter_version,
            "root": self.root,
            "contract_version": self.contract_version,
        }


@dataclass(frozen=True)
class CatalogSpec:
    catalog_id: str
    catalog_hash: str
    datasets: dict[str, str]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CatalogSpec":
        data = _mapping(raw, "catalog")
        raw_datasets = _mapping(data.get("datasets", {}), "catalog.datasets")
        datasets: dict[str, str] = {}
        for logical_id, generation_id in raw_datasets.items():
            key = _required_text(logical_id, "catalog dataset id")
            datasets[key] = _required_text(
                generation_id, f"catalog.datasets.{key}"
            )
        if not datasets:
            raise RuntimeProfileError("catalog.datasets 至少需要一个数据集")
        return cls(
            catalog_id=_required_text(data.get("catalog_id"), "catalog.catalog_id"),
            catalog_hash=_required_text(data.get("catalog_hash"), "catalog.catalog_hash"),
            datasets=datasets,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_id": self.catalog_id,
            "catalog_hash": self.catalog_hash,
            "datasets": dict(self.datasets),
        }


@dataclass(frozen=True)
class AcceptanceSpec:
    status: str = "not_run"
    report_id: str | None = None
    passed_at: str | None = None
    checks: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "AcceptanceSpec":
        data = _mapping(raw or {}, "acceptance")
        status = str(data.get("status", "not_run")).strip()
        if status not in VALID_ACCEPTANCE_STATES:
            raise RuntimeProfileError(f"不支持的验收状态：{status}")
        report_id = data.get("report_id")
        passed_at = data.get("passed_at")
        return cls(
            status=status,
            report_id=str(report_id).strip() if report_id else None,
            passed_at=str(passed_at).strip() if passed_at else None,
            checks=_mapping(data.get("checks", {}), "acceptance.checks"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "report_id": self.report_id,
            "passed_at": self.passed_at,
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class RuntimeProfile:
    profile_id: str
    display_name: str
    environment: str
    state: str
    profile_version: int
    storage: StorageSpec
    catalog: CatalogSpec
    semantics: dict[str, Any] = field(default_factory=dict)
    topology: dict[str, Any] = field(default_factory=dict)
    acceptance: AcceptanceSpec = field(default_factory=AcceptanceSpec)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RuntimeProfile":
        data = _mapping(raw, "runtime profile")
        profile_id = _required_text(data.get("profile_id"), "profile_id")
        if not PROFILE_ID_RE.fullmatch(profile_id):
            raise RuntimeProfileError(
                "profile_id 只能包含小写字母、数字、点、下划线和连字符，长度 3~80"
            )
        environment = _required_text(data.get("environment"), "environment")
        if environment not in VALID_ENVIRONMENTS:
            raise RuntimeProfileError(f"不支持的运行环境：{environment}")
        state = _required_text(data.get("state", "draft"), "state")
        if state not in VALID_STATES:
            raise RuntimeProfileError(f"不支持的配置档状态：{state}")
        try:
            profile_version = int(data.get("profile_version", 1))
        except (TypeError, ValueError) as exc:
            raise RuntimeProfileError("profile_version 必须是正整数") from exc
        if profile_version < 1:
            raise RuntimeProfileError("profile_version 必须是正整数")
        acceptance = AcceptanceSpec.from_dict(data.get("acceptance"))
        if state in {"ready", "active"} and acceptance.status != "passed":
            raise RuntimeProfileError("ready/active 配置档必须已经通过验收")
        now = utc_now()
        return cls(
            profile_id=profile_id,
            display_name=_required_text(data.get("display_name"), "display_name"),
            environment=environment,
            state=state,
            profile_version=profile_version,
            storage=StorageSpec.from_dict(_mapping(data.get("storage", {}), "storage")),
            catalog=CatalogSpec.from_dict(_mapping(data.get("catalog", {}), "catalog")),
            semantics=_mapping(data.get("semantics", {}), "semantics"),
            topology=_mapping(data.get("topology", {}), "topology"),
            acceptance=acceptance,
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
        )

    def next_version(self, **changes: Any) -> "RuntimeProfile":
        return replace(
            self,
            profile_version=self.profile_version + 1,
            updated_at=utc_now(),
            **changes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "display_name": self.display_name,
            "environment": self.environment,
            "state": self.state,
            "profile_version": self.profile_version,
            "storage": self.storage.to_dict(),
            "catalog": self.catalog.to_dict(),
            "semantics": dict(self.semantics),
            "topology": dict(self.topology),
            "acceptance": self.acceptance.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

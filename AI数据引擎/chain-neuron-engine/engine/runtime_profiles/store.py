"""Versioned YAML profile store with one atomic active pointer."""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import yaml

from engine.runtime_profiles.errors import (
    RuntimeProfileConflict,
    RuntimeProfileError,
    RuntimeProfileNotFound,
    RuntimeProfileStateError,
)
from engine.runtime_profiles.models import AcceptanceSpec, RuntimeProfile, utc_now

LEGACY_DATASETS = {
    "oil_master": "legacy-live",
    "non_oil_sales": "legacy-live",
    "oil_sales_plan": "legacy-live",
    "oil_sales_daily": "legacy-live",
    "oil_monthly_sales": "legacy-live",
}


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


class RuntimeProfileStore:
    def __init__(
        self,
        root: Path,
        *,
        allowed_roots: Iterable[Path],
        legacy_db_path: Path,
        relative_root: Path | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.profiles_dir = self.root / "profiles"
        self.snapshots_dir = self.root / "snapshots"
        self.audit_dir = self.root / "audit"
        self.active_file = self.root / "ACTIVE"
        self.allowed_roots = tuple(Path(item).resolve() for item in allowed_roots)
        self.legacy_db_path = Path(legacy_db_path).resolve()
        self.relative_root = Path(relative_root or self.root.parent).resolve()
        self.write_lock_file = self.root / ".profiles.lock"
        self._lock = threading.RLock()

    def initialize(self) -> RuntimeProfile:
        with self._lock:
            with self._file_write_lock():
                for path in (self.profiles_dir, self.snapshots_dir, self.audit_dir):
                    path.mkdir(parents=True, exist_ok=True)
                if not self.active_file.exists():
                    profile_path = self._profile_path("legacy-local-v1")
                    if profile_path.exists():
                        profile = self._load_profile(profile_path)
                    else:
                        profile = self._legacy_profile()
                        self._write_profile(profile, snapshot=True)
                    self._write_active_pointer(
                        profile.profile_id,
                        profile.profile_version,
                        revision=1,
                        reason="bootstrap",
                    )
                    self.append_audit(
                        action="bootstrap",
                        actor="system",
                        reason="首次启动创建 Legacy SQLite 活动配置档",
                        before=None,
                        after=profile,
                        pointer_revision=1,
                    )
                return self.get_active()[1]

    def _legacy_profile(self) -> RuntimeProfile:
        now = utc_now()
        return RuntimeProfile.from_dict({
            "profile_id": "legacy-local-v1",
            "display_name": "Legacy SQLite 当前运行配置",
            "environment": "local",
            "state": "active",
            "profile_version": 1,
            "storage": {
                "type": "legacy_sqlite",
                "adapter_version": "1.0.0",
                "root": str(self.legacy_db_path),
                "contract_version": 1,
            },
            "catalog": {
                "catalog_id": "legacy-sqlite-live",
                "catalog_hash": "legacy-live",
                "datasets": LEGACY_DATASETS,
            },
            "semantics": {"source": "current-engine"},
            "topology": {"schema_version": 1},
            "acceptance": {
                "status": "passed",
                "report_id": "legacy-bootstrap",
                "passed_at": now,
                "checks": {"bootstrap": True},
            },
            "created_at": now,
            "updated_at": now,
        })

    def resolve_storage_root(self, profile: RuntimeProfile) -> Path:
        candidate = Path(profile.storage.root)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.relative_root / candidate).resolve()
        if not any(_is_within(resolved, allowed) for allowed in self.allowed_roots):
            raise RuntimeProfileError(
                f"storage.root 超出允许的数据目录：{profile.storage.root}"
            )
        return resolved

    def create(self, profile: RuntimeProfile, *, actor: str, reason: str) -> RuntimeProfile:
        with self._lock:
            with self._file_write_lock():
                self.resolve_storage_root(profile)
                path = self._profile_path(profile.profile_id)
                if path.exists():
                    raise RuntimeProfileConflict(f"配置档已存在：{profile.profile_id}")
                if profile.state != "draft" or profile.profile_version != 1:
                    raise RuntimeProfileStateError("新配置档必须从 draft v1 创建")
                self._write_profile(profile, snapshot=True)
                self.append_audit(
                    action="create",
                    actor=actor,
                    reason=reason,
                    before=None,
                    after=profile,
                )
                return profile

    def replace(
        self,
        profile: RuntimeProfile,
        *,
        expected_version: int,
        actor: str,
        reason: str,
        action: str = "update",
    ) -> RuntimeProfile:
        with self._lock:
            with self._file_write_lock():
                current = self.get(profile.profile_id)
                if current.profile_version != expected_version:
                    raise RuntimeProfileConflict(
                        f"配置档版本已变化：期望 v{expected_version}，"
                        f"当前 v{current.profile_version}"
                    )
                if profile.profile_version != current.profile_version + 1:
                    raise RuntimeProfileConflict("新版本必须在当前版本上递增 1")
                self.resolve_storage_root(profile)
                self._write_profile(profile, snapshot=True)
                self.append_audit(
                    action=action,
                    actor=actor,
                    reason=reason,
                    before=current,
                    after=profile,
                )
                return profile

    def list(self) -> list[RuntimeProfile]:
        if not self.profiles_dir.exists():
            return []
        return sorted(
            (self._load_profile(path) for path in self.profiles_dir.glob("*.yaml")),
            key=lambda item: (item.environment, item.profile_id),
        )

    def get(self, profile_id: str) -> RuntimeProfile:
        path = self._profile_path(profile_id)
        if not path.exists():
            raise RuntimeProfileNotFound(f"配置档不存在：{profile_id}")
        return self._load_profile(path)

    def get_active(self) -> tuple[dict[str, Any], RuntimeProfile]:
        if not self.active_file.exists():
            raise RuntimeProfileError("活动配置档指针不存在")
        pointer = self._load_yaml(self.active_file)
        try:
            profile_id = str(pointer["profile_id"])
            profile_version = int(pointer["profile_version"])
            revision = int(pointer["revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeProfileError("活动配置档指针已损坏") from exc
        if revision < 1:
            raise RuntimeProfileError("活动配置档指针 revision 无效")
        profile = self.get(profile_id)
        if profile.profile_version != profile_version:
            raise RuntimeProfileConflict(
                f"活动指针固定 v{profile_version}，当前配置档为 v{profile.profile_version}"
            )
        return pointer, profile

    def activate(
        self,
        profile_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> tuple[dict[str, Any], RuntimeProfile]:
        with self._lock:
            with self._file_write_lock():
                pointer, current = self.get_active()
                if int(pointer["revision"]) != expected_revision:
                    raise RuntimeProfileConflict(
                        f"活动配置已变化：期望 revision {expected_revision}，"
                        f"当前为 {pointer['revision']}"
                    )
                target = self.get(profile_id)
                if target.state not in {"ready", "active"}:
                    raise RuntimeProfileStateError("只有 ready 配置档可以激活")
                if target.acceptance.status != "passed":
                    raise RuntimeProfileStateError("配置档尚未通过验收")
                if target.profile_id == current.profile_id:
                    return pointer, current

                activated = target.next_version(state="active")
                self._write_profile(activated, snapshot=True)
                new_revision = expected_revision + 1
                self._write_active_pointer(
                    activated.profile_id,
                    activated.profile_version,
                    revision=new_revision,
                    reason=reason,
                )

                if current.state == "active":
                    previous_ready = current.next_version(state="ready")
                    self._write_profile(previous_ready, snapshot=True)
                self.append_audit(
                    action="activate",
                    actor=actor,
                    reason=reason,
                    before=current,
                    after=activated,
                    pointer_revision=new_revision,
                )
                return self.get_active()

    @contextmanager
    def _file_write_lock(self, timeout_seconds: float = 5.0):
        """Cross-process lock for profile mutations and ACTIVE compare-and-swap."""
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(
                    self.write_lock_file,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(descriptor, f"pid={os.getpid()} time={utc_now()}".encode("ascii"))
            except FileExistsError as exc:
                try:
                    stale = time.time() - self.write_lock_file.stat().st_mtime > 60
                except FileNotFoundError:
                    continue
                if stale:
                    try:
                        self.write_lock_file.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeProfileConflict("运行配置档正被其他进程修改，请稍后重试") from exc
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(descriptor)
            try:
                self.write_lock_file.unlink()
            except FileNotFoundError:
                pass

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.audit_dir.exists():
            return []
        paths = sorted(self.audit_dir.glob("*.yaml"), reverse=True)[:max(1, min(limit, 1000))]
        return [self._load_yaml(path) for path in paths]

    def append_audit(
        self,
        *,
        action: str,
        actor: str,
        reason: str,
        before: RuntimeProfile | None,
        after: RuntimeProfile | None,
        pointer_revision: int | None = None,
    ) -> None:
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc)
        event = {
            "event_id": str(uuid4()),
            "timestamp": timestamp.isoformat(timespec="milliseconds"),
            "action": action,
            "actor": actor,
            "reason": reason,
            "pointer_revision": pointer_revision,
            "before": self._profile_ref(before),
            "after": self._profile_ref(after),
        }
        name = f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{event['event_id']}.yaml"
        self._atomic_dump(self.audit_dir / name, event)

    @staticmethod
    def _profile_ref(profile: RuntimeProfile | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "state": profile.state,
            "storage_type": profile.storage.type,
            "catalog_id": profile.catalog.catalog_id,
        }

    def _profile_path(self, profile_id: str) -> Path:
        if not re_full_profile_id(profile_id):
            raise RuntimeProfileError("profile_id 格式不合法")
        return self.profiles_dir / f"{profile_id}.yaml"

    def _write_profile(self, profile: RuntimeProfile, *, snapshot: bool) -> None:
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_dump(self._profile_path(profile.profile_id), profile.to_dict())
        if snapshot:
            directory = self.snapshots_dir / profile.profile_id
            directory.mkdir(parents=True, exist_ok=True)
            self._atomic_dump(
                directory / f"v{profile.profile_version:06d}.yaml",
                profile.to_dict(),
            )

    def _write_active_pointer(
        self,
        profile_id: str,
        profile_version: int,
        *,
        revision: int,
        reason: str,
    ) -> None:
        self._atomic_dump(self.active_file, {
            "profile_id": profile_id,
            "profile_version": profile_version,
            "revision": revision,
            "activated_at": utc_now(),
            "reason": reason,
        })

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeProfileError(f"无法读取配置文件 {path.name}：{exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeProfileError(f"配置文件 {path.name} 不是对象")
        return data

    def _load_profile(self, path: Path) -> RuntimeProfile:
        return RuntimeProfile.from_dict(self._load_yaml(path))

    @staticmethod
    def _atomic_dump(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()


def re_full_profile_id(profile_id: str) -> bool:
    from engine.runtime_profiles.models import PROFILE_ID_RE

    return bool(PROFILE_ID_RE.fullmatch(str(profile_id or "")))

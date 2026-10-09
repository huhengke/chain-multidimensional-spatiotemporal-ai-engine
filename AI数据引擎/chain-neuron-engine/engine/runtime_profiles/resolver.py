"""Process-wide runtime-profile store and per-request resolver."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from engine.core.config import DATA_DIR, DB_PATH, ENGINE_ROOT
from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.models import RuntimeProfile
from engine.runtime_profiles.service import RuntimeProfileService
from engine.runtime_profiles.store import RuntimeProfileStore


def _allowed_roots() -> tuple[Path, ...]:
    roots = [DATA_DIR.resolve()]
    cnts_data = (ENGINE_ROOT.parent.parent / "自研AI数据引擎" / "data").resolve()
    roots.append(cnts_data)
    for raw in os.environ.get("RUNTIME_PROFILE_ALLOWED_ROOTS", "").split(os.pathsep):
        if raw.strip():
            roots.append(Path(raw.strip()).resolve())
    return tuple(dict.fromkeys(roots))


@lru_cache(maxsize=1)
def get_runtime_profile_store() -> RuntimeProfileStore:
    store = RuntimeProfileStore(
        DATA_DIR / "runtime_profiles",
        allowed_roots=_allowed_roots(),
        legacy_db_path=DB_PATH,
        relative_root=ENGINE_ROOT,
    )
    store.initialize()
    return store


def get_runtime_profile_service() -> RuntimeProfileService:
    return RuntimeProfileService(get_runtime_profile_store())


def initialize_runtime_profiles() -> RuntimeProfile:
    return get_runtime_profile_store().initialize()


def resolve_active_profile() -> RuntimeProfileContext:
    _, profile = get_runtime_profile_store().get_active()
    return RuntimeProfileContext.from_profile(profile)

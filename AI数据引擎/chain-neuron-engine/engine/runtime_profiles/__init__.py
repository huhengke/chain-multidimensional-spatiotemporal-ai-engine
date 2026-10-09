"""Runtime profiles bind one storage backend to one complete dataset catalog."""

from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.models import RuntimeProfile


def resolve_active_profile() -> RuntimeProfileContext:
    """Resolve lazily so low-level adapters can import profile types safely."""
    from engine.runtime_profiles.resolver import resolve_active_profile as resolve

    return resolve()

__all__ = ["RuntimeProfile", "RuntimeProfileContext", "resolve_active_profile"]

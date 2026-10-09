"""Runtime-profile domain errors."""

from __future__ import annotations


class RuntimeProfileError(ValueError):
    """Base class for invalid profile operations."""


class RuntimeProfileNotFound(RuntimeProfileError):
    """Requested profile does not exist."""


class RuntimeProfileConflict(RuntimeProfileError):
    """Optimistic version or activation revision no longer matches."""


class RuntimeProfileStateError(RuntimeProfileError):
    """Requested lifecycle transition is not allowed."""


class RuntimeProfileStorageError(RuntimeProfileError):
    """The profile storage backend is unavailable or unsupported."""

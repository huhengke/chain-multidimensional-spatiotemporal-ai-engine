"""Storage backends selected by an immutable runtime-profile context."""

from engine.storage.adapters.registry import storage_adapters

__all__ = ["storage_adapters"]

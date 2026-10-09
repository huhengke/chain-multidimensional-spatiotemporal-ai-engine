"""Central storage-adapter registry; query code never selects fallbacks."""

from __future__ import annotations

from engine.runtime_profiles.errors import RuntimeProfileStorageError
from engine.storage.adapters.base import StorageAdapter
from engine.storage.adapters.cnts import CntsAdapter
from engine.storage.adapters.legacy_sqlite import LegacySqliteAdapter


class StorageAdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, StorageAdapter] = {}

    def register(self, adapter: StorageAdapter) -> None:
        self._items[adapter.storage_type] = adapter

    def require(self, storage_type: str) -> StorageAdapter:
        adapter = self._items.get(storage_type)
        if adapter is None:
            raise RuntimeProfileStorageError(
                f"存储适配器尚未注册：{storage_type}；禁止自动回退"
            )
        return adapter

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


storage_adapters = StorageAdapterRegistry()
storage_adapters.register(LegacySqliteAdapter())
storage_adapters.register(CntsAdapter())

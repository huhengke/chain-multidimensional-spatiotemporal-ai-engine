# -*- coding: utf-8 -*-
"""LRU 缓存：高频路由结果与节点聚合。"""
from __future__ import annotations

from collections import OrderedDict
from threading import Lock
from typing import Any


class LRUCache:
    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)

    def invalidate_prefix(self, prefix: str) -> None:
        """写操作后使某前缀的缓存失效。"""
        with self._lock:
            for k in [k for k in self._data if k.startswith(prefix)]:
                del self._data[k]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = self.misses = 0

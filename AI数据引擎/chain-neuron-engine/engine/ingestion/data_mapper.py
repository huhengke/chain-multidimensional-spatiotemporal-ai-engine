# -*- coding: utf-8 -*-
"""DataMapper：按数据集注册声明把来源行映射为标准记录。"""
from __future__ import annotations

from typing import Any

from engine.derivation.time_derivation_engine import TimeDerivationEngine
from engine.ingestion.cleaner import CleanError
from engine.ingestion.generic_mapper import GenericMapper
from engine.storage.dataset_registry import KIND_GENERIC, registry


class DataMapper:
    """通用数据集映射器，不再包含日报/计划专用分支。"""

    def __init__(self, derivation: TimeDerivationEngine | None = None):
        self.derivation = derivation or TimeDerivationEngine()
        self._cache: dict[str, GenericMapper] = {}
        self._revision = -1

    def map_row(self, dataset: str, row: dict) -> dict[str, Any]:
        spec = registry.get(dataset)
        if spec is None or spec.kind != KIND_GENERIC:
            raise CleanError(f"未知或不可导入的数据集：{dataset}")
        if self._revision != registry.revision:
            self._cache.clear()
            self._revision = registry.revision
        mapper = self._cache.get(dataset)
        if mapper is None:
            mapper = GenericMapper(spec, self.derivation)
            self._cache[dataset] = mapper
        return mapper.map_row(row)

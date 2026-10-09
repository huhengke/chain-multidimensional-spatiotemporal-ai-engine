# -*- coding: utf-8 -*-
"""从注册表生成 Agent/大模型可消费的数据集能力契约。"""
from __future__ import annotations

from typing import Any

from engine.features.structure import build_structure_summary
from engine.query.experience import business_aggregates_for_dataset
from engine.storage.dataset_registry import DatasetSpec, registry


def build_dataset_contract(spec: DatasetSpec) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    if spec.kind == "dimension":
        for column in spec.columns:
            fields.append({"source": column.source, "name": column.name,
                           "label": column.label or column.name, "role": "dimension"})
    else:
        fields.append({"source": spec.time_source, "name": spec.time_source,
                       "label": "时间", "role": "time", "semantic_type": "time",
                       "format": spec.time_format, "canonical_time": True})
        for level in spec.levels:
            key = spec.level_value_key.get(level, level)
            fields.append({"source": spec.column_sources.get(key, key), "name": key,
                           "label": spec.level_labels.get(level, level), "role": "dimension",
                           "level": level})
        for metric in spec.metrics:
            fields.append({"source": spec.column_sources.get(metric.source, metric.source),
                           "name": metric.column, "label": metric.label or metric.name,
                           "role": "metric", "metric": metric.name})
    source_origin = "interface" if spec.id in registry.interface_specs else "base"
    source = dict(spec.source) if spec.source else {
        "kind": "excel" if spec.source_file else "base",
        "file": spec.source_file or None,
    }
    source["origin"] = source_origin
    return {
        "contract_version": 1,
        "registry_revision": registry.revision,
        "dataset": spec.id,
        "label": spec.label,
        "aliases": list(spec.aliases),
        "kind": spec.kind,
        "source_origin": source_origin,
        "source": source,
        "time": {"source": spec.time_source, "format": spec.time_format} if spec.is_spiral else None,
        "levels": [{"name": level, "label": spec.level_labels.get(level, level)} for level in spec.levels],
        "metrics": [{"name": m.name, "label": m.label or m.name, "column": m.column} for m in spec.metrics],
        "order_key": spec.order_key or None,
        "fields": fields,
        "base": dict(spec.base),
        "business_aggregates": business_aggregates_for_dataset(spec.id),
        "capabilities": dict(spec.capabilities),
        "structure": build_structure_summary(spec),
        "agent_usage": "可用于数据集发现、字段角色解释和查询条件校验；只读，不直接修改数据",
    }

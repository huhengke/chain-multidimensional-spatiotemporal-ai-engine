# -*- coding: utf-8 -*-
"""数据集空间结构摘要：基座/主干/分枝、DNA 与动态生长。"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from sqlalchemy import and_, case, func as sa_func, or_, select
from sqlalchemy.orm import Session

from engine.core.config import get_settings
from engine.core.params import get_param
from engine.features.growth_plan import build_growth_plan
from engine.storage.dataset_registry import DatasetSpec
from engine.storage.models import SpiralNodeORM
from engine.storage.topology import topology_summary

LAYER_KEYS = ("base", "trunk", "branch")
LAYER_LABELS = {"base": "基座", "trunk": "中通主干", "branch": "中通分枝"}
TIME_LEVELS = {"year", "season", "month", "quarter", "yyyymm"}


def _raw_layers(spec: DatasetSpec) -> dict[str, Any] | None:
    raw = spec.capabilities.get("feature_layers")
    return raw if isinstance(raw, dict) else None


def _fallback_layers(spec: DatasetSpec) -> dict[str, list[dict[str, Any]]]:
    """为未记录 feature_layers 的旧声明生成只读回退视图。"""
    layers: dict[str, list[dict[str, Any]]] = {key: [] for key in LAYER_KEYS}
    for feature in spec.base.get("features") or []:
        if isinstance(feature, dict) and feature.get("source"):
            layers["base"].append({
                "source": str(feature["source"]),
                "name": str(feature.get("name") or feature["source"]),
                "label": str(feature.get("label") or feature["source"]),
                "role": "dimension",
                "semantic_type": str(feature.get("semantic_type") or "other"),
                "query_uses": ["filter", "join"],
            })
    if spec.is_spiral:
        layers["trunk"].append({
            "source": spec.time_source,
            "name": spec.time_source,
            "label": "时间",
            "role": "time",
            "semantic_type": "time",
            "query_uses": ["filter", "group_by"],
        })
        for level in spec.levels:
            key = spec.level_value_key.get(level, level)
            source = spec.column_sources.get(key, key)
            if level in TIME_LEVELS and source == spec.time_source:
                continue
            layers["trunk"].append({
                "source": source,
                "name": key,
                "label": spec.level_labels.get(level, level),
                "role": "dimension",
                "semantic_type": "other",
                "query_uses": ["filter", "group_by"],
            })
        for metric in spec.metrics:
            layers["branch"].append({
                "source": spec.column_sources.get(metric.source, metric.source),
                "name": metric.column,
                "label": metric.label or metric.name,
                "role": "metric",
                "semantic_type": "other",
                "query_uses": ["aggregate", "sort", "compare"],
            })
    return layers


def layer_level_indexes(spec: DatasetSpec, layer: str) -> list[int]:
    """返回某结构层对应的螺旋层级下标（时间派生层也会映射）。"""
    raw = _raw_layers(spec) or _fallback_layers(spec)
    sources = {
        str(item.get("source"))
        for item in (raw.get(layer) or [])
        if isinstance(item, dict) and item.get("source")
    }
    indexes: list[int] = []
    for index, level in enumerate(spec.levels):
        key = spec.level_value_key.get(level, level)
        source = spec.column_sources.get(key, key)
        if source in sources or (source == spec.time_source and spec.time_source in sources):
            indexes.append(index)
        elif level in TIME_LEVELS and spec.time_source in sources:
            indexes.append(index)
    # 分枝经常只包含指标而不包含层级；此时分枝聚合应覆盖当前螺旋路径。
    if layer == "branch" and not indexes and raw.get("branch"):
        if any(str(item.get("role")) == "metric" for item in raw.get("branch") if isinstance(item, dict)):
            indexes = list(range(len(spec.levels)))
    return indexes


def rebuild_plan(spec: DatasetSpec, scope: str) -> dict[str, Any]:
    scope = scope if scope in {"all", "base", "trunk", "branch"} else "all"
    growth_plan = build_growth_plan(spec)
    physical_levels = list(growth_plan.physical_levels)
    if scope == "base":
        selected_levels: list[str] = []
    elif scope == "trunk":
        selected_levels = list(growth_plan.trunk_levels)
    elif scope == "branch":
        selected_levels = list(growth_plan.branch_levels)
    else:
        selected_levels = physical_levels
    indexes = [spec.levels.index(level) for level in selected_levels if level in spec.levels]
    # 基座只对应根节点；主干/分枝使用同一份物理生长计划，避免字段角色
    # 与空间位置混用后生成不可解释的深层路径。
    max_level = max(indexes, default=-1) + 1
    return {
        "scope": scope,
        "level_indexes": indexes,
        "levels": selected_levels,
        "max_level": max_level,
    }


def _empty_growth(spec: DatasetSpec) -> dict[str, Any]:
    raw = spec.capabilities.get("growth")
    config = raw if isinstance(raw, dict) else {}
    enabled = bool(config.get("enabled", spec.is_spiral))
    threshold = int(config.get("threshold") or get_param("growth_threshold"))
    growth_plan = build_growth_plan(spec)
    result: dict[str, Any] = {
        "enabled": enabled,
        "configured": isinstance(raw, dict),
        "stats_available": False,
        "threshold": threshold,
        "node_count": 0,
        "grown_node_count": 0,
        "candidate_node_count": 0,
        "grown_paths": [],
        "plan": growth_plan.as_dict(),
    }
    return result


def _growth_summary(
    spec: DatasetSpec,
    session: Session | None,
    stats: dict[str, Any] | None = None,
    include_paths: bool = True,
) -> dict[str, Any]:
    result = _empty_growth(spec)
    if not spec.is_spiral:
        return result
    result["stats_available"] = session is not None or bool(stats)
    if stats:
        result.update({key: value for key, value in stats.items() if key != "grown_paths"})
        result["grown_paths"] = list(stats.get("grown_paths") or [])
    if session is None:
        return result
    if stats and not include_paths:
        return result

    threshold = int(result["threshold"])
    max_level = len(build_growth_plan(spec).physical_levels)
    base = select(SpiralNodeORM).where(SpiralNodeORM.dataset == spec.id)
    result["node_count"] = int(session.scalar(
        select(sa_func.count()).select_from(base.subquery())
    ) or 0)
    result["grown_node_count"] = int(session.scalar(
        select(sa_func.count()).select_from(
            select(SpiralNodeORM.node_id)
            .where(SpiralNodeORM.dataset == spec.id, SpiralNodeORM.grown.is_(True))
            .subquery()
        )
    ) or 0)
    result["candidate_node_count"] = int(session.scalar(
        select(sa_func.count()).select_from(
            select(SpiralNodeORM.node_id)
            .where(
                SpiralNodeORM.dataset == spec.id,
                SpiralNodeORM.grown.is_(False),
                SpiralNodeORM.record_count >= threshold,
                SpiralNodeORM.level < max_level,
            )
            .subquery()
        )
    ) or 0)
    rows = session.execute(
        select(SpiralNodeORM)
        .where(SpiralNodeORM.dataset == spec.id, SpiralNodeORM.grown.is_(True))
        .order_by(SpiralNodeORM.level, SpiralNodeORM.node_id)
        .limit(12)
    ).scalars().all()
    paths: list[dict[str, Any]] = []
    for node in rows:
        paths.append({
            "node_id": node.node_id,
            "level": node.level,
            "level_name": node.level_name,
            "level_label": spec.level_labels.get(node.level_name, node.level_name),
            "node_value": node.node_value,
            "record_count": node.record_count,
            "path": node.path,
        })
    result["grown_paths"] = paths
    return result


def collect_growth_stats(
    specs: list[DatasetSpec],
    session: Session,
) -> dict[str, dict[str, Any]]:
    """一次性统计数据集列表需要的生长状态，避免每行触发三次节点查询。"""
    spiral_specs = [spec for spec in specs if spec.is_spiral]
    result = {spec.id: _empty_growth(spec) for spec in spiral_specs}
    ids = [spec.id for spec in spiral_specs]
    if not ids:
        return result

    rows = session.execute(
        select(
            SpiralNodeORM.dataset,
            sa_func.count(SpiralNodeORM.node_id),
            sa_func.sum(case((SpiralNodeORM.grown.is_(True), 1), else_=0)),
        )
        .where(SpiralNodeORM.dataset.in_(ids))
        .group_by(SpiralNodeORM.dataset)
    ).all()
    for dataset, node_count, grown_count in rows:
        result[dataset]["node_count"] = int(node_count or 0)
        result[dataset]["grown_node_count"] = int(grown_count or 0)

    candidate_conditions = [
        and_(
            SpiralNodeORM.dataset == spec.id,
            SpiralNodeORM.grown.is_(False),
            SpiralNodeORM.record_count >= int(result[spec.id]["threshold"]),
            SpiralNodeORM.level < len(build_growth_plan(spec).physical_levels),
        )
        for spec in spiral_specs
    ]
    candidate_rows = session.execute(
        select(SpiralNodeORM.dataset, sa_func.count(SpiralNodeORM.node_id))
        .where(or_(*candidate_conditions))
        .group_by(SpiralNodeORM.dataset)
    ).all()
    for dataset, count in candidate_rows:
        result[dataset]["candidate_node_count"] = int(count or 0)
    return result


def build_structure_summary(
    spec: DatasetSpec,
    session: Session | None = None,
    *,
    growth_stats: dict[str, Any] | None = None,
    topology_stats: dict[str, Any] | None = None,
    include_growth_paths: bool = True,
) -> dict[str, Any]:
    raw = _raw_layers(spec)
    layer_source = "declared" if raw is not None else "fallback"
    layers = raw if raw is not None else _fallback_layers(spec)
    fields: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for layer in LAYER_KEYS:
        for item in layers.get(layer) or []:
            if not isinstance(item, dict) or not item.get("source"):
                continue
            source = str(item["source"])
            entry = fields.setdefault(source, {
                "source": source,
                "name": str(item.get("name") or source),
                "label": str(item.get("label") or source),
                "layers": [],
                "layer_labels": [],
                "role": str(item.get("role") or "other"),
                "semantic_type": str(item.get("semantic_type") or "other"),
                "query_uses": [],
                "recommended": bool(item.get("recommended", False)),
            })
            if layer not in entry["layers"]:
                entry["layers"].append(layer)
                entry["layer_labels"].append(LAYER_LABELS[layer])
            for use in item.get("query_uses") or []:
                if use not in entry["query_uses"]:
                    entry["query_uses"].append(use)
            entry["recommended"] = entry["recommended"] or bool(item.get("recommended", False))

    dna_keys: list[str] = []
    for key in (raw or {}).get("dna_keys") or []:
        text = str(key).strip()
        if text and text not in dna_keys:
            dna_keys.append(text)
    if not dna_keys:
        dna_keys = list(fields)

    growth_plan = build_growth_plan(spec)
    spiral_levels = []
    for index, level in enumerate(growth_plan.physical_levels, start=1):
        key = spec.level_value_key.get(level, level)
        source = spec.column_sources.get(key, key)
        field = fields.get(source)
        spiral_levels.append({
            "index": index,
            "name": level,
            "label": spec.level_labels.get(level, level),
            "source": source,
            "layers": list(field["layers"]) if field else [],
        })

    growth = _growth_summary(
        spec,
        session,
        stats=growth_stats,
        include_paths=include_growth_paths,
    )
    physical_topology = topology_stats or {
        "topology_version": 0,
        "node_count": 0,
        "temporal_layers": 0,
        "helix_layers": 0,
        "paired_nodes": 0,
        "strand_a_edges": 0,
        "strand_b_edges": 0,
        "valid": True,
        "errors": [],
        "dna_is_topology_not_query_level": True,
    }
    if topology_stats is None and spec.is_spiral and session is not None:
        # 列表/详情摘要只读取已落库统计；全量对称性校验走专用 /spiral/topology 接口。
        physical_topology = topology_summary(session, spec.id, validate=False).as_dict()
    aggregation_scopes = [
        {"key": "all", "label": "全部节点", "levels": len(growth_plan.physical_levels)},
        {"key": "base", "label": "统一基座", "levels": 0},
        {"key": "trunk", "label": "公共时间主干", "levels": len(growth_plan.trunk_levels)},
        {"key": "branch", "label": "递归业务分枝", "levels": len(growth_plan.branch_levels)},
    ]
    return {
        "feature_layer_source": layer_source,
        "feature_layers": list(fields.values()),
        "layer_counts": {
            layer: sum(1 for field in fields.values() if layer in field["layers"])
            for layer in LAYER_KEYS
        },
        "dna": {
            "layer_count": sum(1 for layer in LAYER_KEYS if layers.get(layer)),
            "length": len(dna_keys),
            "keys": dna_keys,
        },
        "spiral": {
            "layer_count": len(growth_plan.physical_levels),
            "length": len(growth_plan.physical_levels),
            "levels": spiral_levels,
        },
        "growth": growth,
        "physical_topology": physical_topology,
        "aggregation": {
            "default_scope": "all",
            "scopes": aggregation_scopes,
        },
    }


def build_structure_list_summary(spec: DatasetSpec) -> dict[str, Any]:
    """构建数据集列表页所需的轻量结构摘要。

    列表页只需要展示结构计数、DNA 长度、螺旋层数和是否支持动态生长。
    字段明细、DNA 键序列、螺旋层级和实时节点统计由数据集详情接口按需加载，
    避免每次打开列表页都扫描全量 ``spiral_nodes``。
    """
    full = build_structure_summary(spec)
    growth = full["growth"]
    return {
        "feature_layer_source": full["feature_layer_source"],
        "layer_counts": full["layer_counts"],
        "dna": {
            "layer_count": full["dna"]["layer_count"],
            "length": full["dna"]["length"],
        },
        "spiral": {
            "layer_count": full["spiral"]["layer_count"],
            "length": full["spiral"]["length"],
        },
        "growth": {
            "enabled": growth["enabled"],
            "configured": growth["configured"],
            # 列表页不执行实时节点统计；详情页会展示准确统计和路径。
            "stats_available": False,
            "threshold": growth["threshold"],
            "node_count": 0,
            "grown_node_count": 0,
            "candidate_node_count": 0,
            "grown_paths": [],
            "plan": growth.get("plan", {}),
        },
        "aggregation": full["aggregation"],
    }


def build_structure_summaries(
    specs: list[DatasetSpec],
    session: Session,
) -> dict[str, dict[str, Any]]:
    stats = collect_growth_stats(specs, session)
    topology_stats = collect_topology_stats(specs, session)
    return {
        spec.id: build_structure_summary(
            spec,
            growth_stats=stats.get(spec.id),
            topology_stats=topology_stats.get(spec.id),
            include_growth_paths=False,
        )
        for spec in specs
    }


def collect_topology_stats(
    specs: list[DatasetSpec],
    session: Session,
) -> dict[str, dict[str, Any]]:
    """一次性统计数据集物理拓扑，避免列表页逐数据集扫描节点。"""
    ids = [spec.id for spec in specs if spec.is_spiral]
    result = {
        spec.id: {
            "topology_version": 0,
            "node_count": 0,
            "temporal_layers": 0,
            "helix_layers": 0,
            "paired_nodes": 0,
            "strand_a_edges": 0,
            "strand_b_edges": 0,
            "trunk_nodes": 0,
            "branch_nodes": 0,
            "grown_nodes": 0,
            "growth_threshold": int(get_param("growth_threshold")),
            "physical_levels": list(build_growth_plan(spec).physical_levels),
            "spatial_layers": ["inner", "store", "outer"],
            "valid": True,
            "errors": [],
            "dna_is_topology_not_query_level": True,
        }
        for spec in specs
    }
    if not ids:
        return result
    rows = session.execute(
        select(
            SpiralNodeORM.dataset,
            sa_func.max(SpiralNodeORM.topology_version),
            sa_func.count(SpiralNodeORM.node_id),
            sa_func.count(SpiralNodeORM.helix_layer.distinct()),
            sa_func.sum(case((SpiralNodeORM.pair_node_id.is_not(None), 1), else_=0)),
            sa_func.sum(case((SpiralNodeORM.strand_a_next.is_not(None), 1), else_=0)),
            sa_func.sum(case((SpiralNodeORM.strand_b_next.is_not(None), 1), else_=0)),
            sa_func.sum(case((SpiralNodeORM.topology_role == "trunk", 1), else_=0)),
            sa_func.sum(case((SpiralNodeORM.topology_role == "branch", 1), else_=0)),
            sa_func.sum(case((SpiralNodeORM.grown.is_(True), 1), else_=0)),
        )
        .where(SpiralNodeORM.dataset.in_(ids))
        .group_by(SpiralNodeORM.dataset)
    ).all()
    for dataset, version, count, layers, paired, a_edges, b_edges, trunk_nodes, branch_nodes, grown_nodes in rows:
        result[dataset].update({
            "topology_version": int(version or 0),
            "node_count": int(count or 0),
            "temporal_layers": int(layers or 0),
            "helix_layers": int(layers or 0),
            "paired_nodes": int(paired or 0),
            "strand_a_edges": int(a_edges or 0),
            "strand_b_edges": int(b_edges or 0),
            "trunk_nodes": int(trunk_nodes or 0),
            "branch_nodes": int(branch_nodes or 0),
            "grown_nodes": int(grown_nodes or 0),
        })
    return result

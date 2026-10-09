# -*- coding: utf-8 -*-
"""螺旋树路由：树结构、节点详情。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.core.errors import AppException, BadRequestError, NotFoundError
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, get_db
from engine.storage.dataset_registry import registry
from engine.storage.adapters.registry import storage_adapters
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_store import SpiralStore
from engine.storage.topology import topology_summary

router = APIRouter(prefix="/api/v1/engine/spiral", tags=["spiral"])


def _active_storage():
    try:
        context = resolve_active_profile()
        return context, storage_adapters.require(context.storage_type)
    except RuntimeProfileError as exc:
        raise AppException(
            "E4501",
            "活动 AI 数据引擎运行配置不可用",
            detail={"error": str(exc)},
            http_status=503,
        ) from exc


def _topology_node_payload(node) -> dict:
    return {
        "node_id": node.node_id,
        "dataset": node.dataset,
        "level": node.level,
        "level_name": node.level_name,
        "value": node.node_value,
        "parent_id": node.parent_id,
        "path": node.path,
        "record_count": node.record_count,
        "aggregations": node.aggregations or {},
        "grown": bool(node.grown),
        "topology": {
            "version": node.topology_version,
            "role": node.topology_role,
            "helix_layer": node.helix_layer,
            "temporal_layer": node.temporal_layer,
            "time_bucket": node.time_bucket,
            "time_granularity": node.time_granularity,
            "branch_depth": node.branch_depth,
            "growth_parent_id": node.growth_parent_id,
            "growth_dimension": node.growth_dimension,
            "capacity_snapshot": node.capacity_snapshot,
            "growth_version": node.growth_version,
            "ring_index": node.ring_index,
            "strand_a_prev": node.strand_a_prev,
            "strand_a_next": node.strand_a_next,
            "strand_b_prev": node.strand_b_prev,
            "strand_b_next": node.strand_b_next,
            "pair_node_id": node.pair_node_id,
            "feature_slots": node.feature_slots or {},
        },
    }


def _evenly_spaced(items: list, count: int) -> list:
    """Keep a small representative sample without collapsing onto the oldest layer."""
    if count <= 0 or not items:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indexes = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    return [items[index] for index in indexes]


def _topology_slice_nodes(session: Session, dataset: str, limit: int) -> list[SpiralNodeORM]:
    """Return a balanced visual slice with every selected branch's ancestor closure."""
    ordered = (
        SpiralNodeORM.temporal_layer,
        SpiralNodeORM.time_bucket,
        SpiralNodeORM.ring_index,
        SpiralNodeORM.node_id,
    )

    base = list(session.execute(
        select(SpiralNodeORM)
        .where(
            SpiralNodeORM.dataset == dataset,
            SpiralNodeORM.topology_role == "base",
        )
        .order_by(*ordered)
        .limit(1)
    ).scalars().all())

    all_trunks = list(session.execute(
        select(SpiralNodeORM)
        .where(
            SpiralNodeORM.dataset == dataset,
            SpiralNodeORM.topology_role == "trunk",
        )
        .order_by(*ordered)
    ).scalars().all())

    trunk_target = max(3, min(8, limit // 6))
    branch_target = max(0, limit - len(base) - trunk_target)
    parent_key = func.coalesce(SpiralNodeORM.growth_parent_id, SpiralNodeORM.parent_id)
    group_rows = list(session.execute(
        select(
            SpiralNodeORM.temporal_layer,
            parent_key.label("growth_parent"),
        )
        .where(
            SpiralNodeORM.dataset == dataset,
            SpiralNodeORM.topology_role == "branch",
        )
        .group_by(SpiralNodeORM.temporal_layer, parent_key)
        .order_by(SpiralNodeORM.temporal_layer, parent_key)
    ).all())

    group_target = min(6, max(1, branch_target // 6)) if group_rows else 0
    selected_groups = _evenly_spaced(group_rows, group_target)
    per_group = max(2, branch_target // max(1, len(selected_groups)))
    branch_groups: list[list[SpiralNodeORM]] = []
    for temporal_layer, growth_parent in selected_groups:
        conditions = [
            SpiralNodeORM.dataset == dataset,
            SpiralNodeORM.topology_role == "branch",
            parent_key == growth_parent,
        ]
        conditions.append(
            SpiralNodeORM.temporal_layer.is_(None)
            if temporal_layer is None
            else SpiralNodeORM.temporal_layer == temporal_layer
        )
        branch_groups.append(list(session.execute(
            select(SpiralNodeORM)
            .where(*conditions)
            .order_by(
                SpiralNodeORM.branch_depth,
                SpiralNodeORM.ring_index,
                SpiralNodeORM.node_id,
            )
            .limit(per_group)
        ).scalars().all()))

    sampled_branches = [node for group in branch_groups for node in group]
    sampled_ids = {node.node_id for node in sampled_branches}
    ancestor_by_id: dict[str, SpiralNodeORM] = {}
    pending = {
        node.growth_parent_id or node.parent_id
        for node in sampled_branches
        if (node.growth_parent_id or node.parent_id) not in sampled_ids
    }
    for _ in range(8):
        pending.discard(None)
        pending -= set(ancestor_by_id)
        if not pending:
            break
        parents = list(session.execute(
            select(SpiralNodeORM).where(
                SpiralNodeORM.dataset == dataset,
                SpiralNodeORM.node_id.in_(pending),
            )
        ).scalars().all())
        if not parents:
            break
        pending = set()
        for parent in parents:
            ancestor_by_id[parent.node_id] = parent
            next_parent = parent.growth_parent_id or parent.parent_id
            if parent.topology_role == "branch" and next_parent:
                pending.add(next_parent)

    branch_ancestors = sorted(
        (node for node in ancestor_by_id.values() if node.topology_role == "branch"),
        key=lambda node: (
            node.temporal_layer if node.temporal_layer is not None else -1,
            node.branch_depth if node.branch_depth is not None else 0,
            node.ring_index if node.ring_index is not None else -1,
            node.node_id,
        ),
    )
    trunk_ancestors = [
        node for node in ancestor_by_id.values()
        if node.topology_role == "trunk"
    ]

    trunk_ids = {node.node_id for node in trunk_ancestors}
    extra_trunks = _evenly_spaced(
        [node for node in all_trunks if node.node_id not in trunk_ids],
        max(0, trunk_target - len(trunk_ancestors)),
    )
    trunks = sorted(
        [*trunk_ancestors, *extra_trunks],
        key=lambda node: (
            node.temporal_layer if node.temporal_layer is not None else -1,
            node.ring_index if node.ring_index is not None else -1,
            node.node_id,
        ),
    )

    fixed = [*base, *trunks, *branch_ancestors]
    fixed_ids = {node.node_id for node in fixed}
    branch_room = max(0, limit - len(fixed))
    selected_branches: list[SpiralNodeORM] = []
    group_index = 0
    while len(selected_branches) < branch_room and any(branch_groups):
        group = branch_groups[group_index % len(branch_groups)]
        if group:
            node = group.pop(0)
            if node.node_id not in fixed_ids:
                selected_branches.append(node)
        group_index += 1

    items = [*fixed, *selected_branches]
    seen: set[str] = set()
    unique = []
    for node in items:
        if node.node_id in seen:
            continue
        seen.add(node.node_id)
        unique.append(node)

    if len(unique) < limit:
        fallback = list(session.execute(
            select(SpiralNodeORM)
            .where(
                SpiralNodeORM.dataset == dataset,
                SpiralNodeORM.topology_role == "node",
                SpiralNodeORM.node_id.not_in(seen),
            )
            .order_by(*ordered)
            .limit(limit - len(unique))
        ).scalars().all())
        unique.extend(fallback)
    return unique[:limit]


@router.get("/tree")
def get_tree(
    dataset: str = Query(...),
    max_depth: int = Query(default=4, ge=1, le=9),
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.tree(session, dataset, max_depth, context)
    spec = registry.get(dataset)
    if spec is None or not spec.is_spiral:
        raise BadRequestError(
            f"未知数据集：{dataset}", detail={"known": registry.spiral_ids()}
        )
    store = SpiralStore(session)
    tree = store.build_tree(dataset, max_depth=max_depth)
    return {"dataset": dataset, "max_depth": max_depth, "tree": tree}


@router.get("/nodes/{node_id:path}")
def get_node(
    node_id: str,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "CNTS 节点详情必须通过固定 Generation 的拓扑切片读取，不能按旧 spiral_nodes id 查询。",
            detail={**context.response_metadata(), "fallback": "forbidden"},
        )
    store = SpiralStore(session)
    node = store.get_node(node_id)
    if node is None:
        raise NotFoundError("节点不存在", node_id)
    children = store.list_children(node.dataset, node.node_id)
    return {
        "node_id": node.node_id,
        "dataset": node.dataset,
        "level": node.level,
        "level_name": node.level_name,
        "node_value": node.node_value,
        "parent_id": node.parent_id,
        "path": node.path,
        "dimensions": node.dimensions,
        "record_count": node.record_count,
        "aggregations": node.aggregations,
        "grown": node.grown,
        "chain_prev": node.chain_prev,
        "chain_next": node.chain_next,
        "topology": {
            "version": node.topology_version,
            "role": node.topology_role,
            "helix_layer": node.helix_layer,
            "temporal_layer": node.temporal_layer,
            "time_bucket": node.time_bucket,
            "time_granularity": node.time_granularity,
            "branch_depth": node.branch_depth,
            "growth_parent_id": node.growth_parent_id,
            "growth_dimension": node.growth_dimension,
            "capacity_snapshot": node.capacity_snapshot,
            "growth_version": node.growth_version,
            "ring_index": node.ring_index,
            "strand_a_prev": node.strand_a_prev,
            "strand_a_next": node.strand_a_next,
            "strand_b_prev": node.strand_b_prev,
            "strand_b_next": node.strand_b_next,
            "pair_node_id": node.pair_node_id,
            "feature_slots": node.feature_slots or {},
        },
        "children": [
            {
                "node_id": c.node_id,
                "level": c.level,
                "node_value": c.node_value,
                "record_count": c.record_count,
                "aggregations": c.aggregations,
                "topology": {
                    "version": c.topology_version,
                    "role": c.topology_role,
                    "helix_layer": c.helix_layer,
                    "temporal_layer": c.temporal_layer,
                    "time_bucket": c.time_bucket,
                    "time_granularity": c.time_granularity,
                    "branch_depth": c.branch_depth,
                    "ring_index": c.ring_index,
                    "pair_node_id": c.pair_node_id,
                },
            }
            for c in children
        ],
    }


@router.get("/topology")
def get_topology(
    dataset: str = Query(...),
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """查看数据集物理双链拓扑；DNA 仅作为数据链拓扑而非查询层级。"""
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.topology_summary(session, dataset, context)
    spec = registry.get(dataset)
    if spec is None or not spec.is_spiral:
        raise BadRequestError(
            f"未知数据集：{dataset}", detail={"known": registry.spiral_ids()}
        )
    return topology_summary(session, dataset, validate=True).as_dict()


@router.get("/topology/slice")
def get_topology_slice(
    dataset: str = Query(...),
    limit: int = Query(default=96, ge=6, le=180),
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """读取用于空间画布的有限真实节点切片。

    只抽取基座、主干、分枝三类代表节点，不把全量 spiral_nodes 展开到浏览器；
    节点上的双链字段仍来自实际物理存储。DNA 不是查询层级。
    """
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.topology_slice(session, dataset, limit, context)
    spec = registry.get(dataset)
    if spec is None or not spec.is_spiral:
        raise BadRequestError(
            f"未知数据集：{dataset}", detail={"known": registry.spiral_ids()}
        )

    items = _topology_slice_nodes(session, dataset, limit)
    return {
        "dataset": dataset,
        "limit": limit,
        "items": [_topology_node_payload(node) for node in items[:limit]],
    }

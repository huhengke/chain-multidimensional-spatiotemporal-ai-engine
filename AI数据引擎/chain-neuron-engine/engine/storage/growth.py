# -*- coding: utf-8 -*-
"""动态生长：阈值判断、真实分枝物化与拓扑刷新。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.core.params import get_param
from engine.features.growth_plan import physical_levels_for_dataset
from engine.storage.models import SpiralNodeORM

logger = get_logger("storage.growth")


def _default_threshold() -> int:
    return get_param("growth_threshold")


@dataclass
class GrowthCandidate:
    node_id: str
    level: int
    record_count: int
    next_level: str


def compute_checksum(node: SpiralNodeORM) -> str:
    raw = json.dumps(
        {
            "node_id": node.node_id,
            "record_count": node.record_count,
            "aggregations": node.aggregations,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class GrowthJudge:
    def __init__(self, session: Session, threshold: int | None = None):
        self.s = session
        self.threshold = threshold or _default_threshold()

    def scan(self, dataset: str) -> list[GrowthCandidate]:
        """返回达到阈值、未生长、且仍有下一维度可分叉的节点。"""
        levels = physical_levels_for_dataset(dataset)
        max_level = len(levels)
        stmt = select(SpiralNodeORM).where(
            SpiralNodeORM.dataset == dataset,
            SpiralNodeORM.grown.is_(False),
            SpiralNodeORM.record_count >= self.threshold,
            SpiralNodeORM.level < max_level,
        )
        candidates: list[GrowthCandidate] = []
        for node in self.s.execute(stmt).scalars().all():
            next_level = levels[node.level]
            candidates.append(
                GrowthCandidate(
                    node_id=node.node_id,
                    level=node.level,
                    record_count=node.record_count,
                    next_level=next_level,
                )
            )
        return candidates


class GrowthExecutor:
    def __init__(self, session: Session, threshold: int | None = None):
        self.s = session
        self.threshold = threshold or _default_threshold()

    def grow_node(self, node_id: str, force: bool = False) -> SpiralNodeORM:
        node = self.s.get(SpiralNodeORM, node_id)
        if node is None:
            from engine.core.errors import NotFoundError
            raise NotFoundError("节点不存在", node_id)
        if not force and node.record_count < self.threshold:
            from engine.core.errors import BadRequestError
            raise BadRequestError(
                f"节点记录数 {node.record_count} 未达生长阈值 {self.threshold}",
                node_id,
            )
        dataset = node.dataset
        self._materialize(dataset)
        refreshed = self.s.get(SpiralNodeORM, node_id)
        if refreshed is None:
            from engine.core.errors import NotFoundError
            raise NotFoundError("节点在智慧树重建后不再属于物理生长计划", node_id)
        logger.info("节点触发生长重建 node=%s dataset=%s", node_id, dataset)
        return refreshed

    def grow_all_eligible(self, dataset: str) -> dict:
        judge = GrowthJudge(self.s, self.threshold)
        candidates = judge.scan(dataset)
        result = self._materialize(dataset)
        grown_ids = [
            node_id for (node_id,) in self.s.execute(
                select(SpiralNodeORM.node_id).where(
                    SpiralNodeORM.dataset == dataset,
                    SpiralNodeORM.grown.is_(True),
                )
            ).all()
        ]
        branch_count = len([
            value for (value,) in self.s.execute(
                select(SpiralNodeORM.node_id).where(
                    SpiralNodeORM.dataset == dataset,
                    SpiralNodeORM.topology_role == "branch",
                )
            ).all()
        ])
        logger.info("批量生长 dataset=%s grown=%d branches=%d", dataset, len(grown_ids), branch_count)
        return {
            "dataset": dataset,
            "threshold": self.threshold,
            "candidates_before_rebuild": [candidate.node_id for candidate in candidates],
            "grown": grown_ids,
            "branch_count": branch_count,
            **result,
        }

    def _materialize(self, dataset: str) -> dict:
        from engine.storage.spiral_store import SpiralStore
        from engine.storage.topology import rebuild_dataset_topology

        store = SpiralStore(self.s)
        rebuilt = store.rebuild_aggregations(dataset, scope="all")
        pruned = store.prune_to_threshold(dataset, self.threshold)
        topology = rebuild_dataset_topology(self.s, dataset, force=True)
        return {
            "target_nodes": rebuilt["target_nodes"],
            "pruned_nodes": pruned["deleted_nodes"],
            "topology": topology.as_dict(),
        }

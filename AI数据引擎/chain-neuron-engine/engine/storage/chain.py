# -*- coding: utf-8 -*-
"""链式关联：同层兄弟节点按 node_value 排序的双向链（chain_prev / chain_next）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.storage.models import SpiralNodeORM

logger = get_logger("storage.chain")


class ChainLinker:
    def __init__(self, session: Session):
        self.s = session

    def link_dataset(self, dataset: str) -> int:
        """为每个同父分组的同层兄弟建立双向链。返回处理节点数。"""
        stmt = (
            select(SpiralNodeORM)
            .where(SpiralNodeORM.dataset == dataset)
            .order_by(SpiralNodeORM.level, SpiralNodeORM.parent_id, SpiralNodeORM.node_value)
        )
        nodes = list(self.s.execute(stmt).scalars().all())

        # 按 (level, parent_id) 分组
        groups: dict[tuple, list[SpiralNodeORM]] = {}
        for n in nodes:
            groups.setdefault((n.level, n.parent_id), []).append(n)

        count = 0
        for siblings in groups.values():
            for i, node in enumerate(siblings):
                node.chain_prev = siblings[i - 1].node_id if i > 0 else None
                node.chain_next = (
                    siblings[i + 1].node_id if i < len(siblings) - 1 else None
                )
                count += 1
        self.s.commit()
        logger.info("链式关联完成 dataset=%s nodes=%d groups=%d",
                    dataset, count, len(groups))
        return count

# -*- coding: utf-8 -*-
"""SpiralStore：螺旋树节点存取、明细记录查询、JSON 文件物化。

物理实现：
- 节点结构/聚合：SQLite spiral_nodes（深层节点）；
- JSON 文件树：level<=min(spec.json_level, 层数) 的节点物化为 data/spiral/.../*.json
  （原子替换+checksum）；
- 明细：各注册数据集动态事实表，DecimalString 保精度。
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.core.params import get_param
from engine.features.growth_plan import physical_levels_for_dataset
from engine.features.structure import rebuild_plan
from engine.storage.dataset_registry import registry
from engine.storage.dimensions import (
    METRICS_BY_DATASET,
    make_node_id,
    root_node_id,
)
from engine.storage.fact_tables import get_fact_source
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_json import node_json_path, write_node_json

logger = get_logger("storage.spiral")

# 编码/数值类取值（年份、YYYYMM、产品码、定位单元编码…）→ 精确匹配；
# 其余文本取值（地名/组织名）→ 包含匹配，兼容简称。
_NUMERIC_RE = re.compile(r"^\d+(\.\d+)?$")

# 指标定义与层级：统一委托数据集注册表（新增数据集无需改本文件）
__all__ = ["NodeDelta", "SpiralStore", "METRICS_BY_DATASET"]


@dataclass
class NodeDelta:
    """一批数据对某节点的增量。"""

    count: int = 0
    metrics: dict[str, Decimal] = field(default_factory=dict)


class SpiralStore:
    def __init__(self, session: Session):
        self.s = session

    # ---------------- 根节点 ----------------

    def ensure_roots(self) -> None:
        for ds in registry.spiral_ids():
            self._ensure_root(ds)
        self.s.commit()

    def _ensure_root(self, dataset: str) -> SpiralNodeORM:
        rid = root_node_id(dataset)
        node = self.s.get(SpiralNodeORM, rid)
        if node is None:
            node = SpiralNodeORM(
                node_id=rid,
                dataset=dataset,
                level=0,
                level_name="root",
                node_value="root",
                parent_id=None,
                path=rid,
                dimensions={},
                record_count=0,
                aggregations={},
                topology_version=0,
                topology_role="base",
                feature_slots={
                    "semantic_role": "base",
                    "spatial_layers": ["foundation"],
                },
            )
            self.s.add(node)
            self.s.flush()
        return node

    # ---------------- 节点批量增量 ----------------

    def apply_deltas(self, dataset: str, deltas: dict[str, NodeDelta]) -> None:
        """把批量聚合增量合并进节点表（读旧值 Decimal 相加）。"""
        metric_names = METRICS_BY_DATASET[dataset]

        for node_id, delta in deltas.items():
            node = self.s.get(SpiralNodeORM, node_id)
            if node is None:
                node = self._new_node_from_id(dataset, node_id)
                self.s.add(node)

            node.record_count = (node.record_count or 0) + delta.count
            aggs = dict(node.aggregations or {})
            for name in metric_names:
                inc = delta.metrics.get(name, Decimal("0"))
                old = Decimal(aggs.get(name, "0"))
                aggs[name] = format(old + inc, "f")
            node.aggregations = aggs
        self.s.commit()

    def _new_node_from_id(self, dataset: str, node_id: str) -> SpiralNodeORM:
        _, _, path_part = node_id.partition(":")
        values = path_part.split("/") if path_part and path_part != "root" else []
        level = len(values)
        level_names = physical_levels_for_dataset(dataset)
        level_name = level_names[level - 1] if 0 < level <= len(level_names) else "root"

        dims = {
            level_names[i]: values[i] for i in range(min(len(values), len(level_names)))
        }
        parent_values = values[:-1]
        parent_id = (
            make_node_id(dataset, parent_values)
            if values and parent_values
            else root_node_id(dataset) if values else None
        )

        return SpiralNodeORM(
            node_id=node_id,
            dataset=dataset,
            level=level,
            level_name=level_name,
            node_value=values[-1] if values else "root",
            parent_id=parent_id,
            path=node_id.replace(":", "/"),
            dimensions=dims,
            record_count=0,
            aggregations={},
            topology_version=0,
            topology_role="base" if level == 0 else "node",
            feature_slots={"spatial_layers": ["foundation"]} if level == 0 else {},
        )

    # ---------------- 查询 ----------------

    def get_node(self, node_id: str) -> SpiralNodeORM | None:
        return self.s.get(SpiralNodeORM, node_id)

    def list_children(self, dataset: str, parent_id: str) -> list[SpiralNodeORM]:
        stmt = (
            select(SpiralNodeORM)
            .where(SpiralNodeORM.dataset == dataset, SpiralNodeORM.parent_id == parent_id)
            .order_by(SpiralNodeORM.ring_index, SpiralNodeORM.node_value)
        )
        return list(self.s.execute(stmt).scalars().all())

    def build_tree(self, dataset: str, max_depth: int = 4) -> dict:
        root = self.get_node(root_node_id(dataset))
        if root is None:
            return {}
        return self._tree_node(root, max_depth)

    def _tree_node(self, node: SpiralNodeORM, max_depth: int) -> dict:
        item = {
            "node_id": node.node_id,
            "level": node.level,
            "level_name": node.level_name,
            "value": node.node_value,
            "record_count": node.record_count,
            "aggregations": node.aggregations,
            "grown": node.grown,
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
            "children": [],
        }
        if node.level < max_depth:
            for ch in self.list_children(node.dataset, node.node_id):
                item["children"].append(self._tree_node(ch, max_depth))
        return item

    # ---------------- 明细记录查询 ----------------

    def query_records(
        self,
        dataset: str,
        filters: dict,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        source = get_fact_source(dataset)
        conds = self._build_filters(source, dataset, filters)

        # total
        from sqlalchemy import func as sa_func

        id_col = source.column("id")
        count_stmt = select(sa_func.count(id_col)).where(*conds)
        total = self.s.execute(count_stmt).scalar_one()

        stmt = (
            select(source.table)
            .where(*conds)
            .order_by(id_col)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = self.s.execute(stmt).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [self._record_to_dict(dataset, r) for r in rows],
        }

    def _build_filters(self, source, dataset: str, filters: dict) -> list:
        """过滤条件按注册表声明解析：层级名 → 取值键 → 物理列。

        口径与树路由一致：数值/编码类取值精确匹配；文本类取值（地名、
        组织名等）用包含匹配，兼容自然语言简称（"示例区域丁"→"示例区域丁市"）。
        """
        conds = []
        lvk = registry.level_value_key_of(dataset)
        for key, value in (filters or {}).items():
            if value in (None, "", "*"):
                continue
            # 定位单元名称模糊匹配（按名称而非编码定位）
            if key == "store_name":
                col = source.column("store_name")
                if col is not None:
                    conds.append(col.like(f"%{value}%"))
                continue
            col = source.column(lvk.get(key, key))
            if col is None:
                continue
            if isinstance(value, list):
                conds.append(col.in_([str(v) for v in value]))
            elif _NUMERIC_RE.match(str(value)):
                conds.append(col == str(value))
            else:
                conds.append(col.like(f"%{value}%"))
        return conds

    def _record_to_dict(self, dataset: str, row) -> dict:
        return self._generic_record_to_dict(dataset, row)

    @staticmethod
    def _generic_record_to_dict(dataset: str, row) -> dict:
        """通用数据集：按声明列输出，指标以字符串保精度。"""
        spec = registry.require(dataset)
        metric_cols = {m.column for m in spec.metrics}
        mapping = getattr(row, "_mapping", None)
        get_value = (
            (lambda key: mapping.get(key))
            if mapping is not None
            else (lambda key: getattr(row, key, None))
        )
        out: dict = {"id": get_value("id")}
        for col in spec.fact_columns:
            value = get_value(col)
            if value is not None and col in metric_cols:
                value = format(value, "f")
            out[col] = value
        return out

    # ---------------- 聚合直接读取（中环） ----------------

    def read_aggregation(self, node_id: str) -> dict | None:
        node = self.get_node(node_id)
        if node is None:
            return None
        return {
            "node_id": node.node_id,
            "level": node.level,
            "level_name": node.level_name,
            "value": node.node_value,
            "record_count": node.record_count,
            "aggregations": node.aggregations,
        }

    # ---------------- JSON 文件物化 ----------------

    def materialize_json(self, dataset: str) -> int:
        """把 level<=min(spec.json_level, 层数) 的节点物化为 JSON 文件树；返回文件数。"""
        spec = registry.require(dataset)
        json_level = min(spec.json_level, len(spec.levels))
        count = 0
        root = self.get_node(root_node_id(dataset))
        if root is None:
            return 0
        stack: list[SpiralNodeORM] = [root]
        while stack:
            node = stack.pop()
            payload = self._node_payload(node)
            write_node_json(node_json_path(node), payload)
            count += 1
            if node.level < json_level:
                stack.extend(self.list_children(dataset, node.node_id))
        logger.info("JSON 物化完成 dataset=%s files=%d", dataset, count)
        return count

    @staticmethod
    def _node_payload(node: SpiralNodeORM) -> dict:
        body = {
            "node_id": node.node_id,
            "dataset": node.dataset,
            "level": node.level,
            "level_name": node.level_name,
            "value": node.node_value,
            "parent_id": node.parent_id,
            "dimensions": node.dimensions,
            "middle_ring": {
                "record_count": node.record_count,
                "aggregations": node.aggregations,
            },
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
            "grown": node.grown,
        }
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
        body["checksum"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return body

    # ---------------- 全量重算（事实表 → 节点） ----------------

    def purge_zero_nodes(self, dataset: str) -> int:
        """清理零计数残留节点（规则变更/重算后产生）。

        安全性：count=0 节点不可能拥有 count>0 的后代（到达后代必经过该节点），
        故按 record_count=0 一次删除即可连带清除整棵孤立子树。
        """
        deleted = self.s.execute(
            SpiralNodeORM.__table__.delete().where(
                SpiralNodeORM.dataset == dataset,
                SpiralNodeORM.record_count == 0,
            )
        ).rowcount
        self.s.commit()
        logger.info("零计数节点清理 dataset=%s deleted=%d", dataset, deleted)
        return deleted

    # ---------------- 阈值修剪（生长阈值） ----------------

    def prune_to_threshold(self, dataset: str, threshold: int | None = None) -> dict:
        """自顶向下按系统参数 growth_threshold 修剪螺旋树。

        生长语义：record_count >= 阈值的节点才“生长”（保留子层），
        低于阈值的节点停止生长，其整棵子树删除。聚合值在 apply_deltas
        时已逐层上卷，删除深层节点不影响上层数值。逐层判定“父节点存活
        且已达阈值”，父节点被修剪的孤儿同样在该层被删除，一次遍历收敛。
        """
        if threshold is None:
            threshold = get_param("growth_threshold")
        max_level = len(physical_levels_for_dataset(dataset))
        deleted_total = 0

        for level in range(0, max_level + 1):
            if level > 0:
                grown_parents = [
                    nid for (nid,) in self.s.execute(
                        select(SpiralNodeORM.node_id).where(
                            SpiralNodeORM.dataset == dataset,
                            SpiralNodeORM.level == level - 1,
                            SpiralNodeORM.record_count >= threshold,
                        )
                    ).all()
                ]
                stmt = delete(SpiralNodeORM).where(
                    SpiralNodeORM.dataset == dataset,
                    SpiralNodeORM.level == level,
                )
                if grown_parents:
                    stmt = stmt.where(
                        SpiralNodeORM.parent_id.notin_(grown_parents)
                    )
                deleted_total += self.s.execute(stmt).rowcount
        # grown means that a real child was materialized, not merely that the
        # node crossed the threshold.  This keeps UI branch counts honest.
        nodes = self.s.execute(
            select(SpiralNodeORM).where(SpiralNodeORM.dataset == dataset)
        ).scalars().all()
        parent_ids = {node.parent_id for node in nodes if node.parent_id}
        for node in nodes:
            node.grown = bool(
                node.node_id in parent_ids
                and node.record_count >= threshold
                and node.level < max_level
            )

        self.s.commit()
        logger.info(
            "阈值修剪完成 dataset=%s threshold=%s deleted=%d",
            dataset, threshold, deleted_total,
        )
        return {"threshold": int(threshold), "deleted_nodes": int(deleted_total)}

    def rebuild_aggregations(
        self,
        dataset: str,
        scope: str = "all",
        node_ids: list[str] | None = None,
    ) -> dict:
        """按结构范围重算节点聚合。

        ``all`` 重算整棵螺旋树；结构层范围只更新对应深度以内的节点；
        ``node_ids`` 用于只刷新指定节点，避免无关节点重复计算。
        """
        spec = registry.require(dataset)
        source = get_fact_source(dataset)
        metric_names = METRICS_BY_DATASET[dataset]
        # 指标列名取自注册表声明，新增数据集无需改动本文件
        metric_cols = registry.metric_columns_of(dataset)
        plan = rebuild_plan(spec, scope)
        selected = {str(node_id) for node_id in (node_ids or []) if str(node_id).strip()}
        max_level = plan["max_level"]
        root_id = make_node_id(dataset, [])

        def include_node(node_id: str, level: int) -> bool:
            if selected:
                return node_id in selected
            return level <= max_level

        # 只累加用户选择范围内的节点，避免每次都生成无关深层路径。
        accum: dict[str, NodeDelta] = {}
        level_names = physical_levels_for_dataset(dataset)
        rows = source.all_rows(self.s)

        for r in rows:
            vals_by_level = self._row_level_values(dataset, r)
            metrics = {n: getattr(r, metric_cols[n], None) for n in metric_names}
            # root 节点也参与聚合（values 为空）
            if include_node(root_id, 0):
                root_d = accum.setdefault(root_id, NodeDelta())
                root_d.count += 1
                for n in metric_names:
                    mv = metrics.get(n)
                    if mv is not None:
                        root_d.metrics[n] = root_d.metrics.get(n, Decimal("0")) + mv

            values: list[str] = []
            for level_index, lv in enumerate(level_names, start=1):
                v = vals_by_level.get(lv)
                if v in (None, ""):
                    break
                values.append(str(v))
                nid = make_node_id(dataset, values)
                if not include_node(nid, level_index):
                    continue
                d = accum.setdefault(nid, NodeDelta())
                d.count += 1
                for n in metric_names:
                    mv = metrics.get(n)
                    if mv is not None:
                        d.metrics[n] = d.metrics.get(n, Decimal("0")) + mv

        if not selected and plan["scope"] == "all":
            # A physical growth plan can change after field review.  Full
            # rebuilds therefore replace the topology nodes while preserving
            # the fact table, instead of leaving stale paths behind.
            self.s.execute(
                delete(SpiralNodeORM).where(SpiralNodeORM.dataset == dataset)
            )
            self.s.commit()
            self._ensure_root(dataset)
            self.s.commit()
        else:
            # Partial rebuilds keep unrelated nodes intact.
            target_filter = SpiralNodeORM.node_id.in_(selected) if selected else (
                (SpiralNodeORM.dataset == dataset) & (SpiralNodeORM.level <= max_level)
            )
            all_nodes = self.s.execute(
                select(SpiralNodeORM).where(target_filter)
            ).scalars().all()
            for node in all_nodes:
                node.record_count = 0
                node.aggregations = {}
            self.s.commit()
        self.apply_deltas(dataset, accum)
        # 聚合重算可能改变节点集合；完成后同步物理双链拓扑。
        from engine.storage.topology import rebuild_dataset_topology
        rebuild_dataset_topology(self.s, dataset)
        logger.info(
            "聚合范围重算完成 dataset=%s scope=%s selected=%d nodes=%d",
            dataset, plan["scope"], len(selected), len(accum),
        )
        return {
            "scope": plan["scope"],
            "target_levels": plan["levels"],
            "target_nodes": len(accum),
        }

    @staticmethod
    def _row_level_values(dataset: str, row) -> dict:
        """按注册表的 level_value_key 声明，从事实行提取各层取值。"""
        lvk = registry.level_value_key_of(dataset)
        return {lv: getattr(row, key, None) for lv, key in lvk.items()}

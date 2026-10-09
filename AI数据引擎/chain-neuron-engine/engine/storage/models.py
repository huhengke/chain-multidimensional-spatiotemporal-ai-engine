# -*- coding: utf-8 -*-
"""存储 ORM：维表、事实表、螺旋节点表。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import JSON, DateTime, func

from engine.storage.database import Base, DecimalString


class ProductCrosswalk(Base):
    """dataset 级产品源编码到标准产品/类别代码的交叉映射。"""

    __tablename__ = "product_crosswalk"
    __table_args__ = (
        UniqueConstraint("dataset", "source_code", name="uq_product_crosswalk_dataset_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str | None] = mapped_column(String(16), index=True)
    grade: Mapped[str | None] = mapped_column(String(16), index=True)
    canonical_name: Mapped[str | None] = mapped_column(String(128))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal] = mapped_column(DecimalString)
    status: Mapped[str] = mapped_column(String(24), index=True, default="pending_review")
    effective_from: Mapped[str | None] = mapped_column(String(16))
    effective_to: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[object] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[object] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FeatureProposal(Base):
    """AI/规则生成的特征提案；审批前不改变生产映射。"""

    __tablename__ = "feature_proposal"

    proposal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feature_type: Mapped[str] = mapped_column(String(64), index=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True, default="pending_review")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[Decimal] = mapped_column(DecimalString)
    created_at: Mapped[object] = mapped_column(DateTime, server_default=func.now())
    reviewed_at: Mapped[object | None] = mapped_column(DateTime)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))


# ============================== 螺旋节点表 ==============================

class SpiralNodeORM(Base):
    """螺旋树节点（中通周存）：

    - dimensions: 路径上各维度键值；
    - aggregations: 该节点子树聚合（Decimal 以字符串存储）；
    - checksum: 节点内容校验；
    - chain_prev/next: 同层链式（首尾相连）指针。
    """

    __tablename__ = "spiral_nodes"

    # 复合索引（路由热路径：dataset+parent_id / dataset+level）
    __table_args__ = (
        Index("ix_spiral_nodes_ds_parent", "dataset", "parent_id"),
        Index("ix_spiral_nodes_ds_level", "dataset", "level"),
        Index(
            "ix_spiral_nodes_ds_level_name_value",
            "dataset", "level_name", "node_value", "record_count",
        ),
        Index(
            "ix_spiral_nodes_ds_grown_level_record",
            "dataset", "grown", "level", "record_count",
        ),
        Index("ix_spiral_nodes_ds_role_layer_ring", "dataset", "topology_role", "helix_layer", "ring_index"),
        Index(
            "ix_spiral_nodes_ds_temporal_bucket_ring",
            "dataset", "temporal_layer", "time_bucket", "ring_index",
        ),
        Index(
            "ix_spiral_nodes_ds_role_temporal_ring",
            "dataset", "topology_role", "temporal_layer", "time_bucket", "ring_index",
        ),
    )

    node_id: Mapped[str] = mapped_column(String(512), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(16), index=True)
    level: Mapped[int] = mapped_column(Integer, index=True)
    level_name: Mapped[str] = mapped_column(String(32))
    node_value: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[str | None] = mapped_column(String(512), index=True)
    path: Mapped[str] = mapped_column(String(1024), index=True)

    dimensions: Mapped[dict] = mapped_column(JSON, default=dict)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    aggregations: Mapped[dict] = mapped_column(JSON, default=dict)

    grown: Mapped[bool] = mapped_column(Boolean, default=False)
    checksum: Mapped[str | None] = mapped_column(String(64))
    chain_prev: Mapped[str | None] = mapped_column(String(512))
    chain_next: Mapped[str | None] = mapped_column(String(512))

    # 物理双链拓扑：保留语义树字段，通过双链指针和同环配对表达空间布局。
    topology_version: Mapped[int] = mapped_column(Integer, default=0, index=True)
    topology_role: Mapped[str] = mapped_column(String(16), default="node", index=True)
    helix_layer: Mapped[int | None] = mapped_column(Integer, index=True)
    ring_index: Mapped[int | None] = mapped_column(Integer)
    strand_a_prev: Mapped[str | None] = mapped_column(String(512))
    strand_a_next: Mapped[str | None] = mapped_column(String(512))
    strand_b_prev: Mapped[str | None] = mapped_column(String(512))
    strand_b_next: Mapped[str | None] = mapped_column(String(512))
    pair_node_id: Mapped[str | None] = mapped_column(String(512), index=True)
    feature_slots: Mapped[dict] = mapped_column(JSON, default=dict)

    # 物理时间圈与动态生长元数据（拓扑 v2）。helix_layer 仍保留，作为
    # temporal_layer 的兼容别名，避免旧 API/快照立即失效。
    temporal_layer: Mapped[int | None] = mapped_column(Integer, index=True)
    time_bucket: Mapped[str | None] = mapped_column(String(64), index=True)
    time_granularity: Mapped[str | None] = mapped_column(String(16))
    branch_depth: Mapped[int | None] = mapped_column(Integer, index=True)
    growth_parent_id: Mapped[str | None] = mapped_column(String(512))
    growth_dimension: Mapped[str | None] = mapped_column(String(64))
    capacity_snapshot: Mapped[int | None] = mapped_column(Integer)
    growth_version: Mapped[int | None] = mapped_column(Integer)

    updated_at: Mapped[object] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

# -*- coding: utf-8 -*-
"""TreeRouter：中通路由遍历 —— 剪枝 / 下钻 / 聚合直读，生成可视化路径。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.features.growth_plan import physical_levels_for_dataset
from engine.storage.dimensions import LEVEL_LABELS, root_node_id
from engine.storage.dataset_registry import registry
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_store import SpiralStore

logger = get_logger("storage.router")

# 步骤状态
ST_ACTIVE = "active"
ST_PRUNED = "pruned"
ST_HIT = "hit"
ST_ROLLUP = "rollup"

PRUNED_SHOW_LIMIT = 5  # 同父每层最多展示的剪枝节点数
DETAIL_ACTIVE_LIMIT = 10  # 明细模式：活动节点过多时折叠，防止全树展开


@dataclass
class RouteStep:
    level: int
    level_name: str
    level_label: str
    value: str
    status: str
    parent_value: str
    message: str = ""
    # rollup 时附带的聚合
    aggregations: dict | None = None
    record_count: int = 0
    phase: str = "semantic"
    temporal_layer: int | None = None
    time_bucket: str | None = None
    branch_depth: int | None = None


@dataclass
class RouteResult:
    dataset: str
    mode: str  # detail / aggregate
    steps: list[RouteStep] = field(default_factory=list)
    target_node_ids: list[str] = field(default_factory=list)
    pruned_node_count: int = 0
    pruned_record_count: int = 0  # 被剪枝子树的事实行数（业务价值）
    collapsed_messages: list[str] = field(default_factory=list)
    query_path: list[str] = field(default_factory=lambda: [
        "foundation", "middle_inner", "middle_store", "outer_feature", "fact_result",
    ])
    inner_node_ids: list[str] = field(default_factory=list)
    middle_store_node_ids: list[str] = field(default_factory=list)
    outer_feature_keys: list[str] = field(default_factory=list)
    physical_time_buckets: list[str] = field(default_factory=list)
    dna_is_topology_not_query_level: bool = True

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "mode": self.mode,
            "steps": [asdict(s) for s in self.steps],
            "target_node_ids": self.target_node_ids,
            "pruned_node_count": self.pruned_node_count,
            "pruned_record_count": self.pruned_record_count,
            "collapsed_messages": self.collapsed_messages,
            "query_path": self.query_path,
            "inner_node_ids": self.inner_node_ids,
            "middle_store_node_ids": self.middle_store_node_ids,
            "outer_feature_keys": self.outer_feature_keys,
            "physical_time_buckets": self.physical_time_buckets,
            "dna_is_topology_not_query_level": self.dna_is_topology_not_query_level,
        }


class TreeRouter:
    def __init__(self, store: SpiralStore):
        self.store = store
        self.s: Session = store.s

    def route(
        self,
        dataset: str,
        conditions: dict,
        is_aggregate: bool,
        group_by: str | None = None,
    ) -> RouteResult:
        levels = list(physical_levels_for_dataset(dataset))
        result = RouteResult(dataset=dataset, mode="aggregate" if is_aggregate else "detail")
        time_levels = {
            "time", "date", "day", "week", "weekofyear", "month", "yyyymm",
            "year", "season", "quarter", "period", "datetime", "timestamp",
        }

        root = self.store.get_node(root_node_id(dataset))
        if root is None:
            return result

        # 汇总目标层：group_by 优先；否则为条件中最深的指定层；
        # 无任何条件时目标为 root（直读全量，禁止让调用方自行加总）
        target_level = 0
        if is_aggregate:
            if group_by and group_by in levels:
                target_level = levels.index(group_by) + 1
            else:
                specified = [i + 1 for i, lv in enumerate(levels) if lv in conditions]
                target_level = max(specified) if specified else 0
                if target_level == 0:
                    result.steps.append(
                        self._step(root, ST_ROLLUP, self._rollup_message(root), phase="middle_store")
                    )
                    result.target_node_ids = [root.node_id]
                    result.middle_store_node_ids = [root.node_id]
                    self._record_physical(result, root, "middle_store")
                    logger.info("聚合路由直读 root dataset=%s", dataset)
                    return result

        active: list[SpiralNodeORM] = [root]

        for depth, level_name in enumerate(levels, start=1):
            wanted = conditions.get(level_name)
            next_active: list[SpiralNodeORM] = []

            for parent in active:
                children = self.store.list_children(dataset, parent.node_id)

                if not children:
                    # 到达未展开层（叶）：明细命中
                    if not is_aggregate:
                        result.steps.append(self._step(
                            parent, ST_HIT,
                            f"命中节点：{parent.node_value}（记录数 {parent.record_count}）",
                            phase=self._phase_for(parent, time_levels),
                        ))
                        self._record_physical(result, parent, self._phase_for(parent, time_levels))
                    continue

                shown_pruned = 0
                if wanted in (None, ""):
                    matched_ids = {ch.node_id for ch in children}
                    fuzzy = False
                else:
                    wanted_set = (
                        {str(w) for w in wanted}
                        if isinstance(wanted, (list, tuple))
                        else {str(wanted)}
                    )
                    hits = [
                        ch for ch in children if str(ch.node_value) in wanted_set
                    ]
                    fuzzy = False
                    if not hits:
                        # 自然语言常给简称（"示例区域丁"→"示例区域丁市"）：精确无命中时
                        # 回退为"节点值包含所填词"，多命中全部进入（上层同值合并）
                        hits = [
                            ch for ch in children
                            if any(w in str(ch.node_value) for w in wanted_set)
                        ]
                        fuzzy = bool(hits)
                    matched_ids = {ch.node_id for ch in hits}
                for ch in children:
                    if ch.node_id in matched_ids:
                        next_active.append(ch)
                        phase = self._phase_for(ch, time_levels)
                        result.steps.append(self._step(
                            ch, ST_ACTIVE,
                            "特征匹配，进入" + ("（包含匹配回退）" if fuzzy else ""),
                            parent_value=parent.node_value,
                            phase=phase,
                        ))
                        self._record_physical(result, ch, phase)
                    else:
                        result.pruned_node_count += 1
                        result.pruned_record_count += ch.record_count or 0
                        shown_pruned += 1
                        if shown_pruned <= PRUNED_SHOW_LIMIT:
                            phase = self._phase_for(ch, time_levels)
                            result.steps.append(self._step(
                                ch, ST_PRUNED,
                                f"特征不匹配，剪枝（子分叉 {ch.record_count} 条记录）",
                                parent_value=parent.node_value,
                                phase=phase,
                            ))
                if shown_pruned > PRUNED_SHOW_LIMIT:
                    result.collapsed_messages.append(
                        f"{LEVEL_LABELS.get(level_name, level_name)}层另有 "
                        f"{shown_pruned - PRUNED_SHOW_LIMIT} 个剪枝分叉未展开显示"
                    )

            active = next_active

            # 明细模式：候选范围过大时折叠路径（数据本身仍由 records 查询返回）
            if not is_aggregate and len(active) > DETAIL_ACTIVE_LIMIT:
                result.collapsed_messages.append(
                    f"{LEVEL_LABELS.get(level_name, level_name)}层以下候选范围较大"
                    f"（{len(active)} 个分叉），路径折叠，直接返回明细记录"
                )
                result.target_node_ids = [n.node_id for n in active[:DETAIL_ACTIVE_LIMIT]]
                logger.info("明细路由折叠 depth=%s active=%d", level_name, len(active))
                break

            if is_aggregate and depth == target_level:
                # 直读中环聚合，停止下钻
                for n in active:
                    result.steps.append(
                        self._step(n, ST_ROLLUP, self._rollup_message(n), phase="middle_store")
                    )
                    result.target_node_ids.append(n.node_id)
                    result.middle_store_node_ids.append(n.node_id)
                    self._record_physical(result, n, "middle_store")
                logger.info(
                    "聚合路由到达目标层 level=%s nodes=%d pruned_records=%d",
                    level_name, len(active), result.pruned_record_count,
                )
                break

        # 明细：叶节点 hit（若遍历到最后一层）
        if not is_aggregate:
            # 折叠分支已写入 target_node_ids；同一节点重复追加会让调用方
            # 把同一子树的聚合算两次。
            already_target = set(result.target_node_ids)
            for n in active:
                result.steps.append(
                    self._step(
                        n, ST_HIT,
                        f"命中：{n.node_value}（记录数 {n.record_count}）",
                        phase=self._phase_for(n, time_levels),
                    )
                )
                self._record_physical(result, n, self._phase_for(n, time_levels))
                if n.node_id not in already_target:
                    result.target_node_ids.append(n.node_id)
                    already_target.add(n.node_id)

        return result

    @staticmethod
    def _step(node: SpiralNodeORM, status: str, message: str,
              parent_value: str = "", phase: str = "semantic") -> RouteStep:
        return RouteStep(
            level=node.level,
            level_name=node.level_name,
            level_label=LEVEL_LABELS.get(node.level_name, node.level_name),
            value=node.node_value,
            status=status,
            parent_value=parent_value,
            message=message,
            aggregations=node.aggregations if status == ST_ROLLUP else None,
            record_count=node.record_count or 0,
            phase=phase,
            temporal_layer=node.temporal_layer,
            time_bucket=node.time_bucket,
            branch_depth=node.branch_depth,
        )

    @staticmethod
    def _phase_for(node: SpiralNodeORM, time_levels: set[str]) -> str:
        if node.topology_role == "branch" or (node.branch_depth or 0) > 0:
            return "outer_feature"
        if node.level_name in time_levels or node.topology_role in {"trunk", "base"}:
            return "middle_inner"
        return "middle_store"

    @staticmethod
    def _record_physical(result: RouteResult, node: SpiralNodeORM, phase: str) -> None:
        if phase == "middle_inner" and node.node_id not in result.inner_node_ids:
            result.inner_node_ids.append(node.node_id)
        if phase == "outer_feature" and node.level_name not in result.outer_feature_keys:
            result.outer_feature_keys.append(node.level_name)
        if node.time_bucket and node.time_bucket not in result.physical_time_buckets:
            result.physical_time_buckets.append(node.time_bucket)

    @staticmethod
    def _rollup_message(node: SpiralNodeORM) -> str:
        aggs = node.aggregations or {}
        spec = registry.require(node.dataset)
        parts: list[str] = []
        for metric in spec.metrics:
            if metric.name not in aggs:
                continue
            unit = " 元" if "amount" in metric.name.lower() else (
                " 吨" if "ton" in metric.name.lower() else ""
            )
            parts.append(f"{metric.label or metric.name} {aggs[metric.name]}{unit}")
        if parts:
            return f"直接读取中环聚合值：{'；'.join(parts)}（不下钻子分叉）"
        return "直接读取中环聚合值（Decimal 精度）"

# -*- coding: utf-8 -*-
"""一致性校验：独立路径从事实表重算，逐节点比对节点表。

- count 严格相等；
- 指标 Decimal 严格相等（禁止 float 比较）；
- root 行数 == 事实表行数；
- 不修改任何数据。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.storage.dataset_registry import registry
from engine.storage.dimensions import LEVELS_BY_DATASET, make_node_id
from engine.storage.fact_tables import get_fact_source
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_store import METRICS_BY_DATASET, NodeDelta, SpiralStore

logger = get_logger("storage.consistency")

ISSUE_LIMIT = 50


class ConsistencyChecker:
    def __init__(self, session: Session):
        self.s = session

    def check(self, dataset: str) -> dict:
        store = SpiralStore(self.s)

        # 1) 独立重算
        expected = self._recompute(dataset)

        # 2) 节点表快照
        nodes = list(
            self.s.execute(
                select(SpiralNodeORM).where(SpiralNodeORM.dataset == dataset)
            ).scalars().all()
        )
        actual_ids = {n.node_id for n in nodes}
        expected_ids = set(expected.keys())

        issues: list[dict] = []

        # 多余节点 / 缺失节点
        for nid in sorted(actual_ids - expected_ids):
            issues.append({"type": "unexpected_node", "node_id": nid})
        for nid in sorted(expected_ids - actual_ids):
            issues.append({"type": "missing_node", "node_id": nid})

        # count 与指标比对
        for node in nodes:
            exp = expected.get(node.node_id)
            if exp is None:
                continue
            if node.record_count != exp.count:
                issues.append({
                    "type": "count_mismatch",
                    "node_id": node.node_id,
                    "expected": exp.count,
                    "actual": node.record_count,
                })
            for mname, val in exp.metrics.items():
                actual_val = node.aggregations.get(mname)
                # 统一为 Decimal 比较
                actual_dec = Decimal(str(actual_val)) if actual_val is not None else Decimal("0")
                if actual_dec != val:
                    issues.append({
                        "type": "metric_mismatch",
                        "node_id": node.node_id,
                        "metric": mname,
                        "expected": str(val),
                        "actual": str(actual_dec),
                    })
            if len(issues) >= ISSUE_LIMIT:
                break

        result = {
            "dataset": dataset,
            "ok": len(issues) == 0,
            "fact_row_count": expected.get(make_node_id(dataset, []), NodeDelta()).count,
            "node_count": len(nodes),
            "expected_node_count": len(expected),
            "issue_count": len(issues),
            "issues": issues,
            "checked_at": datetime.now().isoformat(timespec="seconds"),
        }
        logger.info("一致性校验 dataset=%s ok=%s issues=%d",
                    dataset, result["ok"], len(issues))
        return result

    # ---------------- 内部：独立重算 ----------------

    def _recompute(self, dataset: str) -> dict[str, NodeDelta]:
        """完全不依赖节点表，从事实表独立聚合（与 SpiralStore.rebuild 同算法）。"""
        store = SpiralStore(self.s)
        metric_names = METRICS_BY_DATASET[dataset]
        level_names = LEVELS_BY_DATASET[dataset]
        # 事实表访问与指标列名均取自注册表声明
        rows = get_fact_source(dataset).all_rows(self.s)

        accum: dict[str, NodeDelta] = {}
        metric_cols = registry.metric_columns_of(dataset)

        for r in rows:
            vals_by_level = store._row_level_values(dataset, r)
            root_d = accum.setdefault(make_node_id(dataset, []), NodeDelta())
            root_d.count += 1
            # root 指标每行只累加一次（禁止随层级重复累加）
            for mname in metric_names:
                mv = getattr(r, metric_cols[mname], None)
                if mv is not None:
                    root_d.metrics[mname] = root_d.metrics.get(
                        mname, Decimal("0")) + mv

            values: list[str] = []
            for lv in level_names:
                v = vals_by_level.get(lv)
                if v in (None, ""):
                    break
                values.append(str(v))
                nid = make_node_id(dataset, values)
                d = accum.setdefault(nid, NodeDelta())
                d.count += 1
                for mname in metric_names:
                    mv = getattr(r, metric_cols[mname], None)
                    if mv is not None:
                        d.metrics[mname] = d.metrics.get(
                            mname, Decimal("0")) + mv

        return accum

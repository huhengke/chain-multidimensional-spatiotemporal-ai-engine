# -*- coding: utf-8 -*-
"""在 engine.db 的副本上验证 prune_to_threshold：数值不变、树按阈值收敛。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collections import Counter

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker

from engine.storage.dimensions import LEVELS_BY_DATASET
from engine.storage.models import SpiralNodeORM
from engine.storage.spiral_store import SpiralStore

DB = "data/prune_test.db"
eng = create_engine(f"sqlite:///{DB}", connect_args={"check_same_thread": False, "timeout": 60})
S = sessionmaker(bind=eng, expire_on_commit=False)

s = S()
store = SpiralStore(s)


def snapshot(label: str) -> dict:
    out = {}
    for ds in ("oil_monthly_sales", "oil_sales_plan", "non_oil_sales"):
        rows = s.execute(
            select(SpiralNodeORM.level, func.count())
            .where(SpiralNodeORM.dataset == ds)
            .group_by(SpiralNodeORM.level)
        ).all()
        root = s.execute(
            select(SpiralNodeORM.record_count, SpiralNodeORM.aggregations)
            .where(SpiralNodeORM.dataset == ds, SpiralNodeORM.level == 0)
        ).all()
        out[ds] = {
            "total_nodes": sum(c for _, c in rows),
            "by_level": {lv: c for lv, c in rows},
            "root": [(rc, json.dumps(ag, sort_keys=True)) for rc, ag in root],
        }
    print(f"--- {label} ---")
    for ds, d in out.items():
        print(ds, "nodes=", d["total_nodes"], "root=", d["root"])
        print("  by_level=", dict(sorted(d["by_level"].items())))
    return out


before = snapshot("BEFORE")

t0 = time.time()
for ds in ("oil_monthly_sales", "oil_sales_plan", "non_oil_sales"):
    if ds not in LEVELS_BY_DATASET:
        continue
    r = store.prune_to_threshold(ds)
    print(ds, "prune ->", r, f"({time.time()-t0:.1f}s)")

after = snapshot("AFTER")

ok = True
for ds in before:
    if before[ds]["root"] != after[ds]["root"]:
        ok = False
        print(f"!! root changed for {ds}")
print("root aggregates identical:", ok)

# 一致性检查：剩余节点除 root 外父节点必须存在且 record_count>=阈值
from sqlalchemy.orm import aliased
for ds in ("oil_monthly_sales", "oil_sales_plan", "non_oil_sales"):
    child = aliased(SpiralNodeORM)
    parent = aliased(SpiralNodeORM)
    missing = s.execute(
        select(func.count(child.node_id))
        .outerjoin(
            parent,
            (parent.dataset == ds) & (parent.node_id == child.parent_id),
        )
        .where(child.dataset == ds, child.level > 0, parent.node_id == None)  # noqa
    ).scalar()
    below = s.execute(
        select(func.count(child.node_id))
        .join(parent, (parent.dataset == ds) & (parent.node_id == child.parent_id))
        .where(child.dataset == ds, child.level > 0, parent.record_count < 10000)
    ).scalar()
    print(ds, "orphan_children=", missing, "children_of_low_parents=", below)

s.close()

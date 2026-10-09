# -*- coding: utf-8 -*-
"""维度层级定义与节点路径工具。

层级/指标等元数据已迁至**数据集注册表**（`engine/config/datasets.yaml`
+ `data/datasets_custom.yaml`），本模块保留的常量均为**委托注册表的只读视图**，
调用方仍按字典方式使用，新增数据集无需改动调用点。
"""
from __future__ import annotations

from engine.storage.dataset_registry import (
    LevelLabels,
    LevelsByDataset,
    MetricsByDataset,
    registry,
)

# 层级顺序视图：{dataset: [level, ...]}
LEVELS_BY_DATASET = LevelsByDataset()

# 指标名视图：{dataset: (metric_name, ...)}
METRICS_BY_DATASET = MetricsByDataset()

# 层级中文显示名视图：{level: 中文名}
LEVEL_LABELS = LevelLabels()

# 标准内置维度（1+1 路由的第一个 1）
BUILTIN_DIMENSIONS = ("time", "location", "org", "product")


def levels_of(dataset: str) -> list[str]:
    return list(registry.levels_of(dataset))


def make_node_id(dataset: str, values: list[str] | tuple[str, ...]) -> str:
    """构造节点 ID：dataset:v1/v2/..."""
    if not values:
        return f"{dataset}:root"
    return f"{dataset}:" + "/".join(str(v) for v in values)


def make_path_text(dataset: str, values: list[str] | tuple[str, ...]) -> str:
    return "/".join([dataset, *[str(v) for v in values]])


def root_node_id(dataset: str) -> str:
    return f"{dataset}:root"
# -*- coding: utf-8 -*-
"""统一查询使用的项目经验规则。

查询经验与字段推荐共用同一个版本化 YAML 资产，但不把业务数据写入
经验库。规则只负责解释查询范围、时间比较和通用语义过滤，实际结果仍
必须由注册数据集和统一查询引擎计算。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from engine.core.config import ENGINE_CONFIG_DIR


EXPERIENCE_PATH = ENGINE_CONFIG_DIR / "ai_field_recommendations.yaml"


@lru_cache(maxsize=4)
def _load(path_str: str, mtime_ns: int) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def query_experience() -> dict[str, Any]:
    """读取 YAML 中的基座经验，文件缺失时返回安全的空配置。"""
    try:
        return _load(str(EXPERIENCE_PATH), EXPERIENCE_PATH.stat().st_mtime_ns)
    except OSError:
        return {}


def base_semantics() -> dict[str, Any]:
    """读取基座专用语义规则。

    新版本使用 base_semantics；旧版本曾使用 query_semantics，
    这里保留只读回退以便升级期间不让 Agent 查询中断。
    """
    raw = query_experience()
    current = raw.get("base_semantics")
    if isinstance(current, dict):
        return current
    legacy = raw.get("query_semantics")
    return legacy if isinstance(legacy, dict) else {}


def comparison_definitions() -> dict[str, dict[str, Any]]:
    raw = base_semantics()
    definitions = raw.get("time_comparisons") or {}
    return definitions if isinstance(definitions, dict) else {}


def actual_sales_scope() -> dict[str, Any]:
    raw = base_semantics()
    scope = raw.get("dataset_scope", {}).get("actual_sales")
    return scope if isinstance(scope, dict) else {}


def location_semantics() -> dict[str, Any]:
    raw = base_semantics()
    location = raw.get("semantic_filters", {}).get("location")
    return location if isinstance(location, dict) else {}


def business_aggregate_definitions() -> dict[str, dict[str, Any]]:
    """返回经过版本化经验库确认的业务聚合口径。"""
    raw = base_semantics()
    definitions = raw.get("business_aggregates") or {}
    return definitions if isinstance(definitions, dict) else {}


def business_aggregates_for_dataset(dataset: str) -> dict[str, dict[str, Any]]:
    """只返回适用于目标数据集的业务聚合，供契约和查询计划解释。"""
    result: dict[str, dict[str, Any]] = {}
    for key, value in business_aggregate_definitions().items():
        if not isinstance(value, dict):
            continue
        datasets = value.get("datasets") or []
        if not datasets or dataset in {str(item) for item in datasets}:
            result[str(key)] = value
    return result

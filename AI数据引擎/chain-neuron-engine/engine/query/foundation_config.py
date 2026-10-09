# -*- coding: utf-8 -*-
"""可审计的基座特征配置：经验库 + AI建议 + 人工确认的运行态资产。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from engine.core.config import DATA_DIR, ENGINE_CONFIG_DIR
from engine.query.experience import base_semantics, query_experience

CONFIG_PATH = ENGINE_CONFIG_DIR / "foundation_features.yaml"
SNAPSHOT_DIR = DATA_DIR / "foundation_feature_snapshots"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _field(item: dict[str, Any], *, source: str = "experience_yaml") -> dict[str, Any]:
    semantic_type = str(item.get("semantic_type") or "other").strip()
    layer = str(item.get("layer") or ("trunk" if semantic_type == "time" else "base")).strip()
    return {
        "source": str(item.get("source") or "").strip(),
        "name": str(item.get("name") or item.get("source") or "").strip(),
        "label": str(item.get("label") or item.get("name") or item.get("source") or "").strip(),
        "semantic_type": semantic_type,
        "layer": layer,
        "enabled": bool(item.get("enabled", True)),
        "recommendation_source": str(item.get("recommendation_source") or source),
        "reason": str(item.get("reason") or item.get("rationale") or "基座用于语义过滤、翻译、归一化和纠偏"),
    }


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    config = dict(raw or {})
    config["version"] = int(config.get("version") or 1)
    config["catalog"] = "foundation_features"
    config["updated_at"] = str(config.get("updated_at") or _now())
    global_semantics = config.get("global_semantics")
    if not isinstance(global_semantics, dict):
        global_semantics = {}
    global_semantics.setdefault("principle", base_semantics().get("principle") or "基座只负责语义过滤、翻译、归一化、纠偏和派生条件，不承载原始时间序列")
    global_semantics.setdefault("experience_file", "engine/config/ai_field_recommendations.yaml")
    global_semantics.setdefault("time_policy", "原始时间序列默认属于中通主干；节日、季节、同比、环比等语义由基座翻译为可执行条件")
    global_semantics.setdefault("common_time_semantics", {
        "本周": "当前周周一至今天",
        "上周": "上一完整自然周（周一至周日）",
        "本月": "当月第一天至今天",
        "上月": "上一完整自然月",
        "同比": "上一年度相同日期/期间",
        "环比": "紧邻上一完整同粒度期间",
    })
    config["global_semantics"] = global_semantics
    catalog = config.get("experience_recommendations")
    if not isinstance(catalog, list):
        catalog = query_experience().get("entries") or []
    config["experience_recommendations"] = [dict(item) for item in catalog if isinstance(item, dict)]
    bindings = config.get("dataset_bindings")
    if not isinstance(bindings, dict):
        bindings = {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for dataset, fields in bindings.items():
        if not isinstance(fields, list):
            continue
        seen: set[str] = set()
        values: list[dict[str, Any]] = []
        for item in fields:
            if not isinstance(item, dict):
                continue
            value = _field(item, source=str(item.get("recommendation_source") or "manual"))
            if not value["source"] or value["source"] in seen:
                continue
            seen.add(value["source"])
            values.append(value)
        cleaned[str(dataset)] = values
    config["dataset_bindings"] = cleaned
    temporal = config.get("temporal_bindings")
    if not isinstance(temporal, dict):
        temporal = {}
    cleaned_temporal: dict[str, list[dict[str, Any]]] = {}
    for dataset, fields in temporal.items():
        if not isinstance(fields, list):
            continue
        values: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in fields:
            if not isinstance(item, dict):
                continue
            value = _field({**item, "semantic_type": "time", "layer": "trunk"}, source=str(item.get("recommendation_source") or "manual"))
            if value["source"] and value["source"] not in seen:
                values.append(value)
                seen.add(value["source"])
        cleaned_temporal[str(dataset)] = values
    config["temporal_bindings"] = cleaned_temporal
    # 旧版本配置没有 temporal_bindings 时，按注册表声明补齐时间入口；
    # 这里只登记主干时间字段，不把日期字段写入 dataset_bindings 基座。
    try:
        from engine.storage.dataset_registry import registry

        for spec in registry.all_specs().values():
            if not spec.time_source or spec.id in config["temporal_bindings"]:
                continue
            config["temporal_bindings"][spec.id] = [_field({
                "source": spec.time_source,
                "name": spec.time_source,
                "label": "时间",
                "semantic_type": "time",
                "layer": "trunk",
                "reason": f"数据集声明的时间入口（{spec.time_format}）；用于本周、上周、同比和环比的时间范围编译",
            }, source="dataset_declaration")]
    except Exception:
        # 配置查看不能因为注册表暂时不可用而失败。
        pass
    suggestions = config.get("pending_suggestions")
    config["pending_suggestions"] = [dict(item) for item in suggestions if isinstance(item, dict)] if isinstance(suggestions, list) else []
    return config


def default_config() -> dict[str, Any]:
    from engine.storage.dataset_registry import registry

    bindings: dict[str, list[dict[str, Any]]] = {}
    temporal_bindings: dict[str, list[dict[str, Any]]] = {}
    for spec in registry.all_specs().values():
        if spec.time_source:
            temporal_bindings[spec.id] = [_field({
                "source": spec.time_source,
                "name": spec.time_source,
                "label": "时间",
                "semantic_type": "time",
                "layer": "trunk",
                "reason": f"数据集声明的时间入口（{spec.time_format}）；用于本周、上周、同比和环比的时间范围编译",
            }, source="dataset_declaration")]
        if bool((spec.base or {}).get("enabled")):
            fields = []
            for item in (spec.base or {}).get("features") or []:
                if isinstance(item, dict) and item.get("source"):
                    fields.append(_field(item, source="experience_yaml"))
            bindings[spec.id] = fields
    return _normalise({
        "version": 1,
        "catalog": "foundation_features",
        "updated_at": _now(),
        "global_semantics": {
            "principle": base_semantics().get("principle") or "基座只负责语义过滤、翻译、归一化、纠偏和派生条件，不承载原始时间序列",
            "experience_file": "engine/config/ai_field_recommendations.yaml",
            "time_policy": "原始时间序列默认属于中通主干；季节、节日、同比、环比等由基座翻译为可执行条件",
            "common_time_semantics": {
                "本周": "当前周周一至今天",
                "上周": "上一完整自然周（周一至周日）",
                "本月": "当月第一天至今天",
                "上月": "上一完整自然月",
                "同比": "上一年度相同日期/期间",
                "环比": "紧邻上一完整同粒度期间",
            },
        },
        "dataset_bindings": bindings,
        "temporal_bindings": temporal_bindings,
        "experience_recommendations": query_experience().get("entries") or [],
        "pending_suggestions": [],
    })


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        config = default_config()
        save_config(config, bump=False)
        return config
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    return _normalise(raw if isinstance(raw, dict) else {})


def save_config(config: dict[str, Any], *, bump: bool = True) -> dict[str, Any]:
    normalized = _normalise(config)
    if bump:
        normalized["version"] = max(int(normalized.get("version") or 1), 1) + 1
    normalized["updated_at"] = _now()
    ENGINE_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        snapshot = SNAPSHOT_DIR / f"foundation_features_v{int(normalized['version']) - (1 if bump else 0)}.yaml"
        snapshot.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    temp = CONFIG_PATH.with_suffix(".yaml.tmp")
    temp.write_text(yaml.safe_dump(normalized, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temp.replace(CONFIG_PATH)
    return normalized


def effective_base_features(dataset: str) -> list[dict[str, Any]]:
    config = load_config()
    return [item for item in (config.get("dataset_bindings", {}).get(dataset) or []) if item.get("enabled") and item.get("layer") == "base"]


def add_suggestions(suggestions: list[dict[str, Any]]) -> dict[str, Any]:
    config = load_config()
    existing = {str(item.get("id")) for item in config.get("pending_suggestions") or []}
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        item = dict(suggestion)
        item.setdefault("id", f"foundation-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
        item.setdefault("status", "pending_review")
        if str(item["id"]) in existing:
            continue
        config["pending_suggestions"].append(item)
        existing.add(str(item["id"]))
    return save_config(config)


def decide_suggestion(suggestion_id: str, approve: bool) -> dict[str, Any]:
    config = load_config()
    suggestions = config.get("pending_suggestions") or []
    target = next((item for item in suggestions if str(item.get("id")) == suggestion_id), None)
    if target is None:
        raise KeyError(suggestion_id)
    target["status"] = "approved" if approve else "rejected"
    if approve:
        dataset = str(target.get("dataset") or "").strip()
        fields = target.get("fields") or []
        current = config.setdefault("dataset_bindings", {}).setdefault(dataset, [])
        temporal_current = config.setdefault("temporal_bindings", {}).setdefault(dataset, [])
        known = {str(item.get("source")) for item in current}
        temporal_known = {str(item.get("source")) for item in temporal_current}
        for item in fields:
            if not isinstance(item, dict) or not str(item.get("source") or "").strip():
                continue
            is_time = str(item.get("semantic_type") or "").strip() == "time" or str(item.get("layer") or "") == "trunk"
            value = _field({**item, "layer": "trunk" if is_time else "base"}, source=str(target.get("source") or "ai"))
            target_list = temporal_current if is_time else current
            target_known = temporal_known if is_time else known
            if value["source"] not in target_known:
                target_list.append(value)
                target_known.add(value["source"])
    return save_config(config)

# -*- coding: utf-8 -*-
"""为产品三个数据集补齐 feature_layers 语义声明（不改层级/指标/物理列，不动数据）。

声明字段一律来自随项目交付的经验库 `engine/config/ai_field_recommendations.yaml`，
脚本不写死任何"某列属于哪个语义"的判断；经验库没命中的列按维度保守落 trunk。

用法：
  python scripts/declare_oil_features.py --dry-run    # 只打印将要提交的声明
  python scripts/declare_oil_features.py              # 经 PUT /datasets/{ds} 提交（引擎需已启动）
"""
from __future__ import annotations

import copy
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.ingestion.field_recommendations import (  # noqa: E402
    default_feature_layer,
    default_query_uses,
    match_recommendations,
)

BASE = "http://127.0.0.1:8000"


def _feature(source: str, name: str, label: str, *, role: str, layer_hint: str | None = None):
    matches = match_recommendations(source, label)
    best = matches[0] if matches else None
    resolved_role = (best.role if best and best.role != "ignore" else role) or role
    semantic = best.semantic_type if best else "other"
    base_candidate = bool(best.base_candidate) if best else False
    layer = layer_hint or (
        best.feature_layer if best and best.feature_layer in {"base", "trunk", "branch"}
        else default_feature_layer(resolved_role, semantic, base_candidate)
    )
    if resolved_role == "metric":
        layer = "branch"
    uses = list(best.query_uses) if best and best.query_uses else default_query_uses(
        resolved_role, semantic, layer
    )
    return layer, {
        "source": source,
        "name": name,
        "label": label or source,
        "role": resolved_role,
        "semantic_type": semantic,
        "query_uses": uses,
        "recommended": bool(best),
    }


def build_layers(draft: dict) -> dict:
    # source 一律用 Excel 表头（注册表校验按表头核对），name 用物理列名，
    # 查询侧 `_feature_physical_name` 取 name，两边都认。
    layers: dict[str, list[dict]] = {"base": [], "trunk": [], "branch": []}
    time_source = str(draft["time"]["source"])
    layer, item = _feature(time_source, time_source, "时间", role="time", layer_hint="trunk")
    layers[layer].append(item)
    for level in draft.get("levels") or []:
        if level.get("derive"):
            continue  # 派生时间层由时间入口负责，与 _fallback_layers 同口径
        layer, item = _feature(
            str(level["source"]),
            str(level.get("name") or level["source"]),
            str(level.get("label") or ""),
            role="dimension",
        )
        layers[layer].append(item)
    for metric in draft.get("metrics") or []:
        layer, item = _feature(
            str(metric["source"]), str(metric["name"]), str(metric.get("label") or ""),
            role="metric",
        )
        layers[layer].append(item)
    for column in draft.get("columns") or []:
        layer, item = _feature(
            str(column["source"]), str(column["name"]), str(column.get("label") or ""),
            role="dimension",
        )
        layers[layer].append(item)
    return {
        **layers,
        "dna_keys": [],
        "analysis_version": "oil-feature-declaration-v1",
        "reviewed": True,
    }


def drafts() -> list[tuple[str, dict]]:
    from scripts.reregister_oil import OIL_PLAN, OIL_SALES
    from scripts.register_oil_sales_daily import DRAFT as DAILY_DRAFT
    return [
        (OIL_SALES["dataset"], OIL_SALES["draft"]),
        (OIL_PLAN["dataset"], OIL_PLAN["draft"]),
        ("oil_sales_daily", DAILY_DRAFT),
    ]


def _req(method: str, path: str, token: str = "", body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    from engine.api.routes.datasets import _build_feature_layers, _build_generic

    payload = []
    for dataset, draft in drafts():
        body = _build_generic(dataset, draft)
        layers = build_layers(draft)
        normalized = _build_feature_layers(layers, body)
        payload.append((dataset, {**copy.deepcopy(draft), "feature_layers": normalized}))
        print(f"\n===== {dataset}")
        for layer in ("base", "trunk", "branch"):
            items = normalized[layer]
            print(f"  {layer}（{len(items)}）: " + ", ".join(
                f"{i['source']}:{i['semantic_type']}" for i in items))

    if dry_run:
        print("\n[dry-run] 未提交")
        return 0

    st, tok = _req("POST", "/api/v1/auth/login",
                   body={"username": "admin", "password": "admin123"})
    assert st == 200, (st, tok)
    token = tok["access_token"]
    for dataset, draft in payload:
        st, res = _req("PUT", f"/api/v1/engine/datasets/{dataset}", token, body=draft)
        print(f"[{dataset}] PUT:", st, res.get("message") or str(res)[:200])
        if st != 200:
            return 1
        caps = ((res.get("dataset") or {}).get("capabilities") or {}).get("feature_layers")
        if not caps:
            print(f"[{dataset}] 警告：响应里没有 feature_layers")
    return 0


if __name__ == "__main__":
    sys.exit(main())

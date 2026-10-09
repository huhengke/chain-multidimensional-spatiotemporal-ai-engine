# -*- coding: utf-8 -*-
"""项目经验字段推荐库。

经验库是随项目交付的长期资产，不属于业务数据库。这里仅负责读取、校验
和按字段名匹配 active 条目；推荐结果仍需经过 AI/规则安全校验和人工确认，
不会直接修改数据集声明或业务数据。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from engine.core.config import ENGINE_CONFIG_DIR

RECOMMENDATIONS_PATH = ENGINE_CONFIG_DIR / "ai_field_recommendations.yaml"
VALID_ROLES = frozenset({"time", "dimension", "metric", "ignore"})
VALID_FEATURE_LAYERS = frozenset({"base", "trunk", "branch", "none"})
VALID_QUERY_USES = frozenset({"filter", "group_by", "aggregate", "sort", "join", "compare"})
VALID_SEMANTIC_TYPES = frozenset({
    "product_code", "product_name", "alias", "category",
    "org_code", "org_name", "time", "other",
})
VALID_STATUSES = frozenset({"active", "retired"})
# 经验库采用版本化追加式维护；v2 增加公共相对时间语义与时间查询契约，
# 同时保持 v1 的字段条目结构兼容。
CATALOG_SCHEMA_VERSION = 2


def default_feature_layer(
    role: str,
    semantic_type: str,
    base_candidate: bool,
) -> str:
    """给字段一个保守的结构落点。

    基座不是“所有可识别字段”的默认容器，而是经过经验库/模型明确建议、
    并由用户确认后才启用的语义翻译能力。时间列属于事实数据的主干入口，
    不因为它可以派生季节、季度就自动进入基座。
    """
    if role == "ignore":
        return "none"
    if base_candidate:
        return "base"
    if role == "metric":
        return "branch"
    return "trunk"


def default_query_uses(role: str, semantic_type: str, feature_layer: str) -> list[str]:
    """不依赖具体行业的查询用途建议。"""
    if feature_layer == "none" or role == "ignore":
        return []
    if semantic_type in {"time", "product_code", "product_name", "alias", "category", "org_code", "org_name"}:
        uses = ["filter", "group_by"]
    elif role == "dimension":
        uses = ["filter", "group_by", "sort"]
    elif role == "metric":
        uses = ["aggregate", "sort", "compare"]
    else:
        uses = []
    if feature_layer == "base" and "join" not in uses:
        uses.append("join")
    return uses


class RecommendationCatalogError(ValueError):
    """经验推荐库无法加载或不符合结构约束。"""


@dataclass(frozen=True)
class FieldRecommendation:
    entry_id: str
    aliases: tuple[str, ...]
    role: str
    semantic_type: str
    base_candidate: bool
    scopes: tuple[str, ...]
    confidence: float
    rationale: str
    source: str
    status: str
    added_at: str
    revision: int
    feature_layer: str = "none"
    query_uses: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.entry_id,
            "revision": self.revision,
            "role": self.role,
            "semantic_type": self.semantic_type,
            "base_candidate": self.base_candidate,
            "scopes": list(self.scopes),
            "confidence": self.confidence,
            "rationale": self.rationale,
            "source": self.source,
            "status": self.status,
            "feature_layer": self.feature_layer,
            "query_uses": list(self.query_uses),
        }


def normalize_field_name(value: Any) -> str:
    """统一字段名的大小写、分隔符、括号和空白，便于别名匹配。"""
    text = str(value or "").strip().casefold()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"[\s_\-./\\:：()（）]+", "", text)
    return text


def _as_list(value: Any, field: str, entry_id: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 {field} 必须为非空列表")
    result = [str(item).strip() for item in value if str(item).strip()]
    if not result:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 {field} 不能为空")
    return result


def _parse_entry(item: Any, index: int) -> FieldRecommendation:
    if not isinstance(item, dict):
        raise RecommendationCatalogError(f"第 {index + 1} 个推荐条目必须为对象")
    entry_id = str(item.get("id") or "").strip()
    if not entry_id:
        raise RecommendationCatalogError(f"第 {index + 1} 个推荐条目缺少 id")
    role = str(item.get("role") or "").strip()
    if role not in VALID_ROLES:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 role 非法：{role}")
    semantic_type = str(item.get("semantic_type") or "").strip()
    if semantic_type not in VALID_SEMANTIC_TYPES:
        raise RecommendationCatalogError(
            f"条目 {entry_id} 的 semantic_type 非法：{semantic_type}"
        )
    status = str(item.get("status") or "").strip()
    if status not in VALID_STATUSES:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 status 非法：{status}")
    confidence = float(item.get("confidence"))
    if not 0 <= confidence <= 1:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 confidence 必须在 0 到 1 之间")
    revision = int(item.get("revision"))
    if revision < 1:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 revision 必须为正整数")
    feature_layer = str(item.get("feature_layer") or "").strip()
    if not feature_layer:
        feature_layer = default_feature_layer(role, semantic_type, bool(item.get("base_candidate", False)))
    if feature_layer not in VALID_FEATURE_LAYERS:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 feature_layer 非法：{feature_layer}")
    raw_uses = item.get("query_uses")
    if raw_uses is None:
        query_uses = default_query_uses(role, semantic_type, feature_layer)
    elif isinstance(raw_uses, list):
        query_uses = [str(v).strip() for v in raw_uses if str(v).strip()]
    else:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 query_uses 必须为列表")
    unknown_uses = sorted(set(query_uses) - VALID_QUERY_USES)
    if unknown_uses:
        raise RecommendationCatalogError(f"条目 {entry_id} 的 query_uses 非法：{unknown_uses}")
    return FieldRecommendation(
        entry_id=entry_id,
        aliases=tuple(_as_list(item.get("aliases"), "aliases", entry_id)),
        role=role,
        semantic_type=semantic_type,
        base_candidate=bool(item.get("base_candidate", False)),
        scopes=tuple(_as_list(item.get("scopes"), "scopes", entry_id)),
        confidence=confidence,
        rationale=str(item.get("rationale") or "项目经验推荐").strip(),
        source=str(item.get("source") or "project_experience").strip(),
        status=status,
        added_at=str(item.get("added_at") or "").strip(),
        revision=revision,
        feature_layer=feature_layer,
        query_uses=tuple(query_uses),
    )


def _read_catalog(path: Path) -> tuple[FieldRecommendation, ...]:
    if not path.exists():
        raise RecommendationCatalogError(f"经验推荐库不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RecommendationCatalogError(f"经验推荐库读取失败：{exc}") from exc
    if not isinstance(raw, dict):
        raise RecommendationCatalogError("经验推荐库根节点必须为对象")
    if int(raw.get("version", 0)) != CATALOG_SCHEMA_VERSION:
        raise RecommendationCatalogError(
            f"经验推荐库 version 必须为 {CATALOG_SCHEMA_VERSION}"
        )
    if str(raw.get("catalog") or "") != "ai_field_recommendations":
        raise RecommendationCatalogError("经验推荐库 catalog 标识不正确")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RecommendationCatalogError("经验推荐库 entries 必须为非空列表")
    parsed = tuple(_parse_entry(item, index) for index, item in enumerate(entries))
    ids = [item.entry_id for item in parsed]
    if len(ids) != len(set(ids)):
        raise RecommendationCatalogError("经验推荐库存在重复 id；修订时必须追加新 id")
    aliases_seen: dict[str, str] = {}
    for item in parsed:
        for alias in item.aliases:
            normalized = normalize_field_name(alias)
            if not normalized:
                raise RecommendationCatalogError(f"条目 {item.entry_id} 存在空别名")
            # 允许同一字段被多个经验条目解释，匹配时按置信度与 revision 选择。
            aliases_seen.setdefault(normalized, item.entry_id)
    return parsed


def load_recommendations(path: Path = RECOMMENDATIONS_PATH) -> tuple[FieldRecommendation, ...]:
    """按文件 mtime 缓存读取结果；文件变化后自动重新校验。"""
    return _load_recommendations_cached(str(path), path.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _load_recommendations_cached(path_str: str, _mtime_ns: int) -> tuple[FieldRecommendation, ...]:
    return _read_catalog(Path(path_str))


def catalog_status(path: Path = RECOMMENDATIONS_PATH) -> dict[str, Any]:
    """返回前置检查使用的公开状态，不暴露文件内容之外的敏感信息。"""
    try:
        entries = load_recommendations(path)
        active = sum(item.status == "active" for item in entries)
        retired = sum(item.status == "retired" for item in entries)
        return {
            "ok": True,
            "path": str(path),
            "version": CATALOG_SCHEMA_VERSION,
            "entries": len(entries),
            "active_entries": active,
            "retired_entries": retired,
        }
    except (OSError, RecommendationCatalogError, ValueError) as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}


def match_recommendations(
    source: Any,
    label: Any = "",
    *,
    scope: str | None = None,
) -> list[FieldRecommendation]:
    """返回字段名/显示名命中的 active 推荐，按置信度和 revision 降序排列。"""
    candidates = {normalize_field_name(source), normalize_field_name(label)} - {""}
    if not candidates:
        return []
    matches: list[FieldRecommendation] = []
    for item in load_recommendations():
        if item.status != "active":
            continue
        if scope and item.scopes and scope not in item.scopes and "all" not in item.scopes:
            continue
        aliases = {normalize_field_name(alias) for alias in item.aliases}
        if candidates & aliases:
            matches.append(item)
    return sorted(matches, key=lambda item: (item.confidence, item.revision), reverse=True)


def enrich_column_with_recommendations(
    item: dict[str, Any], *, scope: str | None = None,
) -> dict[str, Any]:
    """将经验命中附加到字段画像，并以最高置信度推荐作为当前建议基线。"""
    matches = match_recommendations(item.get("source"), item.get("cn") or item.get("label"), scope=scope)
    item["knowledge_matches"] = [match.public() for match in matches]
    item["knowledge_confidence"] = matches[0].confidence if matches else None
    item["recommendation_sources"] = ["rule_fallback"]
    item["ai_conflict"] = False
    item["needs_manual_review"] = False
    if not matches:
        return item
    best = matches[0]
    item["knowledge_role"] = best.role
    item["knowledge_semantic_type"] = best.semantic_type
    item["knowledge_base_candidate"] = best.base_candidate
    item["feature_layer"] = best.feature_layer
    item["query_uses"] = list(best.query_uses)
    item["guess_role"] = best.role
    item["feature_role"] = best.role
    item["semantic_type"] = best.semantic_type
    item["base_candidate"] = best.base_candidate
    item["suggestion_source"] = "knowledge"
    item["recommendation_source"] = "knowledge"
    item["suggestion_reason"] = best.rationale
    item["recommendation_sources"] = ["knowledge", "rule_fallback"]
    item["analysis_version"] = f"recommendations-v{CATALOG_SCHEMA_VERSION}-r{best.revision}"
    return item

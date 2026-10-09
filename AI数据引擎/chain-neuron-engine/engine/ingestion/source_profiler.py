# -*- coding: utf-8 -*-
"""统一的原始字段画像与 AI 建议适配器。

Excel 和数据库表都先经过同一份统计画像；AI 服务不可用时保留可解释的
规则建议，并明确标记为 rule_fallback，避免把规则结果冒充模型结果。
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from engine.core.config import get_settings
from engine.core.logging import get_logger
from engine.ingestion.excel_reader import default_derive_for, slugify_name
from engine.ingestion.field_recommendations import (
    VALID_FEATURE_LAYERS,
    VALID_QUERY_USES,
    default_feature_layer,
    default_query_uses,
    enrich_column_with_recommendations,
    match_recommendations,
)

logger = get_logger("ingestion.profiler")

_TIME_HINTS = ("月", "日期", "时间", "年月", "month", "date", "time", "period", "yyyymm")
_CODE_HINTS = (
    "编码", "代码", "编号", "标识", "序号", "code", "_id", "id_", "id",
    "no", "key", "flag", "状态码",
)
_METRIC_HINTS = (
    "金额", "数量", "业务数量", "业务", "收入", "价格", "单价", "总额", "余额",
    "amount", "qty", "quantity", "volume", "sales", "sale", "revenue", "price", "total", "value", "count",
)
_YYYYMM = re.compile(r"^\d{4}(0[1-9]|1[0-2])$")
_YYYYMMDD = re.compile(r"^\d{8}$")
# 年月文本（允许日期单元格落到 Excel 后的 " 00:00:00" 时间尾巴）
_YM_SEP = re.compile(r"^\d{4}[-/.年]\d{1,2}月?(?:[ T]\d.*)?$")
# 完整日期文本：2026-04-01 / 2026/4/1 / 2026年4月1日 / 2026-04-01 00:00:00
_YMD_SEP = re.compile(r"^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:[ T]\d.*)?$")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _numeric(value: Any) -> bool:
    try:
        Decimal(_text(value).replace(",", ""))
        return bool(_text(value))
    except (InvalidOperation, ValueError):
        return False


def _time_like(value: Any) -> bool:
    text = _text(value)
    return bool(
        _YYYYMM.match(text)
        or _YYYYMMDD.match(text)
        or _YMD_SEP.match(text)
        or _YM_SEP.match(text)
    )


def _time_format(values: list[str]) -> str:
    non_empty = [v for v in values if v]
    if not non_empty:
        return "yyyymm"
    total = len(non_empty)
    if sum(bool(_YYYYMM.match(v)) for v in non_empty) / total >= 0.8:
        return "yyyymm"
    if sum(bool(_YYYYMMDD.match(v)) for v in non_empty) / total >= 0.8:
        return "yyyymmdd"
    # 日粒度必须早于年月判断：`2026-04` 只命中 _YM_SEP，`2026-04-01` 只命中 _YMD_SEP。
    if sum(bool(_YMD_SEP.match(v)) for v in non_empty) / total >= 0.8:
        return "yyyy-mm-dd"
    if sum(bool(_YM_SEP.match(v)) for v in non_empty) / total >= 0.8:
        return "yyyy-mm"
    return "yyyymm"


def _is_identifier_hint(source: str) -> bool:
    """判断字段名是否更像编码/ID/标志位，避免误判为时间或指标。"""
    text = str(source or "").strip().lower()
    if not text:
        return False
    if any(token in text for token in ("编码", "代码", "编号", "标识", "序号", "状态码")):
        return True
    if re.search(r"(?:^|[_\-\s])(id|code|no|key|flag)(?:$|[_\-\s])", text):
        return True
    return text.endswith(("id", "code", "_no", "no", "_key", "key", "flag"))


def _semantic_type(source: str, role: str) -> str:
    text = str(source or "").strip().lower()
    if role == "time" or any(token in text for token in _TIME_HINTS):
        return "time"
    if any(token in text for token in ("产品", "产品", "商品", "物料", "gas", "product", "material")):
        if _is_identifier_hint(text):
            return "product_code"
        if any(token in text for token in ("名称", "name", "desc", "描述")):
            return "product_name"
        return "category"
    if any(token in text for token in ("别名", "alias")):
        return "alias"
    if any(token in text for token in ("类别", "品类", "分类", "category", "type")):
        return "category"
    if any(token in text for token in ("组织", "机构", "部门", "公司", "定位单元", "网点", "org", "branch", "company", "store", "site")):
        if _is_identifier_hint(text):
            return "org_code"
        if any(token in text for token in ("名称", "name", "desc", "描述")):
            return "org_name"
    return "other"


def _rule_role(source: str, values: list[str]) -> tuple[str, str]:
    non_empty = [v for v in values if v and v not in {"*", "NULL", "null", "-", "None"}]
    if not non_empty:
        return "ignore", "抽样数据全部为空"
    numeric_ratio = sum(_numeric(v) for v in non_empty) / len(non_empty)
    time_ratio = sum(_time_like(v) for v in non_empty) / len(non_empty)
    source_text = source.lower()
    hint = any(token in source_text for token in _TIME_HINTS)
    identifier_hint = _is_identifier_hint(source)
    metric_hint = any(token in source_text for token in _METRIC_HINTS)
    unique_ratio = len(set(non_empty)) / len(non_empty)
    if identifier_hint:
        return "dimension", f"字段名含编码/标识线索，按离散维度处理（数值率 {numeric_ratio:.0%}）"
    if time_ratio >= 0.8 or (hint and numeric_ratio >= 0.8):
        return "time", f"时间格式匹配率 {time_ratio:.0%}，字段名含时间线索 {hint}"
    if numeric_ratio >= 0.8 and metric_hint:
        return "metric", f"数值匹配率 {numeric_ratio:.0%}，适合作为可聚合指标"
    if numeric_ratio >= 0.8:
        return "dimension", "字段为高数值率离散字段但缺少指标线索，按编码/离散维度候选处理"
    if unique_ratio >= 0.98 and len(non_empty) > 20:
        return "dimension", f"非空值较多且为离散文本，适合作为维度候选"
    return "dimension", f"非数值离散字段，适合作为维度候选（唯一值率 {unique_ratio:.0%}）"


def _request_ai(columns: list[dict[str, Any]], source_name: str) -> dict[str, dict[str, Any]]:
    if not columns:
        return {}
    compact_columns = []
    for item in columns:
        compact_matches = []
        for match in (item.get("knowledge_matches") or [])[:2]:
            if not isinstance(match, dict):
                continue
            compact_matches.append({
                "id": match.get("id"),
                "role": match.get("role"),
                "semantic_type": match.get("semantic_type"),
                "base_candidate": match.get("base_candidate"),
                "feature_layer": match.get("feature_layer"),
                "query_uses": match.get("query_uses") or [],
                "confidence": match.get("confidence"),
                "rationale": match.get("rationale"),
            })
        compact_columns.append({
            "source": item["source"],
            "sample": item.get("sample"),
            "numeric_ratio": item.get("numeric_ratio", 0),
            "time_ratio": item.get("time_ratio", 0),
            "unique_ratio": item.get("unique_ratio", 0),
            "rule_role": item.get("guess_role"),
            "semantic_type": item.get("semantic_type", "other"),
            "base_candidate": item.get("base_candidate", False),
            "feature_layer": item.get("feature_layer", "none"),
            "query_uses": item.get("query_uses", []),
            "evidence": {
                "non_null_rate": (item.get("evidence") or {}).get("non_null_rate"),
                "sample_values": (item.get("evidence") or {}).get("sample_values", [])[:3],
            },
            "knowledge_matches": compact_matches,
        })
    settings = get_settings()
    payload = {
        "source_name": source_name,
        "columns": compact_columns,
    }
    try:
        response = httpx.post(
            f"{settings.AGENT_BASE_URL.rstrip('/')}/api/v1/analyze/fields",
            json=payload,
            # Codex/DeepSeek 实际推理可能超过 3 秒；过短会把真实模型误判为规则回退。
            timeout=65,
        )
        response.raise_for_status()
        body = response.json()
        if str(body.get("backend") or "") in {"unavailable", "harness-rule", "mock"}:
            return {}
        known_sources = {
            str(item.get("source") or "") for item in columns if item.get("source")
        }
        result: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for item in body.get("columns", []):
            source = str(item.get("source") or "")
            role = str(item.get("role") or "")
            if (
                source in known_sources
                and source not in seen
                and role in {"time", "dimension", "metric", "ignore"}
            ):
                seen.add(source)
                semantic = str(item.get("semantic_type") or "other")
                if semantic not in {
                    "product_code", "product_name", "alias", "category", "org_code",
                    "org_name", "time", "other",
                }:
                    semantic = "other"
                feature_layer = str(item.get("feature_layer") or "").strip()
                if feature_layer not in VALID_FEATURE_LAYERS:
                    feature_layer = default_feature_layer(
                        role, semantic, bool(item.get("base_candidate", False))
                    )
                raw_uses = item.get("query_uses")
                query_uses = (
                    [str(value) for value in raw_uses if str(value) in VALID_QUERY_USES]
                    if isinstance(raw_uses, list)
                    else default_query_uses(role, semantic, feature_layer)
                )
                result[source] = {
                    "role": role,
                    "reason": str(item.get("reason") or "模型建议"),
                    "base_candidate": bool(item.get("base_candidate", False)),
                    "semantic_type": semantic,
                    "feature_layer": feature_layer,
                    "query_uses": query_uses,
                }
        return result
    except Exception as exc:
        logger.info("AI 字段分析不可用，使用规则回退 source=%s error=%s", source_name, exc)
        return {}


def _apply_knowledge_baseline(
    columns: list[dict[str, Any]], *, scope: str | None = None,
) -> None:
    for item in columns:
        enrich_column_with_recommendations(item, scope=scope)


def _merge_ai_suggestion(item: dict[str, Any], suggestion: dict[str, Any]) -> None:
    """合并模型建议；高置信度项目经验优先，冲突只标记不静默覆盖。"""
    knowledge = match_recommendations(
        item.get("source"), item.get("cn") or item.get("label"),
    )
    best = knowledge[0] if knowledge else None
    ai_role = suggestion["role"]
    ai_semantic = suggestion.get("semantic_type") or _semantic_type(
        str(item.get("source") or ""), ai_role
    )
    ai_base = bool(suggestion.get("base_candidate", False))
    if ai_role in {"time", "metric"} or ai_semantic == "time":
        # 时间序列和指标不是基座语义；即使模型误返回 true，也必须降级到
        # 主干/分枝，避免把事实数据列误当成语义翻译能力。
        ai_base = False
    ai_layer = str(suggestion.get("feature_layer") or "").strip()
    if ai_layer not in VALID_FEATURE_LAYERS:
        ai_layer = default_feature_layer(ai_role, ai_semantic, ai_base)
    ai_uses = suggestion.get("query_uses")
    if not isinstance(ai_uses, list):
        ai_uses = default_query_uses(ai_role, ai_semantic, ai_layer)
    ai_uses = [str(value) for value in ai_uses if str(value) in VALID_QUERY_USES]
    conflict = bool(
        best and (
            best.role != ai_role
            or best.semantic_type != ai_semantic
            or best.base_candidate != ai_base
            or best.feature_layer != ai_layer
            or tuple(best.query_uses) != tuple(ai_uses)
        )
    )
    if best and best.confidence >= 0.8:
        item["ai_conflict"] = conflict
        item["needs_manual_review"] = conflict
        item.setdefault("recommendation_sources", [])
        if "ai" not in item["recommendation_sources"]:
            item["recommendation_sources"].append("ai")
        item["ai_suggestion"] = {
            "role": ai_role,
            "semantic_type": ai_semantic,
            "base_candidate": ai_base,
            "feature_layer": ai_layer,
            "query_uses": ai_uses,
            "reason": suggestion["reason"],
        }
        if conflict:
            item["suggestion_reason"] = (
                f"经验库建议：{item.get('suggestion_reason') or best.rationale}；"
                f"AI 建议：{suggestion['reason']}，存在冲突需人工确认"
            )
        return

    item["guess_role"] = ai_role
    item["feature_role"] = ai_role
    item["suggestion_source"] = "ai"
    item["recommendation_source"] = "ai"
    item["suggestion_reason"] = suggestion["reason"]
    item["semantic_type"] = ai_semantic
    item["base_candidate"] = ai_base
    item["feature_layer"] = ai_layer
    item["query_uses"] = ai_uses
    item.setdefault("recommendation_sources", [])
    if "ai" not in item["recommendation_sources"]:
        item["recommendation_sources"].append("ai")
    item["ai_conflict"] = conflict
    item["needs_manual_review"] = conflict
    item["analysis_version"] = "ai+knowledge" if best else "ai"


def profile_records(
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    source_name: str,
    row_count: int | None = None,
    file_token: str = "",
    sheet: str = "database",
    scope: str | None = None,
) -> dict[str, Any]:
    """对任意字典行做统一字段画像，并尝试调用 Agent 的模型分析。"""
    names = [str(c.get("name") or c.get("source") or "") for c in columns]
    names = [name for name in names if name]
    if not names and rows:
        names = list(rows[0])
    preview = [{name: (_text(row.get(name)) or None) for name in names} for row in rows[:20]]
    analyzed: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, name in enumerate(names):
        values = [_text(row.get(name)) for row in rows[:200]]
        non_empty = [value for value in values if value]
        role, reason = _rule_role(name, values)
        guess_name = slugify_name(name, f"field{index + 1}")
        base = guess_name
        suffix = 2
        while guess_name in used:
            guess_name = f"{base}_{suffix}"
            suffix += 1
        used.add(guess_name)
        item = {
            "index": index,
            "source": name,
            "cn": str((columns[index] if index < len(columns) else {}).get("label") or name),
            "sample": non_empty[0] if non_empty else None,
            "non_null": len(non_empty),
            "numeric_ratio": round(sum(_numeric(value) for value in non_empty) / len(non_empty), 4) if non_empty else 0,
            "time_ratio": round(sum(_time_like(value) for value in non_empty) / len(non_empty), 4) if non_empty else 0,
            "unique_ratio": round(len(set(non_empty)) / len(non_empty), 4) if non_empty else 0,
            "guess_role": role,
            "feature_role": role,
            "guess_name": guess_name,
            "guess_label": str((columns[index] if index < len(columns) else {}).get("label") or name),
            "suggestion_source": "rule_fallback",
            "recommendation_source": "rule_fallback",
            "suggestion_reason": reason,
            "semantic_type": _semantic_type(name, role),
            # 语义类型只是分析线索，不等于基座候选；基座必须由经验库/AI
            # 明确建议，并在向导中由用户确认。
            "base_candidate": False,
        }
        item["feature_layer"] = default_feature_layer(
            role, item["semantic_type"], bool(item["base_candidate"])
        )
        item["query_uses"] = default_query_uses(
            role, item["semantic_type"], item["feature_layer"]
        )
        item["evidence"] = {
            "non_null_rate": round(len(non_empty) / len(values), 4) if values else 0,
            "numeric_ratio": item["numeric_ratio"],
            "time_ratio": item["time_ratio"],
            "unique_ratio": item["unique_ratio"],
            "sample_values": list(dict.fromkeys(non_empty))[:5],
        }
        if role == "time":
            item["guess_time_format"] = _time_format(non_empty)
        analyzed.append(item)

    _apply_knowledge_baseline(analyzed, scope=scope)
    ai_columns = [
        item for item in analyzed
        if not item.get("knowledge_matches")
        or float(item.get("knowledge_confidence") or 0) < 0.8
    ]
    ai = _request_ai(ai_columns, source_name)
    for item in analyzed:
        suggestion = ai.get(item["source"])
        if suggestion and _accept_ai_suggestion(item, suggestion):
            _merge_ai_suggestion(item, suggestion)
    time_columns = [item for item in analyzed if item["guess_role"] == "time"]
    time_column = time_columns[0] if time_columns else None
    for item in time_columns[1:]:
        item["guess_role"] = "dimension"
    default_derive = default_derive_for(item_time_format(time_column) if time_column else None)
    return {
        "file_name": source_name,
        "file_token": file_token,
        "sheet": sheet,
        "sheets": [sheet],
        "header_mode": "single",
        "header_rows": 1,
        "row_count": row_count if row_count is not None else len(rows),
        "row_count_estimated": row_count is not None,
        "row_count_capped": False,
        "columns": analyzed,
        "preview": preview,
        "time_column": time_column["source"] if time_column else None,
        "time_format": item_time_format(time_column) if time_column else "yyyymm",
        "default_derive": default_derive,
        "analysis_source": (
            "ai+knowledge" if ai and any(item.get("knowledge_matches") for item in analyzed)
            else "ai" if ai
            else "knowledge" if any(item.get("knowledge_matches") for item in analyzed)
            else "rule_fallback"
        ),
        "recommendation_catalog": {
            "matched_fields": sum(bool(item.get("knowledge_matches")) for item in analyzed),
            "total_fields": len(analyzed),
        },
    }


def apply_ai_suggestions(result: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    """给现有 Excel 探测结果补充模型建议和可审计理由。"""
    columns = list(result.get("columns") or [])
    _apply_knowledge_baseline(columns)
    result["columns"] = columns
    ai_columns = [
        item for item in columns
        if not item.get("knowledge_matches")
        or float(item.get("knowledge_confidence") or 0) < 0.8
    ]
    ai = _request_ai(ai_columns, source_name)
    for item in columns:
        item.setdefault("suggestion_source", "rule_fallback")
        item.setdefault("recommendation_source", item.get("suggestion_source") or "rule_fallback")
        item.setdefault("feature_role", item.get("guess_role") or "ignore")
        item.setdefault("suggestion_reason", "基于字段画像的规则建议")
        item.setdefault("semantic_type", _semantic_type(
            str(item.get("source") or ""), str(item.get("guess_role") or "dimension")
        ))
        item.setdefault("base_candidate", False)
        item.setdefault(
            "feature_layer",
            default_feature_layer(
                str(item.get("guess_role") or "dimension"),
                str(item.get("semantic_type") or "other"),
                bool(item.get("base_candidate")),
            ),
        )
        item.setdefault(
            "query_uses",
            default_query_uses(
                str(item.get("guess_role") or "dimension"),
                str(item.get("semantic_type") or "other"),
                str(item.get("feature_layer") or "none"),
            ),
        )
        item.setdefault("evidence", {
            "non_null_rate": None,
            "numeric_ratio": item.get("numeric_ratio", 0),
            "time_ratio": item.get("time_ratio", 0),
            "unique_ratio": item.get("unique_ratio", 0),
        })
        suggestion = ai.get(str(item.get("source") or ""))
        if suggestion and _accept_ai_suggestion(item, suggestion):
            _merge_ai_suggestion(item, suggestion)
    time_seen = False
    for item in result.get("columns") or []:
        if item.get("guess_role") != "time":
            continue
        if time_seen:
            item["guess_role"] = "dimension"
            item["feature_role"] = "dimension"
        else:
            time_seen = True
    time_column = next(
        (item.get("source") for item in result.get("columns") or []
         if item.get("guess_role") == "time"),
        None,
    )
    result["time_column"] = time_column
    result["analysis_source"] = (
        "ai+knowledge" if ai and any(item.get("knowledge_matches") for item in columns)
        else "ai" if ai
        else "knowledge" if any(item.get("knowledge_matches") for item in columns)
        else "rule_fallback"
    )
    result["recommendation_catalog"] = {
        "matched_fields": sum(bool(item.get("knowledge_matches")) for item in columns),
        "total_fields": len(columns),
    }
    return result


def _accept_ai_suggestion(item: dict[str, Any], suggestion: dict[str, Any]) -> bool:
    """只接受当前输入字段的建议，并阻止模型覆盖明显的编码字段。"""
    source = str(item.get("source") or "")
    if not source or item.get("reserved") or item.get("non_null", 1) == 0:
        return False
    role = str(suggestion.get("role") or "")
    if source.lower() == "id":
        return False
    if _is_identifier_hint(source) and role in {"time", "metric"}:
        logger.warning("忽略模型对编码字段的高风险建议 source=%s role=%s", source, role)
        return False
    return True


def item_time_format(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("guess_time_format") or "yyyymm")

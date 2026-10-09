# -*- coding: utf-8 -*-
"""Agent 服务（独立进程，端口 8001）。

前端对话页只与本进程通信；本进程经“数据引擎通道”访问引擎。
后期整体切换本地 Agent 时，前端 URL 与引擎均零改动。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent.python_agent.orchestrator import Orchestrator
from engine.core.config import get_settings
from engine.core.logging import configure_logging, get_logger
from engine.ingestion.field_recommendations import (
    VALID_FEATURE_LAYERS,
    VALID_QUERY_USES,
    default_feature_layer,
    default_query_uses,
)

configure_logging()
logger = get_logger("agent.server")

app = FastAPI(title="链式神经元 Agent 服务", version="1.0.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.CORS_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator = Orchestrator()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class FieldAnalysisRequest(BaseModel):
    source_name: str = ""
    columns: list[dict[str, Any]] = Field(default_factory=list)


class FoundationAnalysisRequest(BaseModel):
    idea: str = ""
    datasets: list[dict[str, Any]] = Field(default_factory=list)


class RulesAnalysisRequest(BaseModel):
    idea: str = ""
    rules: list[dict[str, Any]] = Field(default_factory=list)


_TEMPORAL_IDEA_WORDS = (
    "上周", "本周", "下周", "上个月", "上月", "本月", "下个月", "下月",
    "日期", "时间", "日均", "同比", "环比", "工作日", "节日", "季节", "季度",
)


def _temporal_idea(idea: str) -> bool:
    return any(token in str(idea or "") for token in _TEMPORAL_IDEA_WORDS)


def _temporal_column(column: dict[str, Any]) -> bool:
    if bool(column.get("canonical_time")):
        return True
    semantic = str(column.get("semantic_type") or "").strip().lower()
    role = str(column.get("role") or column.get("feature_role") or "").strip().lower()
    text = f"{column.get('source') or ''} {column.get('name') or ''} {column.get('label') or ''}".lower()
    return semantic == "time" or role == "time" or any(
        token in text for token in ("date", "time", "日期", "时间", "年月", "月份")
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": "chain-neuron-agent",
            "time": datetime.now().isoformat(timespec="seconds")}


@app.post("/api/v1/chat")
def chat(body: ChatRequest) -> dict:
    logger.info("对话：%s", body.message[:80])
    result = _orchestrator.handle(body.message)
    return result


@app.post("/api/v1/analyze/fields")
def analyze_fields(body: FieldAnalysisRequest) -> dict:
    """由 Agent/模型分析注册阶段的原始字段，不直接修改引擎声明。"""
    prompt = (
        "你是数据集字段分析助手。只输出 JSON，格式为 "
        '{\"columns\":[{\"source\":\"原字段名\",'
        '\"role\":\"time|dimension|metric|ignore\",'
        '\"feature_layer\":\"base|trunk|branch|none\",'
        '\"query_uses\":[\"filter|group_by|aggregate|sort|join|compare\"],'
        '\"reason\":\"不超过60字的依据\"}]}。'
        "每个输入字段都必须输出一次；空字段或无数据字段选 ignore；"
        "时间日期选 time；可聚合数值选 metric；离散文本选 dimension。"
        "base 是用于过滤、翻译、归一化和跨数据集映射的公共语义，trunk 是稳定公共分组路径，branch 是业务末端细分或指标。时间字段默认属于 trunk，不能仅因可派生季节/季度就放入 base；metric 默认属于 branch。"
        "不得因为字段名包含产品、类别甲等词就改变通用判断；字段名只是证据之一。\n"
        "每项还需给出 semantic_type（product_code/product_name/alias/category/org_code/org_name/time/other）和 base_candidate 布尔值；"
        f"数据源：{body.source_name}\n字段画像：{body.columns}"
    )
    text = _orchestrator.llm.chat([{"role": "user", "content": prompt}], expect_json=True)
    try:
        import json
        data = json.loads(text)
        columns = data.get("columns") if isinstance(data, dict) else None
        if not isinstance(columns, list):
            raise ValueError("模型未返回 columns 列表")
        source_items = [
            item for item in body.columns
            if isinstance(item, dict) and str(item.get("source") or "").strip()
        ]
        source_map = {str(item["source"]): item for item in source_items}
        valid = []
        seen: set[str] = set()
        semantic_types = {
            "product_code", "product_name", "alias", "category",
            "org_code", "org_name", "time", "other",
        }
        for item in columns:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            if (
                not source
                or source not in source_map
                or source in seen
                or item.get("role") not in {"time", "dimension", "metric", "ignore"}
            ):
                continue
            seen.add(source)
            semantic = str(item.get("semantic_type") or "other")
            if semantic not in semantic_types:
                semantic = "other"
            base_candidate = bool(item.get("base_candidate", False))
            if item.get("role") in {"time", "metric"} or semantic == "time":
                base_candidate = False
            feature_layer = str(item.get("feature_layer") or "").strip()
            if feature_layer not in VALID_FEATURE_LAYERS:
                feature_layer = default_feature_layer(str(item["role"]), semantic, base_candidate)
            raw_uses = item.get("query_uses")
            query_uses = (
                [str(value) for value in raw_uses if str(value) in VALID_QUERY_USES]
                if isinstance(raw_uses, list)
                else default_query_uses(str(item["role"]), semantic, feature_layer)
            )
            valid.append({
                "source": source,
                "role": str(item["role"]),
                "reason": str(item.get("reason") or "模型建议"),
                "semantic_type": semantic,
                "base_candidate": base_candidate,
                "feature_layer": feature_layer,
                "query_uses": query_uses,
            })
        for source, original in source_map.items():
            if source in seen:
                continue
            role = str(original.get("rule_role") or "ignore")
            if role not in {"time", "dimension", "metric", "ignore"}:
                role = "ignore"
            semantic = str(original.get("semantic_type") or "other")
            if semantic not in semantic_types:
                semantic = "other"
            base_candidate = bool(original.get("base_candidate", False))
            if role in {"time", "metric"} or semantic == "time":
                base_candidate = False
            feature_layer = str(original.get("feature_layer") or "").strip()
            if feature_layer not in VALID_FEATURE_LAYERS:
                feature_layer = default_feature_layer(role, semantic, base_candidate)
            raw_uses = original.get("query_uses")
            query_uses = (
                [str(value) for value in raw_uses if str(value) in VALID_QUERY_USES]
                if isinstance(raw_uses, list)
                else default_query_uses(role, semantic, feature_layer)
            )
            valid.append({
                "source": source,
                "role": role,
                "reason": "模型未返回，采用规则建议",
                "semantic_type": semantic,
                "base_candidate": base_candidate,
                "feature_layer": feature_layer,
                "query_uses": query_uses,
            })
        if not valid:
            raise ValueError("输入字段为空")
        return {"backend": _orchestrator.llm.backend, "columns": valid}
    except Exception as exc:
        logger.warning("字段模型建议解析失败：%s", exc)
        return {"backend": "unavailable", "columns": []}


@app.post("/api/v1/analyze/foundation")
def analyze_foundation(body: FoundationAnalysisRequest) -> dict:
    """分析基座候选；只生成待人工确认的建议，不直接修改引擎配置。"""
    # 时间问题使用经验库和字段声明做确定性前置筛选：不能让模型把组织、
    # 商品、记录或指标字段误推荐成“上周”的字段。原始日期仍归中通主干，
    # 基座只提供相对时间语义的翻译规则。
    if _temporal_idea(body.idea):
        suggestions = []
        for dataset in body.datasets:
            if not isinstance(dataset, dict):
                continue
            did = str(dataset.get("id") or dataset.get("dataset") or "").strip()
            if not did:
                continue
            fields = []
            all_columns = [column for column in dataset.get("columns") or [] if isinstance(column, dict)]
            canonical_columns = [column for column in all_columns if column.get("canonical_time")]
            candidate_columns = canonical_columns or all_columns
            for column in candidate_columns:
                if not isinstance(column, dict) or not _temporal_column(column):
                    continue
                source = str(column.get("source") or column.get("name") or "").strip()
                if not source:
                    continue
                fields.append({
                    "source": source,
                    "name": str(column.get("name") or source),
                    "label": str(column.get("label") or source),
                    "semantic_type": "time",
                    "layer": "trunk",
                    "reason": "这是数据集声明的时间入口；建议放入中通主干，用于把上周、本周、同比和环比编译为可执行日期范围。",
                })
            if not fields:
                continue
            suggestions.append({
                "dataset": did,
                "fields": fields,
                "rationale": (
                    f"{body.idea}；只关联日期/时间字段。"
                    + ("当前字段是月粒度，不能精确支持上周。" if fields and any(str(f.get("source")) == "Month" for f in fields) else "")
                    + ("当前没有声明的日期字段，需要重新注册日粒度时间列。" if not fields else "")
                ),
                "source": "ai+experience_guard",
                "status": "pending_review",
            })
        return {"backend": "ai+experience_guard", "suggestions": suggestions}
    prompt = (
        "你是通用 AI 数据引擎的基座语义分析助手。只输出 JSON："
        '{"suggestions":[{"dataset":"数据集id","fields":[{"source":"字段","name":"字段名",'
        '"label":"显示名","semantic_type":"product_code|product_name|alias|category|org_code|org_name|time",'
        '"reason":"不超过80字"}],"rationale":"不超过120字"}]}。'
        "基座只用于跨数据集的语义过滤、翻译、归一化、纠偏和派生条件；原始时间序列必须留在中通主干，指标留在分枝。"
        "只能从输入字段中选择候选，不得虚构字段；建议必须进入待确认状态。\n"
        f"用户想法：{body.idea}\n数据集与字段：{body.datasets}"
    )
    try:
        import json
        text = _orchestrator.llm.chat([{"role": "user", "content": prompt}], expect_json=True)
        payload = json.loads(text)
        raw = payload.get("suggestions") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise ValueError("模型未返回 suggestions")
        known: dict[str, set[str]] = {}
        for dataset in body.datasets:
            if isinstance(dataset, dict):
                did = str(dataset.get("id") or dataset.get("dataset") or "")
                known[did] = {str(c.get("source") or c.get("name") or "") for c in dataset.get("columns") or [] if isinstance(c, dict)}
        suggestions = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            did = str(item.get("dataset") or "")
            if did not in known:
                continue
            fields = []
            for field in item.get("fields") or []:
                if not isinstance(field, dict):
                    continue
                source = str(field.get("source") or "")
                semantic = str(field.get("semantic_type") or "other")
                if source in known[did] and semantic in {"product_code", "product_name", "alias", "category", "org_code", "org_name"}:
                    fields.append({**field, "source": source, "semantic_type": semantic})
            if fields:
                suggestions.append({**item, "dataset": did, "fields": fields, "source": _orchestrator.llm.backend, "status": "pending_review"})
        if suggestions:
            return {"backend": _orchestrator.llm.backend, "suggestions": suggestions}
    except Exception as exc:
        logger.warning("基座建议模型解析失败：%s", exc)
    return {"backend": "rule_fallback", "suggestions": []}


@app.post("/api/v1/analyze/rules")
def analyze_rules(body: RulesAnalysisRequest) -> dict:
    """把用户想法转换成可编辑的值映射/条件规则草稿。"""
    prompt = (
        "你是通用 AI 数据引擎的时间派生规则助手。只输出 JSON，格式为 "
        '{"suggestions":[{"name":"规则英文名","source":"month|weekday|date|其他字段",'
        '"precision":"month|day|","mapping":{"输入值或范围":"输出值"},'
        '"rules":[{"condition":"条件表达式","value":"输出值"}],'
        '"rationale":"不超过120字"}]}。'
        "只能生成通用的值映射或条件规则，不得输出 SQL、代码、文件操作或直接修改配置的指令；"
        "一个规则只负责一种派生语义；如果同时要求季节和季度，必须拆成两条建议；"
        "月份和星期的 mapping 键必须使用逗号分隔的整数列表（例如 3,4,5），不要使用 1-3 范围；"
        "mapping 和 rules 至少有一个非空；日期规则使用 month/day/weekday 等可读条件；"
        "不要把行业字段写死，必须围绕用户想法生成；建议必须进入待人工确认状态。"
        f"\n用户想法：{body.idea}\n现有规则上下文：{body.rules}"
    )
    try:
        import json

        text = _orchestrator.llm.chat([{"role": "user", "content": prompt}], expect_json=True)
        payload = json.loads(text)
        raw = payload.get("suggestions") if isinstance(payload, dict) else None
        suggestions = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                source = str(item.get("source") or "").strip()
                if not name or not source:
                    continue
                mapping = item.get("mapping") if isinstance(item.get("mapping"), dict) else {}
                clean_mapping = {
                    str(key).strip(): str(value).strip()
                    for key, value in mapping.items()
                    if str(key).strip() and str(value).strip()
                }
                raw_rules = item.get("rules") if isinstance(item.get("rules"), list) else []
                clean_rules = []
                for rule in raw_rules:
                    if not isinstance(rule, dict):
                        continue
                    condition = str(rule.get("condition") or "").strip()
                    value = str(rule.get("value") or "").strip()
                    if condition and value:
                        clean_rules.append({"condition": condition, "value": value})
                if not clean_mapping and not clean_rules:
                    continue
                suggestions.append({
                    "name": name,
                    "source": source,
                    "precision": str(item.get("precision") or "").strip(),
                    "mapping": clean_mapping,
                    "rules": clean_rules,
                    "rationale": str(item.get("rationale") or "模型建议，请人工核对").strip(),
                    "source_type": "ai",
                    "status": "pending_review",
                })
        return {
            "backend": _orchestrator.llm.backend,
            "suggestions": suggestions,
            "status": "pending_review",
        }
    except Exception as exc:
        logger.warning("规则模型建议解析失败：%s", exc)
        return {"backend": "unavailable", "suggestions": [], "status": "pending_review"}

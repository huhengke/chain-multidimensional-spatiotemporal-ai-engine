# -*- coding: utf-8 -*-
"""时间派生规则路由：查看 / AI 建议 / 热更新 / 回滚。"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

import fastapi
import httpx
import yaml
from fastapi import APIRouter, Body, Depends

from engine.api.schemas_api import RuleUpdateRequest
from engine.core.config import ENGINE_CONFIG_DIR, get_settings
from engine.core.errors import AppException, BadRequestError, NotFoundError
from engine.core.logging import get_logger
from engine.derivation.rule_loader import RULES_FILE, SNAPSHOT_DIR, RuleLoader
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.adapters.registry import storage_adapters

router = APIRouter(prefix="/api/v1/engine/derive-rules", tags=["rules"])
logger = get_logger("api.rules")


def _active_storage():
    try:
        context = resolve_active_profile()
        return context, storage_adapters.require(context.storage_type)
    except RuntimeProfileError as exc:
        raise AppException(
            "E4501",
            "活动 AI 数据引擎运行配置不可用",
            detail={"error": str(exc)},
            http_status=503,
        ) from exc


def _require_mutable_rules() -> None:
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "当前时间规则已固定在活动 CNTS Catalog 中；请修改候选规则资产并构建新 Catalog，验收后再原子切换。",
            detail={
                **context.response_metadata(),
                "action": "build_new_candidate_catalog",
                "fallback": "forbidden",
            },
        )


def _rule_suggestions(body: dict[str, Any]) -> list[dict[str, Any]]:
    """在 Agent/模型不可用时给出可审阅的确定性规则草稿。

    这里只覆盖通用时间语义，不把任何行业字段写死到规则配置中。草稿只返回给前端，
    直到用户加入规则表单并保存，才会写入 YAML 和热加载。
    """
    idea = str(body.get("idea") or "").strip()
    suggestions: list[dict[str, Any]] = []

    def add(
        name: str,
        source: str,
        *,
        precision: str = "",
        mapping: dict[str, str] | None = None,
        rules: list[dict[str, str]] | None = None,
        rationale: str,
    ) -> None:
        suggestions.append({
            "id": f"rule-{uuid.uuid4().hex[:12]}",
            "name": name,
            "source": source,
            "precision": precision,
            "mapping": mapping or {},
            "rules": rules or [],
            "rationale": rationale,
            "source_type": "rule_fallback",
            "status": "pending_review",
        })

    if any(token in idea for token in ("季节", "春夏秋冬")):
        add(
            "season",
            "month",
            precision="month",
            mapping={"3,4,5": "春季", "6,7,8": "夏季", "9,10,11": "秋季", "12,1,2": "冬季"},
            rationale="按月份把日期翻译成春、夏、秋、冬，适用于跨数据集的通用季节语义。",
        )

    if "季度" in idea:
        add(
            "quarter",
            "month",
            precision="month",
            mapping={"1,2,3": "Q1", "4,5,6": "Q2", "7,8,9": "Q3", "10,11,12": "Q4"},
            rationale="按月份生成季度标签，原始日期仍保留在中通时间入口。",
        )

    if any(token in idea for token in ("上半年", "下半年", "半年")):
        add(
            "half_year",
            "month",
            precision="month",
            mapping={"1,2,3,4,5,6": "上半年", "7,8,9,10,11,12": "下半年"},
            rationale="按月份把时间翻译为上半年或下半年，供基座编译为标准时间条件。",
        )

    if any(token in idea for token in ("单月", "双月", "奇数月", "偶数月")):
        add(
            "month_parity",
            "month",
            precision="month",
            mapping={"1,3,5,7,9,11": "单月", "2,4,6,8,10,12": "双月"},
            rationale="按月份奇偶生成单月/双月标签，原始年月仍保留在中通主干。",
        )

    if any(token in idea for token in ("工作日", "周末")):
        add(
            "weekday_type",
            "weekday",
            precision="day",
            mapping={"0,1,2,3,4": "工作日", "5,6": "周末"},
            rationale="按星期值区分工作日和周末，要求日期已经解析到日粒度。",
        )

    holiday_rules: list[dict[str, str]] = []
    if any(token in idea for token in ("双11", "双十一")):
        holiday_rules.append({"condition": "month=11 and day=11", "value": "双11"})
    if "黑色星期五" in idea:
        holiday_rules.append({"condition": "month=11 and day>=25 and day<=30", "value": "黑色星期五"})
    if "618" in idea:
        holiday_rules.append({"condition": "month=6 and day>=15 and day<=20", "value": "618大促"})
    if holiday_rules:
        add(
            "holiday",
            "date",
            precision="day",
            rules=holiday_rules,
            rationale="按日期条件标记节日或活动日；建议先确认日期范围，再加入规则表单。",
        )

    if any(token in idea for token in ("节气", "二十四节气")):
        preset = RuleLoader().get_rule("solar_term") or {}
        add(
            "solar_term",
            "date",
            precision="day",
            rules=list(preset.get("rules") or []),
            rationale="载入可编辑的二十四节气日期窗口预置；需要日粒度，天文时刻级精度应由业务日历确认。",
        )

    return suggestions


@router.get("")
def get_rules(
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.time_rules(None, context)
    loader = RuleLoader()
    return {
        "version": loader.version,
        "loaded_at": loader.loaded_at.isoformat(timespec="seconds")
        if loader.loaded_at
        else None,
        "rules": loader.rules,
        "yaml": RULES_FILE.read_text(encoding="utf-8"),
        **context.response_metadata(),
    }


def _sanitize_suggestions(raw: Any) -> list[dict[str, Any]]:
    """限制模型输出为规则编辑器可以安全展示的字段。"""
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    invalid_shape = False
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        source = str(item.get("source") or "").strip()
        if not name or not source:
            continue
        mapping: dict[str, str] = {}
        raw_mapping = item.get("mapping")
        if isinstance(raw_mapping, dict):
            for key, value in raw_mapping.items():
                key_text = str(key).strip()
                value_text = str(value).strip()
                if key_text and value_text:
                    if source in {"month", "weekday"}:
                        tokens = [token.strip() for token in key_text.split(",")]
                        if not tokens or any(not token.isdigit() for token in tokens):
                            invalid_shape = True
                    season_tokens = ("春季", "夏季", "秋季", "冬季", "季节")
                    if source == "month" and any(token in value_text for token in season_tokens) and (
                        "Q" in value_text or "季度" in value_text
                    ):
                        # 季节和季度必须拆成两条规则，避免一个值同时承担两个语义。
                        invalid_shape = True
                    mapping[key_text] = value_text
        conditions: list[dict[str, str]] = []
        raw_conditions = item.get("rules")
        if isinstance(raw_conditions, list):
            for condition in raw_conditions:
                if not isinstance(condition, dict):
                    continue
                condition_text = str(condition.get("condition") or "").strip()
                value_text = str(condition.get("value") or "").strip()
                if condition_text and value_text:
                    conditions.append({"condition": condition_text, "value": value_text})
        if not mapping and not conditions:
            continue
        result.append({
            "id": f"rule-{uuid.uuid4().hex[:12]}",
            "name": name,
            "source": source,
            "precision": str(item.get("precision") or "").strip(),
            "mapping": mapping,
            "rules": conditions,
            "rationale": str(item.get("rationale") or "模型建议，请结合字段粒度和业务口径确认").strip(),
            "source_type": str(item.get("source_type") or "ai").strip(),
            "status": "pending_review",
        })
    return [] if invalid_shape else result


@router.post("/analyze")
def analyze_rules(
    body: dict[str, Any] = Body(...),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict[str, Any]:
    """根据用户想法生成待人工确认的规则草稿，不直接修改当前规则。"""
    _require_mutable_rules()
    request_body = {
        "idea": str(body.get("idea") or "").strip(),
        "rules": body.get("rules") if isinstance(body.get("rules"), list) else [],
    }
    if not request_body["idea"]:
        raise BadRequestError("请输入需要分析的规则想法")

    settings = get_settings()
    suggestions: list[dict[str, Any]] = []
    backend = "rule_fallback"
    try:
        response = httpx.post(
            f"{settings.AGENT_BASE_URL.rstrip('/')}/api/v1/analyze/rules",
            json=request_body,
            timeout=65,
        )
        if response.is_success:
            payload = response.json()
            suggestions = _sanitize_suggestions(payload.get("suggestions"))
            if suggestions:
                backend = str(payload.get("backend") or "ai")
    except Exception as exc:
        logger.warning("规则建议 Agent 调用失败，使用规则回退：%s", exc)

    if not suggestions:
        suggestions = _rule_suggestions(request_body)
    return {
        "backend": backend,
        "suggestions": suggestions,
        "message": "建议已生成，仅作为待确认草稿；加入规则表单并保存后才会生效",
    }


@router.post("/reload")
def reload_rules(
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    _require_mutable_rules()
    loader = RuleLoader()
    loader.reload()
    return {"version": loader.version, "rules_count": len(loader.rules)}


@router.put("")
def update_rules(
    body: RuleUpdateRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    """热更新：校验 → 快照存档 → 落盘 → 重新加载（不停机）。"""
    _require_mutable_rules()
    try:
        parsed = yaml.safe_load(body.yaml_content)
    except yaml.YAMLError as e:
        raise BadRequestError(f"YAML 解析失败：{e}")
    if not isinstance(parsed, dict):
        raise BadRequestError("YAML 顶层必须是映射结构")

    loader = RuleLoader()
    snap_path = loader.snapshot()  # 旧版本先存档

    RULES_FILE.write_text(body.yaml_content, encoding="utf-8")
    try:
        loader.reload()
    except Exception:
        # 加载失败自动回滚到快照
        shutil.copy2(snap_path, RULES_FILE)
        loader.reload()
        raise
    return {"version": loader.version, "rules_count": len(loader.rules),
            "message": "规则已热更新并生效，无需重启"}


@router.post("/rollback")
def rollback_rules(
    version: int,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    _require_mutable_rules()
    snapshot = SNAPSHOT_DIR / f"time_derivation_rules_v{version}.yaml"
    if not snapshot.exists():
        raise NotFoundError("目标版本快照不存在", str(snapshot))
    RuleLoader().snapshot()  # 当前版本存档
    shutil.copy2(snapshot, RULES_FILE)
    loader = RuleLoader()
    loader.reload()
    return {"version": loader.version, "message": f"已回滚到版本 {version}"}


def _last_snapshot() -> Path:
    snaps = sorted(SNAPSHOT_DIR.glob("time_derivation_rules_v*.yaml"))
    if not snaps:
        raise BadRequestError("无可用快照，回滚失败")
    return snaps[-1]

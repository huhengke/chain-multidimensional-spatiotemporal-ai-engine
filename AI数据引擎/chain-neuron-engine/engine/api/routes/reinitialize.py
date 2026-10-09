# -*- coding: utf-8 -*-
"""安全的数据重初始化接口。

preview/analyze 只读；execute 必须由管理员显式发送固定确认文本，先备份后清空
引擎业务数据并按根目录数据源重新导入。该接口不删除账号、模型配置或数据集声明。
"""
from __future__ import annotations

import shutil
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from engine.core.config import DATA_DIR, DB_PATH, WORKSPACE_ROOT, get_settings
from engine.core.errors import BadRequestError
from engine.features.registry import (
    create_feature_proposals,
    list_proposals,
    profile_dataset,
    sync_product_crosswalk,
)
from engine.ingestion.excel_reader import probe_file
from engine.ingestion.connectors.base import create_connector
from engine.ingestion.datasource_config import get_datasource_config
from engine.ingestion.field_recommendations import catalog_status
from engine.ingestion.models import ImportTask, ST_PENDING, ST_RUNNING
from engine.ingestion.service import get_ingestion_service
from engine.ingestion.source_profiler import apply_ai_suggestions, profile_records
from engine.model_config import public_model_config
from engine.runtime_profiles.guards import require_mutable_legacy
from engine.security.deps import allow_user_or_service, get_db, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.dataset_registry import DatasetSpec, registry
from engine.storage.dynamic_table import get_dynamic_dim_table, get_dynamic_table
from engine.storage.dynamic_table import drop_dim_table, drop_dynamic_table
from engine.storage.fact_tables import get_dim_source, get_fact_source
from engine.storage.database import engine as db_engine
from engine.storage.models import FeatureProposal, ProductCrosswalk, SpiralNodeORM
from engine.storage.maintenance import compact_sqlite_database, prune_database_backups
from engine.storage.spiral_json import purge_all_dataset_json, purge_dataset_json

router = APIRouter(prefix="/api/v1/engine/reinitialize", tags=["reinitialize"])
STATE_PATH = DATA_DIR / "reinitialize_state.json"
_ingestion_service = get_ingestion_service()


def _state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"status": "not_started"}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown"}


def _write_state(payload: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _declared_specs() -> list[DatasetSpec]:
    """全部声明，包括已停用基座；用于预览、迁移和清理。"""
    return list(registry.all_specs().values())


def _clear_dataset_rows(session: Session, spec: DatasetSpec) -> int:
    """清空一个数据集的记录，允许物理表已经被注销流程删除。"""
    try:
        if spec.is_spiral:
            return int(get_fact_source(spec.id).delete_all(session) or 0)
        table = get_dynamic_dim_table(spec)
        return int(session.execute(table.delete()).rowcount or 0)
    except OperationalError:
        return 0


def _clear_shared_runtime(session: Session) -> dict[str, int]:
    """清除跨数据集共享的旧运行态，但保留账号、模型配置和经验库。"""
    models = (ProductCrosswalk, FeatureProposal, ImportTask)
    cleared: dict[str, int] = {}
    for model in models:
        cleared[model.__tablename__] = int(session.execute(delete(model)).rowcount or 0)
    cleared[SpiralNodeORM.__tablename__] = int(
        session.execute(delete(SpiralNodeORM)).rowcount or 0
    )
    return cleared


def _source_check(spec: DatasetSpec) -> dict[str, Any]:
    """只读检查一个 Excel/数据库来源是否可用于重新注册。"""
    matched = _match_source(spec)
    if isinstance(matched, dict):
        connector = None
        try:
            config = get_datasource_config(str(matched.get("datasource_id") or ""))
            connector = create_connector(config)
            columns = connector.describe_table(str(matched.get("table") or ""))
            return {
                "dataset": spec.id,
                "kind": "database",
                "source": matched,
                "ok": bool(columns),
                "detail": f"数据库表可读取，字段 {len(columns)} 个",
            }
        except Exception as exc:
            return {
                "dataset": spec.id,
                "kind": "database",
                "source": matched,
                "ok": False,
                "detail": str(exc)[:300],
            }
        finally:
            if connector is not None:
                try:
                    connector.close()
                except Exception:
                    pass
    if matched:
        return {
            "dataset": spec.id,
            "kind": "excel",
            "source": str(matched),
            "ok": True,
            "detail": f"文件存在：{matched.name}",
        }
    return {
        "dataset": spec.id,
        "kind": "excel",
        "source": spec.source_file,
        "ok": False,
        "detail": "未匹配到可导入的 Excel 文件",
    }


def _state_is_stale(state: dict[str, Any]) -> bool:
    """判断上一次重初始化状态是否已经被后续人工注册/导入覆盖。

    仅当当前注册表出现了上次清理预期之外的数据集时，才允许开启新的
    受控清空；同一轮清理完成后仍然保持门禁，避免误点重复删除。
    """
    if state.get("status") != "awaiting_manual_registration":
        return False
    expected = {
        str(item.get("dataset") or "")
        for item in (state.get("expected_datasets") or [])
        if isinstance(item, dict) and str(item.get("dataset") or "").strip()
    }
    current = set(registry.all_specs())
    return bool(current - expected)


def _readiness() -> dict[str, Any]:
    """注销全部前的只读门禁，不修改注册表和业务数据。"""
    catalog = catalog_status()
    specs = _declared_specs()
    sources = [_source_check(spec) for spec in specs]
    tasks = _ingestion_service.list_tasks(limit=100)
    running_tasks = [
        task.task_uuid for task in tasks
        if task.status in {ST_PENDING, ST_RUNNING}
        and _ingestion_service.is_running(task.task_uuid)
    ]
    stale_tasks = [
        task.task_uuid for task in tasks
        if task.status in {ST_PENDING, ST_RUNNING}
        and not _ingestion_service.is_running(task.task_uuid)
    ]
    state = _state()

    from engine.core.config import get_settings
    from engine.model_config import public_model_config

    settings = get_settings()
    model = public_model_config()
    active_id = str(model.get("active") or "")
    active_provider = next(
        (item for item in model.get("providers", []) if str(item.get("id")) == active_id),
        {},
    )
    agent_reachable = False
    agent_error = ""
    try:
        response = httpx.get(f"{settings.AGENT_BASE_URL.rstrip('/')}/healthz", timeout=2)
        agent_reachable = response.is_success
        if not agent_reachable:
            agent_error = f"Agent HTTP {response.status_code}"
    except Exception as exc:
        agent_error = str(exc)[:200]
    model_ready = bool(active_provider.get("api_key_configured")) and agent_reachable

    blockers: list[str] = []
    if not catalog.get("ok"):
        blockers.append(f"经验推荐库不可用：{catalog.get('error')}")
    for source in sources:
        if not source.get("ok"):
            blockers.append(f"数据集 {source['dataset']} 来源不可用：{source['detail']}")
    if running_tasks:
        blockers.append(f"存在运行中的导入任务：{', '.join(running_tasks[:5])}")
    state_stale = _state_is_stale(state)
    if state.get("status") in {"awaiting_manual_import", "awaiting_manual_registration", "completed"} and not state_stale:
        blockers.append(f"已有重初始化状态：{state.get('status')}，不能重复开始")
    elif state.get("status") == "started" and state.get("cleared_at"):
        blockers.append("已有重初始化清理结果，当前等待人工重建，不能重复开始")
    if not model_ready:
        blockers.append("当前模型通道尚未通过运行态检查；可先运行 scripts/test_model_channel.py，或明确接受规则回退")

    return {
        "ready": not blockers,
        "blockers": blockers,
        "catalog": catalog,
        "model": {
            "active": active_id,
            "provider": active_provider.get("name") or active_id,
            "api_key_configured": bool(active_provider.get("api_key_configured")),
            "agent_reachable": agent_reachable,
            "agent_error": agent_error or None,
            "ready": model_ready,
        },
        "datasets": sources,
        "running_import_tasks": running_tasks,
        "stale_import_tasks": stale_tasks,
        "reinitialize_state": {**state, "stale": state_stale},
        "dataset_count": len(specs),
        "mode": "unregister_all_reimport",
    }


def _create_field_proposals(session: Session, datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把每个数据源的字段画像固化为待确认提案，不直接改变注册契约。"""
    created: list[dict[str, Any]] = []
    existing = session.execute(
        select(FeatureProposal).where(FeatureProposal.feature_type.in_(["field_role", "base_feature_candidate"]))
    ).scalars().all()
    existing_keys = {
        (row.dataset, str((row.payload or {}).get("source") or ""), row.feature_type)
        for row in existing
    }
    role_labels = {
        "time": "时间特征",
        "dimension": "维度特征",
        "metric": "指标特征",
        "ignore": "忽略字段",
    }
    for item in datasets:
        profile = item.get("profile") or {}
        dataset = str(item.get("dataset") or "")
        for column in profile.get("columns") or []:
            source = str(column.get("source") or "").strip()
            role = str(column.get("guess_role") or "other").strip()
            if not dataset or not source or role not in role_labels:
                continue
            feature_type = "base_feature_candidate" if column.get("base_candidate") else "field_role"
            key = (dataset, source, feature_type)
            if key in existing_keys:
                continue
            confidence = Decimal("0.90") if column.get("suggestion_source") == "ai" else Decimal("0.65")
            payload = {
                "source": source,
                "role": role,
                "feature_role": column.get("feature_role") or role,
                "role_label": role_labels[role],
                "semantic_type": column.get("semantic_type") or "other",
                "base_candidate": bool(column.get("base_candidate")),
                "feature_layer": column.get("feature_layer") or "none",
                "query_uses": column.get("query_uses") or [],
                "suggested_name": column.get("guess_name") or source,
                "suggested_label": column.get("guess_label") or source,
                "suggestion_source": column.get("suggestion_source") or "rule_fallback",
                "suggestion_reason": column.get("suggestion_reason") or "字段画像建议",
                "knowledge_matches": column.get("knowledge_matches") or [],
                "knowledge_confidence": column.get("knowledge_confidence"),
                "ai_conflict": bool(column.get("ai_conflict")),
                "needs_manual_review": bool(column.get("needs_manual_review")),
                "analysis_version": column.get("analysis_version") or "rule-only",
            }
            proposal = FeatureProposal(
                proposal_id=f"field-{dataset}-{source}"[:64],
                feature_type=feature_type,
                dataset=dataset,
                status="pending_review",
                payload=payload,
                evidence={
                    "analysis_source": profile.get("analysis_source") or "rule_fallback",
                    "non_null": column.get("non_null"),
                    "numeric_ratio": column.get("numeric_ratio"),
                    "time_ratio": column.get("time_ratio"),
                    "unique_ratio": column.get("unique_ratio"),
                    "feature_layer": column.get("feature_layer") or "none",
                    "query_uses": column.get("query_uses") or [],
                    "sample_values": (column.get("evidence") or {}).get("sample_values") or [column.get("sample")],
                },
                confidence=confidence,
            )
            # 同一字段既是基座候选又有字段角色时，proposal_id 不能冲突。
            if session.get(FeatureProposal, proposal.proposal_id) is not None:
                proposal.proposal_id = f"field-{dataset}-{source}-base"[:64]
            session.add(proposal)
            existing_keys.add(key)
            created.append({
                "proposal_id": proposal.proposal_id,
                "feature_type": feature_type,
                "dataset": dataset,
                "status": proposal.status,
                "payload": payload,
                "confidence": format(confidence, "f"),
            })
    return created


def _match_source(spec: DatasetSpec) -> Path | dict[str, Any] | None:
    if spec.source.get("kind") == "database":
        return dict(spec.source)
    if spec.source_file:
        path = WORKSPACE_ROOT / spec.source_file
        if path.exists():
            return path
    candidates = sorted(WORKSPACE_ROOT.glob("*.xlsx"))
    if not candidates:
        return None
    alias = {
        "non_oil_sales": ("业务域乙", "线上商城"),
        "oil_master": ("产品基础",),
    }.get(spec.id, tuple(filter(None, (spec.label, spec.id))))
    for candidate in candidates:
        stem = candidate.stem
        if any(token and token in stem for token in alias):
            return candidate
    return None


def _current_count(session: Session, spec: DatasetSpec) -> int:
    try:
        if spec.is_spiral:
            return get_fact_source(spec.id).count(session)
        table = get_dynamic_dim_table(spec)
        return int(session.execute(select(func.count()).select_from(table)).scalar_one())
    except OperationalError:
        return 0


def _preview(session: Session, *, include_probe: bool, include_proposals: bool) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for spec in _declared_specs():
        path = _match_source(spec)
        source_is_database = isinstance(path, dict)
        item: dict[str, Any] = {
            "dataset": spec.id,
            "label": spec.label,
            "kind": spec.kind,
            "source_file": path.get("table") if source_is_database else path.name if path else spec.source_file or None,
            "source_exists": bool(path),
            "source_kind": "database" if source_is_database else "excel",
            "current_rows": _current_count(session, spec),
            "capabilities": spec.capabilities,
        }
        if include_probe and path:
            if source_is_database:
                try:
                    config = get_datasource_config(str(path.get("datasource_id") or ""))
                    connector = create_connector(config)
                    try:
                        table = str(path.get("table") or "")
                        columns = connector.describe_table(table)
                        rows = list(connector.fetch_rows(table, limit=200))
                    finally:
                        connector.close()
                    item["profile"] = profile_records(
                        columns, rows, source_name=f"{path.get('datasource_id')}:{path.get('table')}",
                        sheet=table, scope=spec.kind,
                    )
                except Exception as exc:
                    warnings.append(f"数据集 {spec.id} 数据库字段分析失败：{exc}")
            else:
                probe = apply_ai_suggestions(probe_file(path), source_name=path.name)
                item["profile"] = probe
        if include_proposals:
            item["engine_profile"] = profile_dataset(session, spec.id)
        if not path:
            warnings.append(f"数据集 {spec.id} 未在根目录匹配到可导入 .xlsx 文件")
        items.append(item)
    return {"model": public_model_config(), "datasets": items, "warnings": warnings,
            "reinitialize_state": _state()}


@router.get("/preview")
def preview(
    session: Session = Depends(get_db),
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    require_mutable_legacy("重初始化流程")
    return _preview(session, include_probe=False, include_proposals=False)


@router.get("/readiness")
def readiness(
    _identity: dict = Depends(allow_user_or_service),
) -> dict:
    """注销全部前的只读检查；不会创建快照，也不会触碰业务数据。"""
    require_mutable_legacy("重初始化流程")
    return _readiness()


@router.post("/analyze")
def analyze(
    session: Session = Depends(get_db),
    _identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    require_mutable_legacy("重初始化流程")
    # 先同步现有数据的可证据化特征，再生成待审批提案；全过程不删除任何数据。
    sync_product_crosswalk(session)
    create_feature_proposals(session)
    body = _preview(session, include_probe=True, include_proposals=True)
    field_proposals = _create_field_proposals(session, body["datasets"])
    session.commit()
    body["proposals"] = list_proposals(session)
    body["field_proposals_created"] = field_proposals
    body["analysis_mode"] = "profile_and_propose"
    return body


@router.post("/execute")
def execute(
    body: dict[str, Any] = Body(...),
    session: Session = Depends(get_db),
    _identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    require_mutable_legacy("重初始化流程")
    if body.get("confirmation") != "CLEAR_AND_REIMPORT":
        raise BadRequestError(
            "重初始化需要明确确认文本",
            detail={"required_confirmation": "CLEAR_AND_REIMPORT"},
        )
    readiness_result = _readiness()
    if not readiness_result["ready"]:
        allow_missing_sources = bool(body.get("allow_missing_sources"))
        non_source_blockers = [
            blocker for blocker in readiness_result["blockers"]
            if not (blocker.startswith("数据集 ") and "来源不可用" in blocker)
        ]
        if not allow_missing_sources or non_source_blockers:
            raise BadRequestError(
                "注销全部前置检查未通过",
                detail=readiness_result,
            )
    state = _state()
    if state.get("status") in {"completed", "awaiting_manual_import", "awaiting_manual_registration"} and not _state_is_stale(state):
        raise BadRequestError(
            "一次性架构升级验收已经启动、等待人工导入或完成，禁止重复清库；如需再次执行请清理状态并重新备份",
            detail={"state": state},
        )
    if state.get("status") == "started" and state.get("cleared_at"):
        raise BadRequestError(
            "一次性架构升级清理已完成，当前等待人工重建，禁止重复清库",
            detail={"state": state},
        )

    sources = [(spec, _match_source(spec)) for spec in _declared_specs()]

    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"engine-before-reinitialize-{datetime.now():%Y%m%d-%H%M%S}.db"
    declaration_snapshot = registry.snapshot()
    backup_cleanup: dict[str, Any] = {
        "keep_count": get_settings().REINITIALIZE_BACKUP_KEEP_COUNT,
        "retained": [],
        "deleted": [],
        "deleted_bytes": 0,
    }
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_path)
        backup_cleanup = prune_database_backups(
            backup_dir,
            pattern="engine-before-reinitialize-*.db",
            keep_count=get_settings().REINITIALIZE_BACKUP_KEEP_COUNT,
            protected={backup_path},
        )
    _write_state({
        "status": "started",
        "mode": "unregister_all_reimport",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "backup_path": str(backup_path),
        "backup_cleanup": backup_cleanup,
        "declaration_snapshot": str(declaration_snapshot) if declaration_snapshot else None,
        "expected_datasets": [
            {
                "dataset": spec.id,
                "label": spec.label,
                "kind": spec.kind,
                "declared_source": spec.source,
                "matched_source": str(path) if isinstance(path, Path) else path,
            }
            for spec, path in sources
        ],
        "next_step": "恢复基座数据集或重新注册界面数据集，逐个执行 AI 字段分析、人工确认特征契约后，再从数据集管理发起导入",
    })

    # 注销全部：先清业务数据、共享运行态和物化文件，再删除界面注册声明；
    # 基座声明保留并进入可恢复停用状态，但不再出现在主列表。
    interface_ids = [spec.id for spec, _path in sources if spec.id in registry.interface_specs]
    base_ids = [spec.id for spec, _path in sources if spec.id in registry.base_specs]
    cleared_rows: dict[str, int] = {}
    purged_json: dict[str, int] = {}
    for spec, _path in sources:
        cleared_rows[spec.id] = _clear_dataset_rows(session, spec)
        purged_json[spec.id] = purge_dataset_json(spec.id)
    shared_cleared = _clear_shared_runtime(session)
    # 兼容曾经被注销声明删除、但仍遗留在文件系统中的数据集目录。
    for dataset, file_count in purge_all_dataset_json().items():
        purged_json.setdefault(dataset, file_count)

    # 先提交 ORM 删除，再通过独立连接执行 DDL。SQLite 的写事务在
    # session.commit() 前仍持有锁，若此时跨连接 DROP TABLE，会触发
    # "database is locked"，导致清理停在半完成状态。
    session.commit()

    for spec, _path in sources:
        if spec.id in interface_ids:
            if spec.kind == "dimension":
                drop_dim_table(spec, db_engine)
            else:
                drop_dynamic_table(spec, db_engine)

    # 注册表变更必须在业务数据提交后进行，避免清理过程中读取到已移除的动态声明。
    for dataset in interface_ids:
        registry.remove(dataset)
    for dataset in base_ids:
        registry.disable_base(dataset)

    # 大批 DELETE/DROP 只会把 SQLite 页放入 freelist，不会把文件空间还给操作系统。
    # 业务清理已经提交，VACUUM 失败不回滚清理，但会在状态中留下可审计错误。
    storage_compaction = compact_sqlite_database(db_engine, DB_PATH)

    _write_state({
        **_state(),
        "status": "awaiting_manual_registration",
        "cleared_at": datetime.now().isoformat(timespec="seconds"),
        "unregistered_interface_datasets": interface_ids,
        "disabled_base_datasets": base_ids,
        "cleared_rows": cleared_rows,
        "cleared_shared_runtime": shared_cleared,
        "purged_json_files": purged_json,
        "storage_compaction": storage_compaction,
        "next_step": "恢复基座数据集或重新注册界面数据集，然后使用经验库+AI+规则完成字段分析、确认特征并导入",
    })

    return {
        "message": "已完成注销全部并清空业务数据；现在进入人工重新注册/恢复、AI 字段分析、特征确认和导入阶段，不会自动导入旧声明",
        "backup_path": str(backup_path),
        "backup_cleanup": backup_cleanup,
        "storage_compaction": storage_compaction,
        "tasks": [],
        "expected_datasets": [spec.id for spec, _ in sources],
        "state": _state(),
    }

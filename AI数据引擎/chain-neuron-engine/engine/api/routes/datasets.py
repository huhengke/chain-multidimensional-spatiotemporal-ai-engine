# -*- coding: utf-8 -*-
"""数据集注册路由：查看 / 探测 / 注册 / 删除 / 导入 / 重算 / 浏览。

公开数据集类型只有通用事实与维表/主数据；来源只有基座数据与界面注册。
所有事实数据均由注册表声明驱动，不保留专用 ORM/Mapper 管线。
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.config import BLOBS_DIR
from engine.core.errors import AppException, BadRequestError, NotFoundError
from engine.core.logging import get_logger
from engine.features.contracts import build_dataset_contract
from engine.features.structure import build_structure_list_summary, build_structure_summary, rebuild_plan
from engine.api.schemas_api import RebuildRequest
from engine.ingestion.cleaner import TIME_FORMATS
from engine.ingestion.excel_reader import probe_file, slugify_name
from engine.ingestion.connectors.base import create_connector
from engine.ingestion.datasource_config import get_datasource_config
from engine.ingestion.models import MODE_DEDUPE, VALID_MODES
from engine.ingestion.service import get_ingestion_service
from engine.ingestion.source_profiler import apply_ai_suggestions, profile_records
from engine.query.experience import actual_sales_scope
from engine.runtime_profiles.errors import RuntimeProfileError
from engine.runtime_profiles.resolver import resolve_active_profile
from engine.security.deps import allow_user_or_service, get_db, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.database import (
    DecimalString,
    SessionLocal,
    engine as db_engine,
    ensure_columns,
)
from engine.storage.dataset_registry import (
    KIND_DIMENSION,
    KIND_GENERIC,
    dump_spec,
    parse_spec,
    registry,
)
from engine.storage.dynamic_table import (
    DIM_COLUMN_LENGTH,
    RESERVED_FACT_COLUMNS,
    drop_dim_table,
    drop_dynamic_table,
    get_dynamic_dim_table,
    get_dynamic_table,
)
from engine.storage.fact_tables import get_fact_source
from engine.storage.models import SpiralNodeORM
from engine.storage.adapters.registry import storage_adapters
from engine.storage.spiral_json import purge_dataset_json
from engine.storage.spiral_store import SpiralStore

router = APIRouter(prefix="/api/v1/engine/datasets", tags=["datasets"])
logger = get_logger("api.datasets")

_service = get_ingestion_service()

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,59}$")
# 可由时间自动派生的记录字段（与 TimeDerivationEngine 一致）
# day 只在日粒度时间格式（cleaner.DAY_LEVEL_FORMATS）下才有值。
DERIVE_ALLOWED = (
    "year", "month", "yyyymm", "season", "quarter", "week", "day",
    "half_year", "month_parity", "solar_term",
)


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


def _require_legacy_management() -> None:
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "当前活动配置为 CNTS 不可变 Catalog；注册、导入、注销和重建必须先生成新的候选 Catalog，不能修改活动 Generation。",
            detail={
                **context.response_metadata(),
                "action": "build_new_candidate_catalog",
                "fallback": "forbidden",
            },
        )


# ---------------- 文件与命名校验 ----------------


def _save_upload(file: UploadFile) -> Path:
    """校验并落盘上传文件，返回 BLOBS_DIR 内路径。"""
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise BadRequestError("仅支持 .xlsx 文件")
    BLOBS_DIR.mkdir(parents=True, exist_ok=True)
    target = BLOBS_DIR / Path(file.filename).name
    target.write_bytes(file.file.read())
    return target


def _resolve_token(token: str) -> Path:
    """把探测阶段返回的 file_token 还原为文件路径（限制在 BLOBS_DIR 内）。"""
    name = Path(str(token)).name
    target = (BLOBS_DIR / name).resolve()
    if BLOBS_DIR.resolve() not in target.parents or not target.exists():
        raise NotFoundError("上传文件已失效，请重新上传", str(token))
    return target


def _check_id(dataset: str) -> str:
    if not ID_RE.match(dataset or ""):
        raise BadRequestError(
            "数据集 id 必须为小写字母开头的英文标识（可含数字/下划线，≤40 位）",
            detail={"id": dataset},
        )
    return dataset


def _check_name(name: str, what: str) -> str:
    if not NAME_RE.match(name or ""):
        raise BadRequestError(
            f"{what}必须为英文标识（小写字母/下划线开头，可含数字，≤60 位）",
            detail={"name": name},
        )
    return name


def _check_not_reserved(name: str, what: str) -> str:
    """拒绝引擎保留列名（动态表代理主键），避免建表时 DuplicateColumnError。"""
    if name in RESERVED_FACT_COLUMNS:
        raise BadRequestError(
            f"{what} {name} 是引擎保留列名，请改用其他物理列名",
            detail={"column": name, "reserved": sorted(RESERVED_FACT_COLUMNS),
                    "hint": "动态事实表固定有自增主键 id；在向导中把该列的"
                            "「物理列名」改成别的名字（如 order_id）即可继续"},
        )
    return name


# ---------------- 声明构造（向导草案 → 注册表声明） ----------------


def _build_declaration(dataset: str, draft: dict) -> dict:
    """把界面向导提交的草案规范化为注册表声明（物理表名/列名在此定稿）。"""
    kind = str(draft.get("kind") or KIND_GENERIC).strip()
    if kind not in (KIND_GENERIC, KIND_DIMENSION):
        raise BadRequestError(
            f"界面注册仅支持 generic / dimension，收到：{kind}",
            detail={"valid": [KIND_GENERIC, KIND_DIMENSION]},
        )
    label = str(draft.get("label") or "").strip() or dataset
    out: dict = {"id": dataset, "kind": kind, "label": label}
    if draft.get("source_file"):
        out["source_file"] = str(draft["source_file"])
    if isinstance(draft.get("source"), dict):
        source = dict(draft["source"])
        source_kind = str(source.get("kind") or "excel")
        if source_kind not in {"excel", "database"}:
            raise BadRequestError("数据来源 kind 必须为 excel 或 database")
        if source_kind == "database" and not source.get("datasource_id"):
            raise BadRequestError("数据库来源缺少 datasource_id")
        if source_kind == "database" and not source.get("table"):
            raise BadRequestError("数据库来源缺少 table")
        out["source"] = source
    if isinstance(draft.get("capabilities"), dict):
        out["capabilities"] = dict(draft["capabilities"])

    body = _build_dimension(dataset, draft) if kind == KIND_DIMENSION else _build_generic(dataset, draft)
    base = _build_base(draft, body)
    raw_feature_layers = draft.get("feature_layers")
    if raw_feature_layers is None and isinstance(draft.get("capabilities"), dict):
        raw_feature_layers = draft["capabilities"].get("feature_layers")
    feature_layers = _build_feature_layers(raw_feature_layers, body)
    if feature_layers:
        capabilities = dict(out.get("capabilities") or {})
        capabilities["feature_layers"] = feature_layers
        out["capabilities"] = capabilities
    return {**out, **body, **({"base": base} if base else {})}


def _build_feature_layers(raw: object, declaration: dict) -> dict:
    """校验并保存通用的基座/主干/分枝特征计划，不绑定某个行业。"""
    if not isinstance(raw, dict):
        return {}
    valid_layers = ("base", "trunk", "branch")
    valid_roles = ("time", "dimension", "metric", "ignore")
    valid_uses = {"filter", "group_by", "aggregate", "sort", "join", "compare"}
    if "columns" in declaration:
        available = {str(col.get("source")) for col in declaration.get("columns") or []}
        available.update(str(col.get("name")) for col in declaration.get("columns") or [])
    else:
        available = set(str(value) for value in (declaration.get("column_sources") or {}).values())
        available.update(str(key) for key in (declaration.get("column_sources") or {}).keys())
        if declaration.get("time", {}).get("source"):
            available.add(str(declaration["time"]["source"]))

    normalized: dict[str, list[dict]] = {layer: [] for layer in valid_layers}
    seen_by_layer: dict[str, set[str]] = {layer: set() for layer in valid_layers}
    for layer in valid_layers:
        items = raw.get(layer) or []
        if not isinstance(items, list):
            raise BadRequestError(f"feature_layers.{layer} 必须为列表")
        for item in items:
            if not isinstance(item, dict):
                raise BadRequestError(f"feature_layers.{layer} 存在非法字段项")
            source = str(item.get("source") or "").strip()
            if not source:
                raise BadRequestError(f"feature_layers.{layer} 字段缺少 source")
            if source not in available:
                raise BadRequestError(
                    f"特征计划字段 {source} 不在当前数据集声明中",
                    detail={"available_sources": sorted(available)},
                )
            if source in seen_by_layer[layer]:
                raise BadRequestError(f"feature_layers.{layer} 特征计划字段重复：{source}")
            role = str(item.get("role") or "dimension").strip()
            if role not in valid_roles:
                raise BadRequestError(f"特征计划字段 {source} 的 role 非法")
            semantic_type = str(item.get("semantic_type") or "other").strip()
            if layer == "base" and (role == "time" or semantic_type == "time"):
                raise BadRequestError(
                    f"基座特征计划字段 {source} 不能使用时间字段；时间字段应归入中通主干"
                )
            uses = item.get("query_uses") or []
            if not isinstance(uses, list):
                raise BadRequestError(f"特征计划字段 {source} 的 query_uses 必须为列表")
            unknown_uses = sorted(set(str(value) for value in uses) - valid_uses)
            if unknown_uses:
                raise BadRequestError(
                    f"特征计划字段 {source} 的 query_uses 非法",
                    detail={"unknown": unknown_uses, "valid": sorted(valid_uses)},
                )
            seen_by_layer[layer].add(source)
            normalized[layer].append({
                "source": source,
                "name": str(item.get("name") or source),
                "label": str(item.get("label") or source),
                "role": role,
                "semantic_type": semantic_type,
                "query_uses": [str(value) for value in uses],
                "recommended": bool(item.get("recommended", False)),
            })

    dna_keys = raw.get("dna_keys") or []
    if not isinstance(dna_keys, list):
        raise BadRequestError("feature_layers.dna_keys 必须为列表")
    dna_keys = [str(value).strip() for value in dna_keys if str(value).strip()]
    confirmed = set().union(*(seen_by_layer[layer] for layer in valid_layers))
    unknown_dna = sorted(set(dna_keys) - confirmed)
    if unknown_dna:
        raise BadRequestError(
            "feature_layers.dna_keys 必须来自已确认的基座/主干/分枝特征",
            detail={"unknown": unknown_dna, "confirmed": sorted(confirmed)},
        )
    dna_keys = list(dict.fromkeys(dna_keys))
    return {
        **normalized,
        "dna_keys": dna_keys,
        "analysis_version": str(raw.get("analysis_version") or "universal-field-review-v1"),
        "reviewed": bool(raw.get("reviewed", False)),
    }


def _build_base(draft: dict, declaration: dict) -> dict:
    """规范化可选基座声明；基座是数据源属性，不绑定某一种业务数据。"""
    raw = draft.get("base")
    if not isinstance(raw, dict) or not bool(raw.get("enabled")):
        return {}
    semantic_types = {
        "product_code", "product_name", "alias", "category", "org_code", "org_name", "time", "other",
    }
    if "columns" in declaration and "dim_table" in declaration:
        columns = declaration.get("columns") or []
        physical_by_source = {str(c["source"]): str(c["name"]) for c in columns}
        labels = {str(c["source"]): str(c.get("label") or c["source"]) for c in columns}
    else:
        column_sources = declaration.get("column_sources") or {}
        physical_by_source = {str(source): str(name) for name, source in column_sources.items()}
        time_cfg = declaration.get("time") or {}
        time_source = str(time_cfg.get("source") or "")
        if time_source:
            physical_by_source.setdefault(time_source, time_source)
        labels = {source: source for source in physical_by_source}
    features: list[dict] = []
    seen: set[str] = set()
    for feature in raw.get("features") or []:
        if not isinstance(feature, dict):
            continue
        source = str(feature.get("source") or "").strip()
        if not source or source in seen:
            continue
        if source not in physical_by_source:
            raise BadRequestError(
                f"基座特征字段 {source} 不在当前数据集声明中",
                detail={"available_sources": sorted(physical_by_source)},
            )
        semantic_type = str(feature.get("semantic_type") or "other").strip()
        if semantic_type == "time":
            raise BadRequestError(
                f"基座特征 {source} 不能使用时间字段；时间序列应归入通用事实的中通主干"
            )
        if semantic_type not in semantic_types:
            raise BadRequestError(
                f"基座特征 {source} 的 semantic_type 非法",
                detail={"valid": sorted(semantic_types)},
            )
        seen.add(source)
        features.append({
            "source": source,
            "name": str(feature.get("name") or physical_by_source[source]),
            "label": str(feature.get("label") or labels.get(source) or source),
            "semantic_type": semantic_type,
        })
    if not features:
        raise BadRequestError("已选择基座数据源，但至少需要确认一个基座特征字段")
    return {"enabled": True, "features": features}


def _build_dimension(dataset: str, draft: dict) -> dict:
    columns_in = draft.get("columns") or []
    if not columns_in:
        raise BadRequestError("维表数据集至少需要声明一列")
    columns: list[dict] = []
    seen: set[str] = set()
    for i, col in enumerate(columns_in):
        source = str(col.get("source") or "").strip()
        if not source:
            raise BadRequestError("维表列缺少 source（Excel 表头）")
        name = str(col.get("name") or "").strip() or slugify_name(source, f"col{i + 1}")
        _check_name(name, f"维表列 {source} 的物理列名")
        if name in seen:
            raise BadRequestError(f"维表物理列名重复：{name}")
        seen.add(name)
        columns.append({
            "source": source,
            "name": name,
            "label": str(col.get("label") or source).strip(),
        })

    primary_key = str(draft.get("primary_key") or "").strip()
    if not primary_key:
        raise BadRequestError("维表数据集必须指定主键列")
    if primary_key not in {c["source"] for c in columns} | {c["name"] for c in columns}:
        raise BadRequestError(
            f"主键 {primary_key} 不在列声明中",
            detail={"columns": [c["source"] for c in columns]},
        )
    return {"dim_table": f"dim_{dataset}", "primary_key": primary_key,
            "columns": columns}


def _build_generic(dataset: str, draft: dict) -> dict:
    time_cfg = draft.get("time") or {}
    time_source = str(time_cfg.get("source") or "").strip()
    time_format = str(time_cfg.get("format") or "yyyymm").strip().lower()
    if not time_source:
        raise BadRequestError("通用数据集必须指定时间列")
    if time_format not in TIME_FORMATS:
        raise BadRequestError(
            f"时间格式非法：{time_format}", detail={"valid": list(TIME_FORMATS)}
        )

    levels_in = draft.get("levels") or []
    if not levels_in:
        raise BadRequestError("通用数据集至少需要一个层级")

    fact_columns: list[str] = []
    level_keys: list[str] = []          # 层级记录键（默认指纹）
    level_value_key: dict[str, str] = {}
    level_labels: dict[str, str] = {}
    column_sources: dict[str, str] = {}
    derive: list[str] = []

    def _push(record_key: str) -> None:
        if record_key not in fact_columns:
            fact_columns.append(record_key)

    for lv in levels_in:
        key = str(lv.get("key") or "").strip()
        if not key:
            raise BadRequestError("层级缺少 key（层级标识）")
        _check_name(key, f"层级标识 {key}")
        if key in level_labels:
            raise BadRequestError(f"层级标识重复：{key}")
        level_labels[key] = str(lv.get("label") or key).strip() or key

        if lv.get("derive"):
            record_key = str(lv.get("key_of") or key).strip()
            if record_key not in DERIVE_ALLOWED:
                raise BadRequestError(
                    f"层级 {key} 的派生字段不支持：{record_key}",
                    detail={"valid": list(DERIVE_ALLOWED)},
                )
            if record_key not in derive:
                derive.append(record_key)
        else:
            source = str(lv.get("source") or "").strip()
            if not source:
                raise BadRequestError(f"层级 {key} 缺少 source（Excel 表头）")
            record_key = str(lv.get("name") or "").strip() or slugify_name(
                source, f"col{len(fact_columns) + 1}"
            )
            _check_name(record_key, f"层级 {key} 的物理列名")
            _check_not_reserved(record_key, f"层级 {key} 的物理列名")
            if record_key in fact_columns:
                raise BadRequestError(f"物理列名重复：{record_key}")
            column_sources[record_key] = source

        level_value_key[key] = record_key
        level_keys.append(record_key)
        _push(record_key)

    metrics: list[dict] = []
    for m in draft.get("metrics") or []:
        source = str(m.get("source") or "").strip()
        name = str(m.get("name") or "").strip() or slugify_name(
            source, f"metric{len(metrics) + 1}"
        )
        _check_name(name, f"指标 {name or source} 的名称")
        _check_not_reserved(name, f"指标 {name or source} 的名称")
        if not source:
            raise BadRequestError(f"指标 {name} 缺少 source（Excel 表头）")
        if name in fact_columns or any(mm["name"] == name for mm in metrics):
            raise BadRequestError(f"指标名与已有列冲突：{name}")
        column_sources[name] = source
        _push(name)
        metrics.append({
            "name": name,
            "source": name,
            "column": name,
            "label": str(m.get("label") or name).strip() or name,
        })
    if not metrics:
        raise BadRequestError("通用数据集至少需要声明一个指标")

    # 附加事实列：不在层级链中、但需要随事实行保存的维度（名称/备注等）
    for c in draft.get("columns") or []:
        source = str(c.get("source") or "").strip()
        if not source:
            raise BadRequestError("附加事实列缺少 source（Excel 表头）")
        record_key = str(c.get("name") or "").strip() or slugify_name(
            source, f"col{len(fact_columns) + 1}"
        )
        _check_name(record_key, f"事实列 {record_key} 的名称")
        _check_not_reserved(record_key, f"事实列 {record_key} 的名称")
        if record_key in fact_columns:
            continue
        column_sources[record_key] = source
        _push(record_key)

    required = [str(v) for v in (draft.get("required") or []) if str(v).strip()]
    unknown = [k for k in required if k not in set(level_keys) | {m["name"] for m in metrics}]
    if unknown:
        raise BadRequestError(
            "required 含未知字段", detail={"unknown": unknown, "known": level_keys}
        )

    return {
        "fact_table": f"fact_{dataset}",
        "time": {"source": time_source, "format": time_format},
        "derive": derive,
        "levels": list(level_labels),
        "level_labels": level_labels,
        "level_value_key": level_value_key,
        "metrics": metrics,
        "fact_columns": fact_columns,
        "fingerprint": level_keys,
        "json_level": min(4, len(level_labels)),
        "column_sources": column_sources,
        **({"required": required} if required else {}),
        **({"order_key": str(draft.get("order_key") or "").strip()}
           if str(draft.get("order_key") or "").strip() else {}),
    }


# ---------------- 物理表维护 ----------------


def _ddl_of(column) -> str:
    return "TEXT" if isinstance(column.type, DecimalString) else f"VARCHAR({DIM_COLUMN_LENGTH})"


def _link_table(spec):
    """取（并按声明构建）动态表对象。"""
    return (get_dynamic_dim_table(spec) if spec.kind == KIND_DIMENSION
            else get_dynamic_table(spec))


def _ensure_tables(spec) -> list[str]:
    """按声明建表并补齐新增列（声明变更后热更新物理表）。"""
    table = _link_table(spec)
    table.create(bind=db_engine, checkfirst=True)
    with db_engine.begin() as conn:
        ensure_columns(conn, table.name, [(c.name, _ddl_of(c)) for c in table.columns])
    return [table.name]


def _purge_nodes(dataset: str) -> int:
    session = SessionLocal()
    try:
        count = (
            session.query(SpiralNodeORM)
            .filter(SpiralNodeORM.dataset == dataset).delete()
        )
        session.commit()
        return count
    finally:
        session.close()


def _cell(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


# ---------------- 路由 ----------------


@router.get("")
def list_datasets(
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """当前有效数据集定义；已注销基座不再出现在主列表。

    这里只返回轻量结构摘要。字段归属、DNA 键序列、螺旋层级和实时生长/拓扑
    统计由 ``GET /{dataset}`` 按需加载，避免列表页同步扫描全量节点。
    """
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.list_datasets(session, context)
    snapshot = registry.raw(include_disabled=False)
    for item in snapshot["datasets"]:
        item["structure"] = build_structure_list_summary(
            registry.require(str(item["id"]))
        )
    scope = actual_sales_scope()
    return {
        "version": registry.version,
        "loaded_at": registry.loaded_at.isoformat(timespec="seconds")
        if registry.loaded_at
        else None,
        "total": len(snapshot["datasets"]),
        "items": snapshot["datasets"],
        # 跨实际数据集的业务范围口径来自经验库：Agent 按这份口径枚举，
        # 不在各自运行时里另写一套"什么算通用事实"的判定。
        "sales_scope": {
            "include_kinds": list(scope.get("include_kinds") or ["generic"]),
            "exclude_terms": list(scope.get("exclude_terms") or []),
        },
        **context.response_metadata(),
    }


@router.get("/disabled")
def list_disabled_datasets(
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """恢复/迁移入口使用的已注销基座清单，不混入主数据集列表。"""
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return {
            "version": context.profile_version,
            "total": 0,
            "items": [],
            **context.response_metadata(),
        }
    snapshot = registry.raw(include_disabled=True)
    items = [item for item in snapshot["datasets"] if item.get("disabled")]
    return {
        "version": registry.version,
        "total": len(items),
        "items": items,
        **context.response_metadata(),
    }


@router.post("/probe")
def probe_dataset(
    file: UploadFile = File(...),
    sheet: str | None = Form(None),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """上传 Excel 探测表头与列画像（注册向导第一步：预览 + 建议字段角色）。"""
    path = _save_upload(file)
    result = probe_file(path, sheet)
    # 引擎保留列（如 id）标为保留并禁止建议为时间/指标：避免采纳默认建议后建表冲突
    reserved = 0
    for col in result.get("columns", []):
        if col.get("guess_name") not in RESERVED_FACT_COLUMNS:
            continue
        col["reserved"] = True
        col["guess_role"] = "ignore"
        col.pop("guess_time_format", None)
        reserved += 1
        # 保留列若被认成时间列同样作废（向导不给时间列改物理名的入口）
        if col.get("source") and col["source"] == result.get("time_column"):
            result["time_column"] = None
            result["time_format"] = "yyyymm"
            result["default_derive"] = []
    logger.info("数据集探测 file=%s cols=%d reserved=%d",
                path.name, len(result["columns"]), reserved)
    return apply_ai_suggestions(result, source_name=path.name)


@router.post("/probe-database")
def probe_database_dataset(
    datasource_id: str = Form(...),
    table: str = Form(...),
    limit: int = Form(200),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """读取数据库表字段/样例，生成与 Excel 相同的注册前画像。"""
    if not table.strip():
        raise BadRequestError("数据库表名不能为空")
    limit = min(max(int(limit), 20), 1000)
    config = get_datasource_config(datasource_id)
    connector = create_connector(config)
    try:
        columns = connector.describe_table(table)
        rows = list(connector.fetch_rows(table, limit=limit))
    finally:
        connector.close()
    result = profile_records(
        columns,
        rows,
        source_name=f"{datasource_id}:{table}",
        row_count=None,
        sheet=table,
    )
    result["source"] = {
        "kind": "database",
        "datasource_id": datasource_id,
        "table": table,
    }
    return result


@router.get("/{dataset}")
def get_dataset(
    dataset: str,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return {
            **adapter.dataset_detail(adapter.runtime_for_context(context), dataset),
            **context.response_metadata(),
        }
    spec = registry.specs.get(dataset) or registry.base_specs.get(dataset)
    if spec is None:
        raise BadRequestError(f"未知数据集：{dataset}", detail={"known": registry.ids()})
    body = dump_spec(spec)
    source_origin = "interface" if dataset in registry.interface_specs else "base"
    body["source_origin"] = source_origin
    body["origin"] = source_origin
    body["editable"] = source_origin == "interface"
    body["disabled"] = dataset in registry.disabled_base_ids
    body["can_unregister"] = not body["disabled"]
    body["can_restore"] = body["disabled"] and source_origin == "base"
    body["metric_labels"] = (
        registry.metric_labels_of(dataset)
        if spec.is_spiral and not body["disabled"] else {}
    )
    body["structure"] = build_structure_summary(spec, session)
    body.update(context.response_metadata())
    return body


@router.get("/{dataset}/contract")
def get_dataset_contract(
    dataset: str,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """返回由当前数据集声明生成的 Agent/大模型能力契约。"""
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.dataset_contract(session, dataset, context)
    return {
        **build_dataset_contract(registry.require(dataset)),
        **context.response_metadata(),
    }


@router.put("/{dataset}")
def upsert_dataset(
    dataset: str,
    body: dict = Body(...),
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    """注册/覆盖自定义数据集：规范化为声明 → 自检 → 落盘 → 建表（失败回滚声明）。

    顺序保证「要么声明与物理表都就绪，要么什么都不变」：
    1) 声明自检（含保留列名/列冲突）在**落盘前**完成；
    2) 落盘后建表若失败，撤销刚写入的声明，避免坏声明留在 yaml 里
       导致引擎重启建表或注销接口再次失败。
    """
    _require_legacy_management()
    _check_id(dataset)
    if dataset in registry.base_specs:
        raise BadRequestError(
            f"数据集 {dataset} 属于基座数据，禁止通过界面注册接口修改",
            detail={"source_origin": "base", "known": sorted(registry.base_specs)},
        )

    declaration = _build_declaration(dataset, body)
    spec = parse_spec(declaration)
    try:  # 声明自检：构建表对象即可暴露列名冲突等结构问题（不落盘）
        _link_table(spec)
    except Exception as e:
        logger.warning("数据集声明自检失败 id=%s err=%s", dataset, e)
        raise BadRequestError(
            f"数据集声明无法建表：{e}",
            detail={"error": str(e)[:300], "hint": "请检查层级/指标的物理列名是否合法"},
        ) from e

    previous = registry.interface_specs.get(dataset)
    registry.upsert(spec)
    try:
        tables = _ensure_tables(spec)
    except Exception as e:
        registry.revert(dataset, previous)
        logger.exception("数据集建表失败，声明已回滚 id=%s", dataset)
        raise BadRequestError(
            f"数据集 {dataset} 物理表创建失败，声明已回滚：{e}",
            detail={"error": str(e)[:300]},
        ) from e
    logger.info("数据集已登记 id=%s kind=%s tables=%s", dataset, spec.kind, tables)
    return {
        "dataset": dump_spec(registry.require(dataset)),
        "tables": tables,
        "message": "数据集声明已保存并热加载生效，物理表已就绪",
    }


@router.delete("/{dataset}")
def delete_dataset(
    dataset: str,
    drop_data: bool = True,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    """注销数据集；基座数据写入可恢复的运行态停用清单。

    drop_data=true 时一并删除物理表与螺旋节点。

    先注销声明、再做物理清理：即便声明已损坏（无法构建表），
    注销也一定能成功，作为坏声明的逃生通道；清理失败回报到 cleanup_error。
    """
    _require_legacy_management()
    spec = registry.require(dataset)
    source_origin = "interface" if dataset in registry.interface_specs else "base"
    if source_origin == "interface":
        registry.remove(dataset)
    elif dataset in registry.base_specs:
        registry.disable_base(dataset)
    else:
        raise BadRequestError(f"数据集不存在或不可注销：{dataset}")
    dropped: list[str] = []
    nodes = 0
    json_files = 0
    cleanup_error: str | None = None
    if drop_data:
        try:
            if spec.kind == KIND_DIMENSION:
                drop_dim_table(spec, db_engine)
                dropped.append(spec.dim_table)
            else:
                nodes = _purge_nodes(dataset)   # 节点属于已注销的声明，先清
                drop_dynamic_table(spec, db_engine)
                dropped.append(spec.fact_table)
            json_files = purge_dataset_json(dataset)
        except Exception as e:
            cleanup_error = str(e)[:300]
            logger.exception("数据集物理清理失败（声明已注销）id=%s", dataset)
    logger.info("数据集已注销 id=%s drop=%s nodes=%d cleanup_error=%s",
                dataset, dropped, nodes, cleanup_error)
    return {"dataset": dataset, "dropped_tables": dropped,
            "purged_nodes": nodes, "purged_json_files": json_files,
            "cleanup_error": cleanup_error,
            "message": (
                "数据集已注销；基座配置保留，可恢复"
                if source_origin == "base" and not cleanup_error
                else "数据集已注销"
                if not cleanup_error
                else "数据集已注销；物理表清理失败，请管理员手工处理"
            ),
            "source_origin": source_origin,
            "reversible": source_origin == "base"}


@router.post("/{dataset}/restore")
def restore_dataset(
    dataset: str,
    identity: dict = Depends(require_roles(ROLE_ADMIN)),
) -> dict:
    """恢复已注销的基座数据集并重建其声明对应的物理表。"""
    _require_legacy_management()
    if dataset not in registry.base_specs:
        raise BadRequestError(f"只有基座数据集支持恢复：{dataset}")
    if dataset not in registry.disabled_base_ids:
        raise BadRequestError(f"数据集 {dataset} 当前未注销")

    spec = registry.restore_base(dataset)
    try:
        tables = _ensure_tables(spec)
    except Exception as e:
        registry.disable_base(dataset)
        logger.exception("基座数据集恢复建表失败，已回到注销状态 id=%s", dataset)
        raise BadRequestError(
            f"基座数据集 {dataset} 恢复失败：{e}",
            detail={"error": str(e)[:300]},
        ) from e
    return {
        "dataset": dataset,
        "tables": tables,
        "message": "基座数据集已恢复；请重新导入 Excel 或注册来源数据",
    }


@router.post("/{dataset}/import")
def import_dataset(
    dataset: str,
    mode: str = Form(MODE_DEDUPE),
    file: UploadFile | None = File(None),
    file_token: str | None = Form(None),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """按数据集声明导入 Excel（复用统一导入任务通道，可轮询进度）。"""
    _require_legacy_management()
    spec = registry.require(dataset)
    if mode not in VALID_MODES:
        raise BadRequestError("mode 必须为 dedupe / replace / append")
    if file is not None and file.filename:
        path = _save_upload(file)
    elif file_token:
        path = _resolve_token(file_token)
    elif spec.source.get("kind") == "database":
        path = None
    else:
        raise BadRequestError("请提供上传文件 file/file_token，或先注册数据库来源数据集")

    source_kind = str(spec.source.get("kind") or "excel")
    task_uuid = _service.start_import(
        dataset,
        path,
        mode=mode,
        source_kind=source_kind,
        source_ref=spec.source,
    )
    logger.info("数据集导入已触发 dataset=%s kind=%s source=%s mode=%s",
                dataset, spec.kind, source_kind, mode)
    return {"task_uuid": task_uuid, "dataset": dataset, "mode": mode,
            "file": path.name if path else spec.source.get("table"),
            "source_kind": source_kind, "status": "pending"}


@router.post("/{dataset}/rebuild")
def rebuild_dataset(
    dataset: str,
    body: RebuildRequest | None = Body(default=None),
    session: Session = Depends(get_db),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """按用户选择的结构范围重算螺旋节点聚合。"""
    _require_legacy_management()
    spec = registry.require(dataset)
    if not spec.is_spiral:
        raise BadRequestError(f"维表数据集 {dataset} 不参与聚合，无需重算")
    request = body or RebuildRequest()
    plan = rebuild_plan(spec, request.scope)
    store = SpiralStore(session)
    store.ensure_roots()
    result = store.rebuild_aggregations(
        dataset,
        scope=request.scope,
        node_ids=request.node_ids,
    )
    pruned = None
    topology = None
    if not request.node_ids and result["scope"] == "all":
        # 全量重算后按系统参数 growth_threshold 修剪螺旋树
        pruned = store.prune_to_threshold(dataset)
        # 修剪会删除旧节点，必须强制重建双链，否则剩余节点仍可能指向
        # 已删除的节点，页面会出现“有分枝但拓扑不完整”的假象。
        from engine.storage.topology import rebuild_dataset_topology
        topology = rebuild_dataset_topology(session, dataset, force=True)
    from engine.storage.spiral_json import purge_dataset_json
    purge_dataset_json(dataset)
    files = store.materialize_json(dataset)
    scope_labels = {
        "all": "全部节点",
        "base": "基座相关节点",
        "trunk": "中通主干节点",
        "branch": "中通分枝节点",
    }
    return {"dataset": dataset, "json_files": files,
            "scope": result["scope"],
            "target_levels": result["target_levels"],
            "target_nodes": result["target_nodes"],
            "pruned": pruned,
            "topology": topology.as_dict() if topology else None,
            "plan": plan,
            "message": (
                f"已按{scope_labels.get(result['scope'], result['scope'])}重算聚合并重新物化 JSON"
                + (f"；按生长阈值 {pruned['threshold']} 修剪 {pruned['deleted_nodes']} 个节点"
                   if pruned else "")
            )}


@router.get("/{dataset}/levels/{level}/values")
def level_values(
    dataset: str,
    level: str,
    q: str | None = None,
    limit: int = 20,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """层级取值检索（精确→包含回退）：给 Agent/前端的条件值解析用。

    候选优先来自螺旋树中该层真实存在过的节点值（不编造、不查词表）；
    层级节点被生长阈值修剪后，回退到注册表声明的事实表列直接检索。
    唯一命中直接采用；多命中可整组作为条件（引擎支持列表 + 同值合并）。
    """
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.level_values(session, dataset, level, q, limit, context)
    spec = registry.require(dataset)
    if not spec.is_spiral:
        raise BadRequestError(f"维表数据集 {dataset} 没有螺旋层级")
    requested = level
    if level not in spec.levels:
        # Agent 下传的是语义槽名（big_area/station/gas_code…），不是层级名：
        # 运行时解析成该数据集真实存在的层级，两侧都不必维护映射表。
        from engine.query.level_resolution import level_by_key_hint

        hinted = level_by_key_hint(spec, level)
        if hinted is None:
            raise BadRequestError(
                f"数据集 {dataset} 没有层级：{level}",
                detail={"levels": list(spec.levels), "requested": requested},
            )
        level = hinted
    limit = min(max(1, int(limit)), 100)

    base = (
        select(
            SpiralNodeORM.node_value,
            sa_func.sum(SpiralNodeORM.record_count),
        )
        .where(SpiralNodeORM.dataset == dataset, SpiralNodeORM.level_name == level)
        .group_by(SpiralNodeORM.node_value)
    )
    term = (q or "").strip()
    matched = "all"
    stmt = base
    if term:
        exact = session.execute(
            stmt.where(SpiralNodeORM.node_value == term).limit(limit)
        ).all()
        if exact:
            matched, stmt, rows = "exact", None, exact
        else:
            matched = "contains"
            stmt = stmt.where(SpiralNodeORM.node_value.contains(term))
    if stmt is not None:
        rows = session.execute(
            stmt.order_by(sa_func.sum(SpiralNodeORM.record_count).desc()).limit(limit)
        ).all()

    if not rows:
        matched, rows = _fact_level_values(session, spec, level, term, limit)
    display_names: dict[str, str] = {}
    if not rows and term:
        # 编码层存的是编码，自然语言给的是名称：按事实表自身的 1:1 函数依赖
        # 反查配对名称列，命中后回传真实编码，Agent/前端不猜值。
        from engine.query.level_resolution import display_map, resolve_level_values

        resolved = resolve_level_values(session, spec, level, [term])
        if resolved["codes"]:
            matched = "paired"
            rows = [(code, 0) for code in resolved["codes"][:limit]]
            display_names = display_map(session, spec, level, resolved["codes"][:limit])
    return {
        "dataset": dataset,
        "level": level,
        "level_label": spec.level_labels.get(level, level),
        "query": term or None,
        "matched": matched,
        "display_names": display_names,
        "items": [
            {"value": v, "record_count": int(c or 0)} for v, c in rows
        ],
    }


def _fact_level_values(
    session: Session, spec, level: str, term: str, limit: int
) -> tuple[str, list]:
    """事实表取值检索回退（精确→包含）：螺旋树该层无节点时使用。"""
    from engine.storage.dynamic_table import get_dynamic_table

    table = get_dynamic_table(spec)
    column = table.c.get(spec.level_value_key.get(level, level))
    if column is None:
        return "all", []
    grouped = (
        select(column, sa_func.count())
        .select_from(table)
        .where(column.isnot(None), column != "")
        .group_by(column)
    )
    if term:
        exact = session.execute(
            grouped.where(column == term).limit(limit)
        ).all()
        if exact:
            return "exact", list(exact)
        contained = session.execute(
            grouped.where(column.contains(term))
            .order_by(sa_func.count().desc()).limit(limit)
        ).all()
        return "contains", list(contained)
    return "all", list(
        session.execute(grouped.order_by(sa_func.count().desc()).limit(limit)).all()
    )


@router.get("/{dataset}/semantic-probe")
def semantic_probe(
    dataset: str,
    term: str,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """基座取值自检：某个自然语言词在该数据集的组织/产品列里是否真的存在。

    Agent 下发地点、品类之类的语义条件之前先问它，命中才下传；未命中就丢弃
    该条件并如实告知，避免把提问用词当成组织名而阻断整条查询。
    """
    context, adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        return adapter.semantic_probe(session, dataset, term, context)
    spec = registry.require(dataset)
    from engine.query.foundation import probe_semantic_value

    return probe_semantic_value(session, spec, term)


@router.get("/{dataset}/rows")
def list_rows(
    dataset: str,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_db),
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    """浏览数据集物理行（维表 / 通用事实表通用）。"""
    context, _adapter = _active_storage()
    if context.storage_type != "legacy_sqlite":
        raise BadRequestError(
            "当前 CNTS 活动 Catalog 不开放原始明细浏览；请通过统一查询读取聚合结果，或构建具备明细浏览能力的新适配器版本。",
            detail={**context.response_metadata(), "fallback": "forbidden"},
        )
    spec = registry.require(dataset)
    if spec.kind == KIND_DIMENSION:
        table = get_dynamic_dim_table(spec)
        columns = [{"name": c.name, "label": c.label or c.name} for c in spec.columns]
    else:
        table = get_fact_source(dataset).table
        labels = registry.metric_labels_of(dataset)
        columns = [{"name": c, "label": labels.get(c, c)}
                   for c in spec.fact_columns if c in table.c]

    page = max(1, int(page))
    page_size = min(max(1, int(page_size)), 200)
    total = session.execute(select(sa_func.count()).select_from(table)).scalar_one()
    rows = session.execute(
        select(table).limit(page_size).offset((page - 1) * page_size)
    ).mappings().all()
    items = [{k: _cell(v) for k, v in row.items() if k != "id"} for row in rows]
    return {"dataset": dataset, "kind": spec.kind, "columns": columns,
            "total": total, "page": page, "page_size": page_size, "items": items}


@router.post("/reload")
def reload_datasets(
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """重新读取注册表文件（含界面手改自定义 yaml 的场景）。"""
    _require_legacy_management()
    registry.reload()
    created = []
    for spec in registry.specs.values():
        if spec.id in registry.interface_specs:
            created.extend(_ensure_tables(spec))
    return {
        "version": registry.version,
        "total": len(registry.raw(include_disabled=False)["datasets"]),
        "ids": registry.ids(),
        "tables": created,
        "message": "数据集注册表已热加载并生效",
    }

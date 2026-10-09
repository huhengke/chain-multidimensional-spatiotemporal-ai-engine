# -*- coding: utf-8 -*-
"""数据源路由：配置展示、连通性测试、表发现和字段预览。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from engine.api.schemas_api import DatasourceTestRequest
from engine.core.errors import BadRequestError
from engine.ingestion.connectors.base import create_connector
from engine.ingestion.datasource_config import (
    get_datasource_config,
    list_datasource_configs,
    safe_datasource_config,
)
from engine.security.deps import allow_user_or_service, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR

router = APIRouter(prefix="/api/v1/engine/datasources", tags=["datasources"])

# 导入触发连接器注册
import engine.ingestion.connectors.mysql_connector  # noqa: F401
import engine.ingestion.connectors.postgres_connector  # noqa: F401
import engine.ingestion.connectors.oracle_connector  # noqa: F401
import engine.ingestion.connectors.mongo_connector  # noqa: F401


@router.get("")
def list_datasources(
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    items = [safe_datasource_config(ds) for ds in list_datasource_configs()]
    return {"items": items}


@router.post("/test")
def test_datasource(
    body: DatasourceTestRequest,
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    config = body.config
    if "type" not in config:
        raise BadRequestError("config 必须包含 type")
    connector = create_connector(config)
    try:
        result = connector.test_connection()
    except Exception as e:
        return {"ok": False, "message": str(e)}
    return result


@router.get("/{source_id}/tables")
def list_tables(
    source_id: str,
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """从已配置数据源读取表/集合清单，供数据集注册向导选择。"""
    config = get_datasource_config(source_id)
    connector = create_connector(config)
    try:
        items = connector.list_tables()
        return {"source_id": source_id, "items": items}
    finally:
        connector.close()


@router.get("/{source_id}/tables/{table:path}/preview")
def preview_table(
    source_id: str,
    table: str,
    limit: int = Query(200, ge=1, le=1000),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """读取数据库表字段和少量样例；不写入引擎业务表。"""
    config = get_datasource_config(source_id)
    connector = create_connector(config)
    try:
        columns = connector.describe_table(table)
        rows = list(connector.fetch_rows(table, limit=limit))
        return {"source_id": source_id, "table": table,
                "columns": columns, "rows": rows, "sampled": len(rows)}
    finally:
        connector.close()

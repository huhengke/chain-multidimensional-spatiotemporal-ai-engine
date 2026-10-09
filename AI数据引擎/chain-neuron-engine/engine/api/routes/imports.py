# -*- coding: utf-8 -*-
"""导入路由：文件上传、测试数据一键导入、任务进度查询。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from engine.core.config import BLOBS_DIR, WORKSPACE_ROOT
from engine.core.errors import BadRequestError, NotFoundError
from engine.ingestion.models import (
    MODE_DEDUPE,
    VALID_MODES,
    ImportTask,
)
from engine.ingestion.service import get_ingestion_service
from engine.runtime_profiles.guards import require_mutable_legacy
from engine.security.deps import allow_user_or_service, get_db, require_roles
from engine.security.models import ROLE_ADMIN, ROLE_OPERATOR
from engine.storage.dataset_registry import registry

router = APIRouter(prefix="/api/v1/engine/import", tags=["import"])

_service = get_ingestion_service()


def _task_dict(t: ImportTask) -> dict:
    def iso(v: datetime | None) -> str | None:
        return v.isoformat(timespec="seconds") if v else None

    return {
        "task_uuid": t.task_uuid,
        "dataset": t.dataset,
        "file_name": t.file_name,
        "source_kind": t.source_kind,
        "source_ref": t.source_ref or {},
        "mode": t.mode,
        "status": t.status,
        "phase": t.phase,
        "total": t.total,
        "processed": t.processed,
        "succeeded": t.succeeded,
        "failed": t.failed,
        "skipped": t.skipped,
        "error_sample": t.error_sample,
        "message": t.message,
        "started_at": iso(t.started_at),
        "finished_at": iso(t.finished_at),
    }


@router.post("/upload")
def upload(
    dataset: str = Form(...),
    mode: str = Form(MODE_DEDUPE),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    require_mutable_legacy("数据导入通道")
    if not registry.exists(dataset):
        raise BadRequestError(
            f"未知数据集：{dataset}", detail={"known": registry.ids()}
        )
    if mode not in VALID_MODES:
        raise BadRequestError("mode 必须为 dedupe / replace / append")
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise BadRequestError("仅支持 .xlsx 文件")

    BLOBS_DIR.mkdir(parents=True, exist_ok=True)
    target = BLOBS_DIR / file.filename
    target.write_bytes(file.file.read())

    task_uuid = _service.start_import(dataset, target, mode=mode)
    return {"task_uuid": task_uuid, "status": "pending", "file": file.filename}


@router.post("/test-data")
def import_test_data(
    dataset: str = Form(...),
    mode: str = Form(MODE_DEDUPE),
    identity: dict = Depends(require_roles(ROLE_ADMIN, ROLE_OPERATOR)),
) -> dict:
    """使用根目录随产品手册分发的测试数据启动导入（只读引用，不搬迁）。"""
    require_mutable_legacy("数据导入通道")
    if mode not in VALID_MODES:
        raise BadRequestError("mode 必须为 dedupe / replace / append")
    spec = registry.require(dataset)
    if not spec.source_file:
        raise BadRequestError(f"数据集 {dataset} 未登记随包测试数据文件")
    path = WORKSPACE_ROOT / spec.source_file
    if not path.exists():
        raise NotFoundError("测试数据文件不存在", str(path))
    task_uuid = _service.start_import(dataset, path, mode=mode)
    return {"task_uuid": task_uuid, "dataset": dataset,
            "mode": mode, "file": path.name}


@router.get("/tasks/{task_uuid}")
def get_task(
    task_uuid: str,
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    task = _service.get_task(task_uuid)
    result = _task_dict(task)
    result["running"] = _service.is_running(task_uuid)
    return result


@router.get("/tasks")
def list_tasks(
    dataset: str | None = None,
    identity: dict = Depends(allow_user_or_service),
) -> dict:
    tasks = _service.list_tasks(dataset=dataset)
    return {"items": [_task_dict(t) for t in tasks]}

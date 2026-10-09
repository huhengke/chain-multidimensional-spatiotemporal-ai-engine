# -*- coding: utf-8 -*-
"""数据接入 ORM：导入任务表（导入状态实时监控）。"""
from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy import JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from engine.storage.database import Base

# 任务状态
ST_PENDING = "pending"
ST_RUNNING = "running"
ST_SUCCESS = "success"
ST_PARTIAL = "partial"
ST_FAILED = "failed"

# 导入阶段（用于前端展示和故障定位）
PHASE_QUEUED = "queued"
PHASE_PREPARING = "preparing"
PHASE_READING = "reading"
PHASE_WRITING = "writing"
PHASE_FINALIZING = "finalizing"
PHASE_MATERIALIZING = "materializing"
PHASE_COMPLETED = "completed"
PHASE_FAILED = "failed"

# 导入模式
MODE_DEDUPE = "dedupe"      # 默认：按业务指纹跳过已存在记录（幂等）
MODE_REPLACE = "replace"    # 先清空该数据集，再全量导入
MODE_APPEND = "append"      # 直接追加（不去重）
VALID_MODES = (MODE_DEDUPE, MODE_REPLACE, MODE_APPEND)


class ImportTask(Base):
    __tablename__ = "import_task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_uuid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    dataset: Mapped[str] = mapped_column(String(16), index=True)
    file_name: Mapped[str] = mapped_column(String(256))
    file_path: Mapped[str] = mapped_column(String(512))
    source_kind: Mapped[str] = mapped_column(String(16), default="excel")
    source_ref: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default=ST_PENDING, index=True)
    phase: Mapped[str] = mapped_column(String(32), default=PHASE_QUEUED)
    mode: Mapped[str] = mapped_column(String(16), default=MODE_DEDUPE)

    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)

    error_sample: Mapped[list] = mapped_column(JSON, default=list)  # 错误样例（行号+原因）
    message: Mapped[str | None] = mapped_column(String(512))

    started_at: Mapped[object | None] = mapped_column(DateTime)
    finished_at: Mapped[object | None] = mapped_column(DateTime)
    created_at: Mapped[object] = mapped_column(DateTime, server_default=func.now())

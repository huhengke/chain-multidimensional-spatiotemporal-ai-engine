# -*- coding: utf-8 -*-
"""IngestionService：导入门面。

- start_import：创建任务并在后台线程执行（独立 DB 会话），立即返回任务 id；
- get_task：查询实时进度（processed/succeeded/failed/skipped + 状态）；
- API 层与 Agent 通道统一走此服务。
"""
from __future__ import annotations

import threading
from functools import lru_cache
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import select

from engine.core.errors import NotFoundError
from engine.core.logging import get_logger
from engine.ingestion.batch_importer import BatchImporter
from engine.ingestion.models import MODE_DEDUPE, ImportTask
from engine.storage.database import SessionLocal

logger = get_logger("ingestion.service")


class IngestionService:
    def __init__(self):
        self._threads: dict[str, threading.Thread] = {}

    def start_import(self, dataset: str, file_path: str | Path | None,
                     mode: str = MODE_DEDUPE, *,
                     source_kind: str = "excel",
                     source_ref: dict | None = None) -> str:
        if file_path is not None:
            file_path = Path(file_path)
        # 先在当前会话创建任务记录
        session = SessionLocal()
        try:
            importer = BatchImporter(session)
            task = importer.create_task(
                dataset, file_path, mode=mode,
                source_kind=source_kind, source_ref=source_ref,
            )
            task_uuid = task.task_uuid
        finally:
            session.close()

        t = threading.Thread(
            target=self._run_in_thread,
            args=(task_uuid,),
            name=f"import-{task_uuid[:8]}",
            daemon=True,
        )
        self._threads[task_uuid] = t
        t.start()
        logger.info("导入后台线程已启动 uuid=%s", task_uuid)
        return task_uuid

    def _run_in_thread(self, task_uuid: str) -> None:
        session = SessionLocal()
        try:
            importer = BatchImporter(session)
            importer.run_task(task_uuid)
        except Exception:
            logger.exception("后台导入线程异常 uuid=%s", task_uuid)
        finally:
            session.close()

    def start_import_sequence(
        self,
        items: Sequence[tuple],
    ) -> list[dict[str, str]]:
        """创建一组任务并由单独线程顺序执行，避免 SQLite 并发写入争用。"""
        created: list[dict[str, str]] = []
        session = SessionLocal()
        try:
            importer = BatchImporter(session)
            for item in items:
                dataset, path, mode = item[:3]
                source_kind = item[3] if len(item) > 3 else "excel"
                source_ref = item[4] if len(item) > 4 else None
                path_obj = Path(path) if path is not None else None
                task = importer.create_task(
                    dataset, path_obj, mode=mode,
                    source_kind=source_kind, source_ref=source_ref,
                )
                created.append({
                    "dataset": dataset,
                    "task_uuid": task.task_uuid,
                    "file_name": task.file_name,
                })
        finally:
            session.close()
        thread = threading.Thread(
            target=self._run_sequence,
            args=([item["task_uuid"] for item in created],),
            name="reinitialize-import-sequence",
            daemon=True,
        )
        for item in created:
            self._threads[item["task_uuid"]] = thread
        thread.start()
        return created

    def _run_sequence(self, task_uuids: list[str]) -> None:
        try:
            for task_uuid in task_uuids:
                try:
                    self._run_in_thread(task_uuid)
                except Exception:
                    logger.exception("顺序导入任务异常 uuid=%s", task_uuid)
                    break
        finally:
            for task_uuid in task_uuids:
                self._threads.pop(task_uuid, None)

    def get_task(self, task_uuid: str) -> ImportTask:
        session = SessionLocal()
        try:
            task = session.execute(
                select(ImportTask).where(ImportTask.task_uuid == task_uuid)
            ).scalar_one_or_none()
            if task is None:
                raise NotFoundError("导入任务不存在", task_uuid)
            # detach 为普通属性读取
            session.expunge(task)
            return task
        finally:
            session.close()

    def list_tasks(self, dataset: str | None = None, limit: int = 20) -> list[ImportTask]:
        session = SessionLocal()
        try:
            stmt = select(ImportTask).order_by(ImportTask.id.desc()).limit(limit)
            if dataset:
                stmt = select(ImportTask).where(
                    ImportTask.dataset == dataset
                ).order_by(ImportTask.id.desc()).limit(limit)
            tasks = list(session.execute(stmt).scalars().all())
            for t in tasks:
                session.expunge(t)
            return tasks
        finally:
            session.close()

    def is_running(self, task_uuid: str) -> bool:
        t = self._threads.get(task_uuid)
        return bool(t and t.is_alive())


@lru_cache
def get_ingestion_service() -> IngestionService:
    """全局单例：多路由共用同一实例，`is_running` 状态才准确。"""
    return IngestionService()

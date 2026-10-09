"""Health adapter for the existing SQLite-backed execution path."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from engine.runtime_profiles.context import RuntimeProfileContext
from engine.runtime_profiles.models import RuntimeProfile
from engine.storage.adapters.base import StorageAdapter


class LegacySqliteAdapter(StorageAdapter):
    storage_type = "legacy_sqlite"

    def health(self, profile: RuntimeProfile) -> dict[str, Any]:
        path = Path(profile.storage.root).resolve()
        if not path.is_file():
            return {"status": "error", "message": f"SQLite 文件不存在：{path}"}
        uri = f"file:{path.as_posix()}?mode=ro"
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
                connection.execute("PRAGMA query_only=ON")
                query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
                schema_version = int(
                    connection.execute("PRAGMA schema_version").fetchone()[0]
                )
                connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            return {"status": "error", "message": f"SQLite 只读检查失败：{exc}"}
        if query_only != 1:
            return {"status": "error", "message": "SQLite 未进入只读模式"}
        return {
            "status": "ok",
            "message": "SQLite 可只读访问（快速检查）",
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "schema_version": schema_version,
            "check_mode": "read_only_connectivity",
        }

    def execute_query(
        self,
        session: Session,
        request: dict[str, Any],
        trace_id: str,
        context: RuntimeProfileContext,
    ) -> dict[str, Any]:
        from engine.query.unified import execute_unified_query

        return execute_unified_query(session, request, trace_id)

# -*- coding: utf-8 -*-
"""SQLite storage maintenance helpers used by destructive admin workflows."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine


def prune_database_backups(
    backup_dir: Path,
    *,
    pattern: str,
    keep_count: int,
    protected: set[Path] | None = None,
) -> dict[str, Any]:
    """Keep the newest matching backups and delete older files inside backup_dir only."""
    root = backup_dir.resolve()
    protected_paths = {path.resolve() for path in (protected or set())}
    keep = max(1, int(keep_count))
    candidates = sorted(
        (path for path in root.glob(pattern) if path.is_file()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    retained = set(candidates[:keep]) | protected_paths
    deleted: list[dict[str, Any]] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in retained:
            continue
        if resolved.parent != root:
            raise ValueError(f"refusing backup path outside configured directory: {resolved}")
        size = resolved.stat().st_size
        resolved.unlink()
        deleted.append({"path": str(resolved), "bytes": size})
    return {
        "keep_count": keep,
        "retained": [str(path.resolve()) for path in candidates if path.resolve() in retained],
        "deleted": deleted,
        "deleted_bytes": sum(item["bytes"] for item in deleted),
    }


def compact_sqlite_database(engine: Engine, database_path: Path) -> dict[str, Any]:
    """Best-effort VACUUM after a destructive clear; report failure without hiding cleanup success."""
    path = database_path.resolve()
    before = path.stat().st_size if path.exists() else 0
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
            connection.exec_driver_sql("VACUUM")
        after = path.stat().st_size if path.exists() else 0
        return {
            "completed": True,
            "before_bytes": before,
            "after_bytes": after,
            "saved_bytes": max(0, before - after),
        }
    except Exception as error:  # cleanup already committed; return an auditable warning
        return {
            "completed": False,
            "before_bytes": before,
            "after_bytes": path.stat().st_size if path.exists() else before,
            "saved_bytes": 0,
            "error": str(error),
        }

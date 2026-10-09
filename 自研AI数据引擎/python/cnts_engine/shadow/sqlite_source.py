from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from .models import SourceFingerprint


TABLE = "fact_oil_sales_daily"
SOURCE_COLUMNS = (
    "id",
    "day",
    "bigareacode",
    "branchcode",
    "areacode",
    "oucode",
    "category",
    "gascode",
    "salesvol",
    "qty",
    "amount",
    "universalcode",
    "shortname",
    "salesmode",
    "checkmode",
    "branchname",
    "areaname",
    "bigareaname",
    "price",
    "guns",
    "isoperate",
    "datatype",
    "remark",
)


class SqliteOilDailySource:
    """A guarded, read-only adapter over the legacy oil daily table."""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database).resolve()
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("source connection is not open")
        return self._connection

    def __enter__(self) -> "SqliteOilDailySource":
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        uri = f"{self.database.as_uri()}?mode=ro"
        self._connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only=ON")
        query_only = int(self._connection.execute("PRAGMA query_only").fetchone()[0])
        if query_only != 1:
            self.close()
            raise RuntimeError("failed to enforce SQLite query_only mode")
        self._validate_contract()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _validate_contract(self) -> None:
        table = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        if table is None:
            raise RuntimeError(f"source table {TABLE} does not exist")
        actual = {
            str(row[1]).lower()
            for row in self.connection.execute(f'PRAGMA table_info("{TABLE}")')
        }
        missing = sorted(set(SOURCE_COLUMNS) - actual)
        if missing:
            raise RuntimeError(f"source table is missing columns: {', '.join(missing)}")

    def fingerprint(self) -> SourceFingerprint:
        stat = self.database.stat()
        wal_path = Path(f"{self.database}-wal")
        if wal_path.is_file():
            wal_stat = wal_path.stat()
            wal_bytes = wal_stat.st_size
            wal_modified_ns = wal_stat.st_mtime_ns
        else:
            wal_bytes = 0
            wal_modified_ns = 0
        control = self.connection.execute(
            f"""
            SELECT COUNT(*), MIN(id), MAX(id), MIN(day), MAX(day), COUNT(DISTINCT day)
            FROM {TABLE}
            """
        ).fetchone()
        return SourceFingerprint(
            database=str(self.database),
            bytes=stat.st_size,
            modified_ns=stat.st_mtime_ns,
            wal_bytes=wal_bytes,
            wal_modified_ns=wal_modified_ns,
            row_count=int(control[0]),
            min_id=control[1],
            max_id=control[2],
            min_day=control[3],
            max_day=control[4],
            distinct_days=int(control[5]),
            page_count=int(self.connection.execute("PRAGMA page_count").fetchone()[0]),
            schema_version=int(
                self.connection.execute("PRAGMA schema_version").fetchone()[0]
            ),
            data_version=int(
                self.connection.execute("PRAGMA data_version").fetchone()[0]
            ),
        )

    def iter_rows(self, chunk_size: int = 10_000) -> Iterator[sqlite3.Row]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        columns = ", ".join(f'"{column}"' for column in SOURCE_COLUMNS)
        last_id = -1
        seen = 0
        while True:
            rows = self.connection.execute(
                f"SELECT {columns} FROM {TABLE} WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, chunk_size),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                row_id = int(row["id"])
                if row_id <= last_id:
                    raise RuntimeError("source ids are not strictly increasing")
                last_id = row_id
                seen += 1
                yield row
        if seen == 0:
            raise RuntimeError("source table contains no rows")

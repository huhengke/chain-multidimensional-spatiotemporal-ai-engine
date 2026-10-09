from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from cnts_engine.catalog.models import canonical_hash

from .models import SourceBinding


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    database: str
    database_bytes: int
    database_modified_ns: int
    wal_bytes: int
    wal_modified_ns: int
    table: str
    row_count: int
    min_key: Any
    max_key: Any
    min_time: str | None
    max_time: str | None
    schema_hash: str
    schema_version: int
    data_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SqliteTableSource:
    """Read-only, keyset-paginated source used only while building a candidate."""

    def __init__(
        self,
        database: str | Path,
        binding: SourceBinding,
        columns: Sequence[str],
    ) -> None:
        self.database = Path(database).resolve()
        self.binding = binding
        self.columns = tuple(dict.fromkeys(columns))
        self._connection: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("source connection is not open")
        return self._connection

    def __enter__(self) -> "SqliteTableSource":
        if not self.database.is_file():
            raise FileNotFoundError(self.database)
        self._connection = sqlite3.connect(
            f"{self.database.as_uri()}?mode=ro",
            uri=True,
            timeout=10.0,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA query_only=ON")
        if int(self._connection.execute("PRAGMA query_only").fetchone()[0]) != 1:
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
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (self.binding.table,),
        ).fetchone()
        if table is None:
            raise RuntimeError(f"source table does not exist: {self.binding.table}")
        actual = {
            str(row[1]).lower()
            for row in self.connection.execute(
                f'PRAGMA table_info("{self.binding.table}")'
            )
        }
        required = {self.binding.key_column, *self.columns}
        if self.binding.time_column:
            required.add(self.binding.time_column)
        missing = sorted(name for name in required if name.lower() not in actual)
        if missing:
            raise RuntimeError(
                f"source table {self.binding.table} is missing columns: {', '.join(missing)}"
            )

    def snapshot(self) -> SourceSnapshot:
        stat = self.database.stat()
        wal_path = Path(f"{self.database}-wal")
        wal_stat = wal_path.stat() if wal_path.is_file() else None
        time_select = (
            f', MIN("{self.binding.time_column}"), MAX("{self.binding.time_column}")'
            if self.binding.time_column
            else ", NULL, NULL"
        )
        control = self.connection.execute(
            f'SELECT COUNT(*), MIN("{self.binding.key_column}"), '
            f'MAX("{self.binding.key_column}"){time_select} '
            f'FROM "{self.binding.table}"'
        ).fetchone()
        table_info = [
            list(row)
            for row in self.connection.execute(
                f'PRAGMA table_info("{self.binding.table}")'
            )
        ]
        return SourceSnapshot(
            database=str(self.database),
            database_bytes=stat.st_size,
            database_modified_ns=stat.st_mtime_ns,
            wal_bytes=wal_stat.st_size if wal_stat else 0,
            wal_modified_ns=wal_stat.st_mtime_ns if wal_stat else 0,
            table=self.binding.table,
            row_count=int(control[0]),
            min_key=control[1],
            max_key=control[2],
            min_time=str(control[3]) if control[3] is not None else None,
            max_time=str(control[4]) if control[4] is not None else None,
            schema_hash=canonical_hash(table_info),
            schema_version=int(
                self.connection.execute("PRAGMA schema_version").fetchone()[0]
            ),
            data_version=int(
                self.connection.execute("PRAGMA data_version").fetchone()[0]
            ),
        )

    def iter_rows(self, *, chunk_size: int = 10_000) -> Iterator[sqlite3.Row]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        selected = tuple(dict.fromkeys((self.binding.key_column, *self.columns)))
        columns = ", ".join(f'"{column}"' for column in selected)
        last_key: Any = None
        seen = 0
        while True:
            if last_key is None:
                sql = (
                    f"SELECT {columns} FROM \"{self.binding.table}\" "
                    f"ORDER BY \"{self.binding.key_column}\" LIMIT ?"
                )
                params = (chunk_size,)
            else:
                sql = (
                    f"SELECT {columns} FROM \"{self.binding.table}\" "
                    f"WHERE \"{self.binding.key_column}\" > ? "
                    f"ORDER BY \"{self.binding.key_column}\" LIMIT ?"
                )
                params = (last_key, chunk_size)
            rows = self.connection.execute(sql, params).fetchall()
            if not rows:
                break
            for row in rows:
                key = row[self.binding.key_column]
                if key is None or (last_key is not None and key <= last_key):
                    raise RuntimeError("source key is null, duplicated, or out of order")
                last_key = key
                seen += 1
                yield row
        if seen == 0:
            raise RuntimeError(f"source table {self.binding.table} contains no rows")

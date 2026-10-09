# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine

from engine.storage.maintenance import compact_sqlite_database, prune_database_backups


class StorageMaintenanceTests(unittest.TestCase):
    def test_prune_database_backups_keeps_newest_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for index in range(4):
                path = root / f"engine-before-reinitialize-2026010{index + 1}-000000.db"
                path.write_bytes(bytes([index]) * 128)
                os.utime(path, (100 + index, 100 + index))
                files.append(path)

            result = prune_database_backups(
                root,
                pattern="engine-before-reinitialize-*.db",
                keep_count=1,
                protected={files[-1]},
            )

            self.assertEqual([path.name for path in root.glob("*.db")], [files[-1].name])
            self.assertEqual(result["deleted_bytes"], 128 * 3)
            self.assertEqual(len(result["deleted"]), 3)

    def test_compact_sqlite_database_reclaims_deleted_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE samples(id INTEGER PRIMARY KEY, payload TEXT)")
            connection.executemany(
                "INSERT INTO samples(payload) VALUES (?)",
                [("x" * 1024,) for _ in range(4000)],
            )
            connection.commit()
            connection.execute("DELETE FROM samples WHERE id <= 3500")
            connection.commit()
            connection.close()
            before = path.stat().st_size

            engine = create_engine(f"sqlite:///{path.as_posix()}")
            try:
                result = compact_sqlite_database(engine, path)
            finally:
                engine.dispose()

            self.assertTrue(result["completed"], result)
            self.assertLess(path.stat().st_size, before)
            with closing(sqlite3.connect(path)) as verified:
                self.assertEqual(verified.execute("SELECT count(*) FROM samples").fetchone()[0], 500)
                self.assertEqual(verified.execute("PRAGMA quick_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()

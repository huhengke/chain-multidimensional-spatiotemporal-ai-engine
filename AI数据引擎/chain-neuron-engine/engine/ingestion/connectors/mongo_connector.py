# -*- coding: utf-8 -*-
"""MongoDB 连接器（需要 pymongo：pip install pymongo）。"""
from __future__ import annotations

import time
from collections.abc import Iterator

from engine.core.errors import ErrorCode, AppException
from engine.ingestion.connectors.base import BaseConnector, register_connector, validate_table_identifier


@register_connector
class MongoConnector(BaseConnector):
    type_name = "mongo"

    def _client(self):
        try:
            import pymongo
        except ImportError:
            raise AppException(
                ErrorCode.DATASOURCE_CONNECT_ERROR[0],
                "缺少 MongoDB 驱动，请执行：pip install pymongo",
                http_status=400,
            )
        kwargs = {
            "host": self.config.get("host", "127.0.0.1"),
            "port": int(self.config.get("port", 27017)),
            "serverSelectionTimeoutMS": 5000,
        }
        user = self.config.get("username")
        if user:
            kwargs["username"] = user
            kwargs["password"] = self.config.get("password", "")
        return pymongo.MongoClient(**kwargs)

    def test_connection(self) -> dict:
        t0 = time.time()
        client = self._client()
        try:
            client.admin.command("ping")
        finally:
            client.close()
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000)}

    def fetch_rows(self, table: str, limit: int | None = None) -> Iterator[dict]:
        table = validate_table_identifier(table)
        client = self._client()
        db = client[self.config.get("database", "")]
        coll = db[table]
        cursor = coll.find(limit=limit) if limit else coll.find()
        try:
            for doc in cursor:
                doc.pop("_id", None)
                yield doc
        finally:
            client.close()

    def list_tables(self) -> list[dict]:
        client = self._client()
        try:
            db = client[self.config.get("database", "")]
            return [{"name": name} for name in sorted(db.list_collection_names())]
        finally:
            client.close()

    def describe_table(self, table: str) -> list[dict]:
        table = validate_table_identifier(table)
        sample = next(self.fetch_rows(table, limit=1), {})
        return [{"name": name, "type": type(value).__name__, "nullable": "YES"}
                for name, value in sample.items()]

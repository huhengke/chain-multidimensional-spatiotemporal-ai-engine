# -*- coding: utf-8 -*-
"""PostgreSQL 连接器（需要 psycopg2：pip install psycopg2-binary）。"""
from __future__ import annotations

import time
from collections.abc import Iterator

from engine.core.errors import ErrorCode, AppException
from engine.ingestion.connectors.base import BaseConnector, register_connector, validate_table_identifier


@register_connector
class PostgresConnector(BaseConnector):
    type_name = "postgres"

    def _connect(self):
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError:
            raise AppException(
                ErrorCode.DATASOURCE_CONNECT_ERROR[0],
                "缺少 PostgreSQL 驱动，请执行：pip install psycopg2-binary",
                http_status=400,
            )
        return psycopg2.connect(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 5432)),
            user=self.config.get("username", ""),
            password=self.config.get("password", ""),
            dbname=self.config.get("database", ""),
            connect_timeout=5,
            cursor_factory=psycopg2.extras.RealDictCursor,
        )

    def test_connection(self) -> dict:
        t0 = time.time()
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000)}

    def fetch_rows(self, table: str, limit: int | None = None) -> Iterator[dict]:
        conn = self._connect()
        table = validate_table_identifier(table)
        sql = f"SELECT * FROM {table}" + (f" LIMIT {int(limit)}" if limit else "")
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                for row in cur:
                    yield dict(row)
        finally:
            conn.close()

    def list_tables(self) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_schema AS schema, table_name AS name "
                    "FROM information_schema.tables WHERE table_type='BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog','information_schema') "
                    "ORDER BY table_schema, table_name"
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def describe_table(self, table: str) -> list[dict]:
        table = validate_table_identifier(table)
        schema, name = (table.split(".", 1) if "." in table else ("public", table))
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT column_name AS name, data_type AS type, "
                    "is_nullable AS nullable FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                    (schema, name),
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

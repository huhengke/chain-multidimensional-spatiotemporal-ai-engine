# -*- coding: utf-8 -*-
"""MySQL 连接器（需要 pymysql：pip install pymysql）。"""
from __future__ import annotations

import time
from collections.abc import Iterator

from engine.core.errors import ErrorCode, AppException
from engine.ingestion.connectors.base import BaseConnector, register_connector, validate_table_identifier


@register_connector
class MySQLConnector(BaseConnector):
    type_name = "mysql"

    def _connect(self):
        try:
            import pymysql
        except ImportError:
            raise AppException(
                ErrorCode.DATASOURCE_CONNECT_ERROR[0],
                "缺少 MySQL 驱动 pymysql，请执行：pip install pymysql",
                http_status=400,
            )
        return pymysql.connect(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 3306)),
            user=self.config.get("username", ""),
            password=self.config.get("password", ""),
            database=self.config.get("database", ""),
            connect_timeout=5,
            cursorclass=pymysql.cursors.DictCursor,
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
                    yield row
        finally:
            conn.close()

    def list_tables(self) -> list[dict]:
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TABLE_NAME AS name FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME",
                    (self.config.get("database", ""),),
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def describe_table(self, table: str) -> list[dict]:
        conn = self._connect()
        table = validate_table_identifier(table)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COLUMN_NAME AS name, DATA_TYPE AS type, "
                    "IS_NULLABLE AS nullable FROM information_schema.columns "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                    (self.config.get("database", ""), table),
                )
                return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

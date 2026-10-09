# -*- coding: utf-8 -*-
"""Oracle 连接器（需要 cx_Oracle：pip install cx_Oracle，并配置 Instant Client）。"""
from __future__ import annotations

import time
from collections.abc import Iterator

from engine.core.errors import ErrorCode, AppException
from engine.ingestion.connectors.base import BaseConnector, register_connector, validate_table_identifier


@register_connector
class OracleConnector(BaseConnector):
    type_name = "oracle"

    def _connect(self):
        try:
            import cx_Oracle
        except ImportError:
            raise AppException(
                ErrorCode.DATASOURCE_CONNECT_ERROR[0],
                "缺少 Oracle 驱动，请执行：pip install cx_Oracle（并安装 Instant Client）",
                http_status=400,
            )
        dsn = cx_Oracle.makedsn(
            self.config.get("host", "127.0.0.1"),
            int(self.config.get("port", 1521)),
            service_name=self.config.get("service_name", "ORCL"),
        )
        return cx_Oracle.connect(
            user=self.config.get("username", ""),
            password=self.config.get("password", ""),
            dsn=dsn,
        )

    def test_connection(self) -> dict:
        t0 = time.time()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUAL")
            cur.fetchone()
            cur.close()
        finally:
            conn.close()
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000)}

    def fetch_rows(self, table: str, limit: int | None = None) -> Iterator[dict]:
        conn = self._connect()
        table = validate_table_identifier(table)
        cur = conn.cursor()
        sql = f"SELECT * FROM {table}"
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        try:
            for row in cur:
                data = dict(zip(cols, row))
                yield data
                if limit is not None:
                    limit -= 1
                    if limit <= 0:
                        break
        finally:
            cur.close()
            conn.close()

    def list_tables(self) -> list[dict]:
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute("SELECT table_name AS name FROM user_tables ORDER BY table_name")
            return [{"name": row[0]} for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    def describe_table(self, table: str) -> list[dict]:
        table = validate_table_identifier(table)
        conn = self._connect()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT column_name AS name, data_type AS type, nullable "
                "FROM user_tab_columns WHERE table_name=:table_name "
                "ORDER BY column_id",
                table_name=table.upper(),
            )
            return [{"name": row[0], "type": row[1], "nullable": row[2]} for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

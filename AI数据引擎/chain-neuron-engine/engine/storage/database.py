# -*- coding: utf-8 -*-
"""SQLite 数据库引擎与会话管理。"""
from __future__ import annotations

from collections.abc import Generator
from decimal import Decimal

from sqlalchemy import create_engine, event
from sqlalchemy import Text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from engine.core.config import DB_PATH


class DecimalString(TypeDecorator):
    """以文本形式存储 Decimal，杜绝 SQLite 浮点误差。"""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, Decimal):
            return format(value, "f")
        return format(Decimal(str(value)), "f")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value)


class Base(DeclarativeBase):
    pass


class _DecimalSum:
    """SQLite 精确十进制求和，避免内置 SUM 把 TEXT 指标转成 float。"""

    def __init__(self) -> None:
        self.total = Decimal("0")
        self.found = False

    def step(self, value) -> None:
        if value in (None, ""):
            return
        self.total += Decimal(str(value))
        self.found = True

    def finalize(self):
        return format(self.total, "f") if self.found else None


engine = create_engine(
    f"sqlite:///{DB_PATH.as_posix()}",
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    """让导入写入与进度查询在 SQLite 上可短暂等待而不是立即锁失败。"""
    dbapi_connection.create_aggregate("decimal_sum", 1, _DecimalSum)
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """建表（导入各 ORM 模块以注册 metadata）。"""
    # 延迟导入，避免循环引用
    from engine.storage import models  # noqa: F401
    from engine.security import models as security_models  # noqa: F401
    from engine.ingestion import models as ingest_models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # 已有库补建复合索引（create_all 不会修改已存在的表）
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_parent "
            "ON spiral_nodes(dataset, parent_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_level "
            "ON spiral_nodes(dataset, level)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_level_name_value "
            "ON spiral_nodes(dataset, level_name, node_value, record_count)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_grown_level_record "
            "ON spiral_nodes(dataset, grown, level, record_count)"
        )
        ensure_columns(conn, "spiral_nodes", [
            ("topology_version", "INTEGER DEFAULT 0"),
            ("topology_role", "VARCHAR(16)"),
            ("helix_layer", "INTEGER"),
            ("ring_index", "INTEGER"),
            ("strand_a_prev", "VARCHAR(512)"),
            ("strand_a_next", "VARCHAR(512)"),
            ("strand_b_prev", "VARCHAR(512)"),
            ("strand_b_next", "VARCHAR(512)"),
            ("pair_node_id", "VARCHAR(512)"),
            ("feature_slots", "JSON"),
            ("temporal_layer", "INTEGER"),
            ("time_bucket", "VARCHAR(64)"),
            ("time_granularity", "VARCHAR(16)"),
            ("branch_depth", "INTEGER"),
            ("growth_parent_id", "VARCHAR(512)"),
            ("growth_dimension", "VARCHAR(64)"),
            ("capacity_snapshot", "INTEGER"),
            ("growth_version", "INTEGER"),
        ])
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_topology_version "
            "ON spiral_nodes(topology_version)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_helix_ring "
            "ON spiral_nodes(dataset, helix_layer, ring_index)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_pair "
            "ON spiral_nodes(dataset, pair_node_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_role_layer_ring "
            "ON spiral_nodes(dataset, topology_role, helix_layer, ring_index)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_temporal_bucket_ring "
            "ON spiral_nodes(dataset, temporal_layer, time_bucket, ring_index)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_spiral_nodes_ds_role_temporal_ring "
            "ON spiral_nodes(dataset, topology_role, temporal_layer, time_bucket, ring_index)"
        )

        # 已有库补列（SQLite 支持 ADD COLUMN；create_all 不改已有表）
        ensure_columns(conn, "import_task", [
            ("mode", "VARCHAR(16) DEFAULT 'dedupe'"),
            ("source_kind", "VARCHAR(16) DEFAULT 'excel'"),
            ("source_ref", "JSON"),
            ("phase", "VARCHAR(32) DEFAULT 'queued'"),
        ])
        # 兼容升级前已完成的任务：避免刷新页面后出现“成功但排队中/总数 0”。
        conn.exec_driver_sql(
            "UPDATE import_task SET total = processed "
            "WHERE total = 0 AND processed > 0 "
            "AND status IN ('success', 'partial', 'failed')"
        )
        conn.exec_driver_sql(
            "UPDATE import_task SET phase = "
            "CASE WHEN status IN ('success', 'partial') THEN 'completed' "
            "WHEN status = 'failed' THEN 'failed' ELSE phase END "
            "WHERE status IN ('success', 'partial', 'failed')"
        )
    # 通用数据集（generic）动态表 + 维表（dimension）：按注册表声明建表
    ensure_registry_tables()


def ensure_columns(conn, table: str, specs: list[tuple[str, str]]) -> None:
    """缺列补列：检查 PRAGMA table_info，缺失则 ALTER TABLE ADD COLUMN。"""
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    existing = {r[1] for r in rows}
    for name, ddl_type in specs:
        if name not in existing:
            conn.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"
            )


def ensure_registry_tables() -> list[str]:
    """为注册表中全部通用数据集与维表建物理表（幂等）。"""
    from engine.storage.dynamic_table import (
        ensure_dim_tables, ensure_dynamic_tables, register_metadata,
    )
    register_metadata()
    dynamic_ids = ensure_dynamic_tables(engine)
    dim_ids = ensure_dim_tables(engine)
    # 注册声明可能在已有数据库上新增字段（例如记录主键）；create_all
    # 不会补列，因此启动时按当前 DatasetSpec 做一次安全的 ADD COLUMN。
    from engine.storage.dynamic_table import get_dynamic_dim_table, get_dynamic_table
    from engine.storage.dataset_registry import registry
    with engine.begin() as conn:
        for dataset in dynamic_ids:
            table = get_dynamic_table(registry.require(dataset))
            ensure_columns(conn, table.name, [
                (column.name, "TEXT" if isinstance(column.type, DecimalString)
                 else "VARCHAR(128)")
                for column in table.columns
            ])
        for dataset in dim_ids:
            table = get_dynamic_dim_table(registry.require(dataset))
            ensure_columns(conn, table.name, [
                (column.name, "VARCHAR(128)") for column in table.columns
            ])
    return dynamic_ids + dim_ids


def get_session() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def session_scope() -> Session:
    """非 Web 场景直接获取会话（调用方负责关闭）。"""
    return SessionLocal()

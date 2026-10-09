# -*- coding: utf-8 -*-
"""动态事实表：按数据集声明构建物理表（通用数据集零代码接入）。

列类型策略（精度红线）：
- 维度列 → String（文本比较，参与节点路径）；
- 指标列 → DecimalString（以 TEXT 存储 Decimal，禁用 float）；
- 主键 id → Integer 自增。
"""
from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.storage.database import Base, DecimalString
from engine.storage.dataset_registry import DatasetSpec, registry

logger = get_logger("storage.dynamic_table")

DIM_COLUMN_LENGTH = 128
# 引擎保留列：动态事实表的代理主键（见 build_table）。
# 声明中的层级/指标不得占用，否则构建 Table 时会抛 DuplicateColumnError。
RESERVED_FACT_COLUMNS = frozenset({"id"})
_METADATA = MetaData()
# 动态表缓存：dataset → (注册表 revision, Table)
_CACHE: dict[str, tuple[int, Table]] = {}


def metric_columns_of(spec: DatasetSpec) -> set[str]:
    """指标物理列名集合。"""
    return {m.column for m in spec.metrics}


def dimension_columns_of(spec: DatasetSpec) -> list[str]:
    """维度物理列名（fact_columns 去掉指标列，保持声明顺序）。"""
    metrics = metric_columns_of(spec)
    return [c for c in spec.fact_columns if c not in metrics]


def build_table(spec: DatasetSpec) -> Table:
    """按声明构建 Table 对象（不落库）。"""
    metrics = metric_columns_of(spec)
    cols = [Column("id", Integer, primary_key=True, autoincrement=True)]
    if spec.kind == "generic":
        cols.append(Column("__time", String(DIM_COLUMN_LENGTH), index=True))
    for name in spec.fact_columns:
        if name in metrics:
            cols.append(Column(name, DecimalString))
        else:
            cols.append(Column(name, String(DIM_COLUMN_LENGTH), index=True))
    return Table(spec.fact_table, _METADATA, *cols)


def get_dynamic_table(spec: DatasetSpec) -> Table:
    """取动态表（按注册表 revision 缓存，声明变更后自动重建）。"""
    cached = _CACHE.get(spec.id)
    if cached and cached[0] == registry.revision:
        return cached[1]
    # 声明可能已变更（界面改列/改类型）：先移除旧定义，避免同名 Table 冲突
    old = _METADATA.tables.get(spec.fact_table)
    if old is not None:
        _METADATA.remove(old)
    table = build_table(spec)
    _CACHE[spec.id] = (registry.revision, table)
    return table


def ensure_dynamic_tables(engine: Engine) -> list[str]:
    """为全部通用数据集建表（幂等）。返回建表的数据集 id 列表。"""
    created: list[str] = []
    for spec in registry.specs_of_kind("generic"):
        if not spec.fact_table:
            continue
        get_dynamic_table(spec).create(bind=engine, checkfirst=True)
        created.append(spec.id)
    if created:
        logger.info("动态事实表就绪 datasets=%s", created)
    return created


def drop_dynamic_table(spec: DatasetSpec, engine: Engine) -> None:
    """删除动态表（数据集注销时清理数据）。"""
    table = get_dynamic_table(spec)
    table.drop(bind=engine, checkfirst=True)
    _CACHE.pop(spec.id, None)
    logger.info("动态事实表已删除 dataset=%s table=%s", spec.id, spec.fact_table)


def build_dim_table(spec: DatasetSpec) -> Table:
    """按声明构建维表 Table 对象（主键列 + 文本列，不落库）。"""
    pk = spec.primary_column
    cols = [
        Column(c.name, String(DIM_COLUMN_LENGTH), primary_key=True)
        if c.name == pk else Column(c.name, String(DIM_COLUMN_LENGTH))
        for c in spec.columns
    ]
    return Table(spec.dim_table, _METADATA, *cols)


def get_dynamic_dim_table(spec: DatasetSpec) -> Table:
    """取维表（按注册表 revision 缓存，声明变更后自动重建）。"""
    cached = _CACHE.get(spec.id)
    if cached and cached[0] == registry.revision:
        return cached[1]
    # 声明可能已变更：先移除旧定义，避免同名 Table 冲突
    old = _METADATA.tables.get(spec.dim_table)
    if old is not None:
        _METADATA.remove(old)
    table = build_dim_table(spec)
    _CACHE[spec.id] = (registry.revision, table)
    return table


def ensure_dim_tables(engine: Engine) -> list[str]:
    """为全部维表数据集建表（幂等）。返回建表的数据集 id 列表。"""
    created: list[str] = []
    for spec in registry.specs_of_kind("dimension"):
        if not spec.dim_table:
            continue
        get_dynamic_dim_table(spec).create(bind=engine, checkfirst=True)
        created.append(spec.id)
    if created:
        logger.info("维表就绪 datasets=%s", created)
    return created


def drop_dim_table(spec: DatasetSpec, engine: Engine) -> None:
    """删除维表（数据集注销时清理数据）。"""
    table = get_dynamic_dim_table(spec)
    table.drop(bind=engine, checkfirst=True)
    _CACHE.pop(spec.id, None)
    logger.info("维表已删除 dataset=%s table=%s", spec.id, spec.dim_table)


class TableFactSource:
    """通用数据集：基于动态 Table 的事实表访问（接口同 ORMFactSource）。"""

    def __init__(self, dataset: str, table: Table):
        self.dataset = dataset
        self.spec = registry.require(dataset)
        self.table = table

    def column(self, key: str):
        return self.table.c.get(key)

    def has_column(self, key: str) -> bool:
        return key in self.table.c

    def insert_many(self, session: Session, records: list[dict]) -> None:
        cols = [c for c in ("__time", *self.spec.fact_columns) if c in self.table.c]
        if not records:
            return
        session.execute(
            self.table.insert(),
            [{k: r.get(k) for k in cols} for r in records],
        )

    def delete_all(self, session: Session) -> int:
        return session.execute(self.table.delete()).rowcount

    def all_rows(self, session: Session):
        return session.execute(select(self.table)).yield_per(2000)

    def fingerprint_values(self, session: Session, keys: tuple[str, ...]) -> list[tuple]:
        cols = [self.table.c[k] for k in keys if k in self.table.c]
        if not cols:
            return []
        return list(session.execute(select(*cols)).all())

    def count(self, session: Session) -> int:
        from sqlalchemy import func as sa_func

        return session.execute(
            select(sa_func.count()).select_from(self.table)
        ).scalar_one()


class DimTableSource:
    """维表/主数据：按主键 upsert 到独立维表（不建螺旋节点、不参与聚合）。"""

    def __init__(self, dataset: str, table: Table):
        self.dataset = dataset
        self.spec = registry.require(dataset)
        self.table = table
        self.pk = self.spec.primary_column
        self.columns = [c.name for c in self.spec.columns if c.name in table.c]

    def upsert_many(self, session: Session, records: list[dict]) -> None:
        for rec in records:
            stmt = sqlite_insert(self.table).values(
                **{k: rec.get(k) for k in self.columns}
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[self.pk],
                set_={k: stmt.excluded[k] for k in self.columns if k != self.pk},
            )
            session.execute(stmt)

    def delete_all(self, session: Session) -> int:
        return session.execute(self.table.delete()).rowcount

    def all_rows(self, session: Session) -> list:
        return list(session.execute(select(self.table)).all())

    def count(self, session: Session) -> int:
        from sqlalchemy import func as sa_func

        return session.execute(
            select(sa_func.count()).select_from(self.table)
        ).scalar_one()


# 让 Base.metadata 感知动态表（init_db 的 create_all 可一并建表）
def register_metadata() -> None:
    for spec in registry.specs_of_kind("generic"):
        table = get_dynamic_table(spec)
        if table.key not in Base.metadata.tables:
            table.to_metadata(Base.metadata)
    for spec in registry.specs_of_kind("dimension"):
        table = get_dynamic_dim_table(spec)
        if table.key not in Base.metadata.tables:
            table.to_metadata(Base.metadata)

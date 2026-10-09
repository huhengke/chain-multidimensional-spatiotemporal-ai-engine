# -*- coding: utf-8 -*-
"""通用事实表访问抽象：所有事实数据均由数据集注册表驱动。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.orm import Session

from engine.core.logging import get_logger
from engine.storage.dataset_registry import KIND_DIMENSION, registry
logger = get_logger("storage.facts")


class FactSource(ABC):
    """事实表读写统一接口（按数据集声明驱动）。"""

    def __init__(self, dataset: str):
        self.dataset = dataset
        self.spec = registry.require(dataset)

    # ---------------- 列访问 ----------------

    @abstractmethod
    def column(self, key: str):
        """返回物理列对象；列不存在时返回 None。"""

    @abstractmethod
    def has_column(self, key: str) -> bool:
        """列是否存在。"""

    # ---------------- 写入 ----------------

    @abstractmethod
    def insert_many(self, session: Session, records: list[dict]) -> None:
        """批量写入（仅取声明过的列）。"""

    @abstractmethod
    def delete_all(self, session: Session) -> int:
        """清空该数据集事实表，返回删除行数。"""

    # ---------------- 读取 ----------------

    @abstractmethod
    def all_rows(self, session: Session) -> Iterable:
        """流式读取全部行（重算/校验用；返回支持属性访问的行对象）。"""

    @abstractmethod
    def fingerprint_values(self, session: Session, keys: tuple[str, ...]) -> list[tuple]:
        """按指纹列读取去重基准值。"""

    def count(self, session: Session) -> int:
        return session.execute(
            select(sa_func.count()).select_from(self.table)
        ).scalar_one()


def get_fact_source(dataset: str) -> FactSource:
    """按注册表声明取动态事实表访问器。"""
    spec = registry.require(dataset)
    if not spec.is_spiral:
        raise ValueError(f"数据集 {dataset}（kind={spec.kind}）没有事实表")
    # 延迟导入，避免与动态表模块循环依赖
    from engine.storage.dynamic_table import TableFactSource, get_dynamic_table

    return TableFactSource(dataset, get_dynamic_table(spec))


def get_dim_source(dataset: str):
    """按数据集取维表访问器（kind=dimension）。"""
    spec = registry.require_any(dataset)
    if spec.kind != KIND_DIMENSION:
        raise ValueError(f"数据集 {dataset}（kind={spec.kind}）不是维表")
    from engine.storage.dynamic_table import DimTableSource, get_dynamic_dim_table

    return DimTableSource(dataset, get_dynamic_dim_table(spec))

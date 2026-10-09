# -*- coding: utf-8 -*-
"""连接器基类与注册表。"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from engine.core.errors import ErrorCode, AppException
from engine.core.logging import get_logger

logger = get_logger("ingestion.connector")

_ENV_RE = re.compile(r"\$\{(\w+)\}")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def expand_env(value: Any) -> Any:
    """${ENV_NAME} 占位 → 环境变量值。"""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    return value


def validate_table_identifier(table: str) -> str:
    """只允许数据库表名或 schema.table，避免把 UI 输入拼进 SQL。"""
    parts = str(table or "").split(".")
    if not parts or len(parts) > 2 or not all(_IDENTIFIER_RE.match(part) for part in parts):
        raise ValueError("表名只允许字母、数字、下划线和可选 schema.table")
    return ".".join(parts)


class BaseConnector(ABC):
    type_name: str = "base"

    def __init__(self, config: dict):
        self.config = {k: expand_env(v) for k, v in config.items()}

    @abstractmethod
    def test_connection(self) -> dict:
        """测试连通性，返回 {ok, latency_ms, message}。"""

    @abstractmethod
    def fetch_rows(self, table: str, limit: int | None = None) -> Iterator[dict]:
        """拉取行数据（统一产出字典迭代器，进入与 Excel 相同的映射链路）。"""

    def list_tables(self) -> list[dict]:
        """返回可注册的表/集合。具体连接器按数据库方言实现。"""
        raise NotImplementedError("当前数据源驱动未实现表发现")

    def describe_table(self, table: str) -> list[dict]:
        """返回表字段元数据：name/type/nullable。"""
        raise NotImplementedError("当前数据源驱动未实现字段发现")

    def close(self) -> None:  # pragma: no cover - 默认无资源
        pass


_CONNECTOR_TYPES: dict[str, type[BaseConnector]] = {}


def register_connector(cls: type[BaseConnector]) -> type[BaseConnector]:
    _CONNECTOR_TYPES[cls.type_name] = cls
    return cls


def create_connector(config: dict) -> BaseConnector:
    ctype = config.get("type")
    cls = _CONNECTOR_TYPES.get(ctype)
    if cls is None:
        raise AppException(
            ErrorCode.UNSUPPORTED_FILE[0],
            f"不支持的数据源类型：{ctype}",
            http_status=400,
        )
    return cls(config)

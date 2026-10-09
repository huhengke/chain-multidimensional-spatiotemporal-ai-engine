# -*- coding: utf-8 -*-
"""读取已登记的数据源配置，并集中处理数据源 id 校验。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.core.config import ENGINE_CONFIG_DIR, load_yaml_config
from engine.core.errors import NotFoundError

DATASOURCE_FILE = ENGINE_CONFIG_DIR / "datasource.yaml"


def list_datasource_configs() -> list[dict[str, Any]]:
    raw = load_yaml_config(DATASOURCE_FILE)
    return [dict(item) for item in raw.get("datasources", []) or []]


def get_datasource_config(source_id: str) -> dict[str, Any]:
    for item in list_datasource_configs():
        if str(item.get("id") or item.get("name") or "") == source_id:
            return item
    raise NotFoundError("数据源不存在", source_id)


_SENSITIVE_KEYS = {
    "password", "passwd", "secret", "api_key", "token", "access_token",
    "refresh_token", "private_key", "connection_string", "dsn",
}


def safe_datasource_config(item: dict[str, Any]) -> dict[str, Any]:
    """返回数据源元数据；任何凭据或可直接连接的字符串都不出现在响应中。"""
    return {
        key: ("***" if key.lower() in _SENSITIVE_KEYS and value else value)
        for key, value in item.items()
    }

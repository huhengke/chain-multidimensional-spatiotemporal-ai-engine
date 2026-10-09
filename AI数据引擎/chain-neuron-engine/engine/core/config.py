# -*- coding: utf-8 -*-
"""全局配置：路径、环境变量、运行参数。

路径策略（参考可移植性经验）：
- 工程根默认自动探测（本文件向上 3 级），也允许用环境变量 CHAIN_ENGINE_ROOT 覆盖；
- 外部数据源默认位于工程根同级的“数据源”目录，也允许用 CHAIN_SOURCE_DATA_ROOT 覆盖。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml


def _detect_engine_root() -> Path:
    env = os.environ.get("CHAIN_ENGINE_ROOT")
    if env:
        return Path(env).resolve()
    # engine/core/config.py -> engine/core -> engine -> <root>
    return Path(__file__).resolve().parents[2]


ENGINE_ROOT: Path = _detect_engine_root()
SOURCE_DATA_ROOT: Path = Path(
    os.environ.get("CHAIN_SOURCE_DATA_ROOT", str(ENGINE_ROOT.parent / "数据源"))
).resolve()
# 兼容旧导入/重初始化代码；其语义现为“外部数据源根目录”。
WORKSPACE_ROOT: Path = SOURCE_DATA_ROOT

DATA_DIR = ENGINE_ROOT / "data"
SPIRAL_DIR = DATA_DIR / "spiral"
VECTORS_DIR = DATA_DIR / "vectors"
BLOBS_DIR = DATA_DIR / "blobs"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "engine.db"

ENGINE_CONFIG_DIR = ENGINE_ROOT / "engine" / "config"
CONTRACTS_DIR = ENGINE_ROOT / "docs" / "contracts"

# 测试数据（只读）
TEST_PLAN_XLSX = WORKSPACE_ROOT / "定位单元业务域甲计划_2020后.xlsx"
TEST_DAILY_XLSX = WORKSPACE_ROOT / "定位单元级业务域甲日报_2024后.xlsx"

for _p in (SPIRAL_DIR, VECTORS_DIR, BLOBS_DIR, LOG_DIR):
    _p.mkdir(parents=True, exist_ok=True)


class Settings:
    """运行参数（环境变量覆盖）。"""

    ENGINE_PORT: int = int(os.environ.get("ENGINE_PORT", "8000"))
    AGENT_PORT: int = int(os.environ.get("AGENT_PORT", "8001"))
    AGENT_BASE_URL: str = os.environ.get("AGENT_BASE_URL", "http://127.0.0.1:8001")

    JWT_SECRET: str = os.environ.get("JWT_SECRET", "chain-neuron-dev-secret-change-me-32chars")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.environ.get("JWT_EXPIRE_MINUTES", "480"))

    ENGINE_SERVICE_TOKEN: str = os.environ.get(
        "ENGINE_SERVICE_TOKEN", "dev-service-token-please-rotate"
    )

    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

    # 生长阈值（默认 10000；可用环境变量临时调低做演示）
    DEFAULT_GROWTH_THRESHOLD: int = int(os.environ.get("GROWTH_THRESHOLD", "10000"))

    IMPORT_BATCH_SIZE: int = int(os.environ.get("IMPORT_BATCH_SIZE", "2000"))
    REINITIALIZE_BACKUP_KEEP_COUNT: int = max(
        1, int(os.environ.get("REINITIALIZE_BACKUP_KEEP_COUNT", "1"))
    )

    CORS_ORIGINS: tuple[str, ...] = tuple(
        filter(
            None,
            os.environ.get(
                "CORS_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(","),
        )
    )

    # LLM（Agent 层使用，引擎不调用，仅集中声明）
    OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.environ.get("OLLAMA_MODEL", "deepseek-v4.1-flash")
    DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.environ.get(
        "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
    )
    DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    CODEX_BASE_URL: str = os.environ.get(
        "CODEX_BASE_URL", "http://sub2.deepermobile.cn:8080"
    )
    CODEX_MODEL: str = os.environ.get("CODEX_MODEL", "gpt-5.6-luna")
    CODEX_AUTH_FILE: str = os.environ.get(
        "CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json")
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(path: Path) -> dict:
    """读取 YAML 配置；文件不存在时返回空字典。"""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}

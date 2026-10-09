# -*- coding: utf-8 -*-
"""模型供应配置读取器。

只暴露非敏感配置和凭据引用；真实 key 仍由环境变量或本机凭据文件提供。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from engine.core.config import DATA_DIR, ENGINE_CONFIG_DIR, get_settings

CONFIG_PATH = ENGINE_CONFIG_DIR / "model_configs.yaml"
RUNTIME_CONFIG_PATH = DATA_DIR / "model_runtime.yaml"


def _raw() -> dict:
    if not CONFIG_PATH.exists():
        return {"version": 1, "active": "codex_openai", "providers": []}
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _runtime() -> dict:
    if not RUNTIME_CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(RUNTIME_CONFIG_PATH.read_text(encoding="utf-8")) or {}


def _auth_file_has_key(path: str) -> bool:
    """只检查 Codex 本地认证文件是否含凭据，不返回或记录凭据内容。"""
    try:
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and bool(
        str(payload.get("OPENAI_API_KEY") or "").strip()
    )


def public_model_config() -> dict:
    settings = get_settings()
    raw = _raw()
    runtime = _runtime()
    providers = []
    for item in raw.get("providers", []):
        item = dict(item)
        key_env = str(item.get("api_key_env") or "")
        auth_file_env = str(item.get("auth_file_env") or "")
        model_env = str(item.get("model_env") or "")
        base_env = str(item.get("base_url_env") or "")
        override = runtime.get("providers", {}).get(item.get("id"), {})
        item["supports_images"] = bool(
            override.get("supports_images", item.get("supports_images", False))
        )
        item["base_url"] = os.environ.get(
            base_env, override.get("base_url", item.get("default_base_url"))
        )
        item["model"] = os.environ.get(
            model_env, override.get("model", item.get("default_model"))
        )
        auth_file = os.environ.get(
            auth_file_env,
            str(Path.home() / ".codex" / "auth.json") if auth_file_env else "",
        )
        item["api_key_configured"] = bool(os.environ.get(key_env)) or (
            bool(auth_file) and _auth_file_has_key(auth_file)
        )
        item.pop("api_key_env", None)
        item.pop("auth_file_env", None)
        item.pop("default_base_url", None)
        item.pop("default_model", None)
        providers.append(item)
    return {
        "version": raw.get("version", 1),
        "active": runtime.get("active", raw.get("active", "codex_openai")),
        "providers": providers,
        "source": str(CONFIG_PATH),
        "engine_llm_boundary": "引擎只读取配置元数据；模型调用由 Agent Runtime 执行",
        "fallback": {
            "ollama_base_url": settings.OLLAMA_BASE_URL,
            "ollama_model": settings.OLLAMA_MODEL,
        },
    }


def update_model_config(payload: dict) -> dict:
    raw = _raw()
    providers = {str(item.get("id")): item for item in raw.get("providers", [])}
    active = str(payload.get("active") or raw.get("active") or "")
    if active not in providers:
        raise ValueError(f"未知模型通道：{active}")
    provider_updates: dict[str, dict[str, object]] = {}
    for provider_id, values in (payload.get("providers") or {}).items():
        if provider_id not in providers or not isinstance(values, dict):
            continue
        update: dict[str, str] = {}
        for key in ("base_url", "model"):
            value = str(values.get(key) or "").strip()
            if value:
                update[key] = value[:300]
        if isinstance(values.get("supports_images"), bool):
            update["supports_images"] = values["supports_images"]
        if update:
            provider_updates[provider_id] = update
    body = {"version": 1, "active": active, "providers": provider_updates}
    RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = RUNTIME_CONFIG_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(body, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    tmp.replace(RUNTIME_CONFIG_PATH)
    return public_model_config()

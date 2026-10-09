# -*- coding: utf-8 -*-
"""模型通道只读冒烟测试。

不修改模型配置、不写入数据集、不触发导入；只验证当前配置展示、LLM 直连
降级链路，以及 Agent 字段分析接口是否可用。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.python_agent.llm_client import LLMClient  # noqa: E402
from engine.core.config import get_settings  # noqa: E402
from engine.model_config import public_model_config  # noqa: E402


def _public_config() -> dict[str, Any]:
    config = public_model_config()
    providers = []
    for provider in config.get("providers", []):
        item = dict(provider)
        item.pop("api_key", None)
        providers.append(item)
    return {
        "active": config.get("active"),
        "providers": providers,
        "fallback": config.get("fallback"),
    }


def _direct_model_check() -> dict[str, Any]:
    client = LLMClient()
    started = time.perf_counter()
    try:
        text = client.chat(
            [{
                "role": "user",
                "content": (
                    "你是字段分析助手，只返回 JSON："
                    '{"ok":true,"message":"model channel smoke test"}'
                ),
            }],
            expect_json=True,
        )
        parsed = json.loads(text)
        return {
            "ok": isinstance(parsed, dict) and client.backend != "mock",
            "smoke_ok": isinstance(parsed, dict),
            "real_backend": client.backend != "mock",
            "backend": client.backend,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "response_keys": sorted(parsed) if isinstance(parsed, dict) else [],
            "expected_backend": "codex",
        }
    except Exception as exc:  # pragma: no cover - depends on local channels
        return {
            "ok": False,
            "smoke_ok": False,
            "real_backend": False,
            "backend": client.backend,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(exc),
        }


def _agent_field_check() -> dict[str, Any]:
    settings = get_settings()
    base_url = settings.AGENT_BASE_URL.rstrip("/")
    columns = [
        {
            "source": "Month",
            "sample": "202401",
            "numeric_ratio": 1,
            "time_ratio": 1,
            "unique_ratio": 1,
            "rule_role": "time",
            "semantic_type": "time",
            "base_candidate": False,
            "feature_layer": "base",
            "query_uses": ["filter", "group_by"],
        },
        {
            "source": "GasCode",
            "sample": "92",
            "numeric_ratio": 1,
            "time_ratio": 0,
            "unique_ratio": 0.2,
            "rule_role": "dimension",
            "semantic_type": "product_code",
            "base_candidate": True,
            "feature_layer": "base",
            "query_uses": ["filter", "group_by", "join"],
        },
        {
            "source": "Amount",
            "sample": "12.5",
            "numeric_ratio": 1,
            "time_ratio": 0,
            "unique_ratio": 0.8,
            "rule_role": "metric",
            "semantic_type": "other",
            "base_candidate": False,
            "feature_layer": "branch",
            "query_uses": ["aggregate", "sort", "compare"],
        },
    ]
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{base_url}/api/v1/analyze/fields",
            json={"source_name": "model-channel-smoke", "columns": columns},
            timeout=65,
        )
        body = response.json()
        returned = body.get("columns") or []
        contract_ok = all(
            item.get("feature_layer") in {"base", "trunk", "branch", "none"}
            and isinstance(item.get("query_uses"), list)
            for item in returned
        )
        return {
            "ok": response.is_success and bool(returned) and contract_ok,
            "status_code": response.status_code,
            "backend": body.get("backend"),
            "column_count": len(returned),
            "recommendation_contract_ok": contract_ok,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }
    except Exception as exc:  # pragma: no cover - depends on local agent
        return {
            "ok": False,
            "status_code": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "error": str(exc),
        }


def main() -> int:
    result = {
        "read_only": True,
        "model_config": _public_config(),
        "direct_model": _direct_model_check(),
        "agent_field_analysis": _agent_field_check(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if (
        result["direct_model"].get("ok")
        and result["direct_model"].get("backend") == "codex"
        and result["agent_field_analysis"].get("ok")
        and result["agent_field_analysis"].get("backend") == "codex"
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

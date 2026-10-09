# -*- coding: utf-8 -*-
"""LLMClient：Codex 优先的多级降级。

1) 当前 Codex Responses 通道 — 主；
2) DeepSeek 云端 OpenAI 兼容接口 / 本地 Ollama — 兜底；
3) Mock 规则应答 — 最终降级（无 LLM 仍可演示）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from engine.core.config import get_settings
from engine.core.logging import get_logger
from engine.model_config import public_model_config

logger = get_logger("agent.llm")


def _codex_auth_key(path: str) -> str:
    """读取 Codex 本地凭据；只在进程内使用，禁止进入日志或返回值。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = payload.get("OPENAI_API_KEY") if isinstance(payload, dict) else ""
    return str(value or "").strip()


def _responses_text(body: dict[str, Any]) -> str:
    output_text = body.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    chunks: list[str] = []
    for item in body.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


class LLMClient:
    def __init__(self):
        s = get_settings()
        model_config = public_model_config()
        providers = {str(p.get("id")): p for p in model_config.get("providers", [])}
        ollama = providers.get("ollama_local", {})
        deepseek = providers.get("deepseek_harness", {})
        codex = providers.get("codex_openai", {})
        self.active = str(model_config.get("active") or "deepseek_harness")
        self.ollama_url = str(ollama.get("base_url") or s.OLLAMA_BASE_URL)
        self.ollama_model = str(ollama.get("model") or s.OLLAMA_MODEL)
        self.api_key = s.DEEPSEEK_API_KEY
        self.api_base = str(deepseek.get("base_url") or s.DEEPSEEK_BASE_URL)
        self.cloud_model = str(deepseek.get("model") or s.DEEPSEEK_MODEL)
        self.codex_url = str(codex.get("base_url") or s.CODEX_BASE_URL)
        self.codex_model = str(codex.get("model") or s.CODEX_MODEL)
        self.codex_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.codex_api_key:
            auth_file = os.environ.get(
                "CODEX_AUTH_FILE", str(Path.home() / ".codex" / "auth.json")
            )
            self.codex_api_key = _codex_auth_key(auth_file)
        self.backend: str = "mock"

    # ---------------- chat ----------------

    def chat(self, messages: list[dict], expect_json: bool = False) -> str:
        """依次尝试三级后端，返回文本。"""
        if self.active == "codex_openai":
            preferred = (self._codex, self._cloud, self._ollama)
        elif self.active == "deepseek_harness":
            preferred = (self._cloud, self._ollama)
        else:
            preferred = (self._ollama, self._cloud)
        for attempt in (*preferred, self._mock):
            try:
                text = attempt(messages, expect_json)
                self.backend = attempt.__name__[1:]
                logger.info("LLM 后端命中：%s", self.backend)
                return text
            except Exception as e:
                logger.warning("LLM 后端 %s 失败：%s", attempt.__name__, e)
        return ""

    # ---------------- 1) Ollama ----------------

    def _codex(self, messages: list[dict], expect_json: bool) -> str:
        if not self.codex_api_key:
            raise RuntimeError("未找到 Codex 本地认证或 OPENAI_API_KEY")
        payload: dict[str, Any] = {
            "model": self.codex_model,
            "input": messages,
            "store": False,
            "max_output_tokens": 4096,
        }
        if expect_json:
            payload["text"] = {"format": {"type": "json_object"}}
        response = httpx.post(
            f"{self.codex_url.rstrip('/')}/responses",
            headers={
                "Authorization": f"Bearer {self.codex_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        text = _responses_text(response.json())
        if not text:
            raise RuntimeError("Codex Responses 未返回文本内容")
        return text

    def _ollama(self, messages: list[dict], expect_json: bool) -> str:
        r = httpx.post(
            f"{self.ollama_url}/api/chat",
            json={
                "model": self.ollama_model,
                "messages": messages,
                "stream": False,
                "format": "json" if expect_json else None,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    # ---------------- 2) 云端 ----------------

    def _cloud(self, messages: list[dict], expect_json: bool) -> str:
        if not self.api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY")
        r = httpx.post(
            f"{self.api_base}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.cloud_model, "messages": messages,
                  "temperature": 0.1},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    # ---------------- 3) Mock ----------------

    def _mock(self, messages: list[dict], expect_json: bool) -> str:
        user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        if expect_json:
            if "字段分析助手" in user or "\"columns\"" in user:
                return json.dumps({"columns": []}, ensure_ascii=False)
            # Local import avoids the intent -> LLMClient module cycle at import time.
            from agent.python_agent.skills.intent import _mock_intent
            return json.dumps(_mock_intent(user), ensure_ascii=False)
        return user  # 自然语言环节由 orchestrator 模板兜底

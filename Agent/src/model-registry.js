import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

import { config } from "./config.js";

const CACHE_MS = 5000;
const DISPLAY_NAMES = {
  codex_openai: "Codex Responses",
  deepseek_harness: "DeepSeek API",
  ollama_local: "Ollama Local",
};
let cached = null;
let cachedAt = 0;

async function engineModelConfig() {
  if (cached && Date.now() - cachedAt < CACHE_MS) return cached;
  const response = await fetch(`${config.engineBaseUrl}/api/v1/engine/model-config`, {
    headers: {
      "X-Service-Token": config.serviceToken,
      "X-Trace-Id": `agent-models-${randomUUID()}`,
    },
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) {
    throw Object.assign(new Error(`读取模型配置失败：HTTP ${response.status}`), {
      code: "MODEL_CONFIG_UNAVAILABLE",
      status: 503,
    });
  }
  cached = await response.json();
  cachedAt = Date.now();
  return cached;
}

function publicModel(item, active) {
  return {
    id: String(item.id),
    name: String(item.name || DISPLAY_NAMES[item.id] || item.id),
    provider: String(item.provider || ""),
    protocol: String(item.protocol || ""),
    model: String(item.model || ""),
    base_url: String(item.base_url || ""),
    available: Boolean(item.api_key_configured),
    active: item.id === active,
    supports_images: Boolean(item.supports_images),
  };
}

async function ollamaIsReady(baseUrl) {
  try {
    const response = await fetch(`${String(baseUrl || "http://127.0.0.1:11434").replace(/\/$/, "")}/api/tags`, {
      signal: AbortSignal.timeout(1200),
    });
    return response.ok;
  } catch {
    return false;
  }
}

export async function listAgentModels() {
  const source = await engineModelConfig();
  const models = (source.providers || []).map((item) => publicModel(item, source.active));
  const ollama = models.find((item) => item.id === "ollama_local");
  if (ollama) ollama.available = await ollamaIsReady(ollama.base_url);
  return {
    active: models.some((item) => item.id === source.active && item.available)
      ? source.active
      : models.find((item) => item.available)?.id || source.active,
    items: models,
  };
}

async function codexApiKey() {
  if (String(process.env.OPENAI_API_KEY || "").trim()) return process.env.OPENAI_API_KEY.trim();
  const path = process.env.CODEX_AUTH_FILE || join(homedir(), ".codex", "auth.json");
  try {
    const payload = JSON.parse(await readFile(path, "utf8"));
    return String(payload.OPENAI_API_KEY || "").trim();
  } catch {
    return "";
  }
}

function ollamaBaseUrl(value) {
  const base = String(value || "http://127.0.0.1:11434").replace(/\/$/, "");
  return base.endsWith("/v1") ? base : `${base}/v1`;
}

export async function resolveAgentModel(modelId) {
  const source = await engineModelConfig();
  const id = String(modelId || source.active || "");
  const item = (source.providers || []).find((provider) => provider.id === id);
  if (!item) {
    throw Object.assign(new Error(`未知模型：${id}`), { code: "MODEL_NOT_FOUND", status: 422 });
  }

  if (id === "codex_openai") {
    const apiKey = await codexApiKey();
    if (!apiKey) {
      throw Object.assign(new Error("Codex Responses 凭据未配置"), {
        code: "MISSING_CREDENTIAL",
        status: 422,
      });
    }
    return {
      ...publicModel(item, source.active),
      provider_route: "chain-codex",
      environment: {
        OPENAI_API_KEY: apiKey,
        CHAIN_CODEX_BASE_URL: String(item.base_url || "https://api.openai.com/v1"),
        CHAIN_CODEX_MODEL: String(item.model || "gpt-5.6-luna"),
      },
    };
  }

  if (id === "deepseek_harness") {
    if (!String(process.env.DEEPSEEK_API_KEY || "").trim()) {
      throw Object.assign(new Error("DeepSeek API 凭据未配置"), {
        code: "MISSING_CREDENTIAL",
        status: 422,
      });
    }
    return {
      ...publicModel(item, source.active),
      provider_route: "deepseek-official",
      model: "deepseek-flash",
      environment: { DEEPSEEK_API_KEY: process.env.DEEPSEEK_API_KEY.trim() },
    };
  }

  if (id === "ollama_local") {
    if (!(await ollamaIsReady(item.base_url))) {
      throw Object.assign(new Error("Ollama Local 未运行"), {
        code: "MODEL_UNAVAILABLE",
        status: 422,
      });
    }
    return {
      ...publicModel(item, source.active),
      provider_route: "chain-ollama",
      environment: {
        OLLAMA_API_KEY: process.env.OLLAMA_API_KEY || "ollama-local",
        CHAIN_OLLAMA_BASE_URL: ollamaBaseUrl(item.base_url),
        CHAIN_OLLAMA_MODEL: String(item.model || "deepseek-v4.1-flash"),
      },
    };
  }

  throw Object.assign(new Error(`模型通道 ${id} 尚未接入 Harness Runtime`), {
    code: "MODEL_NOT_SUPPORTED",
    status: 422,
  });
}

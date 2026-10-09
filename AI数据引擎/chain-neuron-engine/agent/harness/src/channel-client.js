// 数据引擎通道客户端：Harness 访问引擎的唯一出口
import { randomUUID } from "node:crypto";

import { config } from "./config.js";

// 通道错误：kind = auth(鉴权失败) | unavailable(连不通/超时) | engine(引擎异常)
export class ChannelError extends Error {
  constructor(kind, message) {
    super(message);
    this.name = "ChannelError";
    this.kind = kind;
  }
}

async function request(method, path, body, retries = 1) {
  const url = `${config.engineBaseUrl}${path}`;
  const traceId = randomUUID();
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const res = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-Service-Token": config.serviceToken,
          "X-Trace-Id": traceId,
        },
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        const text = await res.text();
        if (res.status === 401 || res.status === 403) {
          throw new ChannelError(
            "auth",
            `引擎通道鉴权失败（${res.status}），请检查内部服务令牌`
          );
        }
        throw new ChannelError(
          "engine",
          `引擎返回错误（${res.status}）：${text.slice(0, 120)}`
        );
      }
      return await res.json();
    } catch (e) {
      clearTimeout(timer);
      if (e instanceof ChannelError) {
        lastError = e; // 鉴权/引擎错误不重试
      } else {
        lastError = new ChannelError(
          "unavailable",
          "无法连接数据引擎或请求超时"
        );
      }
      if (!(lastError instanceof ChannelError) ||
          (lastError.kind === "unavailable" && attempt < retries)) {
        if (attempt < retries) {
          await new Promise((r) => setTimeout(r, 200 * (attempt + 1)));
          continue;
        }
      }
      break;
    }
  }
  throw lastError;
}

export const channel = {
  healthz: () => request("GET", "/healthz"),
  queryRecords: (payload) =>
    request("POST", "/api/v1/engine/records/query", payload),
  queryAggregations: (payload) =>
    request("POST", "/api/v1/engine/aggregations/query", payload),
  unifiedQuery: (payload) =>
    request("POST", "/api/v1/engine/query", payload),
  planDerivation: (payload) =>
    request("POST", "/api/v1/engine/plan-derivation", payload),
  featureSummary: () => request("GET", "/api/v1/engine/features/summary"),
  featureProfile: (dataset) =>
    request("GET", `/api/v1/engine/features/profile/${encodeURIComponent(dataset)}`),
  featureProposals: (dataset) => request(
    "GET", "/api/v1/engine/features/proposals" + (dataset ? `?dataset=${encodeURIComponent(dataset)}` : "")
  ),
  modelConfig: () => request("GET", "/api/v1/engine/model-config"),
  reinitializePreview: () => request("GET", "/api/v1/engine/reinitialize/preview"),
  reinitializeAnalyze: () => request("POST", "/api/v1/engine/reinitialize/analyze", {}),
  getTree: (dataset, maxDepth = 4) =>
    request(
      "GET",
      `/api/v1/engine/spiral/tree?dataset=${dataset}&max_depth=${maxDepth}`
    ),
  startImport: (dataset) =>
    postForm("/api/v1/engine/import/test-data", { dataset }),
  getImportTask: (uuid) =>
    request("GET", `/api/v1/engine/import/tasks/${uuid}`),
  grow: (payload) => request("POST", "/api/v1/engine/grow", payload),
  listDatasets: () => request("GET", "/api/v1/engine/datasets"),
  datasetContract: (dataset) => request(
    "GET", `/api/v1/engine/datasets/${encodeURIComponent(dataset)}/contract`
  ),
  getDeriveRules: () => request("GET", "/api/v1/engine/derive-rules"),
  levelValues: (dataset, level, q) =>
    request(
      "GET",
      `/api/v1/engine/datasets/${dataset}/levels/${level}/values` +
        (q ? `?q=${encodeURIComponent(q)}` : "")
    ),
  semanticProbe: (dataset, term) =>
    request(
      "GET",
      `/api/v1/engine/datasets/${encodeURIComponent(dataset)}/semantic-probe` +
        `?term=${encodeURIComponent(term)}`
    ),
};

// 基座取值探测缓存（数据集 + 词），与 Python Agent _PROBE_CACHE 语义一致
const PROBE_TTL_MS = 300000;
const probeCache = new Map();

export async function semanticProbe(dataset, term) {
  const text = String(term ?? "").trim();
  if (!text) return { matched: false, configured: false };
  const key = `${dataset}\u0000${text}`;
  const cached = probeCache.get(key);
  if (cached && Date.now() - cached.at < PROBE_TTL_MS) return cached.result;
  let result;
  try {
    const res = await channel.semanticProbe(dataset, text);
    result = {
      matched: Boolean(res?.matched),
      configured: Boolean(res?.configured),
    };
  } catch {
    // 探测失败保留原条件：宁可让引擎如实报无命中，也不静默放宽范围
    return { matched: true, configured: true };
  }
  probeCache.set(key, { at: Date.now(), result });
  return result;
}

// 只下传引擎确认真实存在的基座语义条件（与 Python Agent confirm_base_terms 逐条一致）：
// 无命中的语义条件会被基座阻断整条查询，丢弃并如实说明，Agent 仍不猜值
export async function confirmBaseTerms(datasets, baseFilters) {
  const kept = { ...(baseFilters || {}) };
  const notes = [];
  for (const [key, value] of Object.entries(baseFilters || {})) {
    const terms = (Array.isArray(value) ? value : [value])
      .map((item) => String(item ?? "").trim())
      .filter(Boolean);
    if (!terms.length) continue;
    const probed = [];
    for (const dataset of datasets) {
      for (const term of terms) probed.push(await semanticProbe(dataset, term));
    }
    if (probed.some((item) => item.matched)) continue;
    const label = `${key}=${terms.join("、")}`;
    notes.push(probed.some((item) => item.configured)
      ? `条件“${label}”在数据集真实取值中不存在，本次未按该条件过滤（不猜值）`
      : `条件“${label}”无法翻译：数据集未声明可承接组织/品类的基座特征，`
        + `本次未按该条件过滤（请到「基座特征配置」声明名称/编码列）`);
    delete kept[key];
  }
  return { kept, notes };
}

// 条件值对齐引擎真实层级值（与 Python Agent resolve_conditions 语义一致）：
// 唯一命中→替换；多命中→传列表由引擎同值合并；零命中/异常→保留原值交给引擎模糊回退
const TIME_KEYS = new Set([
  "year", "week", "season", "month", "quarter", "half_year", "month_parity",
  "relative_period", "date_from", "date_to",
]);

export async function resolveConditions(dataset, conditions) {
  const out = { ...conditions };
  for (const [key, val] of Object.entries(conditions || {})) {
    if (TIME_KEYS.has(key) || typeof val !== "string" || !val) continue;
    if (/^\d+$/.test(val)) continue;
    let res;
    try {
      res = await channel.levelValues(dataset, key, val);
    } catch {
      continue;
    }
    const items = (res?.items ?? []).map((it) => it?.value).filter(Boolean);
    if (items.length === 1 && items[0] !== val) out[key] = items[0];
    else if (items.length > 1) out[key] = items;
  }
  return out;
}

// 引擎导入接口是表单提交
async function postForm(path, form) {
  const params = new URLSearchParams(form);
  const traceId = randomUUID();
  const res = await fetch(`${config.engineBaseUrl}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-Service-Token": config.serviceToken,
      "X-Trace-Id": traceId,
    },
    body: params,
  });
  if (!res.ok) {
    const text = await res.text();
    if (res.status === 401 || res.status === 403) {
      throw new ChannelError(
        "auth",
        `引擎通道鉴权失败（${res.status}），请检查内部服务令牌`
      );
    }
    throw new ChannelError(
      "engine",
      `引擎返回错误（${res.status}）：${text.slice(0, 120)}`
    );
  }
  return res.json();
}

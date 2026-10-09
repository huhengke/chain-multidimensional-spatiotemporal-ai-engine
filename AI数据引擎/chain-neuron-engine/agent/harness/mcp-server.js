// DeepSeek Harness MCP bridge: stdio JSON-RPC -> FastAPI engine.
// stdout is reserved for MCP frames; diagnostics go to stderr.
import readline from "node:readline";

import { channel } from "./src/channel-client.js";

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = {
  name: "chain-neuron-engine",
  version: "1.0.0",
};

const ALL_TOOL_DEFINITIONS = [
  {
    name: "engine_healthz",
    description: "检查链式神经元 FastAPI 数据引擎是否可用。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    run: () => channel.healthz(),
  },
  {
    name: "engine_list_datasets",
    description: "列出已注册的数据集及其中文标签。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    run: () => channel.listDatasets(),
  },
  {
    name: "engine_dataset_contract",
    description: "读取已注册数据集的动态字段角色、时间、层级、指标和来源契约。只读。",
    inputSchema: {
      type: "object",
      properties: { dataset: { type: "string" } },
      required: ["dataset"],
      additionalProperties: false,
    },
    run: (args) => channel.datasetContract(requiredString(args.dataset, "dataset")),
  },
  {
    name: "engine_records_query",
    description:
      "查询明细记录。仅用于明细、单据、记录类问题；不要用它计算年度或分组汇总。",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string", default: "oil_monthly_sales" },
        filters: { type: "object", default: {} },
        page: { type: "integer", minimum: 1, default: 1 },
        page_size: { type: "integer", minimum: 1, maximum: 500, default: 20 },
      },
      additionalProperties: false,
    },
    run: (args) => channel.queryRecords({
      dataset: optionalString(args.dataset, "oil_monthly_sales"),
      filters: objectValue(args.filters, "filters"),
      page: integerValue(args.page, 1, 1),
      page_size: integerValue(args.page_size, 20, 1, 500),
    }),
  },
  {
    name: "engine_aggregations_query",
    description:
      "查询业务或计划聚合。引擎会在父层分组包含子层过滤时自动改用精确事实聚合；禁止自行加减或把未过滤总额当作分类结果。",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string", description: "已注册的数据集 id" },
        conditions: { type: "object", default: {} },
        group_by: {
          type: "string",
          description: "可选分组层，例如 month、big_area、branch 或 gas_code",
        },
        base_filters: {
          type: "object",
          description: "通用数据基座语义条件，例如 product、organization 或 category",
          default: {},
        },
      },
      required: ["dataset"],
      additionalProperties: false,
    },
    run: (args) => channel.queryAggregations({
      dataset: requiredString(args.dataset, "dataset"),
      conditions: objectValue(args.conditions, "conditions"),
      group_by: nullableString(args.group_by, "group_by"),
      base_filters: objectValue(args.base_filters, "base_filters"),
    }),
  },
  {
    name: "engine_query",
    description:
      "统一确定性查询入口：先解析时间、产品、组织和数据可用性，再查询业务/计划分支。结果由引擎 Decimal 计算；模型不得自行加减。指定类别合计必须限定类别甲和类别乙，不能使用全品类总额。",
    inputSchema: {
      type: "object",
      properties: {
        intent: { type: "string", default: "sales" },
        period: { type: ["string", "object"], default: "current_month" },
        location: { type: ["string", "null"], default: null },
        product: { type: ["string", "null"], default: null },
        grade: { type: ["string", "null"], default: null },
        group_by: { type: "array", items: { type: "string" }, default: [] },
        products: {
          type: "array",
          items: { type: "string" },
          default: [],
          description: "共同限定产品范围；指定类别合计传 [\"类别甲\", \"类别乙\"]，引擎统一聚合。",
        },
        comparisons: { type: "array", items: { type: "string", enum: ["yoy", "mom"] }, default: [] },
        sort: { type: ["object", "null"], default: null },
        metrics: { type: "array", items: { type: "string" }, default: [] },
        dataset: { type: "string", default: "oil_monthly_sales" },
        filters: { type: "object", default: {} },
        base_filters: {
          type: "object",
          description: "通用数据基座语义条件，例如 product、organization 或 category",
          default: {},
        },
        detail: { type: "boolean", default: false },
      },
      additionalProperties: false,
    },
    run: (args) => channel.unifiedQuery({
      intent: optionalString(args.intent, "sales"),
      period: args.period ?? "current_month",
      location: args.location ?? null,
      product: args.product ?? null,
      grade: args.grade ?? null,
      group_by: Array.isArray(args.group_by) ? args.group_by : [],
      products: Array.isArray(args.products) ? args.products : [],
      comparisons: Array.isArray(args.comparisons) ? args.comparisons : [],
      sort: args.sort ?? null,
      metrics: Array.isArray(args.metrics) ? args.metrics : [],
      dataset: optionalString(args.dataset, "oil_monthly_sales"),
      filters: objectValue(args.filters, "filters"),
      base_filters: objectValue(args.base_filters, "base_filters"),
      detail: Boolean(args.detail),
    }),
  },
  {
    name: "engine_feature_profile",
    description: "读取数据集画像、指标、层级和动态能力声明。只读，不修改数据。",
    inputSchema: {
      type: "object",
      properties: { dataset: { type: "string" } },
      required: ["dataset"],
      additionalProperties: false,
    },
    run: (args) => channel.featureProfile(requiredString(args.dataset, "dataset")),
  },
  {
    name: "engine_feature_proposals",
    description: "读取待审批产品/组织等特征建议。模型只能解释建议，不能直接审批。",
    inputSchema: {
      type: "object",
      properties: { dataset: { type: ["string", "null"] } },
      additionalProperties: false,
    },
    run: (args) => channel.featureProposals(nullableString(args.dataset, "dataset")),
  },
  {
    name: "engine_model_config",
    description: "读取非敏感模型供应配置元数据，不返回真实密钥。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    run: () => channel.modelConfig(),
  },
  {
    name: "engine_spiral_tree",
    description: "读取数据集螺旋树，用于解释路由路径和节点状态。",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        max_depth: { type: "integer", minimum: 1, maximum: 12, default: 4 },
      },
      required: ["dataset"],
      additionalProperties: false,
    },
    run: (args) => channel.getTree(
      requiredString(args.dataset, "dataset"),
      integerValue(args.max_depth, 4, 1, 12),
    ),
  },
  {
    name: "engine_level_values",
    description:
      "查询引擎中真实存在的层级值，用于把用户说法对齐到正式组织层级一、组织层级三、定位单元或产品值。",
    inputSchema: {
      type: "object",
      properties: {
        dataset: { type: "string" },
        level: { type: "string" },
        q: { type: "string", default: "" },
      },
      required: ["dataset", "level"],
      additionalProperties: false,
    },
    run: (args) => channel.levelValues(
      requiredString(args.dataset, "dataset"),
      requiredString(args.level, "level"),
      optionalString(args.q, ""),
    ),
  },
  {
    name: "engine_derive_rules_get",
    description: "读取时间派生规则、版本和加载状态；不修改规则。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    run: () => channel.getDeriveRules(),
  },
];

const LEGACY_TOOL_NAMES = new Set([
  "engine_list_datasets",
  "engine_aggregations_query",
  "engine_spiral_tree",
  "engine_level_values",
  "engine_derive_rules_get",
  "engine_feature_profile",
  "engine_feature_proposals",
  "engine_model_config",
]);
const exposeLegacyTools = process.env.MCP_EXPOSE_LEGACY_TOOLS === "true";
const TOOL_DEFINITIONS = exposeLegacyTools
  ? ALL_TOOL_DEFINITIONS
  : ALL_TOOL_DEFINITIONS.filter((tool) => !LEGACY_TOOL_NAMES.has(tool.name));
const TOOL_MAP = new Map(TOOL_DEFINITIONS.map((tool) => [tool.name, tool]));

function requiredString(value, name) {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${name} 必须是非空字符串`);
  }
  return value.trim();
}

function optionalString(value, fallback) {
  if (value === undefined || value === null) return fallback;
  return requiredString(value, "字符串参数");
}

function nullableString(value, name) {
  if (value === undefined || value === null || value === "") return null;
  return requiredString(value, name);
}

function objectValue(value, name) {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} 必须是 JSON 对象`);
  }
  return value;
}

function integerValue(value, fallback, min, max = Number.MAX_SAFE_INTEGER) {
  if (value === undefined || value === null) return fallback;
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new Error(`整数参数必须在 ${min} 到 ${max} 之间`);
  }
  return value;
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function sendError(id, code, message, data) {
  send({ jsonrpc: "2.0", id, error: { code, message, ...(data ? { data } : {}) } });
}

function sendResult(id, result) {
  send({ jsonrpc: "2.0", id, result });
}

function textResult(value) {
  const normalized = value === undefined ? null : value;
  const text = typeof normalized === "string"
    ? normalized
    : JSON.stringify(normalized, null, 2);
  return {
    content: [{ type: "text", text }],
    structuredContent:
      normalized && typeof normalized === "object"
        ? normalized
        : { value: normalized },
  };
}

async function handleRequest(request) {
  const { id, method, params = {} } = request;
  if (request.jsonrpc !== "2.0" || typeof method !== "string") {
    return sendError(id ?? null, -32600, "无效的 JSON-RPC 请求");
  }

  if (method === "notifications/initialized" || method === "notifications/cancelled") {
    return;
  }

  if (method === "ping") {
    return sendResult(id, {});
  }

  if (method === "initialize") {
    return sendResult(id, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: SERVER_INFO,
      instructions:
        "普通汇总和实际+计划问题优先使用 engine_query；不要自行对引擎返回的 Decimal 数值做加减。指定类别合计使用 products=[\"类别甲\",\"类别乙\"]，并分别查询类别甲、类别乙序列；不得把全品类总额标成指定类别合计。",
    });
  }

  if (method === "tools/list") {
    return sendResult(id, {
      tools: TOOL_DEFINITIONS.map(({ name, description, inputSchema }) => ({
        name,
        description,
        inputSchema,
      })),
    });
  }

  if (method === "tools/call") {
    const name = params?.name;
    const tool = TOOL_MAP.get(name);
    if (!tool) return sendError(id, -32602, `未知工具：${String(name)}`);
    try {
      const result = await tool.run(params?.arguments || {});
      return sendResult(id, textResult(result));
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`[mcp] ${name} failed: ${message}`);
      return sendResult(id, {
        ...textResult({ error: message, tool: name }),
        isError: true,
      });
    }
  }

  return sendError(id, -32601, `不支持的 MCP 方法：${method}`);
}

const input = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});
const pending = new Set();
let inputClosed = false;

function maybeFinish() {
  if (inputClosed && pending.size === 0) process.exitCode = 0;
}

function track(request) {
  const task = Promise.resolve(handleRequest(request)).catch((error) => {
    console.error(`[mcp] request failed: ${error.message}`);
    sendError(request.id ?? null, -32603, "MCP Server 内部错误");
  }).finally(() => {
    pending.delete(task);
    maybeFinish();
  });
  pending.add(task);
}

input.on("line", (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    console.error(`[mcp] invalid JSON: ${error.message}`);
    return sendError(null, -32700, "无法解析 JSON-RPC 请求");
  }
  track(request);
});

input.on("close", () => {
  inputClosed = true;
  maybeFinish();
});

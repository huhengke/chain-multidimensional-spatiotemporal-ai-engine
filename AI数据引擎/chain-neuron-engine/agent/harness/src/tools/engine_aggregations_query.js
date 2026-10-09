// 工具：聚合查询（直读中环，Decimal 字符串）
import { channel } from "../channel-client.js";

export default {
  name: "engine_aggregations_query",
  description: "查询业务聚合（总量/分组），聚合值为 Decimal 字符串，禁止下钻子分叉",
  inputSchema: {
    type: "object",
    properties: {
      dataset: { type: "string", description: "已注册的数据集 id" },
      conditions: { type: "object" },
      group_by: { type: "string" },
      base_filters: { type: "object" },
    },
    required: ["dataset", "conditions"],
  },
  run: ({ dataset, conditions, group_by = null, base_filters = {} }) =>
    channel.queryAggregations({ dataset, conditions, group_by, base_filters }),
};

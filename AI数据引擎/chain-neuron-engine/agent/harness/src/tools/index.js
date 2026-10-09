// Tool Registry：注册全部引擎通道工具
import recordsQuery from "./engine_records_query.js";
import aggregationsQuery from "./engine_aggregations_query.js";
import ingest from "./engine_ingest.js";
import grow from "./engine_grow.js";
import spiralTree from "./engine_spiral_tree.js";
import deriveRules from "./engine_derive_rules.js";

const tools = [
  recordsQuery,
  aggregationsQuery,
  ingest,
  grow,
  spiralTree,
  deriveRules,
];

export const toolRegistry = new Map(tools.map((t) => [t.name, t]));

export function listToolSpecs() {
  return tools.map(({ name, description, inputSchema }) => ({
    function: { name, description, parameters: inputSchema },
    type: "function",
  }));
}

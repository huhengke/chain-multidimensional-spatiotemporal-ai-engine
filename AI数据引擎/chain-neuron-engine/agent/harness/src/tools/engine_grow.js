// 工具：动态生长
import { channel } from "../channel-client.js";

export default {
  name: "engine_grow",
  description: "触发节点分叉生长（达到阈值的节点批量生长）",
  inputSchema: {
    type: "object",
    properties: { dataset: { type: "string", description: "已注册的通用事实数据集 id" } },
    required: ["dataset"],
  },
  run: ({ dataset }) => channel.grow({ dataset }),
};

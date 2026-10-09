// 工具：螺旋树结构
import { channel } from "../channel-client.js";

export default {
  name: "engine_spiral_tree",
  description: "读取螺旋树结构（供路由可视化）",
  inputSchema: {
    type: "object",
    properties: {
      dataset: { type: "string", description: "已注册的通用事实数据集 id" },
      max_depth: { type: "integer", default: 4 },
    },
    required: ["dataset"],
  },
  run: ({ dataset, max_depth = 4 }) => channel.getTree(dataset, max_depth),
};

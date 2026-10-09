// 工具：触发数据导入（根目录随产品手册分发的测试数据）
import { channel } from "../channel-client.js";

export default {
  name: "engine_ingest",
  description: "触发数据集导入并返回任务号（Write-Through，实时进度可查）",
  inputSchema: {
    type: "object",
    properties: {
      dataset: {
        type: "string",
        description: "已注册的数据集 id，如 oil_monthly_sales / oil_sales_plan / non_oil_sales",
      },
    },
    required: ["dataset"],
  },
  run: async ({ dataset }) => {
    const started = await channel.startImport(dataset);
    if (started.task_uuid) {
      const task = await channel.getImportTask(started.task_uuid);
      return { ...started, task };
    }
    return started;
  },
};

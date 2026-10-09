// 工具：明细记录查询
import { channel } from "../channel-client.js";

export default {
  name: "engine_records_query",
  description: "查询业务数据甲明细记录（年/月/季节/组织层级一/组织层级二/定位单元/产品/支付方式 + 分页）",
  inputSchema: {
    type: "object",
    properties: {
      dataset: { type: "string", default: "oil_monthly_sales" },
      filters: { type: "object" },
      page: { type: "integer", default: 1 },
      page_size: { type: "integer", default: 20 },
    },
    required: ["filters"],
  },
  run: ({ dataset = "oil_monthly_sales", filters, page = 1, page_size = 20 }) =>
    channel.queryRecords({ dataset, filters, page, page_size }),
};

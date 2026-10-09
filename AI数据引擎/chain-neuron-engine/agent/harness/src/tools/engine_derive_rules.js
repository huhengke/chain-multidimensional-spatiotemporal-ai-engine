// 工具：时间派生规则状态（reload 由引擎管理接口完成；此处返回能力说明）
export default {
  name: "engine_derive_rules",
  description: "查询时间规则生成器能力（季节/季度/上下半年/单月双月/周/节气/活动日；热加载与版本回滚）",
  inputSchema: { type: "object", properties: {} },
  run: async () => ({
    supported: ["season", "quarter", "half_year", "month_parity", "week", "solar_term", "holiday"],
    degraded_at_month_precision: ["week", "day", "weekday_type", "holiday", "solar_term"],
    hot_reload: true,
    rollback: true,
  }),
};

// 意图识别入口：语义槽位提取（规则降级）。
// 语义与 agent/python_agent/skills/semantic_slots.py 一一对应。
import { channel } from "./channel-client.js";
import { extractSlots, FALLBACK_LABELS, dayGrainedDatasets } from "./semantic.js";

const LABELS_TTL = 60_000;
let labelCache = { at: 0, map: FALLBACK_LABELS, dayGrained: {} };

/**
 * 数据集中文名/别名 → id，以及引擎声明支持日精度的事实数据集
 * （TTL 缓存；引擎不可达时回落静态映射，不阻断查询）。
 */
async function loadDatasetSurface() {
  if (Date.now() - labelCache.at < LABELS_TTL) return labelCache;
  const map = { ...FALLBACK_LABELS };
  try {
    const body = await channel.listDatasets();
    const items = body.items || [];
    for (const item of items) {
      if (!item.id) continue;
      // 别名与中文名同等有效：向导注册的数据集常靠别名被业务方点名
      for (const label of [item.label, ...(item.aliases || [])]) {
        const text = String(label ?? "").trim();
        if (text) map[text] = String(item.id);
      }
    }
    labelCache = { at: Date.now(), map, dayGrained: dayGrainedDatasets(items) };
  } catch (e) {
    console.warn("数据集标签获取失败，使用兜底映射：", e.message);
    labelCache = { at: Date.now(), map, dayGrained: {} };
  }
  return labelCache;
}

export async function recognizeIntent(question) {
  const surface = await loadDatasetSurface();
  const result = extractSlots(question, surface.map, surface.dayGrained);
  return {
    intent: result.intent,
    dataset: result.dataset,
    // 多数据集业务问题的范围与同环比要一路带到编排层：漏掉就退化成单表查询
    datasets: result.datasets || [],
    dataset_scope: result.dataset_scope || "single",
    comparisons: result.comparisons || [],
    // 派生指标的实际侧数据集要一路带到编排层，漏掉就会退回默认月报
    actual_dataset: result.actual_dataset,
    // "提问没给时间、按当前自然月摊算"是Agent 的默认口径，必须传到编排层写明
    period_defaulted: Boolean(result.period_defaulted),
    conditions: result.conditions,
    filters: result.filters,
    base_filters: result.base_filters,
    group_by: result.group_by,
    detail: result.detail,
    derived_metrics: result.derived_metrics || [],
    // 规则层判定「一个可查锚点都没有」：编排层据此反问，而不是查全量
    unresolved: Boolean(result.unresolved),
  };
}

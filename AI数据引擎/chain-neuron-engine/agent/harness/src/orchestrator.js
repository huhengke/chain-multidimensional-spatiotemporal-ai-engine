// Harness 编排：意图 → 注册表数据集 → 统一拓扑查询 → 结果排版。
import { toolRegistry } from "./tools/index.js";
import { channel, confirmBaseTerms, resolveConditions } from "./channel-client.js";
import { recognizeIntent } from "./intent.js";
import { CLARIFY_ANSWER } from "./semantic.js";

// 派生指标的数据集与对比量纲（与 python_agent/skills/plan_analysis.py 一致）：
// 实际侧由提问点名，指标名一律从引擎声明的标签里挑「吨」量纲那一列。
const DEFAULT_ACTUAL_DATASET = "oil_monthly_sales";
const PLAN_DATASET = "oil_sales_plan";
const TON_MARK = "吨";

function routeChain(route, prefix = "") {
  if (!route) return [];
  return (route.steps || []).map((step) => ({
    level: prefix
      ? `${prefix} / ${step.level_label || step.level_name}`
      : (step.level_label || step.level_name),
    value: step.value,
    status: step.status,
    message: step.message,
  }));
}

function nodeLabel(node) {
  const value = String(node?.node_value ?? "");
  const display = String(node?.display_value ?? "").trim();
  return display && display !== value ? `${display}（${value}）` : value;
}

function scopeText(conditions, baseFilters, periodLabel) {
  const parts = [];
  for (const key of ["year", "season", "month", "big_area", "branch", "station"]) {
    const value = conditions?.[key];
    if (!value) continue;
    for (const item of Array.isArray(value) ? value : [value]) {
      let text = String(item);
      // "去年8月"这类相对年份只下传裸月份，年份由引擎补齐。
      // 前缀按提问原词显示，不能把内部编码 "8" 甩给业务方。
      if (key === "month" && /^\d{1,2}$/.test(text)) text = `${Number(text)}月`;
      parts.push(text);
    }
  }
  const location = baseFilters?.organization || baseFilters?.location;
  if (location) parts.unshift(String(location));
  // 引擎换算出来的期间口径（上周/本月/4月第三周）要显式露出，
  // 否则"第三周"这类语义只能靠业务方猜我们按哪种周口径算的。
  if (periodLabel) parts.unshift(String(periodLabel));
  const unique = [...new Set(parts.filter((part) => part.trim()))];
  return unique.length ? `${unique.join(" ")} ` : "";
}

// 相对年份（今年/去年/明年）由时间基座编译成 year 层级条件。
// 年份口径标签只在引擎审计里，答案前缀不露出来的话，业务方无从确认
// "去年"到底取的是哪一年。批量路径的标签在顶层，单数据集路径在
// time_foundation 审计里。
function resolvedYearLabel(plan) {
  const yearPeriod = (plan || {}).resolved_year
    || ((plan || {}).time_foundation || {}).resolved_year || {};
  return String(yearPeriod.label || "");
}

const METRIC_LABELS = {
  monthlyamount: "业务金额", total_amount: "业务金额", amount: "业务金额",
  sale_amount: "业务金额",
  monthlysales: "业务吨数", total_qty_ton: "业务吨数", qty_ton: "业务吨数",
  monthlyqty: "业务升数", total_qty_liter: "业务升数", qty_liter: "业务升数",
  real_amount: "实收金额", pay_amount: "支付金额",
  monsalesplan: "吨数业务计划",
};

function metricUnit(key, label) {
  const text = `${key} ${label}`.toLowerCase();
  if (["amount", "revenue", "金额", "业务金额", "实收", "支付"].some((token) => text.includes(token))) return " 元";
  if (["ton", "吨"].some((token) => text.includes(token)) || ["monthlysales", "monsalesplan"].includes(key)) return " 吨";
  if (["liter", "升"].some((token) => text.includes(token)) || key === "monthlyqty") return " 升";
  return "";
}

function metricLabel(key, labels) {
  const declared = String(labels?.[key] || "").trim();
  // 引擎把物理字段名当 label 时回退到通用名称，与 Python Agent 一致
  return !declared || declared === key ? (METRIC_LABELS[key] || key) : declared;
}

function formatAggregations(aggregations, queryPlan) {
  const labels = (queryPlan || {}).metric_labels || {};
  const parts = Object.entries(aggregations || {}).map(([key, value]) => {
    const label = metricLabel(key, labels);
    return `${label} ${value}${metricUnit(key, String(label))}`;
  });
  return parts.join("；") || "无指标值";
}

/**
 * 本次业务查询要覆盖哪些数据集。
 * 点名了就用点名的；「业务」类提问没点名时按引擎经验库的口径枚举
 * （include_kinds / exclude_terms 来自 `GET /datasets` 的 sales_scope），
 * 本文件不另写一套"什么算通用事实"的判定，也不维护数据集白名单。
 */
async function resolveDatasets(intent) {
  const explicit = [...new Set(
    (intent.datasets || []).map((value) => String(value).trim()).filter(Boolean)
  )];
  if (explicit.length) return explicit;
  const single = String(intent.dataset || DEFAULT_ACTUAL_DATASET).trim();
  if (String(intent.dataset_scope || "single") !== "all_actual") return [single];
  let body;
  try {
    body = await channel.listDatasets();
  } catch {
    return [single];
  }
  const scope = body.sales_scope || {};
  const kinds = new Set(
    (scope.include_kinds || ["generic"]).map((value) => String(value).trim())
  );
  const excluded = (scope.exclude_terms || ["计划", "指标", "预算", "plan"])
    .map((value) => String(value).trim().toLowerCase())
    .filter(Boolean);
  const result = [];
  for (const item of body.items || []) {
    if (!kinds.has(String(item.kind || "")) || item.disabled) continue;
    const searchable = [String(item.label || ""),
      ...(item.aliases || []).map((value) => String(value))]
      .join(" ").toLowerCase();
    if (excluded.some((term) => searchable.includes(term))) continue;
    const id = String(item.id || "").trim();
    if (id) result.push(id);
  }
  return result.length ? result : [single];
}

async function queryDataset(intent, dataset, datasets = [dataset]) {
  const normalized = await resolveConditions(dataset, intent.conditions || {});
  const filters = { ...(intent.filters || {}), ...normalized };
  const { kept, notes } = await confirmBaseTerms(datasets, intent.base_filters || {});
  const data = await channel.unifiedQuery({
    dataset,
    datasets,
    filters,
    base_filters: kept,
    group_by: intent.group_by ? [intent.group_by] : [],
    metrics: intent.metrics || [],
    comparisons: intent.comparisons || [],
    detail: Boolean(intent.detail),
  });
  return { normalized, data, notes, kept };
}

function notesSuffix(notes) {
  const items = (notes || []).filter((note) => String(note).trim());
  return items.length ? `\n提示：${items.join("；")}` : "";
}

/** 批量路径的指标挑选：主业务指标优先，未限定单位时再带上数量指标。 */
function displayMetricKeys(queryPlan, actual, question) {
  const aggregations = (actual || {}).aggregations || {};
  const entries = Object.keys(aggregations);
  if (!entries.length) return [];
  const labels = (queryPlan || {}).metric_labels || {};
  const score = (key) => {
    const text = `${key} ${metricLabel(key, labels)}`.toLowerCase();
    let value = 0;
    if (["amount", "revenue", "金额", "业务金额"].some((t) => text.includes(t))) value += 100;
    if (["sale", "业务"].some((t) => text.includes(t))) value += 20;
    if (["real", "pay", "支付", "实收"].some((t) => text.includes(t))) value -= 20;
    if (["monthlyamount", "total_amount", "sale_amount"].includes(key)) value += 10;
    return value;
  };
  // 与 Python max() 一致：并列时保留先出现的键
  const primary = entries.reduce(
    (best, key) => (score(key) > score(best) ? key : best), entries[0]
  );
  if (["金额", "业务金额", "业务金额"].some((word) => question.includes(word))) {
    return [primary];
  }
  const selected = [primary];
  for (const key of entries) {
    const text = `${key} ${metricLabel(key, labels)}`.toLowerCase();
    if (key === primary) continue;
    if (["吨", "升", "liter", "ton", "qty"].some((t) => text.includes(t))) selected.push(key);
  }
  return selected.slice(0, 4);
}

function formatSelectedMetrics(aggregations, queryPlan, keys) {
  const labels = (queryPlan || {}).metric_labels || {};
  const parts = keys.filter((key) => aggregations[key] !== undefined).map((key) => {
    const label = String(metricLabel(key, labels));
    return `${label} ${aggregations[key]}${metricUnit(key, label)}`;
  });
  return parts.join("；") || formatAggregations(aggregations, {});
}

function formatComparisonMetrics(metrics, queryPlan, keys, label) {
  const labels = (queryPlan || {}).metric_labels || {};
  const parts = [];
  for (const key of keys) {
    const item = metrics[key];
    if (!item) continue;
    const name = String(metricLabel(key, labels));
    const unit = metricUnit(key, name);
    const rateText = item.rate === undefined || item.rate === null
      ? "基期为0，无法计算变化率" : `${label}率 ${item.rate}%`;
    parts.push(`${name} ${item.previous}${unit}，差额 ${item.delta}${unit}，${rateText}`);
  }
  return parts.join("；");
}

/**
 * 跨数据集批量答案：逐数据集如实给数（含无命中、粒度不足、条件落不了位），
 * 合计只统计有结果的那几份，缺口用「说明」写清楚，不拿无数据冒充未参与。
 */
function formatBatch(intent, question, data, conditions, baseFilters, notes) {
  const actualRoot = data.actual || {};
  const datasets = actualRoot.datasets || {};
  const queryPlan = data.query_plan || {};
  // 期间标签只取批量计划顶层的相对期间；相对年份（去年/今年）另有审计标签，
  // 它只用于答案前缀，不能顶掉引擎编译出的 "当前期 202508" 口径。
  const batchPeriodLabel = String((queryPlan.resolved_period || {}).label || "");
  const scope = scopeText(
    conditions, baseFilters, batchPeriodLabel || resolvedYearLabel(queryPlan)
  );
  const lines = [];
  const chain = [];
  const periodLabels = queryPlan.periods || {};
  let scopeShown = false;
  const takeScope = () => {
    if (scopeShown || !scope) return "";
    scopeShown = true;
    return scope;
  };

  for (const [dataset, item] of Object.entries(datasets)) {
    const label = String(item.label || dataset);
    const current = item.current || {};
    const currentPlan = ((queryPlan.queries || {})[dataset] || {}).current || {};
    const keys = displayMetricKeys(currentPlan, current, question);
    // 即使当前期间没有命中，也保留该数据集的真实路由：前端要能看出
    // "已参与查询、当前无数据"，不能因为没结果就从思维链里消失。
    chain.push(...routeChain(current.route, label));
    if (!current.available) {
      if (current.unsupported_conditions) {
        lines.push(current.unsupported_message
          || `${takeScope()}${label}：本次条件无法映射到该数据集的层级，未执行查询。`);
        continue;
      }
      if (current.precision_gap) {
        lines.push(`${takeScope()}${label}：无法按当前请求的日粒度精确计算。`
          + `${current.precision_gap_message || "请检查数据集时间字段精度。"}`);
        continue;
      }
      lines.push(`${takeScope()}${label}：当前期间没有命中数据。`);
      continue;
    }
    const nodes = current.nodes || [];
    if ((currentPlan.group_by || []).length && nodes.length) {
      lines.push(`${takeScope()}${label}（共 ${nodes.length} 项）：`);
      for (const node of nodes) {
        lines.push(`· ${nodeLabel(node)}：`
          + `${formatSelectedMetrics(node.aggregations || {}, currentPlan, keys)}`
          + `（记录 ${node.record_count || 0} 条）`);
      }
    } else {
      const currentPeriod = batchPeriodLabel
        || String((currentPlan.resolved_period || {}).label || "")
        || String(periodLabels.current || "") || "当前期间";
      const parts = [
        `当前期 ${currentPeriod}：`
        + `${formatSelectedMetrics(current.aggregations || {}, currentPlan, keys)}`,
      ];
      if (current.order_count !== undefined) parts.push(`记录数 ${current.order_count} 笔`);
      parts.push(`明细记录 ${current.record_count || 0} 条`);
      lines.push(`${takeScope()}${label}：${parts.join("；")}`);
    }
    for (const comparison of intent.comparisons || []) {
      const comparisonItem = (item.comparisons || {})[comparison];
      if (!comparisonItem) continue;
      const comparisonLabel = comparison === "yoy" ? "同比" : "环比";
      const text = formatComparisonMetrics(
        comparisonItem.metrics || {}, currentPlan, keys, comparisonLabel
      );
      if (text) {
        lines.push(`  ${comparisonLabel}（${comparisonItem.period || "比较期间"}）：${text}`);
      }
    }
  }

  const totals = actualRoot.totals || {};
  if (totals.sales_amount !== undefined && totals.sales_amount !== null) {
    const parts = [`业务金额 ${totals.sales_amount} 元`];
    if (totals.order_count !== undefined && totals.order_count !== null) {
      parts.push(`记录数 ${totals.order_count} 笔`);
    }
    parts.push(`明细记录 ${totals.record_count || 0} 条`);
    lines.push(`通用事实合计：${parts.join("；")}。`);
  }
  const notes3 = [
    ["datasets_without_data", " 在当前期间无命中，已保留在本次查询范围内，未把无数据误当成未参与查询。"],
    ["datasets_precision_gap", " 已识别到时间粒度不足，未按更粗的时间粒度凑数。"],
    ["datasets_unsupported", " 的条件无法在该数据集确认（层级或基座语义），未参与合计。"],
  ];
  for (const [key, suffix] of notes3) {
    const items = (totals[key] || []).map((value) => String(value));
    if (items.length) lines.push(`说明：${items.join("、")}${suffix}`);
  }
  if (!lines.length) lines.push("没有找到符合条件的通用事实数据。");

  const rendered = lines.join("\n");
  const remaining = [...new Set(
    [...(notes || []), ...(data.warnings || [])]
      .map((warning) => String(warning))
      .filter((warning) => warning.trim() && !rendered.includes(warning))
  )];
  if (remaining.length) lines.push(`提示：${remaining.join("；")}`);
  return { answer: lines.join("\n"), data, thought_chain: chain };
}

async function handleSalesQuestion(intent, question = "") {
  const datasets = await resolveDatasets(intent);
  const dataset = datasets[0] || DEFAULT_ACTUAL_DATASET;
  const { data, notes, kept } = await queryDataset(intent, dataset, datasets);
  const conditions = intent.conditions || {};
  // 派生指标（日均/欠进度）只在单期间路径摊算：分组对比里逐组调用会把
  // 一次问题变成 N 次除法，主答案仍给合计，缺口如实说明。
  const batchNotes = (intent.derived_metrics || []).length
    ? [...notes, "分组对比本次给合计，日均/欠进度请按单个期间提问（如「2026年8月日均业务数量」）"]
    : notes;
  if (data.actual && data.actual.datasets) {
    return formatBatch(
      intent, question, data, conditions, kept, batchNotes
    );
  }
  const actual = data.actual || {};
  const label = data.query_plan?.label || dataset;
  // 条件被静默丢弃会让"示例区域甲组织层级一"答成全公司总额：如实报错，不凑数
  if (actual.unsupported_conditions) {
    return {
      answer: `${actual.unsupported_message || `${label}：本次条件无法映射到该数据集的层级，未执行查询。`}`
        + notesSuffix(batchNotes),
      data,
      thought_chain: routeChain(actual.route),
    };
  }
  // 粒度缺口不是"没有数据"：必须说明是时间精度不够，而不是谎称无数据
  if (actual.precision_gap) {
    return {
      answer: `${label}：无法按当前请求的日粒度精确计算。`
        + `${actual.precision_gap_message || "请检查数据集时间字段精度。"}`
        + notesSuffix(batchNotes),
      data,
      thought_chain: routeChain(actual.route),
    };
  }
  // 事实表聚合兜底时拓扑节点为空，但条件命中的记录数才是有无结果的依据
  if (!actual.nodes?.length && !actual.record_count) {
    return {
      answer: `没有找到符合条件的「${label}」数据。` + notesSuffix(batchNotes),
      data,
      thought_chain: routeChain(actual.route),
    };
  }
  // scope 用提问原词而不是编译后的编码：编码是给引擎过滤用的，不是给人看的
  const scope = scopeText(
    conditions,
    kept,
    data.query_plan?.resolved_period?.label || resolvedYearLabel(data.query_plan)
  );
  let answer;
  if (data.query_plan?.group_by?.length) {
    const nodes = actual.nodes || [];
    const lines = [`${scope}${label}（共 ${nodes.length} 项）：`];
    for (const node of nodes) {
      lines.push(`· ${nodeLabel(node)}：${formatAggregations(node.aggregations, data.query_plan)}（记录 ${node.record_count} 条）`);
    }
    answer = lines.join("\n");
  } else {
    const parts = [formatAggregations(actual.aggregations, data.query_plan)];
    if (actual.order_count !== undefined) parts.push(`记录数 ${actual.order_count} 笔`);
    parts.push(`明细记录 ${actual.record_count || 0} 条`);
    answer = `${scope}${label}：${parts.join("；")}。`;
  }
  if (data.warnings?.length) answer += `\n提示：${data.warnings.join("；")}`;
  return { answer: answer + notesSuffix(batchNotes), data, thought_chain: routeChain(actual.route) };
}

async function handlePlan(intent, question = "") {
  // 期间天数、日均、完成率、时间进度与欠进度全部来自引擎
  // （/plan-derivation，Decimal 计算），本函数只做排版，不补一个数。
  const baseFilters = intent.base_filters || {};
  const derived = (intent.derived_metrics || []).map(String);
  const actualDataset = String(intent.actual_dataset || DEFAULT_ACTUAL_DATASET);
  const actualInfo = await tonMetric(actualDataset);
  const planInfo = await tonMetric(PLAN_DATASET);
  const comparable = Boolean(actualInfo.name && planInfo.name);
  const incomparable = `计划与欠进度：${actualInfo.label}没有与${planInfo.label}`
    + `同为${TON_MARK}量纲的可比指标，未计算完成率与欠进度。`;
  const needsPlan = derived.includes("progress_gap")
    || ["计划", "完成率"].some((word) => question.includes(word));
  const payload = { actual_dataset: actualDataset, conditions: intent.conditions || {} };
  let notes = [];
  if (needsPlan && comparable) {
    // 计划侧不做"探测不到就丢条件"：丢了组织条件等于把「示例区域甲的计划」答成全公司的
    // 计划，比拒答危险（与 #42 同一类）。原条件交给引擎，引擎判不了会如实回
    // "未执行查询"，完成率显式缺失而不是算出一个错的数。
    const actualBase = await confirmBaseTerms([actualDataset], baseFilters);
    notes = actualBase.notes;
    payload.base_filters = actualBase.kept;
    payload.plan_dataset = PLAN_DATASET;
    payload.plan_base_filters = baseFilters;
    payload.plan_metric = planInfo.name;
    payload.progress_metric = actualInfo.name;
  } else {
    const actualBase = await confirmBaseTerms([actualDataset], baseFilters);
    notes = actualBase.notes;
    payload.base_filters = actualBase.kept;
  }

  const data = await channel.planDerivation(payload);
  if (data.unsupported) {
    return {
      answer: `${data.message || "本次条件无法确认，未执行派生计算。"}${notesSuffix(notes)}`,
      data,
      thought_chain: [],
    };
  }

  const period = data.period || {};
  const head = `${period.label || "当前期间"}（${basisText(period, data.precision)}，实际侧${actualInfo.label}）`;
  const notice = String(data.notice || "");
  const computed = actualLines(head, data, question, intent, actualInfo.name);
  const lines = [
    computed || notice || `${head}该期间没有符合条件的实际数据，日均与进度不适用。`,
  ];
  if (needsPlan) {
    lines.push(comparable ? progressLine(data.progress || {}, data, planInfo.name) : incomparable);
  }
  if (notice && !lines.includes(notice)) lines.push(notice);
  // 提问没给时间时按当前自然月摊算是**我们的口径**，不是用户的问法，
  // 必须写明，否则"本月欠进度"会被读成"全年欠进度"。
  if (intent.period_defaulted) {
    lines.push(
      `说明：提问未指定期间，本次按当前自然月`
      + `（${period.calendar_label || period.label || "本月"}）摊算。`,
    );
  }
  return {
    answer: lines.filter(Boolean).join("\n") + notesSuffix(notes),
    data,
    thought_chain: [],
  };
}

/** 从引擎声明里取该数据集的「吨」量纲指标：返回 {label, name}，Agent 不写死列名。 */
async function tonMetric(dataset) {
  const contract = await channel.datasetContract(dataset);
  const label = String(contract?.label || dataset);
  for (const metric of contract?.metrics || []) {
    const text = `${metric.label || ""}${metric.name || ""}`;
    if (text.includes(TON_MARK)) return { label, name: String(metric.name || "") };
  }
  return { label, name: "" };
}

/** 按提问里的量纲词在引擎指标标签中挑列。 */
function metricsByMark(question, labels) {
  const marks = [["金额", ["金额", "业务金额"]], ["吨", ["吨"]], ["升", ["升"]]];
  for (const [word, candidates] of marks) {
    if (!question.includes(word)) continue;
    const picked = Object.entries(labels || {})
      .filter(([, label]) => candidates.some((mark) => String(label || "").includes(mark)))
      .map(([name]) => name);
    if (picked.length) return picked;
  }
  return [];
}

function basisText(period, precision) {
  const days = Number(period.basis_days || 0);
  const total = Number(period.period_days || 0);
  const note = precision === "day" ? "日粒度真值" : "月粒度均摊估算";
  if (Number(period.elapsed_days || 0) < total) {
    return `已过 ${days} 天/共 ${total} 天，${note}`;
  }
  return `${days} 天，${note}`;
}

function actualLines(head, data, question, intent, defaultMetric) {
  const actual = data.actual || {};
  if (!actual.record_count) return "";
  const labels = data.metric_labels || {};
  const totals = actual.aggregations || {};
  const daily = actual.daily_average || {};
  const explicit = (intent?.metrics || []).map(String).filter(Boolean);
  const byMark = metricsByMark(question, labels);
  const wanted = explicit.length ? explicit : (byMark.length ? byMark : [defaultMetric]);
  let keys = wanted.filter((key) => totals[key] !== undefined);
  if (!keys.length) keys = Object.keys(totals).slice(0, 1);
  const parts = keys.map((key) => {
    const label = String(metricLabel(key, labels));
    const unit = metricUnit(key, label);
    const text = `${label} ${totals[key]}${unit}`;
    return daily[key] === undefined ? text : `${text}，日均 ${daily[key]}${unit}`;
  });
  parts.push(`明细记录 ${actual.record_count || 0} 条`);
  return `${head}${parts.join("；")}。`;
}

function absText(value) {
  const text = String(value ?? "0");
  return text.startsWith("-") ? text.slice(1) : text;
}

function progressLine(progress, data, planMetric) {
  if (!progress || !Object.keys(progress).length) return "";
  if (!progress.available) {
    return `计划与欠进度：${progress.message || "该期间没有可比的计划数据。"}`;
  }
  const labels = (data.plan || {}).metric_labels || {};
  const label = String(metricLabel(planMetric, labels)) || "业务计划";
  const unit = metricUnit(planMetric, label);
  const behind = progress.status === "behind";
  return `${label} ${progress.plan}${unit}，实际 ${progress.actual}${unit}，`
    + `完成率 ${progress.completion_rate}%，时间进度 ${progress.time_progress}%，`
    + `${behind ? "欠进度" : "进度超出"} ${absText(progress.gap_points)} 个百分点`
    + `（${behind ? "缺口" : "超出"} ${absText(progress.gap_value)}${unit}）。`;
}

async function handleImport(intent) {
  const dataset = intent.dataset || DEFAULT_ACTUAL_DATASET;
  const result = await toolRegistry.get("engine_ingest").run({ dataset });
  const task = result.task || {};
  return {
    answer: `已开始${dataset}数据导入，任务号 ${result.task_uuid}。当前状态：${task.status || "pending"}，已处理 ${task.processed || 0} 条。`,
    data: result,
    thought_chain: [{ level: "数据接入", status: "started", message: `经通道触发 ${dataset} 导入` }],
  };
}

async function runOnce(message, intent) {
  // 规则层一个可查锚点都没有（乱码、寒暄、问天气）：不查全量、不派模型，
  // 直接澄清反问。文案与 Python `CLARIFY_ANSWER` 同串 ⇒ 两套 Agent 逐字一致。
  if (intent.unresolved) {
    return {
      answer: CLARIFY_ANSWER,
      intent,
      thought_chain: [{
        level: "语义解析",
        status: "clarify",
        message: "未解析出期间、数据集或指标，先向用户澄清",
      }],
      llm_backend: "clarify",
    };
  }
  let result;
  switch (intent.intent) {
    case "sales_query":
      result = await handleSalesQuestion(intent, message);
      break;
    case "plan_analysis":
      result = await handlePlan(intent, message);
      break;
    case "data_import":
      result = await handleImport(intent);
      break;
    case "rule_config":
      result = {
        answer: "时间规则生成器支持热加载与版本回滚；月精度可派生季节、季度、上下半年和单月/双月，日粒度规则按精度生效。修改请由管理员在基座特征管理的时间规则生成器中操作。",
        thought_chain: [],
      };
      break;
    default:
      result = { answer: "我可以帮你查询已注册数据集、计划完成率、触发导入或说明派生规则。", thought_chain: [] };
  }
  return {
    answer: result.answer,
    intent,
    thought_chain: result.thought_chain,
    data: result.data,
    llm_backend: "harness-rule",
  };
}

export async function orchestrate(message) {
  let intent = null;
  try {
    intent = await recognizeIntent(message);
    return await runOnce(message, intent);
  } catch (error) {
    const isChannel = error && error.name === "ChannelError";
    const detail = isChannel ? error.message : "系统繁忙，请稍后再试";
    return {
      answer: `抱歉，${detail}。如反复出现，请联系系统管理员。`,
      intent: intent || { raw: message },
      thought_chain: [{ level: "引擎通道", status: "unavailable", message: detail }],
      data: null,
      llm_backend: "harness-rule",
    };
  }
}

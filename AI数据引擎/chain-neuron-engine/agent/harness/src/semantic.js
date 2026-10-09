// 语义槽位提取（规则降级）。语义必须与
// agent/python_agent/skills/semantic_slots.py 完全一致。
const BIG_AREAS = [
  "示例组织甲组织层级一", "示例区域丁组织层级一", "示例区域甲组织层级一", "示例区域戊组织层级一",
  "示例区域己组织层级一", "示例组织丙", "示例区域乙组织层级一", "示例区域庚组织层级一", "示例组织乙",
];
// 组织层级二名单（BranchName 标准值），与 Python 版一致
const BRANCHES = [
  "示例区域壬组织层级二", "示例区域戊组织层级二", "示例区域丁组织层级二", "示例区域癸组织层级二",
  "示例区域子组织层级二", "示例区域丑组织层级二", "示例区域寅组织层级二", "示例区域己组织层级二",
  "示例区域卯组织层级二", "示例区域辰组织层级二", "示例区域巳组织层级二", "示例区域午组织层级二",
  "示例区域甲组织层级二", "示例区域乙组织层级二", "示例区域辛组织层级二", "示例区域庚组织层级二",
  "示例区域未组织层级二", "示例区域申组织层级二",
];
const SEASONS = ["春季", "夏季", "秋季", "冬季"];
const QUARTER_NUM = { 一: 1, 二: 2, 三: 3, 四: 4 };
const WEEK_ORDINAL_NUM = { 一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6 };
// 月内序数周："（2026年）4月第三周"。周一起算的日期区间由引擎时间基座换算。
const MONTH_WEEK_RE = /(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*第\s*([一二三四五六1-6])\s*[周星]/;
// 支付方式：只提取"×支付/×付款"的说法，编码一律由引擎按数据集真实取值
// 解析（resolveConditions → level_values），不维护"移动支付=012007"这类静态映射。
const PAYMENT_MARKERS = ["支付", "付款"];
const PAYMENT_CONNECTOR_RE = /[用与和及的以地按对，,。；;？！?]/;
const PAYMENT_PHRASE_RE = /([\u4e00-\u9fa5]{1,4}(?:支付|付款))(?:方式)?/;
const CATEGORY_BY_FUEL = { 类别甲: "01", 类别乙: "02", 类别丙: "03" };
// 显式日历日期：YYYY年M月D日 / YYYY-M-D / YYYY/M/D / YYYY.M.D
const DATE_TEXT_RE = /(?<!\d)(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)/g;
// 省略年份的 M月D日（必须有"日"，否则与"4月"这类月份条件混淆）
const MD_DATE_RE = /(?<![\d年])(\d{1,2})月(\d{1,2})日/g;
const RELATIVE_PERIODS = {
  本周: "current_week", 这周: "current_week", 本星期: "current_week", 这星期: "current_week",
  上周: "previous_week", 上星期: "previous_week", 前一周: "previous_week",
  下周: "next_week", 下星期: "next_week", 下一周: "next_week",
  本月: "current_month", 这个月: "current_month",
  上月: "previous_month", 上个月: "previous_month", 前一个月: "previous_month",
  下月: "next_month", 下个月: "next_month",
  // 相对年份（与引擎 time_foundation 的别名表逐条一致）：哪一年由引擎换算
  今年: "current_year", 本年: "current_year", 当年: "current_year", 本年度: "current_year",
  去年: "previous_year", 上年: "previous_year", 上一年: "previous_year", 前一年: "previous_year",
  明年: "next_year", 下年: "next_year", 下一年: "next_year",
};
// 命中相对年份时，裸月份不再由 Agent 补当前年（"去年8月"要让引擎配 2025）
const RELATIVE_YEAR_KINDS = new Set(["current_year", "previous_year", "next_year"]);

/** 与 engine/ingestion/cleaner.py 的 DAY_LEVEL_FORMATS 同口径。 */
const DAY_LEVEL_FORMATS = new Set(["yyyymmdd", "yyyy-mm-dd", "d/m/yyyy"]);

/** 引擎注册表快照里的日精度数据集：{id: 中文名}（无声明即空，不建静态表）。 */
export function dayGrainedDatasets(items = []) {
  const result = {};
  for (const item of items) {
    const format = String(item?.time?.format || "");
    if (item?.id && item.kind === "generic" && !item.disabled && DAY_LEVEL_FORMATS.has(format)) {
      result[String(item.id)] = String(item.label || "");
    }
  }
  return result;
}

/** 同业务域判定：中文名前缀重合不足 4 字（一个业务词组）即判为不同域。 */
export function sameBusinessDomain(anchor = "", candidate = "", minShared = 4) {
  const limit = Math.min(anchor.length, candidate.length);
  let shared = limit;
  for (let index = 0; index < limit; index++) {
    if (anchor[index] !== candidate[index]) { shared = index; break; }
  }
  return shared >= minShared;
}

/** 显式日历日期（ISO 升序）。非法日期（2月30日）忽略。 */
function explicitDates(q, baseYear) {
  const found = [];
  const push = (year, month, day) => {
    const value = new Date(Date.UTC(year, month - 1, day));
    if (value.getUTCMonth() !== month - 1 || value.getUTCDate() !== day) return;
    if (year >= 2000 && year <= 2099) found.push(value.toISOString().slice(0, 10));
  };
  for (const match of q.matchAll(DATE_TEXT_RE)) {
    push(Number(match[1]), Number(match[2]), Number(match[3]));
  }
  if (baseYear) {
    for (const match of q.matchAll(MD_DATE_RE)) {
      push(Number(baseYear), Number(match[1]), Number(match[2]));
    }
  }
  return [...new Set(found)].sort();
}

/** 剥离日期字面量（含省略年份形式），避免再被当成月份条件或地点用词。 */
function stripDateText(q) {
  return q.replace(DATE_TEXT_RE, " ").replace(MD_DATE_RE, " ");
}

// 类别代码语：数字 + 「号」。前面是「月/数字」的是日期语境（8月9号），不是产品。
function fuelGradeTerms(q) {
  const generic = ["类别代码甲", "类别代码乙", "类别代码丙", "类别代码丁", "类别代码戊"]
    .filter((value) => q.includes(value));
  if (generic.length) return generic;
  // 紧挨品类词的类别代码（0号类别乙）另走原句：时间剥离会把「8月0号」整串当日期吃掉数字，
  // 漏掉类别代码就会退化成"整个类别乙类"，那是静默扩范围。
  const found = (stripTimeWords(q).match(/(?<![\d月])(\d{1,2})\s*号(?!\d)/g) || [])
    .map((value) => value.match(/\d{1,2}/)[0]);
  (q.match(/(?<!\d)(\d{1,2})\s*号(?=\s*(?:类别甲|类别乙|类别丙|类别己))/g) || [])
    .forEach((value) => found.push(value.match(/\d{1,2}/)[0]));
  return [...new Set(found.map((value) => `${value}号`))];
}

function baseProductFilter(q) {
  // 只原语地下传「类别代码甲」这样的说法，不认识、库里没有的类别代码不折算成整类汽/类别乙：
  // 由引擎拿主数据与事实表真实取值相交去落层，落不了就显式失败。
  const values = fuelGradeTerms(q);
  if (values.length) {
    return values.length === 1 ? values[0] : values;
  }
  // 多品类并列时保留完整范围，合计只能在这些品类内由引擎计算。
  const mentioned = Object.keys(CATEGORY_BY_FUEL).filter((name) => q.includes(name));
  return mentioned.length ? oneOrList(mentioned) : null;
}

function oneOrList(values) {
  const uniq = [...new Set(values)];
  return uniq.length === 1 ? uniq[0] : uniq;
}

function parseTime(q) {
  const yyyymms = q.match(/(?<!\d)(20\d{4})(?!\d)/g) || [];
  let years = q.match(/(20\d{2})\s*年/g) || [];
  years = years.map((s) => s.slice(0, 4));

  const baseYearRaw = years.length === 1 ? years[0] : null;
  const dates = explicitDates(q, baseYearRaw);
  const baseYear = baseYearRaw || (dates.length === 1 ? dates[0].slice(0, 4) : null);
  const rest = dates.length ? stripDateText(q) : q;

  // 月内序数周比年月更精确：只下传语义槽，不再叠加年/月条件
  const monthWeek = q.match(MONTH_WEEK_RE);
  const weekYear = monthWeek ? monthWeek[1] || baseYear : null;
  if (weekYear) {
    const ordinal = WEEK_ORDINAL_NUM[monthWeek[3]] || Number(monthWeek[3]);
    return {
      years: [],
      months: [],
      season: null,
      relativePeriod:
        `month_week:${weekYear}${String(Number(monthWeek[2])).padStart(2, "0")}:${ordinal}`,
      daySpan: null,
    };
  }

  let months = dates.length ? [] : [...yyyymms];

  // 相对时间先判：相对年份要参与裸月份的归属判断
  const season = SEASONS.find((s) => q.includes(s)) || null;
  const periodToken = Object.keys(RELATIVE_PERIODS)
    .sort((a, b) => b.length - a.length)
    .find((word) => q.includes(word));
  const relativePeriod = periodToken ? RELATIVE_PERIODS[periodToken] : null;
  const relativeYear = RELATIVE_YEAR_KINDS.has(relativePeriod) ? relativePeriod : null;

  const quarter = rest.match(/第([一二三四1234])季度/);
  if (quarter && baseYear) {
    const raw = quarter[1];
    const n = QUARTER_NUM[raw] || Number(raw);
    for (let m = 3 * n - 2; m <= 3 * n; m++) {
      months.push(baseYear + String(m).padStart(2, "0"));
    }
  }
  if (rest.includes("上半年") && baseYear) {
    for (let m = 1; m <= 6; m++)
      months.push(baseYear + String(m).padStart(2, "0"));
  }
  if (rest.includes("下半年") && baseYear) {
    for (let m = 7; m <= 12; m++)
      months.push(baseYear + String(m).padStart(2, "0"));
  }
  const monthNums = rest.match(/(?<!\d)(\d{1,2})\s*月/g) || [];
  const today = new Date();
  for (const mm of monthNums) {
    const n = Number(mm.match(/\d{1,2}/)[0]);
    if (!(n >= 1 && n <= 12)) continue;
    if (baseYear) {
      months.push(baseYear + String(n).padStart(2, "0"));
      continue;
    }
    if (relativeYear) {
      // 只下传"几月"，哪一年由引擎时间基座配上相对年份
      months.push(String(n));
      continue;
    }
    // 没写年份按当前年；"8月欠进度"在 9 月问的是今年 8 月，
    // 而问未来月时回看去年（与 Python _parse_time 逐条一致）
    const year = n > today.getMonth() + 1 ? today.getFullYear() - 1 : today.getFullYear();
    months.push(String(year) + String(n).padStart(2, "0"));
  }

  months = [...new Set(months)];
  if (months.length && !relativeYear) years = [];
  // 显式日历日期是比年月更精确的约束；相对时间（上周）交给引擎时间基座换算
  const daySpan = dates.length && !relativePeriod
    ? { date_from: dates[0], date_to: dates[dates.length - 1] }
    : null;
  if (daySpan) years = [];
  return { years, months, season, relativePeriod, daySpan };
}

/** 剥离时间字面量：日期、年月、季度、半年、季节与相对周/月说法。 */
function stripTimeWords(q) {
  let text = stripDateText(q);
  text = text.replace(/第\s*[一二三四五六七八九十\d]{1,3}\s*(?:周|星期)/g, " ");
  text = text.replace(/(?<!\d)\d{1,2}\s*日/g, " ");
  text = text.replace(/第[一二三四1234]季度/g, " ");
  text = text.replace(/20\d{2}\s*年/g, " ");
  text = text.replace(/(?<!\d)20\d{4}/g, " ");
  text = text.replace(/(?<!\d)\d{1,2}\s*月/g, " ");
  text = text.replace(/上半年|下半年|全年/g, " ");
  text = text.replace(/单月|双月|奇数月|偶数月/g, " ");
  for (const token of Object.keys(RELATIVE_PERIODS).sort((a, b) => b.length - a.length)) {
    text = text.split(token).join(" ");
  }
  return text.replace(/[春夏秋冬]季?/g, " ");
}

function findStation(q, labels = FALLBACK_LABELS) {
  // 先剥离时间词，避免贪婪匹配吞进“年/季度/日”
  let t = stripTimeWords(q);
  // 数据集中文名/别名不是站名的一部分：「2025年业务域乙示例定位单元丙」按 2-12 字
  // 贪婪窗口会取出"业务域乙示例定位单元丙"，这个取值在事实表里必然不存在。
  for (const label of Object.keys(labels).sort((a, b) => b.length - a.length)) {
    if (label.length >= 2) t = t.split(label).join(" ");
  }
  const m = t.match(/(示例定位单元[甲乙丙丁戊己庚辛壬癸]|[\u4e00-\u9fa5A-Za-z0-9]{2,12}站)/);
  if (m && !["组织层级一", "各", "季度", "年"].some((b) => m[1].includes(b))) {
    return m[1];
  }
  return null;
}

// 地点候选提取：与 Python `_extract_location` 逐条对齐（同一份词表与截断规则）。
// 只做候选提取，真伪由引擎 semantic-probe 用数据集自己的组织列确认（confirmBaseTerms）。
const LOCATION_STOP_WORDS = [
  "查询", "请问", "告诉我", "总", "的", "数据", "情况", "是多少",
  "业务金额", "业务金额", "业务数量", "业务数量", "业务吨数", "业务升数",
  "业务", "金额", "数量", "吨数", "吨", "升数", "升", "记录数", "记录",
  "同比", "环比", "去年", "上个月", "产品", "业务域乙", "线上商城",
  // 指标与提问用词先从文本里挖掉，避免"日均业务数量"整串成为地点候选
  "日均业务数量", "日均", "业务数量", "欠进度", "完成率", "进度", "计划",
  // 「计划和实际完成情况」里的侧别词已被数据集路由消费，剩下的"实际/完成"
  // 若留在文本里会被 4 字窗口切成"和实际完"当成 organization 下发。
  "实际", "完成",
  "怎么样", "怎样", "怎么",
];
const QUESTION_WORDS = [
  "多少", "如何", "情况", "排名", "对比", "合计", "总计", "汇总",
  "统计", "分布", "占比", "趋势", "哪些", "完成率", "进度", "均值",
];
const GROUPING_PREFIXES = ["各", "每", "按"];
// 与 Python `CLARIFY_ANSWER` 同串：示例问题是**能力说明**，不是数据值。
export const CLARIFY_ANSWER =
  "没能在你的问题里解析出查询条件（期间、数据集或指标），本次未执行查询。"
  + "可以这样问：「2024年示例区域甲组织层级一的业务数据甲金额是多少？」"
  + "「2026年9月业务域甲日报的日均业务数量」「去年8月各组织层级一的业务金额」。";
const ORG_MARKER_SUFFIXES = [
  "组织层级二", "组织层级一", "公司", "组织层级三", "服务节点", "服务节点", "网点", "营业厅",
];
// 候选窗口里遇到这些组织后缀就截断：后缀之后的字属于指标/提问用词，
// 不是地名的一部分（"示例定位单元丙业务数量"的 4 字窗口会产出"示例定位单元丙销"）。
const CUT_AFTER_MARKERS = ["站", "店", "组织层级三", "组织层级一", "公司"];
const NON_LOCATION_WORDS = new Set([
  ...Object.keys(CATEGORY_BY_FUEL),
  "计划", "指标", "完成率", "业务数量", "日均", "欠进度", "服务单元", "示例服务", "统计", "方式",
]);

/** 从"×支付/×付款"里取支付方式说法（先按连接词断句，避免吞掉前一个专名）。 */
function paymentPhrase(text) {
  for (const run of String(text || "").split(PAYMENT_CONNECTOR_RE)) {
    const match = run.match(PAYMENT_PHRASE_RE);
    if (match) return match[1];
  }
  return null;
}

function cutAtMarker(value) {
  const ends = [];
  for (const marker of CUT_AFTER_MARKERS) {
    const index = value.indexOf(marker);
    if (index >= 0) ends.push(index + marker.length);
  }
  return ends.length ? value.slice(0, Math.min(...ends)) : value;
}

export function extractLocation(q, labels = FALLBACK_LABELS) {
  if (q.includes("各组织层级一") || q.includes("各组织层级二")) return null;
  let text = stripTimeWords(q);
  // 数据集中文名指的是哪一棵树，不是地点。必须在停用词之前整串挖掉：
  // 「业务域甲日报」按停用词只剩"日报"，会被当成组织名下发。
  for (const label of Object.keys(labels).sort((a, b) => b.length - a.length)) {
    if (label.length >= 2) text = text.split(label).join(" ");
  }
  for (const token of [...LOCATION_STOP_WORDS].sort((a, b) => b.length - a.length)) {
    text = text.split(token).join(" ");
  }
  const sampleStation = text.match(/示例定位单元[甲乙丙丁戊己庚辛壬癸]/);
  if (sampleStation) return sampleStation[0];
  const usable = (value) => Boolean(value)
    && !GROUPING_PREFIXES.some((prefix) => value.startsWith(prefix))
    && !QUESTION_WORDS.some((word) => value.includes(word))
    && !PAYMENT_MARKERS.some((marker) => value.includes(marker))
    && ![...NON_LOCATION_WORDS].some((word) => value.includes(word));
  // 1) 带组织后缀的专名最可靠：只取后缀前的核心名
  for (const match of text.matchAll(
    new RegExp("([\\u4e00-\\u9fa5]{2,6}?(?:" + ORG_MARKER_SUFFIXES.join("|") + "))", "g")
  )) {
    const core = cutAtMarker(match[1].replace(
      new RegExp("(?:" + ORG_MARKER_SUFFIXES.join("|") + ")$"), ""
    ));
    if (usable(core)) return core;
  }
  const sampleLocation = text.match(/示例区域[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申]/);
  if (sampleLocation) return sampleLocation[0];
  // 2) 没有后缀时只接受短残留专名；更长的残留必然是提问或指标用词
  for (const raw of text.match(/[\u4e00-\u9fa5]{2,6}/g) || []) {
    // "类别代码甲"剥掉类别代码数字后残留的"号"也不是地点的一部分；
    // 连接词开头（和/及/或…）同样不属于专名，否则"和实际完"会被当成地点。
    const candidate = cutAtMarker(raw.replace(/^[在与到从对号和及或并]{1,2}/, ""));
    if (candidate.length >= 2 && usable(candidate)) return candidate;
  }
  return null;
}

// 内置数据集的静态标签兜底：引擎不可达时仍能按既有语义工作
export const FALLBACK_LABELS = {
  "业务域甲月报": "oil_monthly_sales",
  "业务数据甲": "oil_monthly_sales",
  "业务域甲日报": "oil_sales_daily",
  "业务域甲计划": "oil_sales_plan",
  "定位单元级业务域甲日报": "oil_monthly_sales",
  "定位单元业务域甲计划": "oil_sales_plan",
  "业务域乙": "non_oil_sales",
  "业务数据乙": "non_oil_sales",
  "线上商城": "non_oil_sales",
  "商城业务": "non_oil_sales",
};

// 派生指标（日均/欠进度）的实际侧兜底：提问没点名数据集时仍按「业务域甲月报」出数
export const DEFAULT_ACTUAL_DATASET = "oil_monthly_sales";
export const PLAN_DATASET = "oil_sales_plan";

/** 点名了哪个数据集就返回其 id；命中位置越靠前越优先，同起点取更长标签。 */
function bestLabelId(q, labels = FALLBACK_LABELS) {
  let best = null;
  for (const [label, id] of Object.entries(labels)) {
    if (!label) continue;
    let start = q.indexOf(label);
    while (start >= 0) {
      const candidate = { start, length: label.length, id };
      if (!best || candidate.start < best.start
        || (candidate.start === best.start && candidate.length > best.length)) {
        best = candidate;
      }
      start = q.indexOf(label, start + 1);
    }
  }
  return best ? best.id : null;
}

/**
 * 按数据集中文名匹配目标数据集；未命中回落新注册的数据集。
 * 只匹配当前注册表数据集，不做旧数据集别名回退。
 * 命中位置越靠前越优先，同起点取更长标签：纯"最长标签优先"会把
 * "业务数据乙金额"里的「业务数据甲」当成点名，把查询错钉到产品数据集。
 */
export function matchDataset(q, isPlan, labels = FALLBACK_LABELS) {
  return bestLabelId(q, labels) ?? (isPlan ? PLAN_DATASET : DEFAULT_ACTUAL_DATASET);
}

/**
 * 派生指标的实际侧数据集：点名哪张实际表就用哪张。
 * 「2026年9月业务域甲日报的日均业务数量」的数据在日报（日粒度、有 9 月），
 * 固定用月报会如实答成"没有实际数据"；点名计划表只影响计划那一侧取数。
 */
export function actualSide(q, labels = FALLBACK_LABELS) {
  const matched = bestLabelId(q, labels);
  return matched && matched !== PLAN_DATASET ? matched : DEFAULT_ACTUAL_DATASET;
}

// 实际侧/计划侧用词：同时出现时问题是"实际 + 计划"两个数据集，不能只查一边
const ACTUAL_WORDS = ["业务", "业务数量", "业务数量", "业务金额", "业务金额", "记录"];
const PLAN_WORDS = ["计划", "欠进度", "日均", "完成率", "计划值"];
// 只有明确点名计划表时，查询才收窄为单一计划数据集
const PLAN_TABLE_TOKENS = ["业务域甲计划", "定位单元业务域甲计划", "业务计划表"];

/** 点名的数据集（未点名且不是计划提问时返回 null，交给范围口径决定）。 */
function explicitDataset(q, isPlan, labels) {
  return bestLabelId(q, labels) ?? (isPlan ? PLAN_DATASET : null);
}

/** 日期区间覆盖的月份（YYYYMM）：只有月粒度的计划数据按此折算。 */
function monthsInSpan(span) {
  const [start, end] = [span.date_from, span.date_to].map((v) => new Date(`${v}T00:00:00Z`));
  const months = [];
  const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1));
  while (cursor <= end) {
    months.push(`${cursor.getUTCFullYear()}${String(cursor.getUTCMonth() + 1).padStart(2, "0")}`);
    cursor.setUTCMonth(cursor.getUTCMonth() + 1);
  }
  return months;
}

/** 数据集 id → 中文名（同名取最长，即最精确的那个）。 */
function idsByLabel(labels = {}) {
  const byId = {};
  for (const [label, id] of Object.entries(labels)) {
    if (label && String(label).length > String(byId[id] || "").length) byId[id] = label;
  }
  return byId;
}

/** 日均/欠进度：只识别要算哪个派生指标，天数与除法一律在引擎侧完成。 */
export function derivedMetrics(q) {
  const derived = [];
  if (["日均", "日平均", "每天销"].some((word) => q.includes(word))) {
    derived.push("daily_avg");
  }
  if (["欠进度", "进度差", "落后计划"].some((word) => q.includes(word))) {
    derived.push("progress_gap");
  }
  return derived;
}

/** 提问是否要逐项对比（同环比或分组）：这类主答案走批量查询，不转派生计算。 */
function isComparisonOrGrouped(q) {
  return ["同比", "环比"].some((token) => q.includes(token))
    || GROUPING_PREFIXES.some((prefix) => q.includes(prefix));
}

export function extractSlots(q, datasetLabels = FALLBACK_LABELS, dayGrained = {}) {
  if (q.includes("导入") || q.includes("上传") || q.includes("重新导入")) {
    return { intent: "data_import", dataset: "oil_monthly_sales", conditions: {},
             filters: {}, group_by: null, detail: false };
  }
  if (q.includes("规则")) {
    return { intent: "rule_config", dataset: "oil_monthly_sales", conditions: {},
             filters: {}, group_by: null, detail: false };
  }
  const derived = derivedMetrics(q);
  if (q.includes("完成率") || (derived.length && !isComparisonOrGrouped(q))) {
    const { years, months, season, relativePeriod, daySpan } = parseTime(q);
    const monthParity = q.includes("单月") || q.includes("奇数月")
      ? "单月"
      : q.includes("双月") || q.includes("偶数月") ? "双月" : null;
    const actualDataset = actualSide(q, datasetLabels);
    const conditions = {};
    let periodDefaulted = false;
    if (months.length) conditions.month = oneOrList(months);
    else if (daySpan) {
      if (dayGrained[actualDataset] !== undefined) {
        // 点名的实际表有真日值：区间直接按日取数。折算成整月会让
        // 分子变成全月、分母仍是区间天数，日均被摊大。
        conditions.date_from = daySpan.date_from;
        conditions.date_to = daySpan.date_to;
      } else {
        conditions.month = oneOrList(monthsInSpan(daySpan));
      }
    } else if (years.length) conditions.year = oneOrList(years);
    if (season) conditions.season = season;
    if (monthParity) conditions.month_parity = monthParity;
    if (relativePeriod) conditions.relative_period = relativePeriod;
    if (!Object.keys(conditions).length) {
      // 日均/欠进度必须有期间才能摊天数：提问没带时间时按当前自然月，
      // 期间换算与天数仍由引擎时间基座给出。默认口径要在答案里写明，
      // 所以额外打一个标记，不把"没问时间"当成"问了本月"。
      conditions.relative_period = "current_month";
      periodDefaulted = true;
    }
    const product = baseProductFilter(q);
    const base_filters = product ? { product } : {};
    const location = extractLocation(q, datasetLabels);
    if (location) base_filters.organization = location;
    return { intent: "plan_analysis", dataset: PLAN_DATASET,
             actual_dataset: actualDataset, conditions,
             period_defaulted: periodDefaulted,
             filters: {}, base_filters,
             group_by: null, detail: false, derived_metrics: derived };
  }

  const isPlan = q.includes("计划");
  const { years, months, season, relativePeriod, daySpan } = parseTime(q);
  const monthParity = q.includes("单月") || q.includes("奇数月")
    ? "单月"
    : q.includes("双月") || q.includes("偶数月") ? "双月" : null;

  const conditions = {};
  const filters = {};
  if (daySpan) {
    conditions.date_from = daySpan.date_from;
    conditions.date_to = daySpan.date_to;
    filters.date_from = daySpan.date_from;
    filters.date_to = daySpan.date_to;
  } else if (months.length) {
    const val = oneOrList(months);
    conditions.month = val;
    filters.yyyymm = val;
  } else if (years.length) {
    const val = oneOrList(years);
    conditions.year = val;
    filters.year = val;
  }
  if (season) {
    conditions.season = season;
    filters.season = season;
  }
  if (monthParity) {
    conditions.month_parity = monthParity;
    filters.month_parity = monthParity;
  }
  if (relativePeriod) {
    conditions.relative_period = relativePeriod;
    filters.relative_period = relativePeriod;
  }

  for (const area of BIG_AREAS) {
    if (q.includes(area)) {
      conditions.big_area = area;
      filters.big_area = area;
      break;
    }
  }

  // 组织层级二（branch）：按名单精确匹配；特殊 BranchName 由 LLM 解析
  for (const branch of BRANCHES) {
    if (q.includes(branch)) {
      conditions.branch = branch;
      filters.branch = branch;
      break;
    }
  }

  // 数字编码一律从剥离时间字面量后的文本里取："2026-04-01"里的"0"
  // 曾被当成 0 号类别乙类别代码，把整条查询过滤成空结果。
  const timeFree = stripTimeWords(q);
  const gasCodes = timeFree.match(/(?<!\d)(60\d{6}|61\d{6}|90\d{6})(?!\d)/g);
  const base_filters = {};
  // 已被组织层级槽消费的名称不能再拆一半当基座地点候选（"示例区域甲组织层级一"里的
  // "示例区域甲"会让基座翻译失败并阻断整条查询）；定位单元名不在此列。
  const consumed = [conditions.big_area, conditions.branch]
    .filter(Boolean)
    .map((value) => String(value));
  const location = extractLocation(q, datasetLabels);
  if (location && !consumed.some((value) => value.includes(location))) {
    base_filters.organization = location;
  }
  if (gasCodes) {
    const val = oneOrList(gasCodes);
    conditions.gas_code = val;
    filters.gas_code = val;
  } else {
    if (fuelGradeTerms(q).length) {
      // 类别代码只交给通用基座：主数据的物料编码与数据集产品编码层按真实取值相交才落层。
      // 自己下发 grade="92" 会绕过主数据，又在没有"类别代码"层级的数据集上整条拒答。
      const product = baseProductFilter(q);
      if (product) base_filters.product = product;
    } else {
      // 只有一家品类时才是过滤条件；多品类并列是输出分组，不缩小范围
      const mentioned = Object.keys(CATEGORY_BY_FUEL).filter((name) => q.includes(name));
      if (mentioned.length === 1) {
        filters.category = CATEGORY_BY_FUEL[mentioned[0]];
        base_filters.product = mentioned[0];
      } else if (mentioned.length > 1) {
        // 多品类并列既是输出分组，也是合计范围；由基座翻译为类别列表。
        base_filters.product = mentioned;
      }
    }
  }

  const paidCodes = timeFree.match(/(?<!\d)(012\d{3})(?!\d)/g);
  if (paidCodes) {
    const val = oneOrList(paidCodes);
    conditions.check_mode = val;
    filters.check_mode = val;
  } else {
    // 说法原样下传，由引擎按该数据集的真实取值解析；解析不到即显式报错
    const phrase = paymentPhrase(timeFree);
    if (phrase) {
      conditions.check_mode = phrase;
      filters.check_mode = phrase;
    }
  }

  // 定位单元只填语义槽 station，由引擎按数据集声明的服务单元层级归位（X29）。
  // 直接写物理列 store_name 会和基座翻译出的同名列冲突，冲突条件被跳过
  // 过滤后整条查询静默扩成全量（比拒答更危险）。
  const station = findStation(q, datasetLabels);
  if (station) {
    conditions.station = station;
    filters.station = station;
  }

  let group_by = null;
  const groupRules = [
    ["big_area", ["各组织层级一", "组织层级一对比", "组织层级一业务"]],
    ["branch", ["各组织层级二", "组织层级二对比", "各组织层级二"]],
    ["area", ["各组织层级三", "组织层级三对比"]],
    ["month", ["各月", "每月", "月度", "各月业务"]],
    ["gas_code", ["各产品", "每种产品"]],
    ["station", ["各站", "定位单元排名"]],
  ];
  for (const [key, words] of groupRules) {
    if (words.some((w) => q.includes(w))) { group_by = key; break; }
  }
  // 「类别乙、类别甲、合计」按大类输出，合计范围已由基座限定为这两个品类。
  if (!group_by && q.includes("类别乙") && q.includes("类别甲")) group_by = "category";

  let detail = ["明细", "单据", "记录", "有哪些", "业务情况"]
    .some((w) => q.includes(w));
  if (station) {
    detail = true;
    if (!group_by && q.includes("各产品")) group_by = "gas_code";
  }

  const wantsActual = ACTUAL_WORDS.some((word) => q.includes(word));
  const wantsPlan = PLAN_WORDS.some((word) => q.includes(word));
  let explicit = explicitDataset(q, isPlan, datasetLabels);
  if (wantsActual && wantsPlan
    && !PLAN_TABLE_TOKENS.some((token) => q.includes(token))) {
    explicit = null;
  }
  const dataset = explicit
    || matchDataset(q, isPlan && !(wantsActual && wantsPlan), datasetLabels);
  const comparisons = [];
  if (q.includes("同比")) comparisons.push("yoy");
  if (q.includes("环比")) comparisons.push("mom");
  let datasetScope = "all_actual";
  if (explicit) datasetScope = "single";
  else if (wantsActual && wantsPlan) datasetScope = "explicit_multi";
  else if (base_filters.product) datasetScope = "single";
  else if (isPlan) datasetScope = "plan";
  const datasets = [];
  if (datasetScope === "single") datasets.push(dataset);
  else if (datasetScope === "explicit_multi") datasets.push(dataset, PLAN_DATASET);
  // all_actual 不在这里枚举：范围口径由引擎按经验库给出（见 orchestrator）
  //
  // 点名了数据集、又要求日精度（显式日期或"上周"）时，只查月粒度数据集必然
  // 只能得到"粒度不足"。按引擎声明的时间格式把同业务域里真能按日计算的事实
  // 数据集并进来（月报→日报），月粒度那份仍如实报告缺口：不隐藏能力差异，
  // 也不跨业务域污染合计。
  if ((daySpan || relativePeriod) && datasets.length && wantsActual) {
    const anchor = idsByLabel(datasetLabels)[datasets[0]] || datasets[0];
    const extra = Object.entries(dayGrained)
      .filter(([id, label]) => !datasets.includes(id)
        && sameBusinessDomain(anchor, String(label || "")))
      .map(([id]) => id);
    if (extra.length) {
      datasets.push(...extra);
      datasetScope = "explicit_multi";
    }
  }

  // 一个可查锚点都没有（乱码、寒暄、问天气）⇒ 交给编排层反问，而不是替用户
  // 猜一个"全量概览"。`base_filters.organization` 是地点**候选**（未经引擎确认
  // 真伪）所以不算锚点：「哈哈哈」也会抽出 organization=哈哈哈，把它当条件
  // 就等于把噪声答成结果。判定只看规则层，模型不参与，两套 Agent 必然同串。
  const otherBase = Object.fromEntries(
    Object.entries(base_filters).filter(([key]) => key !== "organization")
  );
  const unresolved = Boolean(
    Object.keys(conditions).length || Object.keys(filters).length
    || Object.keys(otherBase).length || group_by || comparisons.length
    || derived.length || detail || wantsActual || wantsPlan
    || bestLabelId(q, datasetLabels) || stripTimeWords(q) !== q
  ) === false;

  return {
    intent: "sales_query",
    dataset,
    datasets,
    dataset_scope: datasetScope,
    conditions,
    filters,
    base_filters,
    group_by,
    detail,
    comparisons,
    derived_metrics: derived,
    unresolved,
  };
}

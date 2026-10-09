# -*- coding: utf-8 -*-
"""语义槽位提取（纯函数，规则降级用）。

输出同时包含：
- conditions：聚合路由条件（层级键；month 值为 YYYYMM；多值用 list → IN）；
- filters：明细查询条件（yyyymm/category/store_name 等；多值用 list）。
两个 Agent 实现（python_agent / Harness）的规则必须与此语义一致。
"""
from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

from engine.core.logging import get_logger
from engine.ingestion.cleaner import DAY_LEVEL_FORMATS

logger = get_logger("agent.slots")

# 内置数据集的静态标签兜底：引擎不可达时仍能按既有语义工作
FALLBACK_LABELS = {
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
}

_LABELS_TTL = 60.0
_LABELS_CACHE: dict[str, Any] = {"at": 0.0, "map": {}, "by_id": {}, "day_grained": {}}

# 派生指标（日均/欠进度）的实际侧兜底：提问没点名数据集时仍按「业务域甲月报」出数
DEFAULT_ACTUAL_DATASET = "oil_monthly_sales"
PLAN_DATASET = "oil_sales_plan"


def dataset_labels(force: bool = False) -> dict[str, str]:
    """数据集中文名 → 数据集 id（TTL 缓存；引擎不可达时回落静态映射）。

    界面注册的新数据集会自动出现在这里，Agent 无需改动即可按中文名路由。
    同一次抓取顺带记录：id → 中文名，以及引擎声明时间格式支持日精度的
    事实数据集（`day_grained`），供日精度问题按业务域扩展承接方。
    """
    now = time.monotonic()
    cached = _LABELS_CACHE["map"]
    if cached and not force and now - _LABELS_CACHE["at"] < _LABELS_TTL:
        return cached
    labels = dict(FALLBACK_LABELS)
    day_grained: dict[str, str] = {}
    try:
        # 延迟导入，避免模块级循环依赖
        from agent.python_agent.engine_channel_client import EngineChannelClient

        with EngineChannelClient(timeout=3.0) as channel:
            for item in channel.list_datasets().get("items", []):
                label = str(item.get("label") or "").strip()
                did = item.get("id")
                if label and did:
                    labels[label] = str(did)
                    for alias in item.get("aliases") or []:
                        alias_text = str(alias or "").strip()
                        if alias_text:
                            labels[alias_text] = str(did)
                time_format = str((item.get("time") or {}).get("format") or "")
                if did and item.get("kind") == "generic" and not item.get("disabled"):
                    if time_format in DAY_LEVEL_FORMATS:
                        day_grained[str(did)] = label
        _LABELS_CACHE.update(
            at=now, map=labels, by_id=_ids_by_label(labels), day_grained=day_grained
        )
    except Exception as e:  # 引擎不可达：保留兜底映射，不阻断查询
        logger.warning("数据集标签获取失败，使用兜底映射：%s", e)
        # 失败时也缓存兜底结果，避免每次对话都重复等待引擎列表请求超时。
        _LABELS_CACHE.update(
            at=now, map=labels, by_id=_ids_by_label(labels), day_grained=day_grained
        )
    return labels


def _ids_by_label(labels: dict[str, str]) -> dict[str, str]:
    """数据集 id → 中文名（同名条目取最长即最精确的那个）。"""
    by_id: dict[str, str] = {}
    for label, dataset in labels.items():
        if len(label) > len(by_id.get(dataset, "")):
            by_id[dataset] = label
    return by_id


def day_grained_datasets() -> dict[str, str]:
    """引擎注册表里时间格式支持日精度的事实数据集：{id: 中文名}。"""
    dataset_labels()
    return dict(_LABELS_CACHE["day_grained"])


def dataset_label(dataset: str) -> str:
    """数据集的引擎声明中文名（用于业务域判定；未知时返回 id）。"""
    dataset_labels()
    return str(_LABELS_CACHE["by_id"].get(dataset) or dataset)


def same_business_domain(anchor: str, candidate: str, min_shared: int = 4) -> bool:
    """按引擎声明的中文名判断是否同一业务对象。

    日精度问题需要把「业务域甲月报」扩展给「业务域甲日报」，
    但不能把「业务域乙明细」卷进来（它会污染产品口径的合计）。
    中文名前缀重合不足 4 字（一个业务词组）即判为不同域。
    """
    shared = min(len(anchor), len(candidate))
    for index, (a, b) in enumerate(zip(anchor, candidate)):
        if a != b:
            shared = index
            break
    return shared >= min_shared


def _best_label_match(question: str) -> tuple[str, str] | None:
    """问题里点名了哪个数据集：返回 (数据集 id, 命中的中文名)。

    按命中位置择优：起点更靠前的胜出，同起点取更长标签。
    纯"最长标签优先"会把"业务数据乙金额"里的「业务数据甲」当成点名，
    把查询错钉到产品数据集上（「业务域乙」命中其实起始更靠前）。
    """
    best: tuple[int, int, str, str] | None = None
    for label, dataset in dataset_labels().items():
        if not label:
            continue
        start = question.find(label)
        while start >= 0:
            candidate = (start, -len(label), label, dataset)
            if best is None or candidate < best:
                best = candidate
            start = question.find(label, start + 1)
    return (best[3], best[2]) if best else None


def match_dataset(question: str, is_plan: bool) -> str:
    """按数据集中文名匹配目标数据集；未命中回落新架构数据集。"""
    matched = _best_label_match(question)
    if matched:
        return matched[0]
    return "oil_sales_plan" if is_plan else "oil_monthly_sales"


# 组织层级一全称（长名优先，避免“示例区域丁”误匹配“示例组织甲”）
BIG_AREAS = [
    "示例组织甲组织层级一", "示例区域丁组织层级一", "示例区域甲组织层级一", "示例区域戊组织层级一",
    "示例区域己组织层级一", "示例组织丙", "示例区域乙组织层级一", "示例区域庚组织层级一", "示例组织乙",
]
# 组织层级二名单（BranchName 标准值；长名如“示例区域壬”无歧义）
BRANCHES = [
    "示例区域壬组织层级二", "示例区域戊组织层级二", "示例区域丁组织层级二", "示例区域癸组织层级二",
    "示例区域子组织层级二", "示例区域丑组织层级二", "示例区域寅组织层级二", "示例区域己组织层级二",
    "示例区域卯组织层级二", "示例区域辰组织层级二", "示例区域巳组织层级二", "示例区域午组织层级二",
    "示例区域甲组织层级二", "示例区域乙组织层级二", "示例区域辛组织层级二", "示例区域庚组织层级二",
    "示例区域未组织层级二", "示例区域申组织层级二",
]
SEASONS = ["春季", "夏季", "秋季", "冬季"]
RELATIVE_PERIODS = {
    "本周": "current_week", "这周": "current_week", "本星期": "current_week", "这星期": "current_week",
    "上周": "previous_week", "上星期": "previous_week", "前一周": "previous_week",
    "下周": "next_week", "下星期": "next_week", "下一周": "next_week",
    "本月": "current_month", "这个月": "current_month",
    "上月": "previous_month", "上个月": "previous_month", "前一个月": "previous_month",
    "下月": "next_month", "下个月": "next_month",
    # 相对年份（与引擎 time_foundation 的别名表逐条对齐）：Agent 只识别说法，
    # 今年是哪一年由引擎按锚定日期换算，"去年8月"里的 8 月也交给引擎配年份。
    "今年": "current_year", "本年": "current_year", "当年": "current_year", "本年度": "current_year",
    "去年": "previous_year", "上年": "previous_year", "上一年": "previous_year", "前一年": "previous_year",
    "明年": "next_year", "下年": "next_year", "下一年": "next_year",
}
# 相对年份语义槽：命中时裸月份不再由 Agent 补当前年，避免
# "去年8月"变成 year=2025 与 month=202608 互相矛盾的空查询。
RELATIVE_YEAR_KINDS = ("current_year", "previous_year", "next_year")
_QUARTER_NUM = {"一": 1, "二": 2, "三": 3, "四": 4}
_WEEK_ORDINAL_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
# 显式日历日期：YYYY年M月D日 / YYYY-M-D / YYYY/M/D / YYYY.M.D / YYYYMMDD
_DATE_TEXT_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)"
)
# 省略年份的 M月D日（必须有"日"，否则与"4月"这类月份条件混淆）
_MD_DATE_RE = re.compile(r"(?<![\d年])(\d{1,2})月(\d{1,2})日")
# 月内序数周："（2026年）4月第三周"。周一起算的日期区间由引擎时间基座换算，
# Agent 只产出语义槽 month_week:YYYYMM:N，绝不自算日历。
_MONTH_WEEK_RE = re.compile(r"(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*第\s*([一二三四五六1-6])\s*[周星]")
# "第N周/第N星期"是时间说法，不可能是定位单元名或地点名的一部分
_ORDINAL_WEEK_RE = re.compile(r"第\s*[一二三四五六七八九十\d]{1,3}\s*(?:周|星期)")

# 支付方式：Agent 只提取"×支付/×付款"这个说法本身，编码一律由引擎按
# 数据集层级真实取值解析（resolve_conditions → level_values），
# 这里不维护"移动支付=012007"这类静态取值映射。
_PAYMENT_MARKERS = ("支付", "付款")
_PAYMENT_CONNECTOR_RE = re.compile(r"[用与和及的以地按对，,。；;？！?]")
_PAYMENT_PHRASE_RE = re.compile(r"([\u4e00-\u9fa5]{1,4}(?:支付|付款))(?:方式)?")
_CATEGORY_BY_FUEL = {"类别甲": "01", "类别乙": "02", "类别丙": "03"}
_LOCATION_STOP_WORDS = (
    "查询", "请问", "告诉我", "总", "的", "数据", "情况", "是多少",
    "业务金额", "业务金额", "业务数量", "业务数量", "业务吨数", "业务升数",
    "业务", "金额", "数量", "吨数", "吨", "升数", "升", "记录数", "记录",
    "同比", "环比", "去年", "上个月", "产品", "业务域乙", "线上商城",
    # 指标与提问用词先从文本里挖掉，避免"日均业务数量"整串成为地点候选
    "日均业务数量", "日均", "业务数量", "欠进度", "完成率", "进度", "计划",
    # 「计划和实际完成情况」里的侧别词已被数据集路由消费，剩下的"实际/完成"
    # 若留在文本里会被 4 字窗口切成"和实际完"当成 organization 下发。
    "实际", "完成",
    "怎么样", "怎样", "怎么",
)
# 提问/分组用词：它们永远是谓语或维度副词，不可能是组织名。
# 命中时整句都不该提取地点（"各月业务数量如何"曾被当成 organization 下发，
# 基座无此取值即阻断整条查询，返回"没有命中数据"）。
_QUESTION_WORDS = (
    "多少", "如何", "情况", "排名", "对比", "合计", "总计", "汇总",
    "统计", "分布", "占比", "趋势", "哪些", "完成率", "进度", "均值",
)
# 提问里连一个可查锚点都没有时的澄清文案。示例问题是**能力说明**，
# 不是数据值——Agent 不造值也不算数，但也没义务替一句乱码编一个答案。
CLARIFY_ANSWER = (
    "没能在你的问题里解析出查询条件（期间、数据集或指标），本次未执行查询。"
    "可以这样问：「2024年示例区域甲组织层级一的业务数据甲金额是多少？」"
    "「2026年9月业务域甲日报的日均业务数量」「去年8月各组织层级一的业务金额」。"
)
_GROUPING_PREFIXES = ("各", "每", "按")
# 组织专名后缀：带后缀的名称才是可靠地点候选（核心名 = 去掉后缀）
_ORG_MARKER_SUFFIXES = (
    "组织层级二", "组织层级一", "公司", "组织层级三", "服务节点", "服务节点", "网点", "营业厅",
)
_ORG_MARKER_RE = re.compile(
    r"([\u4e00-\u9fa5]{2,6}?(?:" + "|".join(_ORG_MARKER_SUFFIXES) + "))"
)
_ORG_MARKER_SUFFIX_RE = re.compile(
    r"(?:" + "|".join(_ORG_MARKER_SUFFIXES) + ")$"
)
# 候选窗口里遇到这些组织后缀就截断：后缀之后的字属于指标/提问用词，
# 不是地名的一部分（"示例定位单元丙业务数量"的 4 字窗口曾产出"示例定位单元丙销"）。
_CUT_AFTER_MARKERS = ("站", "店", "组织层级三", "组织层级一", "公司")


def _cut_at_marker(value: str) -> str:
    ends = [
        index + len(marker)
        for marker in _CUT_AFTER_MARKERS
        for index in [value.find(marker)]
        if index >= 0
    ]
    return value[:min(ends)] if ends else value
# 这些词属于产品/指标/支付维度，绝不能当作地点下传
_NON_LOCATION_WORDS = frozenset(
    list(_CATEGORY_BY_FUEL)
    + ["计划", "指标", "完成率", "业务数量", "日均", "欠进度", "服务单元", "示例服务", "统计", "方式"]
)


def _payment_phrase(text: str) -> str | None:
    """从"×支付/×付款"里取支付方式说法（先按连接词断句，避免吞掉前一个专名）。"""
    for run in _PAYMENT_CONNECTOR_RE.split(text):
        match = _PAYMENT_PHRASE_RE.search(run or "")
        if match:
            return match.group(1)
    return None


# 类别代码语：数字 + 「号」。前面是「月/数字」的是日期语境（8月9号），不是产品。
_FUEL_GRADE_RE = re.compile(r"(?<![\d月])(\d{1,2})\s*号(?!\d)")
# 紧挨品类词的类别代码（0号类别乙）另走原句：时间剥离会把「8月0号」整串当日期吃掉数字，
# 漏掉类别代码就会退化成"整个类别乙类"，那是静默扩范围。
_FUEL_ADJACENT_GRADE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*号(?=\s*(?:类别甲|类别乙|类别丙|类别己))")


def _fuel_grade_terms(q: str) -> list[str]:
    """提问里点名的类别代码原语（类别代码甲/0号/类别代码乙），去掉时间字面量后再取。"""
    found = [
        value
        for value in ("类别代码甲", "类别代码乙", "类别代码丙", "类别代码丁", "类别代码戊")
        if value in q
    ]
    if found:
        return found
    text = _strip_time_words(q)
    found = list(_FUEL_GRADE_RE.findall(text))
    found.extend(_FUEL_ADJACENT_GRADE_RE.findall(q))
    return [f"{value}号" for value in dict.fromkeys(found)]


def _base_product_filter(q: str) -> str | list[str] | None:
    """把规则解析出的产品语义保留给通用数据基座。

    只原语地下传「类别代码甲」这样的说法，不认识、库里没有的类别代码不折算成整类汽/类别乙：
    由引擎拿主数据与事实表真实取值相交去落层，落不了就显式失败。
    """
    values = _fuel_grade_terms(q)
    if values:
        return _one_or_list(values)
    categories = [name for name in _CATEGORY_BY_FUEL if name in q]
    # 多品类并列时保留完整范围，合计只能在这些品类内由引擎计算。
    return _one_or_list(categories) if categories else None


def _extract_location(q: str) -> str | None:
    """提取用户地点语义候选，交给基座按数据集特征分别翻译。

    只做候选提取，不判定真伪：真伪由引擎的 semantic-probe 用数据集自己的
    组织/产品列确认，命中才下传（见 EngineChannelClient.confirm_base_terms）。
    """
    if "各组织层级一" in q or "各组织层级二" in q:
        return None
    text = _strip_time_words(q)
    # 数据集中文名指的是哪一棵树，不是地点。必须在停用词之前整串挖掉：
    # 「业务域甲日报」按停用词只剩"日报"，会被当成组织名下发。
    for label in sorted(dataset_labels(), key=len, reverse=True):
        if len(label) >= 2:
            text = text.replace(label, " ")
    for token in sorted(_LOCATION_STOP_WORDS, key=len, reverse=True):
        text = text.replace(token, " ")

    sample_station = re.search(r"示例定位单元[甲乙丙丁戊己庚辛壬癸]", text)
    if sample_station:
        return sample_station.group(0)

    def usable(value: str) -> bool:
        return bool(value) and not value.startswith(_GROUPING_PREFIXES) \
            and not any(word in value for word in _QUESTION_WORDS) \
            and not any(marker in value for marker in _PAYMENT_MARKERS) \
            and not any(word in value for word in _NON_LOCATION_WORDS)

    # 1) 带组织后缀的专名最可靠：只取后缀前的核心名
    for match in _ORG_MARKER_RE.finditer(text):
        core = _cut_at_marker(_ORG_MARKER_SUFFIX_RE.sub("", match.group(1)))
        if usable(core):
            return core
    # 2) 没有后缀时只接受短残留专名（"示例区域丁""示例区域乙"）；更长的残留
    #    必然是提问或指标用词，早期版本把"夏季示例区域丁组织层级一各"整串当成了地点。
    sample_location = re.search(r"示例区域[甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申]", text)
    if sample_location:
        return sample_location.group(0)
    for candidate in re.findall(r"[\u4e00-\u9fa5]{2,6}", text):
        # "类别代码甲"剥掉类别代码数字后残留的"号"也不是地点的一部分；
        # 连接词开头（和/及/或…）同样不属于专名，否则"和实际完"会被当成地点。
        candidate = _cut_at_marker(re.sub(r"^[在与到从对号和及或并]{1,2}", "", candidate))
        if len(candidate) >= 2 and usable(candidate):
            return candidate
    return None


def _explicit_dataset(question: str, is_plan: bool) -> str | None:
    matched = _best_label_match(question)
    if matched:
        return matched[0]
    if is_plan:
        return "oil_sales_plan"
    return None


def _actual_side(question: str) -> str:
    """派生指标的实际侧数据集：点名哪张实际表就用哪张。

    「2026年9月业务域甲日报的日均业务数量」的数据在日报（日粒度、有 9 月），
    固定用月报会如实答成"没有实际数据"。计划数据集不能当实际侧，点名它
    只影响计划那一侧的取数，实际侧仍回落月报。
    """
    matched = _best_label_match(question)
    if matched and matched[0] != PLAN_DATASET:
        return matched[0]
    return DEFAULT_ACTUAL_DATASET


def _one_or_list(values: list[str]) -> str | list[str]:
    values = list(dict.fromkeys(values))
    return values[0] if len(values) == 1 else values


def _explicit_dates(q: str, base_year: str | None) -> list[str]:
    """问题里的显式日历日期（ISO 升序）。非法日期（2月30日）直接忽略。"""
    found: list[str] = []
    for match in _DATE_TEXT_RE.finditer(q):
        year, month, day = (int(value) for value in match.groups())
        try:
            found.append(date(year, month, day).isoformat())
        except ValueError:
            continue
    if base_year:
        for match in _MD_DATE_RE.finditer(q):
            month, day = (int(value) for value in match.groups())
            try:
                found.append(date(int(base_year), month, day).isoformat())
            except ValueError:
                continue
    return sorted(dict.fromkeys(found))


def _strip_date_text(q: str, base_year: str | None) -> str:
    """剥离日期字面量，避免"4月1日"再被当成月份条件或地点用词。"""
    text = _DATE_TEXT_RE.sub(" ", q)
    return _MD_DATE_RE.sub(" ", text) if base_year else text


def _months_in_span(span: tuple[str, str]) -> list[str]:
    """日期区间覆盖到的月份集合（YYYYMM），供只有月粒度的计划数据折算。"""
    start, end = (date.fromisoformat(value) for value in span)
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year}{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _parse_time(q: str) -> tuple[list[str], list[str], str | None, str | None, tuple[str, str] | None]:
    """返回 (years, months[YYYYMM], season, relative_period, day_span)。

    显式日历日期是比年月更精确的约束：命中日期时不再产出年/月条件，
    改为 (起始日, 结束日) 区间交给引擎时间基座。相对时间（上周）优先，
    它本身由引擎按锚定日期换算，Agent 不自行推算。
    """
    yyyymms = re.findall(r"(?<!\d)(20\d{4})(?!\d)", q)
    years = re.findall(r"(20\d{2})\s*年", q)

    # 月份范围扩展所需基准年：唯一年
    base_year = years[0] if len(years) == 1 else None
    dates = _explicit_dates(q, base_year)
    if dates and len(dates) == 1 and not base_year:
        base_year = dates[0][:4]
    rest = _strip_date_text(q, base_year) if dates else q

    # 月内序数周比年月更精确：只下传语义槽，日期区间交给引擎换算，
    # 不再叠加年/月条件（否则日粒度会被月条件抢先粗化）。
    month_week = _MONTH_WEEK_RE.search(q)
    week_year = (month_week.group(1) or base_year) if month_week else None
    if week_year:
        ordinal = _WEEK_ORDINAL_NUM.get(month_week.group(3)) or int(month_week.group(3))
        token = f"month_week:{week_year}{int(month_week.group(2)):02d}:{ordinal}"
        return [], [], None, token, None

    months: list[str] = list(yyyymms)

    # 相对时间先判：相对年份要参与裸月份的归属判断
    season = next((s for s in SEASONS if s in q), None)
    relative_period = next(
        (RELATIVE_PERIODS[token]
         for token in sorted(RELATIVE_PERIODS, key=len, reverse=True)
         if token in q),
        None,
    )
    relative_year = relative_period if relative_period in RELATIVE_YEAR_KINDS else None

    quarter = re.search(r"第([一二三四1234])季度", rest)
    if quarter and base_year:
        raw = quarter.group(1)
        n = _QUARTER_NUM.get(raw) or int(raw)
        months += [f"{base_year}{m:02d}" for m in range(3 * n - 2, 3 * n + 1)]

    if "上半年" in rest and base_year:
        months += [f"{base_year}{m:02d}" for m in range(1, 7)]
    if "下半年" in rest and base_year:
        months += [f"{base_year}{m:02d}" for m in range(7, 13)]

    # “N月”（1-2 位）结合基准年：没写年份时按当前年，
    # 但“8月欠进度”在 9 月问的是今年 8 月，而 12 月在未来时要回看去年。
    # 提问同时给了相对年份（“去年8月”）时只下传裸月份，哪一年由引擎配。
    today = date.today()
    for mm in re.findall(r"(?<!\d)(\d{1,2})\s*月", rest):
        number = int(mm)
        if not 1 <= number <= 12:
            continue
        if base_year:
            months.append(f"{base_year}{number:02d}")
            continue
        if relative_year:
            months.append(str(number))
            continue
        year = today.year
        if number > today.month:
            year -= 1
        months.append(f"{year}{number:02d}")

    months = list(dict.fromkeys(months))
    # 由月份反推年（月份已含完整年），避免重复年条件
    if months and not relative_year:
        years = []

    day_span: tuple[str, str] | None = None
    if dates and not relative_period:
        day_span = (dates[0], dates[-1])
        years, months = [], []
    return years, months, season, relative_period, day_span


def _strip_time_words(q: str) -> str:
    """剥离时间字面量：日期、年月、季度、半年、季节与相对周/月说法。

    这些词不可能是定位单元名或地点名的一部分。此前只剥年份和月份，
    "2026年4月1日各产品"残留的"日各"、"上周"被当成地点下传，
    基座无此取值即阻断整条查询。
    """
    text = _DATE_TEXT_RE.sub(" ", q)
    text = _MD_DATE_RE.sub(" ", text)
    text = _ORDINAL_WEEK_RE.sub(" ", text)
    text = re.sub(r"(?<!\d)\d{1,2}\s*日", " ", text)
    text = re.sub(r"第[一二三四1234]季度", " ", text)
    text = re.sub(r"20\d{2}\s*年", " ", text)
    text = re.sub(r"(?<!\d)20\d{4}", " ", text)
    text = re.sub(r"(?<!\d)\d{1,2}\s*月", " ", text)
    text = re.sub(r"上半年|下半年|全年", " ", text)
    text = re.sub(r"单月|双月|奇数月|偶数月", " ", text)
    for token in sorted(RELATIVE_PERIODS, key=len, reverse=True):
        text = text.replace(token, " ")
    return re.sub(r"[春夏秋冬]季?", " ", text)


def _find_station(q: str) -> str | None:
    # 先剥离时间词，避免贪婪匹配吞进“年/季度/日”
    t = _strip_time_words(q)
    # 数据集中文名/别名不是站名的一部分：「2025年业务域乙示例定位单元丙」按 2-12 字
    # 贪婪窗口会取出"业务域乙示例定位单元丙"，这个取值在事实表里必然不存在。
    for label in sorted(dataset_labels(), key=len, reverse=True):
        if len(label) >= 2:
            t = t.replace(label, " ")
    m = re.search(r"(示例定位单元[甲乙丙丁戊己庚辛壬癸]|[\u4e00-\u9fa5A-Za-z0-9]{2,12}站)", t)
    if m and not any(bad in m.group(1)
                     for bad in ("组织层级一", "各", "季度", "年")):
        return m.group(1)
    return None


_DAILY_AVG_WORDS = ("日均", "日平均", "每天销")
_PROGRESS_GAP_WORDS = ("欠进度", "进度差", "落后计划")
_PLAN_RATIO_WORDS = ("完成率", "时间进度")
_COMPARISON_WORDS = ("同比", "环比")


def _derived_metrics(q: str) -> list[str]:
    """日均业务数量 / 欠进度：只识别要哪个派生指标，数值一律由引擎算。"""
    derived: list[str] = []
    if any(word in q for word in _DAILY_AVG_WORDS):
        derived.append("daily_avg")
    if any(word in q for word in _PROGRESS_GAP_WORDS):
        derived.append("progress_gap")
    return derived


def _is_comparison_or_grouped(q: str) -> bool:
    """提问是否要逐项对比（同环比或分组）：这类主答案走批量查询，不转派生计算。"""
    return any(token in q for token in _COMPARISON_WORDS) or any(
        prefix in q for prefix in _GROUPING_PREFIXES
    )


_GROUP_TOKEN_RE = re.compile(r"[各每按][\u4e00-\u9fa5]{1,3}")
_SEMANTIC_NOISE_CHARS = re.compile(r"[\s的\-%％。、，,]")


def consumed_semantic_matcher(question: str):
    """返回"这个槽值只是提问里的语义词"判定，供 LLM 槽位合并前剔除。

    规则层已经把派生指标、时间、分组、数据集名这些词各自消费成了语义槽。
    模型再把同一个词当过滤值下发就是纯幻觉（实测把「欠进度」填进 `checkmode`），
    引擎按方案A 会显式拒答"该取值不存在"，于是把一条本来答得对的进度问题
    退化成层级清单。剔除后 Python 的槽位与 Harness 的规则槽位重新一致。
    """
    exact = {token for token in RELATIVE_PERIODS if token in question}
    exact |= {token for token in SEASONS if token in question}
    exact |= {label for label in dataset_labels() if len(label) >= 2 and label in question}
    exact |= set(_GROUP_TOKEN_RE.findall(question))
    for token in ("上半年", "下半年", "第1季度", "导入", "上传", "重新导入"):
        if token in question:
            exact.add(token)
    contains = [word for group in (_DAILY_AVG_WORDS, _PROGRESS_GAP_WORDS,
                                   _PLAN_RATIO_WORDS, _COMPARISON_WORDS)
                for word in group if word in question]

    def matches(value: Any) -> bool:
        texts = value if isinstance(value, (list, tuple)) else [value]
        for text in texts:
            normalized = _SEMANTIC_NOISE_CHARS.sub("", str(text or ""))
            if not normalized:
                continue
            if normalized in exact:
                return True
            if any(word in normalized for word in contains):
                return True
        return False

    return matches


def extract_slots(q: str) -> dict[str, Any]:
    # ---- 特殊意图 ----
    if any(k in q for k in ["导入", "上传", "重新导入"]):
        return {"intent": "data_import", "dataset": "oil_monthly_sales",
                "conditions": {}, "filters": {},
                "group_by": None, "detail": False}
    if "规则" in q:
        return {"intent": "rule_config", "dataset": "oil_monthly_sales",
                "conditions": {}, "filters": {},
                "group_by": None, "detail": False}
    derived = _derived_metrics(q)
    # 完成率之外，日均/欠进度同样要跨实际与计划两个数据集，并且必须由
    # 引擎给出期间天数口径，不能留在业务查询里只回合计。
    # 但分组/同环比提问的主答案是逐项对比，派生词只是附带用词，
    # 不能让一次派生计算抢走整条批量查询。
    if "完成率" in q or (derived and not _is_comparison_or_grouped(q)):
        years, months, season, relative_period, day_span = _parse_time(q)
        actual_dataset = _actual_side(q)
        conds: dict[str, Any] = {}
        period_defaulted = False
        if months:
            conds["month"] = _one_or_list(months)
        elif day_span:
            if actual_dataset in day_grained_datasets():
                # 点名的实际表有真日值：区间直接按日取数。折算成整月会让
                # 分子变成全月、分母仍是区间天数，日均被摊大。
                conds["date_from"], conds["date_to"] = day_span
            else:
                # 计划数据只有月粒度：显式日期折算为区间覆盖的月份，不做日级近似
                conds["month"] = _one_or_list(_months_in_span(day_span))
        elif years:
            conds["year"] = _one_or_list(years)
        if season:
            conds["season"] = season
        month_parity = (
            "单月" if "单月" in q or "奇数月" in q
            else "双月" if "双月" in q or "偶数月" in q else None
        )
        if month_parity:
            conds["month_parity"] = month_parity
        if relative_period:
            conds["relative_period"] = relative_period
        if not conds:
            # 日均/欠进度必须有期间才能摊天数：提问没带时间时按当前自然月，
            # 期间换算与天数仍由引擎时间基座给出。默认口径要在答案里写明，
            # 所以额外打一个标记，不把"没问时间"当成"问了本月"。
            conds["relative_period"] = "current_month"
            period_defaulted = True
        base_product = _base_product_filter(q)
        base_filters: dict[str, Any] = {"product": base_product} if base_product else {}
        location = _extract_location(q)
        if location:
            base_filters["organization"] = location
        return {"intent": "plan_analysis", "dataset": PLAN_DATASET,
                "actual_dataset": actual_dataset,
                "period_defaulted": period_defaulted,
                "conditions": conds, "filters": {},
                "base_filters": base_filters,
                "group_by": None, "detail": False,
                "derived_metrics": derived}

    is_plan = "计划" in q

    # ---- 时间 ----
    years, months, season, relative_period, day_span = _parse_time(q)

    conditions: dict[str, Any] = {}
    filters: dict[str, Any] = {}
    if day_span:
        conditions["date_from"], conditions["date_to"] = day_span
        filters["date_from"], filters["date_to"] = day_span
    elif months:
        val = _one_or_list(months)
        conditions["month"] = val
        filters["yyyymm"] = val
    elif years:
        val = _one_or_list(years)
        conditions["year"] = val
        filters["year"] = val
    if season:
        conditions["season"] = season
        filters["season"] = season
    month_parity = (
        "单月" if "单月" in q or "奇数月" in q
        else "双月" if "双月" in q or "偶数月" in q else None
    )
    if month_parity:
        conditions["month_parity"] = month_parity
        filters["month_parity"] = month_parity
    if relative_period:
        conditions["relative_period"] = relative_period
        filters["relative_period"] = relative_period

    # ---- 组织层级一 ----
    for area in BIG_AREAS:
        if area in q:
            conditions["big_area"] = area
            filters["big_area"] = area
            break

    # ---- 组织层级二（branch）：按名单精确匹配 ----
    # 特殊 BranchName（示例组织甲/示例组织乙示例行业组织层级一等）不做硬映射，由 LLM 解析
    for branch in BRANCHES:
        if branch in q:
            conditions["branch"] = branch
            filters["branch"] = branch
            break

    # ---- 产品 ----
    # 数字编码一律从剥离时间字面量后的文本里取："2026-04-01"里的"0"
    # 曾被当成 0 号类别乙类别代码，把整条查询过滤成空结果。
    time_free = _strip_time_words(q)
    gas_codes = re.findall(r"(?<!\d)(60\d{6}|61\d{6}|90\d{6})(?!\d)", time_free)
    base_filters: dict[str, Any] = {}
    station = _find_station(q)
    # 已被组织层级槽消费的名称不能再拆一半当基座地点候选："示例区域甲组织层级一"里的"示例区域甲"
    # 会让基座翻译失败并阻断整条查询（X17 的同类残留）。定位单元名不在此列，
    # 它同时是基座声明过的取值（见语义探针 non_oil_sales/示例定位单元丙 命中）。
    consumed = [
        str(value) for value in (conditions.get("big_area"), conditions.get("branch"))
        if value
    ]
    location = _extract_location(q)
    if location and any(location in value for value in consumed):
        location = None
    if location:
        base_filters["organization"] = location
    if gas_codes:
        val = _one_or_list(gas_codes)
        conditions["gas_code"] = val
        filters["gas_code"] = val
    else:
        # 类别代码只交给通用基座：主数据的物料编码与数据集产品编码层按真实取值相交才落层。
        # 自己下发 grade="92" 会绕过主数据，又在没有"类别代码"层级的数据集上整条拒答。
        if _fuel_grade_terms(q):
            base_filters["product"] = _base_product_filter(q)
        else:
            mentioned_categories = [name for name in _CATEGORY_BY_FUEL if name in q]
            category = _CATEGORY_BY_FUEL[mentioned_categories[0]] if len(mentioned_categories) == 1 else None
            if category:
                filters["category"] = category
                base_filters["product"] = next(
                    name for name, code in _CATEGORY_BY_FUEL.items() if code == category
                )
            elif len(mentioned_categories) > 1:
                # 多品类并列既是输出分组，也是合计范围。保留全部用户原语，
                # 由基座统一翻译成 category 列表；未过滤全品类不能冒充合计。
                base_filters["product"] = mentioned_categories

    # ---- 支付方式 ----
    paid_codes = re.findall(r"(?<!\d)(012\d{3})(?!\d)", time_free)
    if paid_codes:
        conditions["check_mode"] = _one_or_list(paid_codes)
        filters["check_mode"] = _one_or_list(paid_codes)
    else:
        # 说法原样下传，由引擎按该数据集的真实取值解析；解析不到即显式报错
        phrase = _payment_phrase(time_free)
        if phrase:
            conditions["check_mode"] = phrase
            filters["check_mode"] = phrase

    # ---- 定位单元名 ----
    # 定位单元只填语义槽 station，由引擎按数据集声明的服务单元层级归位（X29）。
    # 直接写物理列 store_name 会和基座翻译出的同名列冲突，冲突条件被跳过
    # 过滤后整条查询静默扩成全量（比拒答更危险）。
    if station:
        conditions["station"] = station
        filters["station"] = station

    # ---- 分组 ----
    group_by = None
    group_rules = [
        ("big_area", ["各组织层级一", "组织层级一对比", "组织层级一业务"]),
        ("branch", ["各组织层级二", "组织层级二对比", "各组织层级二"]),
        ("area", ["各组织层级三", "组织层级三对比"]),
        ("month", ["各月", "每月", "月度", "各月业务"]),
        ("gas_code", ["各产品", "每种产品"]),
        ("station", ["各站", "定位单元排名"]),
    ]
    for key, words in group_rules:
        if any(w in q for w in words):
            group_by = key
            break
    # “类别乙、类别甲、合计”按大类输出；基座已把合计范围限定为这两个品类。
    if not group_by and "类别乙" in q and "类别甲" in q:
        group_by = "category"

    # ---- 明细 ----
    detail = any(
        w in q for w in ["明细", "单据", "记录", "有哪些", "业务情况"]
    )
    # 含具体定位单元名时按明细呈现
    if station:
        detail = True
        if not group_by and "各产品" in q:
            group_by = "gas_code"

    wants_actual = any(word in q for word in ("业务", "业务数量", "业务数量", "业务金额", "业务金额", "记录"))
    wants_plan = any(word in q for word in ("计划", "欠进度", "日均", "完成率", "计划值"))
    explicit_dataset = _explicit_dataset(q, is_plan)
    # “业务数据甲数量 + 计划/欠进度”是实际与计划双数据集问题；只有用户
    # 明确点名“业务域甲计划”时，才把查询收窄为单一计划数据集。
    if wants_actual and wants_plan and not any(token in q for token in ("业务域甲计划", "定位单元业务域甲计划", "业务计划表")):
        explicit_dataset = None
    dataset = explicit_dataset or match_dataset(q, is_plan and not (wants_actual and wants_plan))
    comparisons = []
    if "同比" in q:
        comparisons.append("yoy")
    if "环比" in q:
        comparisons.append("mom")
    if explicit_dataset:
        dataset_scope = "single"
    elif wants_actual and wants_plan:
        dataset_scope = "explicit_multi"
    elif base_filters.get("product"):
        dataset_scope = "single"
    elif is_plan:
        dataset_scope = "plan"
    else:
        dataset_scope = "all_actual"
    datasets: list[str] = []
    if dataset_scope == "single":
        datasets = [dataset]
    elif dataset_scope == "explicit_multi":
        datasets = [dataset, "oil_sales_plan"]
    # 点名了具体数据集、又要求日精度（显式日期或"上周"）时，只查月粒度
    # 数据集必然只能得到"粒度不足"。按引擎声明的时间格式并入同一业务域里
    # 真正能按日计算的事实数据集（如「业务域甲月报」→「业务域甲日报」），
    # 月粒度那一份仍会如实报告缺口，不隐藏能力差异、也不跨业务域污染合计。
    if (day_span is not None or relative_period) and datasets and wants_actual:
        anchor = dataset_label(datasets[0])
        extra = [
            dataset for dataset, label in day_grained_datasets().items()
            if dataset not in datasets and same_business_domain(anchor, label)
        ]
        if extra:
            datasets += extra
            dataset_scope = "explicit_multi"
    # 一个可查锚点都没有（乱码、寒暄、问天气）⇒ 交给编排层反问，而不是
    # 替用户猜一个"全量概览"。`base_filters.organization` 是地点**候选**（未经
    # 引擎确认真伪），所以它不算锚点：「哈哈哈」也会抽出 organization=哈哈哈，
    # 把它当条件就等于把噪声答成结果。判定只看规则层，模型不参与，
    # 两套 Agent 必然同串。
    other_base = {key: value for key, value in base_filters.items() if key != "organization"}
    unresolved = not (
        conditions or filters or other_base or group_by or comparisons
        or derived or detail or wants_actual or wants_plan
        or _best_label_match(q) or _strip_time_words(q) != q
    )
    return {
        "intent": "sales_query",
        "dataset": dataset,
        "datasets": datasets,
        "dataset_scope": dataset_scope,
        "conditions": conditions,
        "filters": filters,
        "base_filters": base_filters,
        "group_by": group_by,
        "detail": detail,
        "comparisons": comparisons,
        "derived_metrics": derived,
        "unresolved": unresolved,
    }

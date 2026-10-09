# -*- coding: utf-8 -*-
"""综合验收：行数 / Decimal 真值 / 阈值修剪不变量 / 事实表聚合兜底 / 系统参数 / 性能 / 验收问题 / 异常输入。

数据集全部走注册表驱动的动态事实表（oil_monthly_sales / oil_sales_plan / non_oil_sales），
不再依赖任何内置 ORM 表，新增数据集会自动纳入行数核对。
派生指标（日均/完成率/欠进度）的分母天数与除法全部在引擎侧用 Decimal 完成，
验收用 SQL 合计复算比对，Agent 只排版。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ENGINE = "http://127.0.0.1:8000"
AGENT = "http://127.0.0.1:8001"
TOKEN = "dev-service-token-please-rotate"

# 对账基线：来自源文件全量导入后的真实值（改动声明后必须分毫不差）
BASELINE = {
    "oil_monthly_sales": {
        "rows": 153183,
        "root": {"monthlyamount": "35735013808.89", "monthlyqty": "4338912485.49",
                 "monthlysales": "3413020.40145"},
    },
    "oil_sales_plan": {"rows": 109121, "root": {"monsalesplan": "12979268.22"}},
    "non_oil_sales": {"rows": 21103, "root": {"total_amount": "13390989.24"}},
}

results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("✔" if ok else "✘"), name, "|", detail)


def http_json(url, body=None, token=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Service-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def engine_call(path, body):
    for i in range(6):
        try:
            return http_json(ENGINE + path, body, token=TOKEN)
        except OSError:
            time.sleep(1)


def admin_token():
    return http_json(ENGINE + "/api/v1/auth/login",
                     {"username": "admin", "password": "admin123"})["access_token"]


def ask(question):
    data = json.dumps({"message": question}).encode()
    req = urllib.request.Request(
        AGENT + "/api/v1/chat", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def agg(dataset, conditions=None, group_by=None):
    body = {"dataset": dataset, "conditions": conditions or {}}
    if group_by:
        body["group_by"] = group_by
    return engine_call("/api/v1/engine/aggregations/query", body)


# ============ 1. 行数与 root 真值 ============
for ds, want in BASELINE.items():
    res = agg(ds)["nodes"][0]
    check(f"{ds} root 行数={want['rows']}", res["record_count"] == want["rows"],
          str(res["record_count"]))
    for metric, value in want["root"].items():
        got = res["aggregations"].get(metric)
        check(f"{ds} root {metric}={value}", got == value, str(got))

y2024 = agg("oil_monthly_sales", {"year": "2024"})["nodes"][0]
check("oil_monthly_sales 2024 金额=14138252049.15",
      y2024["aggregations"]["monthlyamount"] == "14138252049.15",
      y2024["aggregations"]["monthlyamount"])

# ============ 2. 阈值修剪不变量 ============
from sqlalchemy import text

from engine.storage.database import session_scope
from engine.storage.spiral_store import SpiralStore

params = http_json(ENGINE + "/api/v1/engine/system-params", token=TOKEN)
threshold = [p for p in params["items"] if p["key"] == "growth_threshold"][0]["value"]
check(
    "系统参数 growth_threshold 生效",
    isinstance(threshold, int) and 100 <= threshold <= 1000000,
    str(threshold),
)

for ds in BASELINE:
    with session_scope() as s:
        rows = s.execute(text(
            "select node_id, level, parent_id, record_count from spiral_nodes"
            " where dataset=:d"
        ), {"d": ds}).all()
    counts = {node_id: int(rc or 0) for node_id, _lvl, _p, rc in rows}
    orphan = sum(1 for _n, lvl, parent, _rc in rows
                 if lvl > 0 and parent not in counts)
    violation = sum(1 for _n, lvl, parent, _rc in rows
                    if lvl > 0 and parent in counts and counts[parent] < threshold)
    check(f"{ds} 修剪后无孤儿节点", orphan == 0,
          f"nodes={len(rows)} orphan={orphan}")
    check(f"{ds} 修剪后父节点均达阈值({threshold})", violation == 0,
          f"violations={violation}")

# ============ 3. 事实表聚合兜底（分组求和==年聚合） ============
gb = agg("oil_monthly_sales", {"year": "2024"}, "bigareacode")
check("组织层级一分组走事实表兜底", gb.get("fallback") == "fact_aggregate" and gb["nodes"],
      f"nodes={len(gb['nodes'])}")
grouped_total = sum(Decimal(n["aggregations"]["monthlyamount"]) for n in gb["nodes"])
check("组织层级一分组求和==2024 总额",
      format(grouped_total, "f") == y2024["aggregations"]["monthlyamount"],
      f"{grouped_total} vs {y2024['aggregations']['monthlyamount']}")

# ============ 4. 周维度派生 ============
from datetime import date

from engine.derivation.time_derivation_engine import TimeDerivationEngine

derived = TimeDerivationEngine().derive_date(date(2026, 9, 23))
check("日粒度可派生 ISO 周", derived.get("week") == "2026W39", str(derived.get("week")))
oil_levels = http_json(ENGINE + "/api/v1/engine/datasets/oil_monthly_sales", token=TOKEN)
check("oil_monthly_sales 层级链含周", "week" in oil_levels["levels"], str(oil_levels["levels"]))

# ============ 5. 系统参数热更新 ============
tk = admin_token()
probe_threshold = 9000 if threshold != 9000 else 9100
put_req = urllib.request.Request(
    ENGINE + "/api/v1/engine/system-params",
    data=json.dumps({"params": {"growth_threshold": probe_threshold}}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tk},
    method="PUT",
)
updated = json.loads(urllib.request.urlopen(put_req, timeout=60).read())
check("参数热更新生效", updated["params"]["growth_threshold"] == probe_threshold,
      str(updated["params"]))
rollback = json.loads(urllib.request.urlopen(urllib.request.Request(
    ENGINE + "/api/v1/engine/system-params",
    data=json.dumps({"params": {"growth_threshold": threshold}}).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + tk},
    method="PUT",
), timeout=60).read())
check(f"参数回滚到原值 {threshold}", rollback["params"]["growth_threshold"] == threshold,
      str(rollback["params"]))

# ============ 6. 性能 ============
t0 = time.time()
agg("oil_monthly_sales", {"year": "2024"})
check("聚合<100ms", (time.time() - t0) * 1000 < 100,
      "%.0fms" % ((time.time() - t0) * 1000))

t0 = time.time()
engine_call("/api/v1/engine/records/query",
            {"dataset": "oil_monthly_sales", "filters": {"yyyymm": "202401"},
             "page": 1, "page_size": 20})
check("明细<500ms", (time.time() - t0) * 1000 < 500,
      "%.0fms" % ((time.time() - t0) * 1000))

# ============ 7. 验收问题 ============
QUESTIONS = [
    "2024年各组织层级一业务数据甲金额是多少",
    "2026年上周各组织层级一的业务数据甲数量是多少，按照类别乙、类别甲、合计等维度在加上环比（上周）、同比（去年相同时间）、计划、日均业务数量、欠进度",
    "2025年全年业务金额多少？",
    "2024年和2025年各组织层级一业务金额对比",
    "2026年上半年各月业务金额汇总",
    "2024年1月示例区域甲组织层级一用移动支付的业务记录有哪些？",
    "2023年类别甲业务计划总量（吨）是多少？",
]
for q in QUESTIONS:
    try:
        r = ask(q)
        ans = r["answer"].replace("\n", " ")[:160]
        print("·", q)
        print("  →", ans)
    except Exception as e:
        print("·", q, "调用异常", e)

# ============ 9. 事实兜底 route 形状 / 明细口径 / 语义取值探针 ============
ROUTE_VIEW_KEYS = {"pruned_node_count", "pruned_record_count", "collapsed_messages"}
# 点击示例问题的白屏用例：前端 RouteVisualizer 直接读这些字段，缺一个就抛错
check("分组事实兜底 route 含前端可视化字段",
      ROUTE_VIEW_KEYS <= set(gb.get("route") or {}),
      str(sorted(set(gb.get("route") or {}) & ROUTE_VIEW_KEYS)))

detail = engine_call("/api/v1/engine/query", {
    "dataset": "oil_monthly_sales", "datasets": ["oil_monthly_sales"],
    "filters": {"year": "2024", "month": "202403"},
    "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
    "detail": True,
})["actual"]
with session_scope() as s:
    true_rows = s.execute(text(
        "select count(*) from fact_oil_monthly_sales where yyyymm='202403'")).scalar()
    true_amount = s.execute(text(
        "select sum(monthlyamount) from fact_oil_monthly_sales where yyyymm='202403'"
    )).scalar()
check("明细总量按条件口径（不是折叠节点求和）",
      detail["record_count"] == true_rows,
      f"api={detail['record_count']} fact={true_rows}")
check("明细金额==事实表真值",
      Decimal(detail["aggregations"]["monthlyamount"]) == Decimal(str(true_amount)),
      f"api={detail['aggregations']['monthlyamount']} fact={true_amount}")
check("明细 route 同样字段完整",
      ROUTE_VIEW_KEYS <= set(detail.get("route") or {}),
      str(sorted(ROUTE_VIEW_KEYS - set(detail.get("route") or {}))))

for term, dataset, want in [("各月业务数量", "oil_monthly_sales", False), ("示例定位单元丙", "non_oil_sales", True)]:
    probe = http_json(
        ENGINE + "/api/v1/engine/datasets/%s/semantic-probe?term=%s"
        % (dataset, urllib.parse.quote(term)), token=TOKEN
    )
    check(f"语义探针 {dataset}/{term} matched={want}",
          probe["matched"] is want, json.dumps(probe["hits"], ensure_ascii=False)[:120])

# ============ 10. 点击示例问题的两条端到端用例 ============
try:
    clicked = ask("2024年总业务金额是多少？")
    check("示例问题「2024年总业务金额」返回引擎真值",
          "14138252049.15" in clicked["answer"], clicked["answer"][:80])
except Exception as e:
    check("示例问题「2024年总业务金额」返回引擎真值", False, str(e))
try:
    grouped = ask("2024年各月业务数量如何？")
    check("分组提问用词不被当成地点（不阻断、不谎称无数据）",
          "已阻断查询" not in grouped["answer"]
          and "无法翻译" not in grouped["answer"],
          grouped["answer"][:80])
except Exception as e:
    check("分组提问用词不被当成地点（不阻断、不谎称无数据）", False, str(e))

# ============ 11. 日粒度数据集 oil_sales_daily（yyyy-mm-dd） ============
DAILY = "oil_sales_daily"
daily_contract = http_json(ENGINE + "/api/v1/engine/datasets/%s/contract" % DAILY, token=TOKEN)
daily_levels = [item["name"] for item in daily_contract["levels"]]
check("日粒度数据集声明为 yyyy-mm-dd",
      (daily_contract.get("time") or {}).get("format") == "yyyy-mm-dd",
      str(daily_contract.get("time")))
check("日粒度层级链含周与日", {"week", "day"} <= set(daily_levels), str(daily_levels))

with session_scope() as s:
    day_rows = s.execute(text(
        "select count(*) from fact_oil_sales_daily where __time='2026-04-01'")).scalar()
    day_amount = s.execute(text(
        "select sum(amount) from fact_oil_sales_daily where __time='2026-04-01'")).scalar()
    week_rows = s.execute(text(
        "select count(*) from fact_oil_sales_daily where week='2026W14'")).scalar()
    week_amount = s.execute(text(
        "select sum(amount) from fact_oil_sales_daily where week='2026W14'")).scalar()
    empty_day = s.execute(text(
        "select count(*) from fact_oil_sales_daily where week='' or week is null")).scalar()
check("日粒度派生的周/日层级全量落库", empty_day == 0, "空周行数=%s" % empty_day)


def day_query(filters):
    resp = engine_call("/api/v1/engine/query", {
        "dataset": DAILY, "datasets": [DAILY], "filters": filters,
        "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
    })
    return resp["actual"], list(resp.get("warnings") or (resp["actual"].get("warnings") or []))


one_day, one_day_warnings = day_query({"date_from": "2026-04-01", "date_to": "2026-04-01"})
check("某一天按日范围查询==事实真值",
      one_day["record_count"] == day_rows
      and Decimal(str(one_day["aggregations"]["amount"])) == Decimal(str(day_amount)),
      "api=%s/%s fact=%s/%s" % (one_day["record_count"], one_day["aggregations"].get("amount"),
                                day_rows, day_amount))
check("日粒度日范围查询不再被判为月粒度",
      not any("月粒度" in str(w) for w in one_day_warnings), str(one_day_warnings)[:120])

one_week, _ = day_query({"year": "2026", "week": "2026W14"})
check("某一周按 ISO 周层级查询==事实真值",
      one_week["record_count"] == week_rows
      and Decimal(str(one_week["aggregations"]["amount"])) == Decimal(str(week_amount)),
      "api=%s/%s fact=%s/%s" % (one_week["record_count"], one_week["aggregations"].get("amount"),
                               week_rows, week_amount))

try:
    day_text = "%s" % Decimal(str(day_amount)).quantize(Decimal("0.01"))
    day_comma = "{:,.2f}".format(Decimal(str(day_amount)))
    daily_answer = ask("2026年4月1日业务数据甲金额是多少？")
    check("对话可以精确回答某一天的产品金额",
          day_text in daily_answer["answer"] or day_comma in daily_answer["answer"],
          "want=%s|%s ans=%s" % (day_text, day_comma, daily_answer["answer"][:120]))
except Exception as e:
    check("对话可以精确回答某一天的产品金额", False, str(e))

with session_scope() as s:
    mw_rows = s.execute(text(
        "select count(*) from fact_oil_sales_daily"
        " where __time>='2026-04-13' and __time<='2026-04-19'")).scalar()
    mw_amount = s.execute(text(
        "select sum(amount) from fact_oil_sales_daily"
        " where __time>='2026-04-13' and __time<='2026-04-19'")).scalar()
# "4月第三周"由引擎按周一起算编译成日期区间：4 月 1 日是周三，
# 第 1 周是 4/1~4/5 的残周，所以第 3 周是 4/13~4/19。
month_week_resp = engine_call("/api/v1/engine/query", {
    "dataset": DAILY, "datasets": [DAILY],
    "filters": {"relative_period": "month_week:202604:3"},
    "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
})
month_week = month_week_resp["actual"]
day_query_plan = month_week_resp.get("query_plan") or {}
check("月内序数周编译为周一起算区间==事实真值",
      month_week["record_count"] == mw_rows
      and Decimal(str(month_week["aggregations"]["amount"])) == Decimal(str(mw_amount)),
      "api=%s/%s fact=%s/%s" % (month_week["record_count"],
                                month_week["aggregations"].get("amount"),
                                mw_rows, mw_amount))
check("月内序数周口径回写审计标签",
      ((day_query_plan.get("resolved_period") or {}).get("label")) == "2026年4月第3周",
      str(day_query_plan.get("resolved_period")))
try:
    mw_text = "%s" % Decimal(str(mw_amount)).quantize(Decimal("0.01"))
    mw_comma = "{:,.2f}".format(Decimal(str(mw_amount)))
    mw_answer = ask("2026年4月第三周业务数据甲金额是多少？")
    check("对话可以回答某月第N周的产品金额",
          (mw_text in mw_answer["answer"] or mw_comma in mw_answer["answer"])
          and "第3周" in mw_answer["answer"],
          "want=%s|%s ans=%s" % (mw_text, mw_comma, mw_answer["answer"][:160]))
except Exception as e:
    check("对话可以回答某月第N周的产品金额", False, str(e))

# ============ 11.5 相对年份语义槽（今年/去年/明年）============
# 相对年份编译成 year 层级条件而不是日期区间：月粒度数据集只声明 yyyymm
# 时间字段，日期区间会被判为精度不足整条拒答；没有年份槽时则会静默答成全量。
with session_scope() as s:
    prev_year_rows, prev_year_amount = s.execute(text(
        "select count(*), sum(monthlyamount) from fact_oil_monthly_sales "
        "where year='2025'")).one()
    prev_aug_rows, prev_aug_amount = s.execute(text(
        "select count(*), sum(monthlyamount) from fact_oil_monthly_sales "
        "where yyyymm='202508'")).one()

rel_year = engine_call("/api/v1/engine/query", {
    "dataset": "oil_monthly_sales", "datasets": ["oil_monthly_sales"],
    "conditions": {"relative_period": "previous_year"},
    "filters": {"relative_period": "previous_year"},
    "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
})
rel_actual = rel_year["actual"]
rel_audit = (rel_year.get("query_plan") or {}).get("time_foundation") or {}
check("「去年」编译成 year 层级条件取数==事实真值",
      rel_actual["record_count"] == prev_year_rows
      and Decimal(str(rel_actual["aggregations"]["monthlyamount"]))
      == Decimal(str(prev_year_amount))
      and (rel_audit.get("resolved_year") or {}).get("label") == "去年"
      and "date_from" not in (rel_audit.get("normalized_conditions") or {}),
      "api=%s/%s fact=%s/%s audit=%s" % (
          rel_actual["record_count"], rel_actual["aggregations"].get("monthlyamount"),
          prev_year_rows, prev_year_amount, rel_audit.get("resolved_year")))

bare_month = engine_call("/api/v1/engine/query", {
    "dataset": "oil_monthly_sales", "datasets": ["oil_monthly_sales"],
    "conditions": {"relative_period": "previous_year", "month": "8"},
    "filters": {}, "base_filters": {}, "group_by": [], "metrics": [],
    "comparisons": [],
})
bm_actual = bare_month["actual"]
check("「去年8月」的裸月份由引擎配成 202508（Agent 不自派年份）",
      bm_actual["record_count"] == prev_aug_rows
      and Decimal(str(bm_actual["aggregations"]["monthlyamount"]))
      == Decimal(str(prev_aug_amount)),
      "api=%s/%s fact=%s/%s" % (bm_actual["record_count"],
                                bm_actual["aggregations"].get("monthlyamount"),
                                prev_aug_rows, prev_aug_amount))

try:
    rel_answer = ask("去年业务数据甲金额是多少？")
    rel_text = str(Decimal(str(prev_year_amount)).quantize(Decimal("0.01")))
    rel_comma = "{:,.2f}".format(Decimal(str(prev_year_amount)))
    check("对话把「去年」答成去年期间并把年份口径写进答案",
          ("去年" in rel_answer["answer"] or "2025" in rel_answer["answer"])
          and (rel_text in rel_answer["answer"] or rel_comma in rel_answer["answer"]),
          "want=%s ans=%s" % (rel_text, rel_answer["answer"][:200]))
except Exception as e:
    check("对话把「去年」答成去年期间并把年份口径写进答案", False, str(e))

# ============ 12. 基座语义声明与局部判定 ============
ALL_DS = list(BASELINE.keys()) + [DAILY]
with session_scope() as s:
    hk_rows = s.execute(text(
        "select count(*), sum(monthlyamount) from fact_oil_monthly_sales "
        "where year='2024' and bigareacode='100047'")).one()
    store_rows = s.execute(text(
        "select count(*), sum(monthlyamount) from fact_oil_monthly_sales "
        "where year='2024' and oucode='100052001001007'")).one()


def base_query(dataset, term, year="2024"):
    return engine_call("/api/v1/engine/query", {
        "dataset": dataset, "datasets": [],
        "conditions": {"year": year}, "filters": {"year": year},
        "base_filters": {"organization": term},
        "group_by": [], "metrics": [], "comparisons": [],
    })["actual"]


registry_caps = {
    item["id"]: (item.get("capabilities") or {})
    for item in http_json(ENGINE + "/api/v1/engine/datasets", None, TOKEN)["items"]
}
check("产品数据集已声明基座语义特征（元数据驱动，不再缺声明）",
      all((registry_caps.get(ds) or {}).get("feature_layers") for ds in
          ["oil_monthly_sales", "oil_sales_plan", "oil_sales_daily"]),
      str({ds: bool((registry_caps.get(ds) or {}).get("feature_layers"))
           for ds in ["oil_monthly_sales", "oil_sales_plan", "oil_sales_daily"]}))
hk_actual = base_query("oil_monthly_sales", "示例区域甲组织层级一")
check("组织层级一名称经基座声明翻译成层级条件后真过滤==SQL 真值",
      hk_actual.get("record_count") == hk_rows[0]
      and Decimal(str(hk_actual["aggregations"]["monthlyamount"])) == Decimal(str(hk_rows[1])),
      "api=%s|%s fact=%s|%s" % (hk_actual.get("record_count"),
                                 hk_actual["aggregations"]["monthlyamount"],
                                 hk_rows[0], hk_rows[1]))
store_actual = base_query("oil_monthly_sales", "示例定位单元丙")
check("定位单元名称经函数依赖配对到定位单元层后真过滤==SQL 真值",
      store_actual.get("record_count") == store_rows[0]
      and Decimal(str(store_actual["aggregations"]["monthlyamount"])) == Decimal(str(store_rows[1])),
      "api=%s|%s fact=%s|%s" % (store_actual.get("record_count"),
                                 store_actual["aggregations"]["monthlyamount"],
                                 store_rows[0], store_rows[1]))
plan_actual = base_query("oil_sales_plan", "示例组织乙")
check("有声明但取值对不上时报「没有匹配的值」而不是「没有基座声明」",
      plan_actual.get("unsupported_conditions") is True
      and "匹配的值" in (plan_actual.get("unsupported_message") or "")
      and "没有可承接" not in (plan_actual.get("unsupported_message") or ""),
      str(plan_actual.get("unsupported_message"))[:120])

# 编码前缀桥：计划数据集只有变长组织编码（组织层级一名不在本列取值里），
# 组织名要能经其它数据集的"名称→编码"落到本列真实节点，而不是报"取值不存在"。
with session_scope() as s:
    plan_hk_rows = s.execute(text(
        "select count(*), sum(monsalesplan) from fact_oil_sales_plan "
        "where year='2024' and substr(unitcode,1,6)='100047'")).one()
plan_bridge = base_query("oil_sales_plan", "示例区域甲组织层级一")
check("组织层级一名在只有组织编码的计划数据集上经编码前缀桥落到本列真实节点==SQL 真值",
      plan_bridge.get("unsupported_conditions") is not True
      and plan_bridge.get("record_count") == plan_hk_rows[0]
      and Decimal(str(plan_bridge["aggregations"]["monsalesplan"])) == Decimal(str(plan_hk_rows[1])),
      "api=%s|%s fact=%s|%s" % (plan_bridge.get("record_count"),
                                 plan_bridge.get("aggregations", {}).get("monsalesplan"),
                                 plan_hk_rows[0], plan_hk_rows[1]))

base_split = engine_call("/api/v1/engine/query", {
    "dataset": ALL_DS[0], "datasets": ALL_DS,
    "conditions": {"year": "2024"}, "filters": {"year": "2024"},
    "base_filters": {"organization": "示例定位单元丙"},
    "group_by": [], "metrics": [], "comparisons": [],
})
split_totals = base_split["actual"]["totals"]
check("基座判定按数据集局部生效：确认不了的条件不混进无命中清单",
      "oil_monthly_sales" not in (split_totals.get("datasets_unsupported") or [])
      and "oil_sales_plan" in (split_totals.get("datasets_unsupported") or [])
      and "non_oil_sales" in (split_totals.get("datasets_without_data") or []),
      "unsupported=%s without=%s" % (split_totals.get("datasets_unsupported"),
                                     split_totals.get("datasets_without_data")))

# ============ 12.5 组织语义槽按基座声明落位（英文列名数据集） ============
# 业务域乙的 102 层 label 就是英文列名，`station` 这类调用方槽名对不上任何层级；
# 落位依据只能是数据集自己声明的 org_name 特征列 + 事实表真实取值。
with session_scope() as s:
    noi_station_rows, noi_station_amount = s.execute(text(
        "select count(*), sum(total_amount) from fact_non_oil_sales "
        "where year='2025' and store_name like '%示例定位单元丙%'")).one()
    noi_year_rows = s.execute(text(
        "select count(*) from fact_non_oil_sales where year='2025'")).scalar()


def slot_query(conditions, base_filters=None):
    return engine_call("/api/v1/engine/query", {
        "dataset": "non_oil_sales", "datasets": [],
        "conditions": conditions, "filters": conditions,
        "base_filters": base_filters or {},
        "group_by": [], "metrics": [], "comparisons": [],
    })


slot_ok = slot_query({"year": "2025", "station": "示例定位单元丙"})
slot_actual = slot_ok["actual"]
slot_resolution = ((slot_ok.get("query_plan") or {}).get("semantic_resolution") or {})
slot_hit = next((item for item in (slot_resolution.get("conditions") or [])
                 if item.get("requested_key") == "station"), {})
check("组织语义槽在英文列名数据集上按基座声明落到真实层级==SQL 真值",
      slot_actual.get("record_count") == noi_station_rows
      and Decimal(str(slot_actual["aggregations"]["total_amount"]))
      == Decimal(str(noi_station_amount))
      and slot_hit.get("level") == "store_name"
      and "示例定位单元丙" in str(slot_hit.get("resolved_values")),
      "api=%s|%s fact=%s|%s via=%s->%s" % (
          slot_actual.get("record_count"), slot_actual["aggregations"].get("total_amount"),
          noi_station_rows, noi_station_amount, slot_hit.get("requested_key"),
          slot_hit.get("level")))
check("落位决策写进查询计划（问的是 station，过滤跑的是 store_name 必须可审计）",
      bool(slot_hit.get("feature_source")) and "station" not in
      (slot_ok["query_plan"].get("compiled_conditions") or {}),
      "compiled=%s feature=%s" % (slot_ok["query_plan"].get("compiled_conditions"),
                                  slot_hit.get("feature_source")))

with_slot_base = slot_query({"year": "2025", "station": "示例定位单元丙"}, {"organization": "示例定位单元丙"})
check("槽名与基座过滤同时给出时合并同值，不因重复条件互相抵消",
      with_slot_base["actual"].get("record_count") == noi_station_rows,
      "api=%s fact=%s" % (with_slot_base["actual"].get("record_count"), noi_station_rows))

# 同键异值过去会被合并成空列表，空列表在 WHERE 构造里被当作"没有条件"跳过，
# 于是 45 行的示例定位单元丙被静默答成全公司 10575 行（比拒答危险），现在必须显式拒答。
slot_conflict = slot_query({"year": "2025", "store_name": "示例区域丙"}, {"organization": "示例定位单元丙"})
check("直接条件与基座条件同键异值时显式拒答（不静默扩成全量）",
      slot_conflict["actual"].get("unsupported_conditions") is True
      and slot_conflict["actual"].get("record_count") == 0
      and "冲突" in "".join(slot_conflict.get("warnings") or [])
      and "示例区域丙" in (slot_conflict["actual"].get("unsupported_message") or "")
      and noi_year_rows > noi_station_rows,
      "rows=%s msg=%s" % (slot_conflict["actual"].get("record_count"),
                          str(slot_conflict["actual"].get("unsupported_message"))[:120]))

slot_bad = slot_query({"year": "2025", "station": "不存在的站名"})
check("有声明但取值查不到时按取值不存在报错（不返回未过滤全量）",
      slot_bad["actual"].get("unsupported_conditions") is True
      and slot_bad["actual"].get("record_count") == 0
      and "不存在的站名" in (slot_bad["actual"].get("unsupported_message") or "")
      and "真实取值中不存在" in (slot_bad["actual"].get("unsupported_message") or "")
      and noi_year_rows > 0,
      "rows=%s msg=%s" % (slot_bad["actual"].get("record_count"),
                          str(slot_bad["actual"].get("unsupported_message"))[:100]))

# ============ 13. 未建层事实列的取值过滤（支付方式） ============
pay_ok = engine_call("/api/v1/engine/query", {
    "dataset": "oil_monthly_sales", "datasets": [],
    "conditions": {"year": "2025", "check_mode": "012007"},
    "filters": {"year": "2025", "check_mode": "012007"},
    "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
})["actual"]
with session_scope() as s:
    pay_rows = s.execute(text(
        "select count(*) from fact_oil_monthly_sales where year='2025' and checkmode='012007'"
    )).scalar()
    pay_amount = s.execute(text(
        "select sum(monthlyamount) from fact_oil_monthly_sales "
        "where year='2025' and checkmode='012007'")).scalar()
check("未建层事实列按真实取值过滤等于 SQL 真值",
      pay_ok.get("record_count") == pay_rows
      and Decimal(str(pay_ok["aggregations"]["monthlyamount"])) == Decimal(str(pay_amount))
      and pay_rows != 0,
      "api=%s|%s fact=%s|%s" % (pay_ok.get("record_count"),
                                pay_ok["aggregations"]["monthlyamount"], pay_rows, pay_amount))
pay_bad = engine_call("/api/v1/engine/query", {
    "dataset": "oil_monthly_sales", "datasets": [],
    "conditions": {"year": "2025", "check_mode": "移动支付"},
    "filters": {"year": "2025", "check_mode": "移动支付"},
    "base_filters": {}, "group_by": [], "metrics": [], "comparisons": [],
})["actual"]
check("取值不存在的支付说法显式报错（不返回未过滤全量）",
      pay_bad.get("unsupported_conditions") is True
      and "checkmode" in (pay_bad.get("unsupported_message") or "")
      and "移动支付" in (pay_bad.get("unsupported_message") or ""),
      str(pay_bad.get("unsupported_message"))[:120])

# ============ 14. 批量路径的分组渲染与查询范围前缀 ============
try:
    grouped_answer = ask("2024年各月业务数量如何？")["answer"]
    check("分组提问在批量路径逐项渲染而不是只回年度合计",
          "共 12 项" in grouped_answer
          and grouped_answer.count("· 2024") == 12
          and "1249681327.81" in grouped_answer,
          "items=%d ans=%s" % (grouped_answer.count("· 2024"), grouped_answer[:60]))
    check("查询范围前缀不逐行重复",
          len([line for line in grouped_answer.splitlines()
               if line.startswith("2024 ")]) <= 1,
          str([line[:24] for line in grouped_answer.splitlines()
               if line.startswith("2024 ")]))
except Exception as e:
    check("分组提问在批量路径逐项渲染而不是只回年度合计", False, str(e))
    check("查询范围前缀不逐行重复", False, str(e))

# ============ 15. 日均 / 完成率 / 欠进度（引擎侧 Decimal 口径） ============
DERIVE = "/api/v1/engine/plan-derivation"


def derive(conditions, base=None, plan_ds=None, actual="oil_monthly_sales",
           plan_metric="monsalesplan", progress_metric="monthlysales"):
    body = {"actual_dataset": actual, "conditions": conditions,
            "base_filters": base or {}}
    if plan_ds:
        body.update({"plan_dataset": plan_ds, "plan_metric": plan_metric,
                     "progress_metric": progress_metric})
    return engine_call(DERIVE, body)


def fact_total(table, column, where):
    with session_scope() as s:
        row = s.execute(text(
            "select count(*), sum(%s) from %s where %s" % (column, table, where)
        )).one()
    return row[0], str(row[1])


def quotient(total_text, days):
    """按引擎口径复算：小数位取来源位数（至少 2 位），半进位。"""
    places = max(2, -Decimal(total_text).as_tuple().exponent)
    return str((Decimal(total_text) / Decimal(days))
               .quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


aug_rows, aug_sql = fact_total("fact_oil_monthly_sales", "monthlysales", "yyyymm='202608'")
d_aug = derive({"month": "202608"}, plan_ds="oil_sales_plan")
d_real = d_aug["actual"]
aug_total = d_real["aggregations"]["monthlysales"]
check("日均等于合计÷该月真实自然日（202608÷31 天）",
      d_aug["period"]["basis_days"] == 31
      and d_real["record_count"] == aug_rows
      and Decimal(aug_total) == Decimal(aug_sql)
      and d_real["daily_average"]["monthlysales"] == quotient(aug_total, 31),
      "days=%s total=%s avg=%s fact=%s" % (d_aug["period"]["basis_days"],
                                           aug_total,
                                           d_real["daily_average"]["monthlysales"],
                                           aug_sql))

feb_rows, feb_sql = fact_total("fact_oil_monthly_sales", "monthlysales", "yyyymm='202602'")
d_feb = derive({"month": "202602"})
feb_total = d_feb["actual"]["aggregations"]["monthlysales"]
check("月粒度日均按真实自然月天数摊（2026年2月用 28 天，不一律 ÷31）",
      d_feb["period"]["basis_days"] == 28
      and Decimal(feb_total) == Decimal(feb_sql)
      and d_feb["actual"]["daily_average"]["monthlysales"] == quotient(feb_total, 28)
      and d_feb["actual"]["daily_average"]["monthlysales"] != quotient(feb_total, 31),
      "days=%s avg=%s ÷31=%s" % (d_feb["period"]["basis_days"],
                                 d_feb["actual"]["daily_average"]["monthlysales"],
                                 quotient(feb_total, 31)))

y_rows, y_sql = fact_total("fact_oil_monthly_sales", "monthlysales", "year='2024'")
d_year = derive({"year": "2024"})
year_total = d_year["actual"]["aggregations"]["monthlysales"]
check("年度日均含闰年天数（2024 按 366 天）",
      d_year["period"]["period_days"] == 366
      and d_year["actual"]["record_count"] == y_rows
      and Decimal(year_total) == Decimal(y_sql)
      and d_year["actual"]["daily_average"]["monthlysales"] == quotient(year_total, 366),
      "days=%s avg=%s" % (d_year["period"]["period_days"],
                          d_year["actual"]["daily_average"]["monthlysales"]))

d_fold = derive({"date_from": "2026-08-01", "date_to": "2026-08-31"})
check("显式整月区间在月粒度按整月折算并标注口径",
      d_fold["precision"] == "month"
      and d_fold["actual"]["months"] == ["202608"]
      and Decimal(d_fold["actual"]["aggregations"]["monthlysales"]) == Decimal(aug_sql)
      and "按整月（202608）" in d_fold["notice"]
      and "31 个自然日" in d_fold["notice"],
      str(d_fold.get("notice"))[:120])

d_part = derive({"date_from": "2026-08-01", "date_to": "2026-08-15"})
check("非整月区间在月粒度显式不可用（不用整月分子摊半月分母）",
      d_part.get("unsupported") is True and "不是完整自然月" in d_part.get("message", ""),
      str(d_part.get("message"))[:120])

d_season = derive({"season": "春季"})
check("季节口径无法确定自然日区间时显式不可用",
      d_season.get("unsupported") is True
      and "先确定自然日区间" in d_season.get("message", ""),
      str(d_season.get("message"))[:100])

day_rows, day_sql = fact_total(
    "fact_oil_sales_daily", "salesvol", "day between '2026-04-01' and '2026-04-07'")
d_day = derive({"date_from": "2026-04-01", "date_to": "2026-04-07"},
               actual="oil_sales_daily")
day_total = d_day["actual"]["aggregations"]["salesvol"]
check("日粒度数据集按日取数（7 天，日均等于 SQL 复算）",
      d_day["precision"] == "day" and d_day["period"]["basis_days"] == 7
      and d_day["actual"]["record_count"] == day_rows
      and Decimal(day_total) == Decimal(day_sql)
      and d_day["actual"]["daily_average"]["salesvol"] == quotient(day_total, 7),
      "precision=%s days=%s avg=%s fact=%s" % (d_day["precision"],
                                               d_day["period"]["basis_days"],
                                               d_day["actual"]["daily_average"]["salesvol"],
                                               day_sql))

d_lag = derive({"month": "202609"}, actual="oil_sales_daily")
with session_scope() as s:
    lag_fact = s.execute(text(
        "select count(distinct day), max(day), sum(salesvol) from fact_oil_sales_daily "
        "where yyyymm='202609'")).one()
d_lag_total = d_lag["actual"]["aggregations"]["salesvol"]
check("数据滞后时按日历天数摊算并把「仅到哪天、几天有数」写进说明",
      d_lag["precision"] == "day"
      and int(lag_fact[0]) < d_lag["period"]["basis_days"]
      and d_lag["actual"]["days_with_data"] == int(lag_fact[0])
      and d_lag["actual"]["data_through"] == str(lag_fact[1])
      and Decimal(d_lag_total) == Decimal(str(lag_fact[2]))
      and d_lag["actual"]["daily_average"]["salesvol"] == quotient(d_lag_total, 30)
      and "数据仅到 %s" % lag_fact[1] in d_lag["notice"]
      and "期间内 %s 天有实际数据" % lag_fact[0] in d_lag["notice"],
      "days=%s basis=%s through=%s avg=%s notice=%s" % (
          d_lag["actual"]["days_with_data"], d_lag["period"]["basis_days"],
          d_lag["actual"]["data_through"], d_lag["actual"]["daily_average"]["salesvol"],
          d_lag["notice"][:60]))

d_full = derive({"month": "202608"}, actual="oil_sales_daily")
check("覆盖完整时不加滞后说明（不把每天都有的期间说成缺数）",
      "数据仅到" not in str(d_full.get("notice") or "")
      and (d_full["actual"]["days_with_data"] or 0) == d_full["period"]["basis_days"],
      "days=%s basis=%s notice=%s" % (d_full["actual"]["days_with_data"],
                                      d_full["period"]["basis_days"],
                                      str(d_full.get("notice"))[:60]))

d_none = derive({"month": "202609"}, plan_ds="oil_sales_plan")
check("没有实际数据时如实说明，不按 0 计算完成率与欠进度",
      d_none["actual"]["has_data"] is False
      and "没有符合条件的实际数据" in d_none["notice"]
      and d_none["progress"]["available"] is False
      and "不按 0" in d_none["progress"]["message"]
      and "completion_rate" not in d_none["progress"],
      "%s | %s" % (str(d_none.get("notice"))[:60],
                   str(d_none["progress"].get("message"))[:60]))

d_noplan = derive({"month": "202608"}, plan_ds="oil_sales_plan",
                  plan_metric="monthlyamount")
check("计划缺失或为 0 时不给完成率",
      d_noplan["progress"]["available"] is False
      and "计划为 0 或缺失" in d_noplan["progress"]["message"],
      str(d_noplan["progress"].get("message"))[:80])

d_org = derive({"month": "202608"}, base={"organization": "示例组织乙"},
               plan_ds="oil_sales_plan")
company_plan = derive({"month": "202608"}, plan_ds="oil_sales_plan")["progress"]["plan"]
check("地点在计划数据集对不上时显式说明（不静默答成全公司进度）",
      d_org["actual"]["has_data"] is True and d_org["actual"]["record_count"] > 0
      and d_org["progress"]["available"] is False
      and "示例组织乙" in d_org["progress"]["message"]
      and "业务域甲计划" in d_org["progress"]["message"]
      and Decimal(company_plan) > 0,
      "rows=%s | %s" % (d_org["actual"]["record_count"],
                        str(d_org["progress"].get("message"))[:80]))

# 组织层级一级欠进度：计划数据集只有变长组织编码、没有名称列，组织名要经引擎的
# 编码前缀桥落到该列真实节点（示例区域甲组织层级一 → 100047 → 下辖定位单元编码）。
hk_plan_rows, hk_plan_sql = fact_total(
    "fact_oil_sales_plan", "monsalesplan",
    "yyyymm='202608' and substr(unitcode,1,6)='100047'")
hk_act_rows, hk_act_sql = fact_total(
    "fact_oil_monthly_sales", "monthlysales",
    "yyyymm='202608' and bigareacode='100047'")
d_bridge = derive({"month": "202608"}, base={"organization": "示例区域甲"},
                  plan_ds="oil_sales_plan")
d_bridge_query = engine_call("/api/v1/engine/query", {
    "dataset": "oil_sales_plan", "datasets": [],
    "conditions": {"month": "202608"}, "filters": {"month": "202608"},
    "base_filters": {"organization": "示例区域甲"},
    "group_by": [], "metrics": [], "comparisons": [],
})
bridge_actual = d_bridge_query["actual"]
bridge_conditions = json.dumps(
    d_bridge_query["query_plan"].get("compiled_conditions", {}), default=str)
check("组织层级一组织名在计划数据集经编码前缀桥落到真实定位单元编码并按吨复算",
      d_bridge["progress"]["available"] is True
      and Decimal(d_bridge["progress"]["plan"]) == Decimal(hk_plan_sql)
      and Decimal(d_bridge["progress"]["actual"]) == Decimal(hk_act_sql)
      and d_bridge["progress"]["plan"] != company_plan
      and bridge_actual["record_count"] == hk_plan_rows
      and Decimal(bridge_actual["aggregations"]["monsalesplan"]) == Decimal(hk_plan_sql)
      and "100047001001" in bridge_conditions
      and hk_act_rows > 0,
      "plan=%s fact=%s act=%s rows=%s/%s" % (d_bridge["progress"]["plan"], hk_plan_sql,
                                             d_bridge["progress"]["actual"],
                                             bridge_actual["record_count"], hk_plan_rows))

d_prog = d_aug["progress"]
check("完成率/时间进度/欠进度全部来自引擎 Decimal 复算",
      d_prog["available"] is True
      and d_prog["actual"] == aug_total
      and d_prog["completion_rate"] == str(
          (Decimal(aug_total) / Decimal(d_prog["plan"]) * 100)
          .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
      and d_prog["gap_points"] == str(
          (Decimal(d_prog["completion_rate"]) - Decimal(d_prog["time_progress"]))
          .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
      and Decimal(d_prog["expected_by_progress"]) == (
          Decimal(d_prog["plan"]) * Decimal(d_aug["period"]["elapsed_days"])
          / Decimal(d_aug["period"]["period_days"])).quantize(Decimal("0.00001"))
      and Decimal(d_prog["gap_value"]) == (
          Decimal(aug_total) - Decimal(d_prog["expected_by_progress"]))
      and d_prog["status"] == ("on_track" if Decimal(d_prog["gap_points"]) >= 0
                               else "behind"),
      "完成率=%s 时间进度=%s 欠进度=%s 缺口=%s" % (
          d_prog["completion_rate"], d_prog["time_progress"],
          d_prog["gap_points"], d_prog["gap_value"]))

try:
    chat_avg = ask("2026年8月产品日均业务数量是多少？")
    avg_answer = chat_avg["answer"]
    check("对话端日均答案用引擎数字且不再把派生词当地点",
          d_real["daily_average"]["monthlysales"] in avg_answer
          and "31 天" in avg_answer
          and not (chat_avg["intent"].get("base_filters") or {}),
          "%s | base=%s" % (avg_answer[:80],
                            json.dumps(chat_avg["intent"].get("base_filters"),
                                       ensure_ascii=False)))
    chat_gap = ask("8月欠进度怎么样？")
    gap_answer = chat_gap["answer"]
    check("对话端欠进度给出完成率与百分点差（含缺口量）",
          d_prog["completion_rate"] in gap_answer
          and d_prog["gap_points"] in gap_answer
          and "个百分点" in gap_answer,
          gap_answer.splitlines()[-1][:120])
    # 「业务域甲计划」是数据集名、「实际/完成」是侧别与提问用词，4 字窗口曾经
    # 连连接词一起切成"和实际完"当 organization 下传：数值没错但答案挂着一条
    # "该取值不存在"的提示，示例问题读起来像坏了。
    chat_pair = ask("2026年8月业务域甲计划和实际完成情况？")
    check("「计划和实际完成情况」不抽出假组织条件（答案不带无意义提示）",
          not (chat_pair["intent"].get("base_filters") or {})
          and "提示" not in chat_pair["answer"]
          and "84279" in chat_pair["answer"],
          "%s | base=%s" % (chat_pair["answer"][:80],
                            json.dumps(chat_pair["intent"].get("base_filters"),
                                       ensure_ascii=False)))
except Exception as e:
    check("对话端日均答案用引擎数字且不再把派生词当地点", False, str(e))
    check("对话端欠进度给出完成率与百分点差（含缺口量）", False, str(e))
    check("「计划和实际完成情况」不抽出假组织条件（答案不带无意义提示）", False, str(e))

# ============ 16. 异常输入 ============
# 完全无可查锚点的文本必须澄清反问。它曾经降级成"全量概览"，等于把乱码
# 答成一份带合计的业务报告——宁缺不扩，也没义务替一句乱码编一个答案。
# 空串不在这里测：它在接口边界就是 422 参数校验，与语义层无关。
for bad in ["asdfgh乱码???", "今天天气怎么样"]:
    try:
        r = ask(bad)
        print("异常输入 %r → %s" % (bad, r["answer"][:60]))
        check("无锚点输入改为澄清反问且不返回全量结果",
              "没能在你的问题里解析出查询条件" in r["answer"]
              and "合计" not in r["answer"]
              and bool(r["intent"].get("unresolved")),
              "%s → %s | unresolved=%s" % (
                  bad, r["answer"][:60], r["intent"].get("unresolved")))
    except Exception as e:
        check("无锚点输入改为澄清反问且不返回全量结果", False, "%s: %s" % (bad, e))

# ============ 17. 产品语义按主数据真实取值落层 ============
# 类别代码问题过去有两条坏路：Agent 自造 grade="92" 直发（绕过主数据，在没有"类别代码"
# 层的数据集上整条拒答），或者匹配不上时悄悄扩成整个品类（数字看着对、范围错了）。
# 现在只剩一条：主数据的物料编码与目标数据集的产品层按**真实取值**相交才落层。
def product_query(term, conditions=None, dataset="oil_monthly_sales"):
    return engine_call("/api/v1/engine/query", {
        "dataset": dataset, "datasets": [],
        "conditions": conditions or {}, "filters": conditions or {},
        "base_filters": {"product": term},
        "group_by": [], "metrics": [], "comparisons": [],
    })


def base_query(base_filters, dataset="oil_monthly_sales"):
    return engine_call("/api/v1/engine/query", {
        "dataset": dataset, "datasets": [],
        "conditions": {}, "filters": {},
        "base_filters": base_filters,
        "group_by": [], "metrics": [], "comparisons": [],
    })


with session_scope() as s:
    master_rows, master_codes = s.execute(text(
        "select count(*), count(distinct material_code) from dim_oil_master")).one()
check("产品主数据合并后一份声明一个粒度（44 行 / 44 个物料编码）",
      master_rows == 44 and master_codes == 44,
      "rows=%s codes=%s" % (master_rows, master_codes))

rows_92, sql_92 = fact_total("fact_oil_monthly_sales", "monthlyamount",
                             "gascode='60518726'")
a92 = product_query("类别代码甲")["actual"]
check("「类别代码甲」经主数据相交落到产品编码层真过滤==SQL 真值",
      a92["record_count"] == rows_92
      and Decimal(a92["aggregations"]["monthlyamount"]) == Decimal(sql_92)
      and a92["base_decision"]["compiled_conditions"] == {"gascode": "60518726"},
      "rows=%s/%s amount=%s fact=%s" % (a92["record_count"], rows_92,
                                        a92["aggregations"]["monthlyamount"], sql_92))

resolution = (a92["base_decision"].get("product_resolution") or [{}])[0]
check("产品落层依据写进查询计划（可审计为什么落在 gascode 而不是 category）",
      resolution.get("base_dataset") == "oil_master"
      and resolution.get("base_feature") == "material_code"
      and resolution.get("level") == "gascode"
      and resolution.get("resolution") == "product_value_intersection"
      and int(resolution.get("domain_overlap") or 0) > 0,
      json.dumps(resolution, ensure_ascii=False)[:120])

rows_gas, sql_gas = fact_total("fact_oil_monthly_sales", "monthlyamount",
                               "category='01'")
rows_gas_codes, sql_gas_codes = fact_total(
    "fact_oil_monthly_sales", "monthlyamount",
    "gascode in ('60518726','60518727','60523537')")
agas = product_query("类别甲")["actual"]
check("品类「类别甲」落到数据集自己的大类层，与按主数据编码展开逐位相同",
      agas["record_count"] == rows_gas == rows_gas_codes
      and Decimal(agas["aggregations"]["monthlyamount"]) == Decimal(sql_gas)
      and Decimal(sql_gas) == Decimal(sql_gas_codes)
      and agas["base_decision"]["compiled_conditions"] == {"category": "01"}
      and rows_gas > rows_92,
      "rows=%s/%s/%s amount=%s" % (agas["record_count"], rows_gas,
                                   rows_gas_codes, agas["aggregations"]["monthlyamount"]))

gres = (agas["base_decision"].get("product_resolution") or [{}])[0]
check("大类词落层依据可审计（主数据大类码整列域相通，不是别名表）",
      gres.get("base_dataset") == "oil_master"
      and gres.get("base_feature") == "type_code"
      and gres.get("level") == "category"
      and gres.get("resolution") == "category_value_intersection",
      json.dumps(gres, ensure_ascii=False)[:120])

# 同一句「类别甲」在计划表上按编码展开会筛掉 category=01 而 gascode 不是物料编码的行
# （X41②：少 231 行）。大类标记是数据集自己给的口径，命中大类词时应当优先。
rows_plan_gas, sql_plan_gas = fact_total("fact_oil_sales_plan", "monsalesplan",
                                         "category='01'")
plan_gas = product_query("类别甲", dataset="oil_sales_plan")["actual"]
check("同一句「类别甲」在计划表按大类层落层，不漏编码列里的非物料编码行",
      plan_gas["record_count"] == rows_plan_gas
      and Decimal(plan_gas["aggregations"]["monsalesplan"]) == Decimal(sql_plan_gas)
      and plan_gas["base_decision"]["compiled_conditions"] == {"category": "01"},
      "rows=%s/%s amount=%s" % (plan_gas["record_count"], rows_plan_gas,
                                plan_gas["aggregations"]["monsalesplan"]))

rows_day_gas, sql_day_gas = fact_total("fact_oil_sales_daily", "amount",
                                       "category='01'")
day_gas = product_query("类别甲", dataset="oil_sales_daily")["actual"]
check("日粒度上「类别甲」同样落大类层且与产品编码展开等价",
      day_gas["record_count"] == rows_day_gas
      and Decimal(day_gas["aggregations"]["amount"]) == Decimal(sql_day_gas),
      "rows=%s/%s amount=%s" % (day_gas["record_count"], rows_day_gas,
                                day_gas["aggregations"]["amount"]))

# 请求值本身就是事实表取值时不再喂给主数据展开：`01` 会被子串匹配命中物料编码里含
# `01` 的行、扩成两个别的产品编码，与事实表大类条件相与后静默筛成空集（X41①）。
verbatim = base_query({"category": "01"})["actual"]
check("基座大类码逐字命中时直接按该层过滤（曾经筛成空集且不报错）",
      verbatim["record_count"] == rows_gas
      and Decimal(verbatim["aggregations"]["monthlyamount"]) == Decimal(sql_gas)
      and (verbatim["base_decision"].get("product_resolution") or [{}])[0].get(
          "resolution") == "verbatim_fact_value",
      "rows=%s/%s res=%s" % (
          verbatim["record_count"], rows_gas,
          json.dumps((verbatim["base_decision"].get("product_resolution") or [{}])[0],
                     ensure_ascii=False)[:80]))

# 同一个短码可能在两层都逐字存在：计划表 gascode 串进了 `01`（231 行杂码），
# 整列却是 8 位物料编码域。按整列编码宽度挑层，落错层就是 231 行 vs 80615 行。
rows_plan_stray, _ = fact_total("fact_oil_sales_plan", "monsalesplan", "gascode='01'")
plan_verbatim = base_query({"category": "01"}, dataset="oil_sales_plan")["actual"]
check("短码两层都逐字存在时按整列编码宽度落大类层（不吃串进来的杂码）",
      plan_verbatim["record_count"] == rows_plan_gas
      and rows_plan_stray < rows_plan_gas
      and plan_verbatim["base_decision"]["compiled_conditions"] == {"category": "01"},
      "rows=%s/%s stray=%s" % (plan_verbatim["record_count"], rows_plan_gas,
                               rows_plan_stray))

rows_plan_lpg, sql_plan_lpg = fact_total("fact_oil_sales_plan", "monsalesplan",
                                         "category='03'")
plan_lpg = product_query("类别丁", dataset="oil_sales_plan")["actual"]
check("「类别丁」按计划表自己的大类层落层（编码域不相通时不再只能拒答）",
      plan_lpg["record_count"] == rows_plan_lpg
      and Decimal(plan_lpg["aggregations"]["monsalesplan"]) == Decimal(sql_plan_lpg)
      and plan_lpg["base_decision"]["compiled_conditions"] == {"category": "03"},
      "rows=%s/%s amount=%s" % (plan_lpg["record_count"], rows_plan_lpg,
                                plan_lpg["aggregations"]["monsalesplan"]))

# 数据里没有的类别代码必须显式拒答。它曾经被判成"整个类别甲"，比查不到危险得多。
q93 = product_query("类别代码乙")
a93 = q93["actual"]
check("数据中不存在的类别代码显式拒答（不扩成整个品类、不返回未过滤全量）",
      a93["record_count"] == 0 and a93["aggregations"] == {}
      and q93["query_plan"]["unsupported"]["values"] == {"category": ["93"]}
      and "无法执行" in str(q93["query_plan"]["unsupported"].get("message"))
      and bool(a93["base_decision"].get("warnings"))
      and a93["base_decision"].get("product_resolution") is None,
      "msg=%s | warnings=%s" % (
          str(q93["query_plan"]["unsupported"].get("message"))[:50],
          json.dumps(a93["base_decision"].get("warnings"), ensure_ascii=False)))

try:
    rows_92_25, sql_92_25 = fact_total(
        "fact_oil_monthly_sales", "monthlyamount",
        "gascode='60518726' and yyyymm like '2025%'")
    chat_grade = ask("2025年类别代码甲业务金额是多少？")
    check("对话端类别代码问题只下传基座产品词，数字来自主数据落层后的 SQL 真值",
          str(rows_92_25) in chat_grade["answer"]
          and sql_92_25 in chat_grade["answer"]
          and (chat_grade["intent"].get("base_filters") or {}).get("product") == "类别代码甲"
          and "grade" not in json.dumps(chat_grade["intent"].get("conditions") or {},
                                        default=str),
          "%s | base=%s" % (chat_grade["answer"][:60],
                            json.dumps(chat_grade["intent"].get("base_filters"),
                                       ensure_ascii=False)))
except Exception as e:
    check("对话端类别代码问题只下传基座产品词，数字来自主数据落层后的 SQL 真值", False, str(e))

print("\n==== 汇总：%d/%d 通过 ====" %
      (sum(1 for _, ok, _ in results if ok), len(results)))
sys.exit(0 if all(ok for _, ok, _ in results) else 1)

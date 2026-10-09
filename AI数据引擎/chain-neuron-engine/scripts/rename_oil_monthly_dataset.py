# -*- coding: utf-8 -*-
"""把产品月粒度数据集从误名 oil_sales（业务域甲日报）改名注册为
oil_monthly_sales（业务域甲月报），并把日粒度数据集的中文名去掉「（日粒度）」后缀。

声明沿用 reregister_oil.py 的层级/指标/附加列，基座特征计划（feature_layers）
从现存 oil_sales 声明原样带过来，避免改名后组织/定位单元语义丢失。

用法（引擎需在 8000 端口运行，且 data/blobs/ 里有 定位单元级业务域甲日报_2024后.xlsx）：
  python scripts/rename_oil_monthly_dataset.py            # 注册 + 导入 + 校验 + 改日粒度名
  python scripts/rename_oil_monthly_dataset.py --no-import  # 只注册声明，不重导数据
注销旧数据集是破坏性动作，本脚本不做，需人工确认后单独调用
  DELETE /api/v1/engine/datasets/oil_sales?drop_data=true
"""
import json
import sys
import time
import urllib.request
from urllib.parse import urlencode

BASE = "http://127.0.0.1:8000"
OLD = "oil_sales"
NEW = "oil_monthly_sales"
NEW_LABEL = "业务域甲月报"
DAILY = "oil_sales_daily"
DAILY_LABEL = "业务域甲日报"
FILE_TOKEN = "定位单元级业务域甲日报_2024后.xlsx"

# 与 scripts/reregister_oil.py 的 oil_sales 声明一致（月粒度事实表结构不变）
DERIVED = [
    {"key": "year", "label": "年", "derive": True, "key_of": "year"},
    {"key": "season", "label": "季节", "derive": True, "key_of": "season"},
    {"key": "month", "label": "月", "derive": True, "key_of": "yyyymm"},
    {"key": "week", "label": "周", "derive": True, "key_of": "week"},
]
DRAFT = {
    "kind": "generic",
    "label": NEW_LABEL,
    "time": {"source": "Month", "format": "yyyymm"},
    "levels": DERIVED + [
        {"key": "bigareacode", "label": "组织层级一编码", "source": "BigAreaCode", "name": "bigareacode"},
        {"key": "branchcode", "label": "组织层级二编码", "source": "BranchCode", "name": "branchcode"},
        {"key": "areacode", "label": "组织层级三编码", "source": "AreaCode", "name": "areacode"},
        {"key": "oucode", "label": "定位单元组织编码", "source": "OUCode", "name": "oucode"},
        {"key": "category", "label": "产品大类", "source": "Category", "name": "category"},
        {"key": "gascode", "label": "产品编码", "source": "GasCode", "name": "gascode"},
    ],
    "metrics": [
        {"source": "MonthlySales", "name": "monthlysales", "label": "业务吨数"},
        {"source": "MonthlyQty", "name": "monthlyqty", "label": "业务升数"},
        {"source": "MonthlyAmount", "name": "monthlyamount", "label": "业务金额"},
    ],
    "columns": [
        {"source": "UniversalCode", "name": "universalcode"},
        {"source": "ShortName", "name": "shortname"},
        {"source": "SalesMode", "name": "salesmode"},
        {"source": "CheckMode", "name": "checkmode"},
        {"source": "DistributionChannel", "name": "distributionchannel"},
        {"source": "BranchName", "name": "branchname"},
        {"source": "AreaName", "name": "areaname"},
        {"source": "BigAreaName", "name": "bigareaname"},
        {"source": "CompanyNature", "name": "companynature"},
        {"source": "Remark", "name": "remark"},
        {"source": "lg_AreaCode", "name": "lg_areacode"},
    ],
}

# 对账基线：改名重导后必须与源文件全量导入结果分毫不差
ROOT = {"record_count": 153183,
        "monthlysales": "3413020.40145", "monthlyqty": "4338912485.49",
        "monthlyamount": "35735013808.89"}
Y2024_AMOUNT = "14138252049.15"


def _req(method, path, token=None, body=None, form=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def wait_task(token, task_uuid):
    t = {}
    for _ in range(900):
        time.sleep(2)
        _, t = _req("GET", f"/api/v1/engine/import/tasks/{task_uuid}", token)
        if t.get("status") in ("success", "partial", "failed"):
            return t
    return t


def agg(token, dataset, conditions=None):
    st, res = _req("POST", "/api/v1/engine/aggregations/query", token,
                   body={"dataset": dataset, "conditions": conditions or {}})
    assert st == 200, (st, res)
    return res["nodes"][0]


def main():
    no_import = "--no-import" in sys.argv
    _, tok = _req("POST", "/api/v1/auth/login",
                  body={"username": "admin", "password": "admin123"})
    token = tok["access_token"]

    st, old_spec = _req("GET", f"/api/v1/engine/datasets/{OLD}", token)
    assert st == 200, (st, old_spec)
    feature_layers = (old_spec.get("capabilities") or {}).get("feature_layers")
    print(f"{OLD} 现有 feature_layers 层：", sorted(feature_layers or {}),
          "基座列数：", len((feature_layers or {}).get("base") or []))
    draft = dict(DRAFT)
    if feature_layers:
        draft["feature_layers"] = feature_layers

    st, res = _req("PUT", f"/api/v1/engine/datasets/{NEW}", token, body=draft)
    print(f"注册 {NEW}:", st, res.get("message") or json.dumps(res, ensure_ascii=False)[:300])
    assert st == 200, res

    if not no_import:
        st, res = _req("POST", f"/api/v1/engine/datasets/{NEW}/import", token,
                       form={"mode": "replace", "file_token": FILE_TOKEN})
        print(f"{NEW} 导入:", st, res.get("task_uuid"))
        assert st == 200, res
        t = wait_task(token, res["task_uuid"])
        print(f"{NEW} task:", t.get("status"), "succeeded:", t.get("succeeded"),
              "failed:", t.get("failed"))
        if t.get("status") != "success":
            print("errors:", json.dumps(t.get("error_sample", [])[:3], ensure_ascii=False))
            raise SystemExit("导入未成功，旧数据集保持不动")

        node = agg(token, NEW)
        got = {"record_count": node["record_count"], **node["aggregations"]}
        print("root 对账:", json.dumps(got, ensure_ascii=False))
        assert got == ROOT, (got, ROOT)
        y = agg(token, NEW, {"year": "2024"})
        print("2024 金额:", y["aggregations"]["monthlyamount"], "行数:", y["record_count"])
        assert y["aggregations"]["monthlyamount"] == Y2024_AMOUNT

    # 日粒度只改中文名：PUT 是 create(checkfirst)+ensure_columns，不动数据
    st, daily_spec = _req("GET", f"/api/v1/engine/datasets/{DAILY}", token)
    assert st == 200, (st, daily_spec)
    value_keys = set(daily_spec["level_value_key"].values())
    derived_keys = set(daily_spec.get("derive") or [])
    metric_names = {m["name"] for m in daily_spec["metrics"]}
    sources = daily_spec["column_sources"]
    daily_draft = {
        "kind": daily_spec["kind"],
        "label": DAILY_LABEL,
        "time": daily_spec["time"],
        "levels": [
            ({"key": k, "label": daily_spec["level_labels"].get(k) or k, "derive": True,
              "key_of": daily_spec["level_value_key"][k]}
             if daily_spec["level_value_key"][k] in derived_keys
             else {"key": k, "label": daily_spec["level_labels"].get(k) or k,
                   "source": sources[daily_spec["level_value_key"][k]],
                   "name": daily_spec["level_value_key"][k]})
            for k in daily_spec["levels"]
        ],
        "metrics": [{"source": sources[m["name"]], "name": m["name"], "label": m["label"]}
                    for m in daily_spec["metrics"]],
        "columns": [{"source": sources[c], "name": c}
                    for c in daily_spec["fact_columns"]
                    if c in sources and c not in metric_names and c not in value_keys],
    }
    if (daily_spec.get("capabilities") or {}).get("feature_layers"):
        daily_draft["feature_layers"] = daily_spec["capabilities"]["feature_layers"]
    st, res = _req("PUT", f"/api/v1/engine/datasets/{DAILY}", token, body=daily_draft)
    print(f"{DAILY} 改名:", st, res.get("message") or json.dumps(res, ensure_ascii=False)[:300])
    assert st == 200, res
    _, after = _req("GET", f"/api/v1/engine/datasets/{DAILY}", token)
    print("日粒度 label:", after["label"], "| 行数仍为：", agg(token, DAILY)["record_count"])


if __name__ == "__main__":
    main()

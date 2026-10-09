# -*- coding: utf-8 -*-
"""一次性脚本：按修剪后的层级声明重注册 oil_monthly_sales / oil_sales_plan 并 replace 重导入。

层级链：年→季→月→周→组织层级一编码→组织层级二编码→组织层级三编码→定位单元→大类→产品编码；
名称/支付方式等移出层级、保留为附加事实列。
用法：python scripts/reregister_oil.py（引擎需已在 8000 端口运行，且已加载阈值修剪代码）
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"

DERIVED = [
    {"key": "year", "label": "年", "derive": True, "key_of": "year"},
    {"key": "season", "label": "季节", "derive": True, "key_of": "season"},
    {"key": "month", "label": "月", "derive": True, "key_of": "yyyymm"},
    {"key": "week", "label": "周", "derive": True, "key_of": "week"},
]

OIL_SALES = {
    "dataset": "oil_monthly_sales",
    "file_token": "定位单元级业务域甲日报_2024后.xlsx",
    "draft": {
        "kind": "generic",
        "label": "业务域甲月报",
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
    },
}

OIL_PLAN = {
    "dataset": "oil_sales_plan",
    "file_token": "定位单元业务域甲计划_2020后.xlsx",
    "draft": {
        "kind": "generic",
        "label": "业务域甲计划",
        "time": {"source": "Monthly", "format": "yyyymm"},
        "levels": DERIVED + [
            {"key": "unitcode", "label": "组织层级编码", "source": "UnitCode", "name": "unitcode"},
            {"key": "category", "label": "产品类型", "source": "Category", "name": "category"},
            {"key": "gascode", "label": "产品编码", "source": "GasCode", "name": "gascode"},
        ],
        "metrics": [
            {"source": "MonSalesPlan", "name": "monsalesplan", "label": "吨数业务计划"},
        ],
        "columns": [
            {"source": "UnitName", "name": "unitname"},
            {"source": "DistributionChannel", "name": "distributionchannel"},
            {"source": "UniversalCode", "name": "universalcode"},
        ],
    },
}


def _req(method, path, token=None, body=None, form=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        from urllib.parse import urlencode
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


def wait_task(token, task):
    for _ in range(300):
        time.sleep(2)
        st, t = _req("GET", f"/api/v1/engine/import/tasks/{task}", token)
        if t["status"] in ("success", "partial", "failed"):
            return t
    return t


def main():
    st, tok = _req("POST", "/api/v1/auth/login",
                   body={"username": "admin", "password": "admin123"})
    assert st == 200, (st, tok)
    token = tok["access_token"]

    for cfg in (OIL_SALES, OIL_PLAN):
        ds = cfg["dataset"]
        st, res = _req("PUT", f"/api/v1/engine/datasets/{ds}", token, body=cfg["draft"])
        print(f"[{ds}] register:", st, res.get("message") or res)
        assert st == 200, res

        st, res = _req("POST", f"/api/v1/engine/datasets/{ds}/import", token,
                       form={"mode": "replace", "file_token": cfg["file_token"]})
        print(f"[{ds}] import:", st)
        assert st == 200, res
        t = wait_task(token, res["task_uuid"])
        print(f"[{ds}] task:", t["status"], "processed:", t.get("processed"),
              "succeeded:", t.get("succeeded"), "failed:", t.get("failed"))
        if t.get("error_sample"):
            print(f"[{ds}] errors:", json.dumps(t["error_sample"][:5], ensure_ascii=False))
        assert t["status"] in ("success", "partial"), t


if __name__ == "__main__":
    main()

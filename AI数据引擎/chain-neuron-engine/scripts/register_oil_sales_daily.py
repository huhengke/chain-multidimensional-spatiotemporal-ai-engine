# -*- coding: utf-8 -*-
"""注册日粒度业务数据甲数据集 oil_sales_daily（时间列 WorkDay，格式 yyyy-mm-dd）。

指标只取日值列（SalesVol/Qty/Amount），源文件里的 Monthly* 月累计列一律忽略，
避免同一事实表里混入两种口径。
用法：
  python scripts/register_oil_sales_daily.py --declare-only   # 只校验声明、不导入
  python scripts/register_oil_sales_daily.py                 # 声明 + replace 导入
引擎需已在 8000 端口运行。
"""
import json
import sys
import time
import urllib.request
from urllib.parse import urlencode

BASE = "http://127.0.0.1:8000"
DATASET = "oil_sales_daily"
FILE_TOKEN = "定位单元级业务域甲日报_4月5月.xlsx"

DRAFT = {
    "kind": "generic",
    "label": "业务域甲日报",
    "time": {"source": "WorkDay", "format": "yyyy-mm-dd"},
    "levels": [
        {"key": "year", "label": "年", "derive": True, "key_of": "year"},
        {"key": "season", "label": "季节", "derive": True, "key_of": "season"},
        {"key": "month", "label": "月", "derive": True, "key_of": "yyyymm"},
        {"key": "week", "label": "周", "derive": True, "key_of": "week"},
        {"key": "day", "label": "日", "derive": True, "key_of": "day"},
        {"key": "bigareacode", "label": "组织层级一编码", "source": "BigAreaCode", "name": "bigareacode"},
        {"key": "branchcode", "label": "组织层级二编码", "source": "BranchCode", "name": "branchcode"},
        {"key": "areacode", "label": "组织层级三编码", "source": "AreaCode", "name": "areacode"},
        {"key": "oucode", "label": "定位单元组织编码", "source": "OUCode", "name": "oucode"},
        {"key": "category", "label": "产品大类", "source": "Category", "name": "category"},
        {"key": "gascode", "label": "产品编码", "source": "GasCode", "name": "gascode"},
    ],
    "metrics": [
        {"source": "SalesVol", "name": "salesvol", "label": "业务吨数"},
        {"source": "Qty", "name": "qty", "label": "业务升数"},
        {"source": "Amount", "name": "amount", "label": "业务金额"},
    ],
    "columns": [
        {"source": "UniversalCode", "name": "universalcode"},
        {"source": "ShortName", "name": "shortname"},
        {"source": "SalesMode", "name": "salesmode"},
        {"source": "CheckMode", "name": "checkmode"},
        {"source": "BranchName", "name": "branchname"},
        {"source": "AreaName", "name": "areaname"},
        {"source": "BigAreaName", "name": "bigareaname"},
        {"source": "Price", "name": "price"},
        {"source": "Guns", "name": "guns"},
        {"source": "ISOperate", "name": "isoperate"},
        {"source": "DataType", "name": "datatype"},
        {"source": "Remark", "name": "remark"},
    ],
}


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


def wait_task(token, task):
    t = {}
    for _ in range(600):
        time.sleep(2)
        st, t = _req("GET", f"/api/v1/engine/import/tasks/{task}", token)
        if t.get("status") in ("success", "partial", "failed"):
            return t
    return t


def main():
    declare_only = "--declare-only" in sys.argv
    st, tok = _req("POST", "/api/v1/auth/login",
                   body={"username": "admin", "password": "admin123"})
    assert st == 200, (st, tok)
    token = tok["access_token"]

    st, res = _req("PUT", f"/api/v1/engine/datasets/{DATASET}", token, body=DRAFT)
    print(f"[{DATASET}] register:", st, res.get("message") or json.dumps(res, ensure_ascii=False)[:400])
    assert st == 200, res
    if declare_only:
        return

    st, res = _req("POST", f"/api/v1/engine/datasets/{DATASET}/import", token,
                   form={"mode": "replace", "file_token": FILE_TOKEN})
    print(f"[{DATASET}] import:", st, res.get("task_uuid") or res)
    assert st == 200, res
    t = wait_task(token, res["task_uuid"])
    print(f"[{DATASET}] task:", t.get("status"), "processed:", t.get("processed"),
          "succeeded:", t.get("succeeded"), "failed:", t.get("failed"))
    if t.get("error_sample"):
        print(f"[{DATASET}] errors:", json.dumps(t["error_sample"][:5], ensure_ascii=False))


if __name__ == "__main__":
    main()
